---
name: img-host
description: Upload local images to catbox.moe for hosting. Use this when you have a local image file and need a public URL to share or use with tools that only accept HTTP URLs.
version: 3.0.0
author: Hermes Agent
license: MIT
dependencies: [curl]
metadata:
  hermes:
    tags: [image, upload, hosting, catbox, screenshot, share]
    related_skills: []

---

# Image Host Skill

Upload local images to catbox.moe (free, no API key, no rate limits) and get a permanent public URL back.

## Upload Image

```bash
curl -s -F 'reqtype=fileupload' -F 'fileToUpload=@/PATH/TO/IMAGE' https://catbox.moe/user/api.php
```

Returns a direct URL like `https://files.catbox.moe/xxxxxx.png`

## Usage

```bash
# Upload a screenshot or image
curl -s -F 'reqtype=fileupload' -F 'fileToUpload=@/path/to/image.png' https://catbox.moe/user/api.php
```

## Supported Formats

PNG, JPEG, GIF, WEBP, BMP, TIFF, PDF — up to 200MB per file.

## Notes

- Files are stored permanently (no expiry)
- No API key required
- No rate limits

## ⚠️ Critical: MiniMax Does NOT Support Image Input (Any Format)

After extensive investigation (2025-04-12), confirmed:

### MiniMax Anthropic API Compatibility (`/anthropic/v1/messages`)
- `type="image_url"` → **Error 2013: unsupported content type 'image_url'**
- `type="image"` → **Not supported (文档明确说明)**

### MiniMax OpenAI API Compatibility (`/v1/chat/completions`)
- Image input messages → **"当前不支持图像和音频类型的输入"** (文档明确说明)

### Root Cause
MiniMax-M2.7's Anthropic and OpenAI API compatibility layers do NOT support image input in the `messages` content array. This is an **API-level limitation**, not a format issue. There is NO standard API workaround.

### What Works
- **ClawX can do vision** — it likely uses a non-public/internal API endpoint or MiniMax portal direct integration
- **Browser tools work** — they use a separate network stack that can access MiniMax's web UI
- **Hailuo AI (hailuoai.com)** — MiniMax's own web product has vision, use browser to access it

### For Hermes Agent
vision_analyze will NOT work with MiniMax-M2.7 through any standard API format. The tool needs a provider that supports vision (e.g., OpenAI, Anthropic, Google).

### Workaround
Use `browser_vision` tool instead — it uses the browser's network stack and can analyze screenshots/images via web UIs that support vision.
