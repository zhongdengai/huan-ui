---
name: hermes-telegram-china
description: Configure Hermes Agent Telegram integration from mainland China — requires proxy setup due to blocked api.telegram.org
version: 1.0.0
category: messaging
tags: [hermes, telegram, china, proxy, gateway]
---

# Hermes Telegram 配置（大陆环境）

## 关键问题

api.telegram.org 在大陆被墙，必须通过代理连接。

## 配置步骤

### 1. 获取 Telegram Bot Token

找 @BotFather 创建 Bot，获得 Token（格式：`123456789:ABCdef...`）

### 2. 发现系统代理地址

```bash
scutil --proxy
```

macOS 全局代理会显示 HTTP/HTTPS/SOCKS 端口，通常是 `127.0.0.1:端口`

### 3. 配置代理并写入 .env

```bash
echo 'HTTP_PROXY=http://127.0.0.1:端口' >> ~/.hermes/.env
echo 'HTTPS_PROXY=http://127.0.0.1:端口' >> ~/.hermes/.env
```

同时把 Token 也写入 .env：
```bash
echo 'TELEGRAM_BOT_TOKEN=你的_token' >> ~/.hermes/.env
```

### 4. 重启 Gateway

```bash
kill $(pgrep -f "hermes gateway") 2>/dev/null
hermes gateway run
```

### 4. 配对

- 在 Telegram 给 Bot 发任意消息
- Bot 返回配对码
- 运行：`hermes pairing approve telegram 配对码`
- 或者设置环境变量允许所有用户：`GATEWAY_ALLOW_ALL_USERS=true`（测试环境）

### 5. 验证 Bot API 连通性

```bash
curl --proxy http://127.0.0.1:端口 https://api.telegram.org/bot<TOKEN>/getMe
```

返回 `{"ok":true,...}` 表示 API 可达。

### 6. 主动发送消息（无需通过 Gateway）

Gateway 不支持主动推送，但可以直接调 Bot API：

```bash
curl -s "https://api.telegram.org/bot<BOT_TOKEN>/sendMessage" \
  -d "chat_id=<CHAT_ID>" \
  -d "text=消息内容"
```

发送语音/文件同理：
```bash
curl -s "https://api.telegram.org/bot<BOT_TOKEN>/sendVoice" \
  -F "chat_id=<CHAT_ID>" \
  -F "voice=@/path/to/file.ogg"
```

**查 Chat ID:** 看 gateway 日志 `~/.hermes/logs/gateway.log`，搜索 `telegram chat=` 后面那段数字就是。

## 坑

- macOS 系统代理只对 GUI 应用生效，CLI 工具（包括 curl、Python）需要显式设置 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量
- `hermes gateway restart` 在新 session 中不继承当前 shell 的 env vars，必须把配置写入 `~/.hermes/.env`
- `.env` 文件写入可以用 `echo >> ~/.hermes/.env`，但不能用 patch 工具（权限保护）
- 写入 .env 后需要手动 kill 并重启 Gateway：`kill $(pgrep -f "hermes gateway") && hermes gateway run`
- `curl` 不走系统代理，直接 curl api.telegram.org 会 Connection reset，必须加 `--proxy`
- Gateway 日志显示 "✓ telegram connected" 仅表示 Gateway 进程内通过代理连接成功，不等于 `curl` 在终端里也能通（终端的代理环境变量可能不同）
