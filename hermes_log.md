# hermes_log.md — Huanhuan × Hermes WebUI 接入改动记录

> 每次改动前先写这里，保持记录。

---

## 原则
- 优先改 huanhuan（Tauri 前端），不动 hermes-webui 后端
- 宠物窗口通过 Tauri Rust 代理连接 WebUI API（绕过 Origin CORS）

---

## 当前进度

✅ Rust 代理通信（绕过 CORS）
✅ 布局：输入框上 / 头像中 / 气泡下
✅ 交互：左键开输入框 | 右键拖动 | 右键单击出退出菜单
⚠️  等待运行测试确认

---

## 改动列表

### 改动 1：Rust 代理通信（解决 CORS Origin 403）

**问题**：WebUI 服务端校验 Origin header，Tauri webview 请求带 `tauri://localhost` Origin，被拒绝（403）。

**方案**：前端改用 `invoke()` 调用 Rust 命令，Rust 用 `reqwest` 发出请求（无 Origin header），服务端正常响应。

**改动文件**：

- `src-tauri/src/lib.rs` — 新增三个 Tauri 命令：
  - `new_session()` → POST `/api/session/new`，返回 JSON 字符串
  - `chat_start(session_id, message)` → POST `/api/chat/start`，返回 JSON 字符串（含 stream_id）
  - `chat_stream(stream_id)` → GET `/api/chat/stream`，解析 SSE 返回纯文本回复
  - 三个命令都加了 HTTP 状态码检查和空响应检查

- `src/main.js` — 通信改用 `invoke()`：
  - 删掉了 `WEBUI_URL` 常量
  - 删掉了所有 `fetch()` 调用
  - `sendMessage()` 改为三步 `invoke()` 调用
  - 修 bug：补上了缺失的 `bubbleText` 和 `avatar` DOM 变量声明

- `src-tauri/capabilities/default.json` — 已有 http:default 权限（保留）

---

### 改动 2：布局改为上下结构

**布局**：
```
┌──────────────┐
│  输入框 40px  │  ← 头像上方，始终显示
├──────────────┤
│              │
│  头像 240px   │  ← 中间，左键单击聚焦输入框
│              │
└──────────────┘  ← #app 底部 (360px)
  气泡 120px    ← 头像正下方（绝对定位，窗口底部向上展开）
```

**窗口尺寸**：300 × 360 px

**改动文件**：
- `src/index.html` — 重构 flex 垂直布局，输入框始终可见，气泡绝对定位在窗口底部

---

### 改动 3：右键交互逻辑

**交互规则**：
- **左键单击头像** → 聚焦输入框（输入框始终可见，实际上直接打字即可）
- **右键单击（< 5px 移动）** → 显示退出菜单
- **右键拖动（> 5px 移动）** → 移动窗口，不弹菜单

**实现细节**：
- `mousedown` (button=2) 记录起始位置，`wasDrag = false`
- `mousemove` 超过 5px 阈值 → `wasDrag = true`
- `mouseup` (button=2) 时：
  - 若 `wasDrag = false` → 在鼠标位置显示退出菜单
  - 若 `wasDrag = true` → 不弹菜单，清除标记
- 不使用 `contextmenu` 事件（Tauri webview 不触发该事件）

**改动文件**：
- `src/main.js` — 重写拖动逻辑，menuup 弹出菜单
- `src/index.html` — 菜单结构不变

---

### 改动 4：之前的旧布局记录（已废弃）

~~最初版本：气泡(上) + 宠物(中) + 输入框(下)，300×400，左键拖动~~

以上布局已废弃，以改动 2 的上下结构为准。

---

## API 通信流程（当前）

```
前端 sendMessage()
  │
  ├─ invoke('new_session')
  │    └→ POST /api/session/new → { session: { session_id: "xxx" } }
  │
  ├─ invoke('chat_start', { sessionId, message })
  │    └→ POST /api/chat/start → { stream_id: "xxx" }
  │
  └─ invoke('chat_stream', { streamId })
       └→ GET /api/chat/stream → SSE 全文 → 纯文本 reply
```

---

## 待解决

- OpenRouter API key 过期问题（`sk-or-...29bf`），导致 AI 请求 401
  - 临时方案：WebUI 用 `minimax-cn` profile
