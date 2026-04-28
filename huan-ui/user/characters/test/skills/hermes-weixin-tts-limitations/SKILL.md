---
name: hermes-weixin-tts-limitations
description: Hermes Agent WeChat 语音消息能力说明 — 什么能做，什么不能做
category: messaging
---

# Hermes WeChat TTS/语音现状

## 核心限制

**微信只能发文件附件，不能发原生语音消息。**

`gateway/platforms/weixin.py` 的 `_outbound_media_builder` 方法：
- `image/*` → MEDIA_IMAGE
- `video/*` → MEDIA_VIDEO
- 其他（包括 audio/*）→ MEDIA_FILE

音频文件发到微信会显示为"文件"，不是"语音"。用户需要手动点击播放。

## 架构说明

微信接入有两套：
1. **Hermes 内置 weixin.py**（`~/.hermes/weixin/`）— 接收消息，发送只能发文件附件
2. **OpenClaw/ClawX gateway**（`openclaw-gateway` 进程）— 另一个实现，同样没有发送原生语音的能力

## 可行的变通方案

### 方案 1：发文件（现状）
gTTS 生成 MP3 → 用 ffmpeg 转 OGG → 通过 Hermes `send_message` 工具发送
- 收到的是文件，不是语音
- 用户体验差

### 方案 2：换平台
Telegram 原生支持语音消息格式，`text_to_speech` 工具可正常工作

### 方案 3：写代码
在 `weixin.py` 的 `_outbound_media_builder` 中添加 `audio/*` → `ITEM_VOICE` 的处理，需要研究微信 VOICE 消息类型的具体协议格式（接收端在 line 797-803 有参考）

## TTS 后端现状

- **Edge TTS**：国内网络不通，失败
- **gTTS**：可用，`gtts-cli "文字" -o /tmp/voice.mp3`
- **MiniMax 原生**：未测试（可能支持语音生成）

## 配置位置

- WeChat 配置：`~/.hermes/weixin/accounts/`
- Weixin platform 代码：`/Users/jiaqi/hermes-agent/gateway/platforms/weixin.py`
