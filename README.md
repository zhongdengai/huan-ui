# 欢欢 Huanhuan

**中文** | [English](#english)

macOS 桌面 AI 宠物。常驻屏幕角落，随时待命。

![欢欢截图](assets/preview.png)

---

## 故事起源

欢欢是我家一只非常不听话的边境牧羊犬。

某天突发奇想：既然真狗不听话，不如给它造一个 AI 分身来替我打工。

于是打开编辑器，写下了第一版需求文档。那份文档里有一整段"**绝对不要做的事**"：

> 不要加麦克风录音  
> 不要加任何对话相关功能  
> 不要加 TTS / STT  
> 如果你觉得"应该"加点什么，停下来不加

现在这个项目有了语音输入、语音朗读、AI 对话、图片识别、多角色切换……

欢欢（真狗）依然不听话。

---

## 功能特性

- 🐾 **多角色切换** — 欢欢（边境牧羊犬）、瓦力（小机器人）、雪宝（小企鹅）、可自定义
- 🎞️ **帧动画** — 每个角色支持多帧流畅动画，静止时待机，对话时活跃
- 💬 **气泡对话** — 半透明气泡显示 AI 回复，自动消失
- 🎤 **语音输入** — 点击麦克风录音，自动识别转文字
- 🔊 **语音朗读** — macOS 原生 TTS 朗读 AI 回复
- 📎 **图片识别** — 上传图片/截图，AI 理解内容后回答
- 🧠 **独立记忆** — 每个角色有独立的对话记忆和人格
- 🖱️ **随意拖动** — 窗口和气泡均可自由拖动定位
- ⚙️ **首次向导** — 引导配置 AI 供应商（MiniMax、OpenAI、Claude 等）

## 依赖

- macOS 12+（Apple Silicon）
- [Hermes Agent](https://github.com/nousresearch/hermes) — AI 核心引擎
- 任意支持的 AI 供应商账号（MiniMax、OpenAI、Anthropic 等）

## 快速开始

### 方式一：直接下载

前往 [Releases](https://github.com/zhongdengai/huan-ui/releases) 下载 `.dmg`，拖入应用程序文件夹即可。

> 首次打开若被系统拦截，前往「系统设置 → 隐私与安全性」点击「仍然打开」。

### 方式二：从源码运行

```bash
# 1. 安装 Hermes
pip install hermes-agent

# 2. 克隆项目
git clone https://github.com/zhongdengai/huan-ui.git
cd huan-ui

# 3. 安装依赖
npm install
cd huan-ui && pip install -r requirements.txt && cd ..

# 4. 开发模式启动
npm run dev
```

## 角色配置

角色数据存放在 `huan-ui/user/characters/{id}/`：

| 文件 | 说明 |
|------|------|
| `config.json` | 角色元数据（名称、帧数、系统提示词） |
| `SOUL.md` | 角色人格描述（发给 AI 的系统提示） |
| `frames/` | 动画帧图片（`frame-0001.png` 为静态默认图） |

### 添加新角色

1. 在 `huan-ui/user/characters/` 新建文件夹
2. 创建 `config.json`：
   ```json
   {
     "id": "mychar",
     "name": "我的角色",
     "description": "角色简介",
     "system_prompt": "你是...",
     "frames_count": 1
   }
   ```
3. 创建 `SOUL.md` 写入人格
4. 放入 `frames/frame-0001.png`（透明背景 PNG）
5. 重启应用，右键菜单 → 切换人物

### 动画帧

默认每个角色只含一张静态图。完整动画帧包可从 [Releases](https://github.com/zhongdengai/huan-ui/releases) 下载，解压到对应角色的 `frames/` 目录后重启即可。

## 图片识别

需要 [OpenRouter](https://openrouter.io) 免费 API Key：

```yaml
# ~/.hermes/config.yaml
auxiliary:
  vision:
    provider: openrouter
    api_key: sk-or-v1-...
```

或设置环境变量：`export OPENROUTER_API_KEY=sk-or-v1-...`

## 常见问题

**Q：应用启动后白屏？**  
A：检查 Hermes 是否已安装：`hermes --version`

**Q：语音识别不工作？**  
A：需要联网（使用 Google 免费 STT），并确认麦克风权限已授权

**Q：图片识别返回失败？**  
A：检查 OpenRouter API Key 是否配置；免费模型每天 50 次上限

**Q：动画不流畅？**  
A：默认只有静态图，从 Releases 下载动画帧包解压即可

---

## 展望与邀请

这个项目从一个玩笑开始，却意外地走到了今天。

我相信桌面 AI 宠物是一个值得认真对待的方向——不是噱头，而是真正改变人与 AI 交互方式的一种可能。每天打开电脑，它就在那里；不是藏在某个 app 里等你去找，而是作为伙伴陪伴在侧。

目前欢欢还很粗糙，有很多地方我自己都不满意。尤其是**人物动画**这一块——角色的动作、表情、与对话状态的联动，是让宠物真正"活起来"的关键，也是我最希望得到帮助的地方。如果你有动画制作、角色设计、或者前端动效方面的经验，哪怕只是一点点想法，都非常欢迎。

不管是提 Issue、发 PR、贡献新角色的动画帧，还是只是给个 Star，对我来说都是很大的鼓励。

谢谢你花时间看到这里。

## License

MIT License — 详见 [LICENSE](LICENSE)

## 致谢

- [Tauri](https://tauri.app) — 跨平台桌面应用框架
- [Hermes Agent](https://github.com/nousresearch/hermes) — AI 智能体引擎
- [hermes-webui](https://github.com/nesquena/hermes-webui) — huan-ui 基于此项目修改而来（MIT License）
- [OpenRouter](https://openrouter.io) — 多模型 AI API 网关

---

<a name="english"></a>

# Huanhuan

**[中文](#欢欢-huanhuan)** | English

A macOS desktop AI companion. Lives in the corner of your screen, always ready.

---

## The Story

Huanhuan is my real dog — a border collie who never listens.

One day I thought: if the real dog won't work for me, why not build an AI version that will?

So I opened my editor and wrote the first requirements doc. It had an entire section titled **"Things You Must Never Add"**:

> No microphone recording  
> No conversation features  
> No TTS / STT  
> If you think you should add something, stop and don't

The project now has voice input, voice playback, AI chat, image recognition, and multi-character switching.

The real dog still doesn't listen.

---

## Features

- 🐾 **Multiple characters** — Huanhuan (border collie), Wally (robot), Xuebao (penguin), fully customizable
- 🎞️ **Frame animation** — smooth multi-frame animation per character, idle when quiet, active when chatting
- 💬 **Speech bubbles** — semi-transparent bubble shows AI replies, auto-dismisses
- 🎤 **Voice input** — tap the mic, speak, text appears automatically
- 🔊 **Voice playback** — macOS native TTS reads AI responses aloud
- 📎 **Image recognition** — attach screenshots or photos, AI understands and responds
- 🧠 **Independent memory** — each character has its own conversation history and personality
- 🖱️ **Freely draggable** — window and bubble can be repositioned anywhere
- ⚙️ **First-run wizard** — guided setup for AI providers (MiniMax, OpenAI, Claude, etc.)

## Requirements

- macOS 12+ (Apple Silicon)
- [Hermes Agent](https://github.com/nousresearch/hermes) — AI engine
- An API key from any supported AI provider

## Getting Started

### Option 1: Download

Go to [Releases](https://github.com/zhongdengai/huan-ui/releases) and download the `.dmg`. Drag to Applications.

> If macOS blocks the app on first launch, go to System Settings → Privacy & Security and click "Open Anyway".

### Option 2: Run from Source

```bash
# 1. Install Hermes
pip install hermes-agent

# 2. Clone
git clone https://github.com/zhongdengai/huan-ui.git
cd huan-ui

# 3. Install dependencies
npm install
cd huan-ui && pip install -r requirements.txt && cd ..

# 4. Start in dev mode
npm run dev
```

## Adding Characters

Place character data in `huan-ui/user/characters/{id}/`:

| File | Purpose |
|------|---------|
| `config.json` | Metadata (name, frame count, system prompt) |
| `SOUL.md` | Personality description sent to AI |
| `frames/` | Animation frames (`frame-0001.png` is the default static image) |

Animation frame packs can be downloaded from [Releases](https://github.com/zhongdengai/huan-ui/releases). Extract to the character's `frames/` folder and restart.

## Image Recognition

Requires a free [OpenRouter](https://openrouter.io) API key:

```yaml
# ~/.hermes/config.yaml
auxiliary:
  vision:
    provider: openrouter
    api_key: sk-or-v1-...
```

## FAQ

**Q: App shows a white screen on launch?**  
A: Make sure Hermes is installed: `hermes --version`

**Q: Voice recognition not working?**  
A: Internet connection required (uses Google's free STT API). Also check microphone permissions.

**Q: Image recognition failing?**  
A: Check your OpenRouter API key. Free models have a 50 requests/day limit.

**Q: Animation looks choppy or static?**  
A: Only a single static frame is included by default. Download the animation frame pack from Releases.

---

## Vision & Invitation

This project started as a joke and somehow turned into something real.

I believe desktop AI companions are worth taking seriously — not as a gimmick, but as a genuinely different way for people to interact with AI. Instead of opening an app to find your assistant, it's just there, beside you, every time you sit down.

Huanhuan is still rough around the edges, and I know it. The area I care most about — and where I'd most welcome help — is **character animation**. The way a character moves, reacts, and comes alive in sync with conversation is what makes the difference between a widget and a companion. If you have experience in animation, character design, or frontend motion work, even just ideas or feedback, I'd genuinely love to hear from you.

Whether you open an issue, submit a PR, contribute animation frames for a new character, or just leave a star — it all means a lot.

Thanks for reading this far.

---

## License

MIT — see [LICENSE](LICENSE)

## Credits

- [Tauri](https://tauri.app) — cross-platform desktop app framework
- [Hermes Agent](https://github.com/nousresearch/hermes) — AI agent engine
- [hermes-webui](https://github.com/nesquena/hermes-webui) — huan-ui is based on this project (MIT License)
- [OpenRouter](https://openrouter.io) — multi-model AI API gateway
