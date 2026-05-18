use tauri::{Manager, Emitter};
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use chrono::Local;
use regex;
use base64::{Engine as _, engine::general_purpose};
use std::io::{BufRead, BufReader, Write};
use std::sync::Mutex;

/// 返回 app 统一数据目录：~/Library/Application Support/huanhuan/
/// 所有配置、会话、缓存均存在这里，不再散落到 ~/Documents/
fn app_data_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    PathBuf::from(format!("{}/Library/Application Support/huanhuan", home))
}

/// 确保子目录存在，返回完整路径
fn app_data_subdir(sub: &str) -> PathBuf {
    let dir = app_data_dir().join(sub);
    let _ = fs::create_dir_all(&dir);
    dir
}

/// 首次启动时将旧的 ~/Documents/huanhuan/{config,sessions} 迁移到新路径
fn migrate_legacy_data() {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let old_base = PathBuf::from(format!("{}/Documents/huanhuan", home));
    if !old_base.exists() { return; }

    for sub in &["config", "sessions"] {
        let old_dir = old_base.join(sub);
        let new_dir = app_data_subdir(sub);
        if !old_dir.exists() { continue; }
        if let Ok(entries) = fs::read_dir(&old_dir) {
            for entry in entries.flatten() {
                let dst = new_dir.join(entry.file_name());
                if !dst.exists() {
                    let _ = fs::copy(entry.path(), &dst);
                    eprintln!("[migrate] {:?} -> {:?}", entry.path(), dst);
                }
            }
        }
        // 迁移完后删除旧目录（仅当空时）
        let _ = fs::remove_dir(&old_dir);
    }
}

/// 首次启动时从 app bundle 初始化默认角色到用户数据目录
/// 只在用户角色目录为空时执行，不覆盖已有数据
fn init_default_characters(app: &tauri::AppHandle) {
    let user_chars_dir = app_data_subdir("user/characters");

    // 已有角色就跳过
    if let Ok(mut entries) = fs::read_dir(&user_chars_dir) {
        if entries.next().is_some() {
            return;
        }
    }

    let resource_dir = match app.path().resource_dir() {
        Ok(d) => d,
        Err(e) => { eprintln!("[init] resource_dir error: {}", e); return; }
    };
    let defaults_src = resource_dir.join("default-characters");
    if !defaults_src.exists() {
        eprintln!("[init] default-characters not found in bundle");
        return;
    }

    for char_id in &["huanhuan", "wally", "xuebao", "test"] {
        let src = defaults_src.join(char_id);
        let dst = user_chars_dir.join(char_id);
        if let Err(e) = copy_dir_recursive(&src, &dst) {
            eprintln!("[init] failed to copy {}: {}", char_id, e);
        } else {
            eprintln!("[init] initialized character: {}", char_id);
        }
    }
}

/// 递归复制目录
fn copy_dir_recursive(src: &PathBuf, dst: &PathBuf) -> Result<(), String> {
    fs::create_dir_all(dst).map_err(|e| e.to_string())?;
    for entry in fs::read_dir(src).map_err(|e| e.to_string())?.flatten() {
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        if src_path.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else {
            fs::copy(&src_path, &dst_path).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

/// huan-ui 进程追踪
struct HuanUiState {
    pid: Mutex<Option<u32>>,
}

/// TTS say 进程 PID 追踪（精准杀进程，不用 pkill 模糊匹配）
struct TtsState {
    pid: Mutex<Option<u32>>,
}

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
            .env("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
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
            return Err("mlx-whisper 未安装，请运行: pip3 install --break-system-packages mlx-whisper".into());
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
    stop_huan_ui();
    std::thread::sleep(std::time::Duration::from_millis(500));
    app.exit(0);
}

/// setup 页面完成后关闭 setup 窗口，显示主窗口
#[tauri::command]
fn finish_setup(app: tauri::AppHandle) {
    if let Some(setup_win) = app.get_webview_window("setup") {
        let _ = setup_win.hide();
    }
    if let Some(main_win) = app.get_webview_window("main") {
        let _ = main_win.show();
    }
}

/// 检查 Hermes 是否已安装
#[tauri::command]
fn check_hermes_installed() -> bool {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let candidates = [
        format!("{}/hermes-agent", home),
        format!("{}/.hermes/hermes-agent", home),
    ];
    candidates.iter().any(|p| PathBuf::from(p).join("venv/bin/python").exists())
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

/// 用 macOS osascript 弹出原生文件选择对话框，返回选中文件的绝对路径列表
#[tauri::command]
async fn pick_files() -> Result<Vec<String>, String> {
    // 用多行 AppleScript，避免 heredoc 转义问题
    let output = std::process::Command::new("osascript")
        .args([
            "-e", "set theFiles to (choose file with multiple selections allowed with prompt \"选择要发送的文件\")",
            "-e", "set thePaths to {}",
            "-e", "repeat with aFile in theFiles",
            "-e", "  copy POSIX path of aFile to the end of thePaths",
            "-e", "end repeat",
            "-e", "return thePaths",
        ])
        .output()
        .map_err(|e| format!("osascript error: {e}"))?;

    if !output.status.success() {
        // 用户点了取消（exit code 1）→ 返回空列表
        return Ok(vec![]);
    }

    let raw = String::from_utf8_lossy(&output.stdout);
    // osascript 返回逗号分隔的路径列表，例如 "/path/a, /path/b\n"
    let paths: Vec<String> = raw
        .split(',')
        .map(|s| s.trim().trim_end_matches('\n').to_string())
        .filter(|s| !s.is_empty() && s.starts_with('/'))
        .collect();

    Ok(paths)
}

/// 将本地文件上传到 huan-ui /api/upload（multipart POST），返回 workspace 内的服务器路径
/// session_id 为空时 huan-ui 会报错，需要前端先保证有 session
#[tauri::command]
async fn upload_file(local_path: String, session_id: String) -> Result<String, String> {
    let path = std::path::Path::new(&local_path);
    let filename = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("file")
        .to_string();

    let file_bytes = std::fs::read(&local_path)
        .map_err(|e| format!("读取文件失败: {e}"))?;

    // 构造 multipart body（手动，避免引入额外依赖）
    use rand::Rng;
    let rand_suffix: u64 = rand::thread_rng().gen();
    let boundary = format!("----HuanhuanBoundary{rand_suffix:016x}");
    let mut body: Vec<u8> = Vec::new();

    // session_id field
    body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
    body.extend_from_slice(b"Content-Disposition: form-data; name=\"session_id\"\r\n\r\n");
    body.extend_from_slice(session_id.as_bytes());
    body.extend_from_slice(b"\r\n");

    // file field
    body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
    body.extend_from_slice(
        format!("Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n").as_bytes()
    );
    body.extend_from_slice(b"Content-Type: application/octet-stream\r\n\r\n");
    body.extend_from_slice(&file_bytes);
    body.extend_from_slice(b"\r\n");
    body.extend_from_slice(format!("--{boundary}--\r\n").as_bytes());

    let client = reqwest::Client::builder()
        .no_proxy()
        .build()
        .map_err(|e| format!("HTTP client error: {e}"))?;

    let resp = client
        .post("http://localhost:8868/api/upload")
        .header("Content-Type", format!("multipart/form-data; boundary={boundary}"))
        .body(body)
        .send()
        .await
        .map_err(|e| format!("上传请求失败: {e}"))?;

    let status = resp.status();
    let json: serde_json::Value = resp.json().await.map_err(|e| format!("响应解析失败: {e}"))?;

    if !status.is_success() {
        return Err(json["error"].as_str().unwrap_or("上传失败").to_string());
    }

    // 返回服务器上的绝对路径
    let server_path = json["path"]
        .as_str()
        .or_else(|| json["filename"].as_str())
        .ok_or("响应中无 path 字段")?
        .to_string();

    Ok(server_path)
}

/// 流式调用 huan-ui 的 HTTP API 进行对话
/// 逐个 chunk 发送给前端，通过 Tauri 事件系统
#[tauri::command]
async fn chat(app: tauri::AppHandle, message: String, session_id: Option<String>, attachments: Option<Vec<String>>) -> Result<String, String> {
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
        "model": null,  // 由 huan-ui 从 user/config.json 读取，不在此处硬编码
        "workspace": std::env::var("HOME").unwrap_or_else(|_| ".".to_string()),
        "attachments": attachments.unwrap_or_default(),
    });

    // 后台可能正在启动，连接失败时重试（最多等30秒）
    let response = {
        let mut last_err = String::new();
        let mut connected = false;
        let mut result = None;
        for attempt in 0..30 {
            match client.post(url).json(&payload).send().await {
                Ok(r) => {
                    connected = true;
                    result = Some(r);
                    break;
                }
                Err(e) => {
                    last_err = e.to_string();
                    if attempt == 0 {
                        // 第一次失败时通知前端后台在启动
                        let _ = app.emit("chat-backend-starting", ());
                        eprintln!("[chat] huan-ui not ready, waiting... (attempt {})", attempt + 1);
                    }
                    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                }
            }
        }
        if !connected {
            return Err(format!("后台服务启动失败，请重启应用: {}", last_err));
        }
        result.unwrap()
    };

    let status = response.status();

    // session不存在时（服务器重启等），自动重建session并重试
    if status == reqwest::StatusCode::NOT_FOUND {
        eprintln!("[chat] Session {} not found (server restarted?), creating new session...", sid);
        let new_sid = create_new_session().await?;
        eprintln!("[chat] Retrying with new session: {}", new_sid);

        // 立即保存新session ID到文件，让JS下次用正确的ID
        let config_path = app_data_subdir("config").join("currentSession.txt");
        let _ = std::fs::write(&config_path, &new_sid);

        let retry_payload = serde_json::json!({
            "session_id": new_sid,
            "message": message,
            "model": null,  // 由 huan-ui 从 user/config.json 读取，不在此处硬编码
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

        // ── O(n) 过滤 <think>…</think>，避免旧代码每字符分配 String 导致 O(n²) 卡顿 ──
        let has_think = reply_content.contains("<think>");
        if !has_think {
            let _ = app.emit("chat-think-end", ());
            eprintln!("[stream] No <think> tag found, emitted chat-think-end");
        }

        // 用 str::find 扫描，单次遍历，O(n)
        let mut visible_text = String::with_capacity(reply_content.len());
        let mut rest = reply_content.as_str();
        let mut emitted_think_end = !has_think;

        loop {
            match rest.find("<think>") {
                None => {
                    // 没有更多 think 块，剩余全部是可见文本
                    visible_text.push_str(rest);
                    break;
                }
                Some(start) => {
                    // start 之前是可见文本
                    visible_text.push_str(&rest[..start]);
                    let after_open = &rest[start + 7..]; // skip "<think>"
                    match after_open.find("</think>") {
                        None => {
                            // 没有闭合标签，停止
                            break;
                        }
                        Some(end) => {
                            rest = &after_open[end + 8..]; // skip "</think>"
                            if !emitted_think_end {
                                let _ = app.emit("chat-think-end", ());
                                emitted_think_end = true;
                            }
                        }
                    }
                }
            }
        }

        // 逐字符发出 chat-stream 事件；每 50 个字符 yield 一次，保持 tokio executor 响应
        let mut sent_count = 0usize;
        for ch in visible_text.chars() {
            let _ = app.emit("chat-stream", serde_json::json!({ "token": ch.to_string() }));
            sent_count += 1;
            if sent_count % 50 == 0 {
                tokio::task::yield_now().await;
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
    let sessions_dir = app_data_subdir("sessions");

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
    let sessions_dir = app_data_subdir("sessions");

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
    let session_file = app_data_subdir("sessions").join(format!("{}.json", session_id));

    let content = fs::read_to_string(&session_file)
        .map_err(|e| format!("Failed to read session file: {}", e))?;
    serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse session JSON: {}", e))
}

/// 保存当前会话 ID 到配置文件
#[tauri::command]
fn save_current_session_id(session_id: Option<String>) -> Result<(), String> {
    let config_file = app_data_subdir("config").join("currentSession.txt");

    if let Some(sid) = session_id {
        fs::write(&config_file, &sid)
            .map_err(|e| format!("Failed to save current session: {}", e))?;
    } else {
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
    let config_file = app_data_subdir("config").join("currentSession.txt");

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

/// 保存输入框位置
#[tauri::command]
fn save_input_position(left: f64, top: f64) -> Result<(), String> {
    let config_file = app_data_subdir("config").join("inputPosition.txt");
    let content = format!("{},{}", left as i32, top as i32);
    fs::write(&config_file, &content)
        .map_err(|e| format!("Failed to save input position: {}", e))?;
    Ok(())
}

/// 读取保存的输入框位置
#[tauri::command]
fn load_input_position() -> Result<Option<(f64, f64)>, String> {
    let config_file = app_data_subdir("config").join("inputPosition.txt");

    if !config_file.exists() {
        return Ok(None);
    }

    let content = fs::read_to_string(&config_file)
        .map_err(|e| format!("Failed to read input position file: {}", e))?;

    let parts: Vec<&str> = content.trim().split(',').collect();
    if parts.len() == 2 {
        if let (Ok(left), Ok(top)) = (parts[0].parse::<f64>(), parts[1].parse::<f64>()) {
            return Ok(Some((left, top)));
        }
    }

    Ok(None)
}

/// 保存气泡位置
#[tauri::command]
fn save_bubble_position(anchor_y: f64, left: f64) -> Result<(), String> {
    let config_file = app_data_subdir("config").join("bubblePosition.txt");
    let content = format!("{},{}", anchor_y as i32, left as i32);
    fs::write(&config_file, &content)
        .map_err(|e| format!("Failed to save bubble position: {}", e))?;
    Ok(())
}

/// 读取保存的气泡位置
#[tauri::command]
fn load_bubble_position() -> Result<Option<(f64, f64)>, String> {
    let config_file = app_data_subdir("config").join("bubblePosition.txt");

    if !config_file.exists() {
        return Ok(None);
    }

    let content = fs::read_to_string(&config_file)
        .map_err(|e| format!("Failed to read bubble position file: {}", e))?;

    let parts: Vec<&str> = content.trim().split(',').collect();
    if parts.len() == 2 {
        if let (Ok(anchor_y), Ok(left)) = (parts[0].parse::<f64>(), parts[1].parse::<f64>()) {
            return Ok(Some((anchor_y, left)));
        }
    }

    Ok(None)
}

/// 通过 PID 精准停止 say 进程
fn kill_say_pid(pid: u32) {
    let _ = Command::new("kill").args(["-TERM", &pid.to_string()]).output();
}

/// TTS：调用 Mac 原生 say 命令朗读文字，文字写入临时文件避免超长参数问题
#[tauri::command]
fn speak_text(text: String, state: tauri::State<TtsState>) -> Result<(), String> {
    // 精准停止上一个 say 进程（用 PID，不用 pkill 模糊匹配）
    {
        let mut pid_lock = state.pid.lock().unwrap();
        if let Some(old_pid) = pid_lock.take() {
            eprintln!("[TTS] 停止旧 say 进程 PID={}", old_pid);
            kill_say_pid(old_pid);
        }
    }

    // say（无论参数还是 -f 文件方式）遇到 \n\n 都当段落结束符停止朗读
    // 把多余换行压缩成单空格，保证全文连续朗读
    let clean: String = text
        .split('\n')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ");

    eprintln!("[TTS] speak_text 原长度={} 清理后={}, 前50字={:?}",
        text.len(), clean.len(), clean.chars().take(50).collect::<String>());

    let child = Command::new("say")
        .arg(&clean)
        .spawn()
        .map_err(|e| format!("Failed to speak: {}", e))?;

    let new_pid = child.id();
    eprintln!("[TTS] 新 say 进程 PID={}", new_pid);

    // 保存 PID，让 Child 正常释放（不 kill/wait，进程独立运行）
    *state.pid.lock().unwrap() = Some(new_pid);
    std::mem::forget(child);

    Ok(())
}

/// TTS：停止当前朗读
#[tauri::command]
fn stop_speaking(state: tauri::State<TtsState>) -> Result<(), String> {
    let mut pid_lock = state.pid.lock().unwrap();
    if let Some(pid) = pid_lock.take() {
        eprintln!("[TTS] stop_speaking 停止 PID={}", pid);
        kill_say_pid(pid);
    }
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

/// 供前端调用：检查 huan-ui 后台是否已就绪（避免前端 fetch 被 CSP 拦截）
#[tauri::command]
fn check_backend_ready() -> bool {
    is_port_in_use(8868)
}

/// 获取角色列表（代理 /api/characters/list，绕过前端 CSP）
#[tauri::command]
async fn get_characters() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::builder().no_proxy().build()
        .map_err(|e| e.to_string())?;
    let resp = client.get("http://127.0.0.1:8868/api/characters/list")
        .send().await.map_err(|e| e.to_string())?;
    resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
}

/// 切换角色（代理 /api/characters/{id}/switch，绕过前端 CSP）
#[tauri::command]
async fn switch_character(character_id: String) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::builder().no_proxy().build()
        .map_err(|e| e.to_string())?;
    let url = format!("http://127.0.0.1:8868/api/characters/{}/switch", character_id);
    let resp = client.post(&url).send().await.map_err(|e| e.to_string())?;
    resp.json::<serde_json::Value>().await.map_err(|e| e.to_string())
}

/// 启动 huan-ui 服务 (Python webui on port 8868)
/// huan-ui 源码打包在 app bundle Resources/huan-ui/ 里
/// venv 和运行时数据放在 ~/Library/Application Support/huanhuan/
fn start_huan_ui(app: tauri::AppHandle) {
    // 检查 8868 端口是否已被占用（可能已有一个 huan-ui 实例运行）
    if is_port_in_use(8868) {
        eprintln!("[huan-ui] Port 8868 is already in use, skipping startup");
        return;
    }

    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());

    // 1. 找 bundle 里的 huan-ui 源码目录
    let resource_dir = app.path().resource_dir()
        .unwrap_or_else(|_| PathBuf::from("."));
    let bundled_huan_ui = resource_dir.join("huan-ui");

    // 兼容开发模式：bundle 里没有时，回退到本地开发路径
    let huan_ui_src = if bundled_huan_ui.join("server.py").exists() {
        eprintln!("[huan-ui] Using bundled huan-ui at {:?}", bundled_huan_ui);
        bundled_huan_ui
    } else {
        let dev_path = PathBuf::from(format!("{}/Documents/huanhuan/huan-ui", home));
        eprintln!("[huan-ui] Bundle not found, falling back to dev path: {:?}", dev_path);
        dev_path
    };

    if !huan_ui_src.join("server.py").exists() {
        eprintln!("[huan-ui] server.py not found, cannot start huan-ui");
        return;
    }

    // 2. 运行时数据目录：~/Library/Application Support/huanhuan/
    let app_support = PathBuf::from(format!(
        "{}/Library/Application Support/huanhuan", home
    ));
    let venv_dir = app_support.join(".venv");
    let webui_dir = app_support.join("webui");
    let user_dir = app_support.join("user");

    // 确保运行时目录存在
    for dir in [&app_support, &webui_dir, &user_dir] {
        if let Err(e) = std::fs::create_dir_all(dir) {
            eprintln!("[huan-ui] Failed to create dir {:?}: {}", dir, e);
        }
    }

    // 3. 找 hermes-agent（用户自行安装）
    let hermes_agent_dir = {
        let candidates = [
            format!("{}/hermes-agent", home),
            format!("{}/.hermes/hermes-agent", home),
        ];
        candidates.iter()
            .find(|p| PathBuf::from(p).join("venv/bin/python").exists())
            .map(|p| PathBuf::from(p))
    };

    let Some(agent_dir) = hermes_agent_dir else {
        eprintln!("[huan-ui] hermes-agent not found at ~/hermes-agent or ~/.hermes/hermes-agent");
        return;
    };
    let python = agent_dir.join("venv/bin/python");
    eprintln!("[huan-ui] Using hermes-agent: {:?}", agent_dir);

    // 4. 首次运行：创建 venv 并安装 pyyaml
    if !venv_dir.join("bin/python").exists() {
        eprintln!("[huan-ui] Creating venv at {:?}", venv_dir);
        let _ = Command::new(&python)
            .args(["-m", "venv", venv_dir.to_str().unwrap()])
            .output();
        let venv_python = venv_dir.join("bin/python");
        let req_file = huan_ui_src.join("requirements.txt");
        eprintln!("[huan-ui] Installing dependencies...");
        let _ = Command::new(&venv_python)
            .args(["-m", "pip", "install", "-q", "-r", req_file.to_str().unwrap()])
            .output();
        eprintln!("[huan-ui] Dependencies installed");
    }

    let venv_python = venv_dir.join("bin/python");

    // 5. 加载 .env 文件（优先 ~/.hermes/.env，再 hermes-agent/.env）
    let mut extra_env: Vec<(String, String)> = vec![];
    let env_files = [
        PathBuf::from(format!("{}/.hermes/.env", home)),  // 主要：含 API keys
        agent_dir.join(".env"),                             // 备用
    ];

    fn load_env_file(path: &PathBuf, env: &mut Vec<(String, String)>) {
        if let Ok(content) = std::fs::read_to_string(path) {
            let mut added = 0;
            for line in content.lines() {
                let line = line.trim();
                if line.starts_with('#') || line.is_empty() || !line.contains('=') { continue; }
                if let Some((k, v)) = line.split_once('=') {
                    let k = k.trim().to_string();
                    let v = v.trim().trim_matches('"').to_string();
                    // 不重复添加已有的 key
                    if !env.iter().any(|(ek, _)| ek == &k) {
                        env.push((k, v));
                        added += 1;
                    }
                }
            }
            eprintln!("[huan-ui] Loaded {} vars from {:?}", added, path);
        }
    }

    for env_file in &env_files {
        load_env_file(env_file, &mut extra_env);
    }

    eprintln!("[huan-ui] Total env vars loaded: {}", extra_env.len());

    // 6. 启动 server.py
    let app_clone = app.clone();
    let huan_ui_src_clone = huan_ui_src.clone();
    let app_support_clone = app_support.clone();

    std::thread::spawn(move || {
        let mut cmd = Command::new(&venv_python);
        cmd.arg(huan_ui_src_clone.join("server.py"))
           .current_dir(&huan_ui_src_clone)
           .env("HERMES_WEBUI_AGENT_DIR", &agent_dir)
           .env("HERMES_WEBUI_PORT", "8868")
           .env("HERMES_WEBUI_HOST", "127.0.0.1")
           .env("HERMES_HOME", format!("{}/.hermes", home))  // hermes 原始数据目录（profiles/记忆等）
           .env("HERMES_WEBUI_STATE_DIR", app_support_clone.join("webui").to_str().unwrap_or(""))
           .env("HERMES_WEBUI_USER_DIR", app_support_clone.join("user").to_str().unwrap_or(""));

        for (k, v) in &extra_env {
            cmd.env(k, v);
        }

        match cmd.spawn() {
            Ok(mut child) => {
                let pid = child.id();
                eprintln!("[huan-ui] Started (PID: {})", pid);
                if let Some(state) = app_clone.try_state::<HuanUiState>() {
                    *state.pid.lock().unwrap() = Some(pid);
                }
                match child.wait() {
                    Ok(status) => eprintln!("[huan-ui] Exited: {}", status),
                    Err(e) => eprintln!("[huan-ui] Wait error: {}", e),
                }
                if let Some(state) = app_clone.try_state::<HuanUiState>() {
                    *state.pid.lock().unwrap() = None;
                }
            }
            Err(e) => eprintln!("[huan-ui] Failed to start: {}", e),
        }
    });

    // 7. 健康检测：最多等 30 秒
    std::thread::spawn(move || {
        let max_ms: u64 = 30_000;
        let interval = std::time::Duration::from_millis(500);
        let mut elapsed: u64 = 0;
        while elapsed < max_ms {
            std::thread::sleep(interval);
            elapsed += 500;
            if is_port_in_use(8868) {
                eprintln!("[huan-ui] ✓ Ready after ~{}ms", elapsed);
                return;
            }
        }
        eprintln!("[huan-ui] ⚠ Timeout: not ready after {}ms", max_ms);
    });
}

/// 关闭 huan-ui 服务（通过 lsof 找到端口进程并发送 TERM 信号）
fn stop_huan_ui() {
    eprintln!("[huan-ui] Stopping huan-ui on port 8868...");
    // 用 lsof 找到监听 8868 的进程 PID，发送 TERM 信号
    let _ = Command::new("sh")
        .arg("-c")
        .arg("lsof -ti:8868 2>/dev/null | xargs kill -TERM 2>/dev/null; true")
        .output();
    eprintln!("[huan-ui] TERM signal sent");
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
            save_input_position,
            load_input_position,
            save_bubble_position,
            load_bubble_position,
            speak_text,
            stop_speaking,
            warm_stt,
            transcribe_audio,
            stop_voice_recognition,
            check_backend_ready,
            get_characters,
            switch_character,
            finish_setup,
            check_hermes_installed,
            pick_files,
            upload_file
        ])
        .setup(|app| {
            // 旧数据迁移（~/Documents/huanhuan → ~/Library/Application Support/huanhuan）
            migrate_legacy_data();

            // 首次启动：从 bundle 初始化默认角色
            init_default_characters(app.handle());

            // 注册 STT 常驻进程状态
            let stt_script = find_stt_script()
                .unwrap_or_else(|| PathBuf::from("stt_server.py"));
            app.manage(Mutex::new(SttServer::new(stt_script)));

            // 注册 huan-ui 进程状态（用于退出时清理）
            app.manage(HuanUiState { pid: Mutex::new(None) });

            // 注册 TTS say 进程 PID 状态
            app.manage(TtsState { pid: Mutex::new(None) });

            // 启动 huan-ui 服务
            start_huan_ui(app.handle().clone());

            // 首次启动检测：没有 user/config.json → 显示 setup 窗口，隐藏主窗口
            let user_config = app_data_subdir("user").join("config.json");
            if !user_config.exists() {
                // 先隐藏主窗口（alwaysOnTop 透明窗口会拦截 setup 的所有点击）
                if let Some(main_win) = app.get_webview_window("main") {
                    let _ = main_win.hide();
                }
                if let Some(setup_win) = app.get_webview_window("setup") {
                    let _ = setup_win.show();
                    // 强制抢焦点：show() 后窗口可见但不一定成为 key window
                    let _ = setup_win.set_focus();
                }
            }

            let window = app.get_webview_window("main").unwrap();

            if let Some(monitor) = window.current_monitor()? {
                let screen_size = monitor.size();
                let scale = monitor.scale_factor();
                let width = 300.0;
                let height = 360.0;
                let x = (screen_size.width as f64 / scale) - width - 80.0;  // 右边距 +40px
                let y = (screen_size.height as f64 / scale) - height - 140.0 - 150.0; // 底部边距 +100px，额外上移150px为气泡腾出空间
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
