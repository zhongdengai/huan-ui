use tauri::{Manager, Emitter};
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use chrono::Local;
use regex;
use base64::{Engine as _, engine::general_purpose};
use std::io::{BufRead, BufReader, Write};
use std::sync::Mutex;

/// STT 常驻 Python 进程的状态
struct SttServer {
    script_path: PathBuf,
    stdin:  Option<std::process::ChildStdin>,
    stdout: Option<BufReader<std::process::ChildStdout>>,
}

impl SttServer {
    fn new(script_path: PathBuf) -> Self {
        SttServer { script_path, stdin: None, stdout: None }
    }

    /// 确保 Python 服务进程在运行，返回 Err 说明原因
    fn ensure_running(&mut self) -> Result<(), String> {
        // 如果 stdin 管道还活着，认为进程在运行
        if self.stdin.is_some() {
            return Ok(());
        }
        self.start()
    }

    fn start(&mut self) -> Result<(), String> {
        let python = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "python3"]
            .iter()
            .find(|p| std::path::Path::new(p).exists())
            .copied()
            .unwrap_or("python3");
        let mut child = std::process::Command::new(python)
            .arg(&self.script_path)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map_err(|e| format!("无法启动 STT 服务: {}", e))?;

        let child_stdin  = child.stdin.take().ok_or("无法获取 stdin")?;
        let child_stdout = child.stdout.take().ok_or("无法获取 stdout")?;
        let mut reader   = BufReader::new(child_stdout);

        // 等待 READY 信号（最多 10 秒）
        let mut line = String::new();
        reader.read_line(&mut line).map_err(|e| format!("等待 STT 就绪失败: {}", e))?;
        let line = line.trim();
        if line == "IMPORT_ERROR" {
            return Err("SpeechRecognition 未安装，请运行: pip3 install --break-system-packages SpeechRecognition".into());
        }
        if line != "READY" {
            return Err(format!("STT 服务启动异常: {}", line));
        }

        // 把子进程 move 出去，防止被 drop
        std::mem::forget(child);

        self.stdin  = Some(child_stdin);
        self.stdout = Some(reader);
        Ok(())
    }

    /// 发送识别任务，返回识别结果
    fn recognize(&mut self, wav_path: &str, result_path: &str, lang: &str) -> Result<String, String> {
        self.ensure_running()?;

        // 发送任务行
        if let Some(stdin) = &mut self.stdin {
            writeln!(stdin, "{}|{}|{}", wav_path, result_path, lang)
                .map_err(|_| {
                    // 管道断了，清除状态让下次重启
                    self.stdin  = None;
                    self.stdout = None;
                    "STT 服务管道断开，请重试".to_string()
                })?;
        }

        // 等待 DONE
        if let Some(reader) = &mut self.stdout {
            let mut line = String::new();
            reader.read_line(&mut line).map_err(|_| {
                self.stdin  = None;
                self.stdout = None;
                "STT 服务无响应，请重试".to_string()
            })?;
        }

        // 读取结果文件
        let result = fs::read_to_string(result_path).unwrap_or_default();
        let _ = fs::remove_file(result_path);
        Ok(result.trim().to_string())
    }
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&url)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(not(target_os = "macos"))]
    {
        open::that(&url).map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn hide_window(app: tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
}

#[tauri::command]
fn show_window(app: tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
    }
}

/// 流式调用 huan-ui 的 HTTP API 进行对话
/// 逐个 chunk 发送给前端，通过 Tauri 事件系统
#[tauri::command]
async fn chat(app: tauri::AppHandle, message: String, session_id: Option<String>) -> Result<String, String> {
    // 获取或创建 session ID
    let sid = if let Some(s) = session_id {
        if !s.is_empty() { s } else { create_new_session().await? }
    } else {
        create_new_session().await?
    };

    eprintln!("[chat] Starting streaming call to huan-ui. Session: {}, Message: {}", sid, message);

    // 调用 huan-ui HTTP API（获取流式响应）
    // 禁用代理，确保localhost请求直接连接到本地huan-ui
    let client = reqwest::Client::builder()
        .no_proxy()
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {}", e))?;
    let url = "http://localhost:8868/api/chat";

    let payload = serde_json::json!({
        "session_id": sid,
        "message": message,
        "model": "MiniMax-M2.7",
        "workspace": std::env::var("HOME").unwrap_or_else(|_| ".".to_string())
    });

    let response = client
        .post(url)
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("Failed to call huan-ui API: {}", e))?;

    let status = response.status();

    // session不存在时（服务器重启等），自动重建session并重试
    if status == reqwest::StatusCode::NOT_FOUND {
        eprintln!("[chat] Session {} not found (server restarted?), creating new session...", sid);
        let new_sid = create_new_session().await?;
        eprintln!("[chat] Retrying with new session: {}", new_sid);

        // 立即保存新session ID到文件，让JS下次用正确的ID
        let config_path = format!(
            "{}/Documents/huanhuan/config/currentSession.txt",
            std::env::var("HOME").unwrap_or_else(|_| ".".to_string())
        );
        let _ = std::fs::write(&config_path, &new_sid);

        let retry_payload = serde_json::json!({
            "session_id": new_sid,
            "message": message,
            "model": "MiniMax-M2.7",
            "workspace": std::env::var("HOME").unwrap_or_else(|_| ".".to_string())
        });
        let retry_response = client
            .post(url)
            .json(&retry_payload)
            .send()
            .await
            .map_err(|e| format!("Failed to retry huan-ui API: {}", e))?;

        if !retry_response.status().is_success() {
            let err = retry_response.text().await.unwrap_or_default();
            return Err(format!("huan-ui API error after retry: {}", err));
        }

        // 通知JS用新session ID（作为特殊的"chat-stream-end"数据）
        let _ = app.emit("chat-session-renewed", serde_json::json!({ "session_id": new_sid }));
        return process_response(app, retry_response, new_sid).await;
    }

    if !status.is_success() {
        let error_text = response.text().await.unwrap_or_default();
        return Err(format!("huan-ui API error ({}): {}", status, error_text));
    }

    process_response(app, response, sid).await
}

/// 解析huan-ui响应，发送流式事件给前端，返回session ID
async fn process_response(app: tauri::AppHandle, response: reqwest::Response, sid: String) -> Result<String, String> {
    // 收集完整响应（huan-ui 返回的是完整 JSON，多行格式）
    let mut full_response = String::new();
    use futures_util::StreamExt;
    let mut stream = response.bytes_stream();

    while let Some(chunk_result) = stream.next().await {
        let chunk = chunk_result
            .map_err(|e| format!("Failed to read stream: {}", e))?;
        let chunk_str = String::from_utf8_lossy(&chunk);
        full_response.push_str(&chunk_str);
    }

    eprintln!("[stream] Full response length: {}", full_response.len());
    let preview = full_response.chars().take(200).collect::<String>();
    eprintln!("[stream] Full response preview: {}", preview);

    // 解析完整 JSON
    if let Ok(json_val) = serde_json::from_str::<serde_json::Value>(&full_response) {
        // 尝试从多个位置提取回复内容
        let reply_content = if let Some(final_response) = json_val.get("result")
            .and_then(|r| r.get("final_response"))
            .and_then(|v| v.as_str()) {
            final_response.to_string()
        } else if let Some(content) = json_val.get("content").and_then(|v| v.as_str()) {
            content.to_string()
        } else if let Some(answer) = json_val.get("answer").and_then(|v| v.as_str()) {
            answer.to_string()
        } else {
            eprintln!("[stream] Could not find reply content in JSON");
            return Err("Could not extract reply from huan-ui response".to_string());
        };

        eprintln!("[stream] Got reply: {}", reply_content);

        // 检查是否包含 <think> 标签
        let has_think = reply_content.contains("<think>");
        if !has_think {
            let result = app.emit("chat-think-end", ());
            eprintln!("[stream] No <think> tag found, emitted chat-think-end: {:?}", result);
        }

        // 逐个字符处理，过滤 <think> 标签
        let mut inside_think = false;
        let mut i = 0;
        let mut sent_count = 0;
        let chars: Vec<char> = reply_content.chars().collect();

        while i < chars.len() {
            let substr: String = chars[i..].iter().collect();
            if !inside_think && substr.starts_with("<think>") {
                inside_think = true;
                i += 7;
            } else if inside_think && substr.starts_with("</think>") {
                inside_think = false;
                i += 8;
                let _ = app.emit("chat-think-end", ());
            } else if inside_think {
                i += 1;
            } else {
                let _ = app.emit("chat-stream", serde_json::json!({ "token": chars[i].to_string() }));
                sent_count += 1;
                i += 1;
            }
        }

        eprintln!("[stream] Total chars sent: {}", sent_count);
        let _ = app.emit("chat-stream-end", serde_json::json!({ "total": sent_count }));
    } else {
        eprintln!("[stream] Failed to parse JSON response");
        return Err("Failed to parse huan-ui JSON response".to_string());
    }

    eprintln!("[chat] Streaming complete for session: {}", sid);
    Ok(sid)
}

/// 创建新会话，返回 session_id
async fn create_new_session() -> Result<String, String> {
    // 调用 huan-ui 的 /api/session/new 端点创建会话
    let client = reqwest::Client::builder()
        .no_proxy()
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {}", e))?;

    let url = "http://localhost:8868/api/session/new";
    let payload = serde_json::json!({});

    let response = client
        .post(url)
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("Failed to call /api/session/new: {}", e))?;

    let status = response.status();
    if !status.is_success() {
        let error_text = response.text().await.unwrap_or_default();
        return Err(format!("Failed to create session ({}): {}", status, error_text));
    }

    // 解析响应获取 session_id
    let json_val: serde_json::Value = response.json().await
        .map_err(|e| format!("Failed to parse /api/session/new response: {}", e))?;

    let session_id = json_val
        .get("session")
        .and_then(|s| s.get("session_id"))
        .and_then(|id| id.as_str())
        .ok_or_else(|| "Missing session_id in response".to_string())?
        .to_string();

    eprintln!("[chat] Created new session via huan-ui API: {}", session_id);
    Ok(session_id)
}

/// 过滤 <think>...</think> 标签，只返回正文
fn filter_think_tags(text: &str) -> String {
    let re = regex::Regex::new(r"<think>[\s\S]*?</think>").unwrap();
    let filtered = re.replace_all(text, "").to_string();
    filtered.trim().to_string()
}


/// 保存对话到会话数据
/// 如果 session_id 为 None，则创建新会话
#[tauri::command]
fn save_message(
    session_id: Option<String>,
    user_message: String,
    assistant_reply: String,
) -> Result<String, String> {
    let sessions_dir = PathBuf::from(format!(
        "{}/Documents/huanhuan/sessions",
        std::env::var("HOME").unwrap_or_else(|_| ".".to_string())
    ));

    fs::create_dir_all(&sessions_dir)
        .map_err(|e| format!("Failed to create sessions directory: {}", e))?;

    // 使用现有 session ID 或生成新的
    let sid = session_id.unwrap_or_else(|| {
        // 生成格式: YYYYMMDD_HHMMSS_randomhex (与Hermes兼容)
        let timestamp = Local::now().format("%Y%m%d_%H%M%S").to_string();
        let random_suffix = format!("{:06x}", rand::random::<u32>() % 0xFFFFFF);
        format!("{}_{}", timestamp, random_suffix)
    });

    let session_file = sessions_dir.join(format!("{}.json", sid));
    let timestamp = Local::now().format("%H:%M:%S").to_string();

    let mut session_data = if session_file.exists() {
        // 读取现有会话
        let content = fs::read_to_string(&session_file)
            .map_err(|e| format!("Failed to read session file: {}", e))?;
        serde_json::from_str(&content)
            .map_err(|e| format!("Failed to parse session JSON: {}", e))?
    } else {
        // 创建新会话，标题为用户第一条消息的前 20 个字符（安全处理UTF-8）
        let title = user_message.chars().take(20).collect::<String>();
        let title = if title.len() < user_message.len() {
            format!("{}...", title)
        } else {
            title
        };

        serde_json::json!({
            "id": sid,
            "title": title,
            "createdAt": Local::now().to_rfc3339(),
            "messages": []
        })
    };

    // 添加新消息对
    let messages = session_data["messages"]
        .as_array_mut()
        .ok_or("Invalid messages array")?;

    messages.push(serde_json::json!({
        "role": "user",
        "text": user_message,
        "time": timestamp
    }));

    let timestamp = Local::now().format("%H:%M:%S").to_string();
    messages.push(serde_json::json!({
        "role": "assistant",
        "text": assistant_reply,
        "time": timestamp
    }));

    // 保存会话
    let session_json = serde_json::to_string_pretty(&session_data)
        .map_err(|e| format!("Failed to serialize session: {}", e))?;
    fs::write(&session_file, session_json)
        .map_err(|e| format!("Failed to write session file: {}", e))?;

    Ok(sid)
}

/// 读取所有会话列表
#[tauri::command]
fn get_all_sessions() -> Result<Vec<serde_json::Value>, String> {
    let sessions_dir = PathBuf::from(format!(
        "{}/Documents/huanhuan/sessions",
        std::env::var("HOME").unwrap_or_else(|_| ".".to_string())
    ));

    if !sessions_dir.exists() {
        return Ok(Vec::new());
    }

    let mut sessions = Vec::new();
    for entry in fs::read_dir(&sessions_dir)
        .map_err(|e| format!("Failed to read sessions directory: {}", e))?
    {
        let entry = entry.map_err(|e| format!("Failed to read entry: {}", e))?;
        let path = entry.path();

        if path.extension().map_or(false, |ext| ext == "json") {
            let content = fs::read_to_string(&path)
                .map_err(|e| format!("Failed to read session file: {}", e))?;
            if let Ok(session) = serde_json::from_str::<serde_json::Value>(&content) {
                sessions.push(session);
            }
        }
    }

    // 按创建时间倒序排序
    sessions.sort_by(|a, b| {
        let a_time = a.get("createdAt").and_then(|v| v.as_str()).unwrap_or("");
        let b_time = b.get("createdAt").and_then(|v| v.as_str()).unwrap_or("");
        b_time.cmp(a_time)
    });

    Ok(sessions)
}

/// 读取单个会话
#[tauri::command]
fn get_session(session_id: String) -> Result<serde_json::Value, String> {
    let session_file = PathBuf::from(format!(
        "{}/Documents/huanhuan/sessions/{}.json",
        std::env::var("HOME").unwrap_or_else(|_| ".".to_string()),
        session_id
    ));

    let content = fs::read_to_string(&session_file)
        .map_err(|e| format!("Failed to read session file: {}", e))?;
    serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse session JSON: {}", e))
}

/// 保存当前会话 ID 到配置文件
#[tauri::command]
fn save_current_session_id(session_id: Option<String>) -> Result<(), String> {
    let config_dir = PathBuf::from(format!(
        "{}/Documents/huanhuan/config",
        std::env::var("HOME").unwrap_or_else(|_| ".".to_string())
    ));

    fs::create_dir_all(&config_dir)
        .map_err(|e| format!("Failed to create config directory: {}", e))?;

    let config_file = config_dir.join("currentSession.txt");

    if let Some(sid) = session_id {
        fs::write(&config_file, &sid)
            .map_err(|e| format!("Failed to save current session: {}", e))?;
    } else {
        // 如果 session_id 为 None，删除文件或写入空值
        if config_file.exists() {
            fs::remove_file(&config_file)
                .map_err(|e| format!("Failed to remove current session file: {}", e))?;
        }
    }

    Ok(())
}

/// 加载保存的当前会话 ID
#[tauri::command]
fn load_current_session_id() -> Result<Option<String>, String> {
    let config_file = PathBuf::from(format!(
        "{}/Documents/huanhuan/config/currentSession.txt",
        std::env::var("HOME").unwrap_or_else(|_| ".".to_string())
    ));

    if !config_file.exists() {
        return Ok(None);
    }

    let content = fs::read_to_string(&config_file)
        .map_err(|e| format!("Failed to read current session file: {}", e))?;

    let session_id = content.trim().to_string();
    if session_id.is_empty() {
        Ok(None)
    } else {
        Ok(Some(session_id))
    }
}

/// TTS：调用 Mac 原生 say 命令朗读文字（使用系统默认语音）
#[tauri::command]
fn speak_text(text: String) -> Result<(), String> {
    // 先停止任何正在进行的朗读
    let _ = std::process::Command::new("pkill").arg("-f").arg("say ").output();

    // 不指定 -v，直接使用系统朗读设置中的默认语音
    std::process::Command::new("say")
        .arg(&text)
        .spawn()
        .map_err(|e| format!("Failed to speak: {}", e))?;

    Ok(())
}

/// TTS：停止当前朗读
#[tauri::command]
fn stop_speaking() -> Result<(), String> {
    let _ = std::process::Command::new("pkill").arg("-f").arg("say ").output();
    Ok(())
}

/// 找到 stt_server.py 脚本路径
fn find_stt_script() -> Option<PathBuf> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))?;
    [
        exe_dir.join("../Resources/scripts/stt_server.py"),
        exe_dir.join("../../src-tauri/scripts/stt_server.py"),
        exe_dir.join("../../../src-tauri/scripts/stt_server.py"),
        exe_dir.join("../../../../src-tauri/scripts/stt_server.py"),
    ]
    .into_iter()
    .find(|p| p.exists())
}

/// STT：预热 Python 常驻进程（在输入框打开时调用，消除首次识别延迟）
#[tauri::command]
fn warm_stt(state: tauri::State<Mutex<SttServer>>) -> Result<(), String> {
    let mut stt = state.lock().unwrap();
    stt.ensure_running()
}

/// STT：接收前端录音 base64，转 WAV，交给常驻 Python 进程识别
#[tauri::command]
fn transcribe_audio(
    audio_base64: String,
    mime_type: String,
    state: tauri::State<Mutex<SttServer>>,
) -> Result<String, String> {
    // 解码 base64
    let audio_bytes = general_purpose::STANDARD
        .decode(&audio_base64)
        .map_err(|e| format!("base64解码失败: {}", e))?;

    // 扩展名
    let ext = if mime_type.contains("mp4") || mime_type.contains("m4a") { "m4a" }
              else if mime_type.contains("webm") || mime_type.contains("ogg") { "webm" }
              else if mime_type.contains("wav") { "wav" }
              else { "m4a" };

    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();

    let tmp_path = std::env::temp_dir().join(format!("huanhuan_stt_{}.{}", ts, ext));
    fs::write(&tmp_path, &audio_bytes).map_err(|e| format!("保存音频失败: {}", e))?;

    let file_size = fs::metadata(&tmp_path).map(|m| m.len()).unwrap_or(0);
    if file_size < 500 {
        let _ = fs::remove_file(&tmp_path);
        return Err(format!("录音过短 ({} bytes)，请检查麦克风权限", file_size));
    }

    // afconvert → WAV 16kHz（Python speech_recognition 可直接读）
    let wav_path = std::env::temp_dir().join(format!("huanhuan_stt_{}.wav", ts));
    let convert_ok = std::process::Command::new("afconvert")
        .args(["-f", "WAVE", "-d", "LEI16@16000"])
        .arg(&tmp_path).arg(&wav_path)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    let rec_path = if convert_ok && wav_path.exists() { wav_path.clone() } else { tmp_path.clone() };
    let result_path = std::env::temp_dir().join(format!("huanhuan_stt_result_{}.txt", ts));

    // 使用常驻 Python 进程识别
    let raw = {
        let mut stt = state.lock().unwrap();
        stt.recognize(
            rec_path.to_str().unwrap_or(""),
            result_path.to_str().unwrap_or(""),
            "zh-CN",
        )?
    };

    // 清理临时文件
    let _ = fs::remove_file(&tmp_path);
    let _ = fs::remove_file(&wav_path);

    // 解析结果
    if raw.starts_with("ERROR:") {
        let code = raw.trim_start_matches("ERROR:");
        let msg = if code.contains("unknown_value") {
            "未识别到内容，请说清楚后重试".to_string()
        } else if code.contains("empty_result") {
            "未识别到内容，请重试".to_string()
        } else if code.contains("google_api") {
            format!("网络错误，请检查联网: {}", code.trim_start_matches("google_api:"))
        } else {
            format!("识别失败: {}", code)
        };
        Err(msg)
    } else if raw.is_empty() {
        Err("识别无结果，请检查网络连接".to_string())
    } else {
        Ok(raw)
    }
}

/// STT：停止语音识别（中断常驻进程，下次会自动重启）
#[tauri::command]
fn stop_voice_recognition(state: tauri::State<Mutex<SttServer>>) -> Result<(), String> {
    let mut stt = state.lock().unwrap();
    stt.stdin  = None;
    stt.stdout = None;
    let _ = std::process::Command::new("pkill").arg("-f").arg("stt_server.py").output();
    Ok(())
}

/// 检查端口是否已被占用
fn is_port_in_use(port: u16) -> bool {
    match std::net::TcpListener::bind(("127.0.0.1", port)) {
        Ok(_) => false, // 能绑定说明端口未被占用
        Err(_) => true,  // 绑定失败说明端口已被占用
    }
}

/// 启动 huan-ui 服务 (Python webui on port 8868)
fn start_huan_ui() {
    // 检查 8868 端口是否已被占用（可能已有一个 huan-ui 实例运行）
    if is_port_in_use(8868) {
        eprintln!("[huan-ui] Port 8868 is already in use, skipping startup");
        return;
    }

    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let huan_ui_dir = PathBuf::from(format!("{}/Documents/huanhuan/huan-ui", home));
    let start_script = huan_ui_dir.join("start-huan-ui.sh");

    if !start_script.exists() {
        eprintln!("[huan-ui] Start script not found at {:?}", start_script);
        return;
    }

    // 在后台启动 huan-ui 脚本
    std::thread::spawn(move || {
        match Command::new("/bin/zsh")
            .arg("-l")   // login shell，加载 .zshrc/.zprofile，继承 PATH 和 venv
            .arg("-c")
            .arg(format!("bash {}", start_script.display()))
            .spawn()
        {
            Ok(mut child) => {
                eprintln!("[huan-ui] Started huan-ui service (PID: {:?})", child.id());
                // 等待进程，以便捕获错误
                match child.wait() {
                    Ok(status) => {
                        if !status.success() {
                            eprintln!("[huan-ui] Process exited with status: {}", status);
                        }
                    }
                    Err(e) => eprintln!("[huan-ui] Error waiting for process: {}", e),
                }
            }
            Err(e) => eprintln!("[huan-ui] Failed to start huan-ui: {}", e),
        }
    });
}


#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            quit_app,
            hide_window,
            show_window,
            chat,
            open_url,
            save_message,
            get_all_sessions,
            get_session,
            save_current_session_id,
            load_current_session_id,
            speak_text,
            stop_speaking,
            warm_stt,
            transcribe_audio,
            stop_voice_recognition
        ])
        .setup(|app| {
            // 注册 STT 常驻进程状态
            let stt_script = find_stt_script()
                .unwrap_or_else(|| PathBuf::from("stt_server.py"));
            app.manage(Mutex::new(SttServer::new(stt_script)));

            // 启动 huan-ui 服务
            start_huan_ui();

            let window = app.get_webview_window("main").unwrap();

            if let Some(monitor) = window.current_monitor()? {
                let screen_size = monitor.size();
                let scale = monitor.scale_factor();
                let width = 300.0;
                let height = 360.0;
                let x = (screen_size.width as f64 / scale) - width - 80.0;  // 右边距 +40px
                let y = (screen_size.height as f64 / scale) - height - 140.0; // 底部边距 +100px
                window.set_position(tauri::PhysicalPosition::new(
                    (x * scale) as i32,
                    (y * scale) as i32,
                ))?;
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
