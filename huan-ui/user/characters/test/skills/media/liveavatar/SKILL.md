---
name: liveavatar
category: media
description: 实时视频数字人对话 — 用 LiveAvatar 给你接的 AI Agent 加一张脸
---

# LiveAvatar

实时音频驱动的 AI 数字人视频对话 skill。接 Hermes/OpenClaw gateway，说话回答带唇音同步。

## 功能

- 麦克风语音输入 → LiveAvatar 语音识别 → Hermes Agent 处理 → Avatar 语音回答
- 唇音同步的实时视频头像
- 支持多个 Avatar 角色选择
- 回声消除（不会响应自己）
- 文本聊天备选

## 环境要求

- Node.js 18+
- LiveAvatar API Key（免费获取）
- Hermes/OpenClaw Gateway 运行中（端口 18789）
- 浏览器（需要麦克风权限）

## 安装

```bash
npm i -g openclaw-liveavatar
```

## 设置 API Key

1. 去 https://app.liveavatar.com 注册并获取 API Key
2. 设置环境变量：

```bash
export LIVEAVATAR_API_KEY=your_api_key_here
```

或写入 `~/.hermes/config.yaml` 的 `env` 段（ skill_manage 会处理）。

## 运行

```bash
npx openclaw-liveavatar
```

启动后访问 http://localhost:3001

## 故障排除

| 问题 | 解决方案 |
|------|---------|
| "OpenClaw Disconnected" | 确保 Hermes gateway 在跑：`openclaw gateway` |
| "No avatars available" | 检查 `LIVEAVATAR_API_KEY` 是否正确设置 |
| 麦克风不工作 | 浏览器允许麦克风权限，检查系统音频设置 |

## 架构

```
你说话 → LiveAvatar 语音转文字 → Hermes Gateway (localhost:18789) → AI 回复 → Avatar 语音 + 唇音同步
```
