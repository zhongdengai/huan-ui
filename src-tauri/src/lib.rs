use tauri::{Manager, Emitter};
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use chrono::Local;
use regex;

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
        if !s.is_empty() { s } else { create_new_session()? }
    } else {
        create_new_session()?
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
    if !status.is_success() {
        let error_text = response.text().await.unwrap_or_default();
        return Err(format!("huan-ui API error ({}): {}", status, error_text));
    }

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
    // 安全地输出前 200 个字符
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
        eprintln!("[stream] Contains <think> tag: {}", has_think);

        // 如果没有 <think> 标签，立即发送 chat-think-end 事件（表示无思考过程，直接开始回复）
        if !has_think {
            let result = app.emit("chat-think-end", ());
            eprintln!("[stream] No <think> tag found, emitted chat-think-end immediately: {:?}", result);
        }

        // 逐个字符处理回复内容，过滤 <think> 标签
        let mut inside_think = false;
        let mut i = 0;
        let mut sent_count = 0;
        let chars: Vec<char> = reply_content.chars().collect();

        eprintln!("[stream] Total chars to process: {}", chars.len());

        while i < chars.len() {
            let substr: String = chars[i..].iter().collect();

            if !inside_think && substr.starts_with("<think>") {
                inside_think = true;
                i += 7;
                eprintln!("[stream] Found <think> tag at position {}", i);
            } else if inside_think && substr.starts_with("</think>") {
                inside_think = false;
                i += 8;
                eprintln!("[stream] Found </think> tag at position {}", i);
                // think 结束
                let result = app.emit("chat-think-end", ());
                eprintln!("[stream] Emitted chat-think-end: {:?}", result);
            } else if inside_think {
                i += 1;
            } else {
                // 发送给前端
                let result = app.emit("chat-stream", serde_json::json!({ "token": chars[i].to_string() }));
                if result.is_err() {
                    eprintln!("[stream] ERROR emitting token at position {}: {:?}", i, result);
                }
                sent_count += 1;
                i += 1;
            }
        }

        eprintln!("[stream] Total chars sent: {}", sent_count);

        // 发送流式结束信号
        let result = app.emit("chat-stream-end", serde_json::json!({ "total": sent_count }));
        eprintln!("[stream] Emitted chat-stream-end: {:?}", result);
    } else {
        eprintln!("[stream] Failed to parse JSON response");
        return Err("Failed to parse huan-ui JSON response".to_string());
    }

    eprintln!("[chat] Streaming complete for session: {}", sid);

    // 返回会话 ID 给前端（可选，主要用于确认）
    Ok(sid)
}

/// 创建新会话，返回 session_id
fn create_new_session() -> Result<String, String> {
    // 使用简单的十六进制ID（huan-ui 兼容格式）
    let random_id = format!("{:012x}", (std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos() % 0xFFFFFFFFFFFF) as u64);
    let session_id = random_id;

    // 创建会话文件到 ~/.hermes/webui/sessions/
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let sessions_dir = PathBuf::from(format!("{}/.hermes/webui/sessions", home));

    fs::create_dir_all(&sessions_dir)
        .map_err(|e| format!("Failed to create sessions directory: {}", e))?;

    let session_file = sessions_dir.join(format!("{}.json", session_id));
    let session_data = serde_json::json!({
        "session_id": session_id,
        "title": "New Session",
        "workspace": std::env::var("HOME").unwrap_or_else(|_| ".".to_string()),
        "model": "MiniMax-M2.7",
        "messages": []
    });

    fs::write(&session_file, serde_json::to_string_pretty(&session_data)
        .map_err(|e| format!("Failed to serialize session: {}", e))?)
        .map_err(|e| format!("Failed to write session file: {}", e))?;

    eprintln!("[chat] Created new session: {}", session_id);
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
        match Command::new("bash")
            .arg(&start_script)
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
            load_current_session_id
        ])
        .setup(|app| {
            // 启动 huan-ui 服务
            start_huan_ui();

            let window = app.get_webview_window("main").unwrap();

            if let Some(monitor) = window.current_monitor()? {
                let screen_size = monitor.size();
                let scale = monitor.scale_factor();
                let width = 300.0;
                let height = 360.0;
                let margin = 40.0;
                let x = (screen_size.width as f64 / scale) - width - margin;
                let y = (screen_size.height as f64 / scale) - height - margin;
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
