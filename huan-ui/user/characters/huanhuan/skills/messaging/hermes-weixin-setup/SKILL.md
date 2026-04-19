---
name: hermes-weixin-setup
description: Setup Hermes Agent v0.8.0+ to connect personal WeChat via QR code login
category: messaging
---

# Hermes WeChat (Weixin) Setup

Setup Hermes Agent v0.8.0+ to connect to personal WeChat via QR code login.

## Prerequisites

- Hermes Agent v0.8.0+ installed (`hermes --version`)
- Python packages: `qrcode`, `pillow`
  ```bash
  uv pip install qrcode pillow --python /path/to/venv/bin/python
  ```
- Dependencies already in hermes-agent venv: `aiohttp`, `cryptography`

## Steps

### 1. Get QR code URL programmatically

```python
import asyncio
import sys
sys.path.insert(0, '/Users/jiaqi/hermes-agent')

from gateway.platforms.weixin import ILINK_BASE_URL, EP_GET_BOT_QR, _api_get

async def get_qr():
    async with aiohttp.ClientSession() as session:
        resp = await _api_get(session, base_url=ILINK_BASE_URL,
                               endpoint=f"{EP_GET_BOT_QR}?bot_type=3", timeout_ms=5000)
        return resp

result = asyncio.run(get_qr())
print(result.get("qrcode_img_content"))  # The QR URL
print(result.get("qrcode"))  # The token for polling
```

### 2. Generate QR code image and upload

```python
import qrcode
from PIL import Image

url = 'https://liteapp.weixin.qq.com/q/xxx?qrcode=yyy&bot_type=3'
img = qrcode.make(url).convert('RGB')
img.save('/tmp/weixin_qr.png', 'PNG')
```

Upload to catbox:
```bash
curl -s -F "reqtype=fileupload" -F "fileToUpload=@/tmp/weixin_qr.png" https://catbox.moe/user/api.php
```
Send the resulting URL to the user for scanning.

### 3. Poll for scan confirmation

```python
import asyncio, aiohttp, sys, os
sys.path.insert(0, '/Users/jiaqi/hermes-agent')
os.chdir('/Users/jiaqi/hermes-agent')

from gateway.platforms.weixin import ILINK_BASE_URL, EP_GET_QR_STATUS, _api_get

async def poll_status(qrcode):
    async with aiohttp.ClientSession() as s:
        for i in range(90):
            r = await _api_get(s, base_url=ILINK_BASE_URL,
                               endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode}",
                               timeout_ms=35000)
            print(f'[{i}s] status={r.get("status")}')
            if r.get('status') == 'confirmed':
                return r
            await asyncio.sleep(1)

result = asyncio.run(poll_status('YOUR_QR_TOKEN'))
# Returns: {'bot_token': '...', 'ilink_bot_id': '...', 'baseurl': '...'}
```

### 4. Save credentials to ~/.hermes/.env

```
WEIXIN_TOKEN=<bot_token from result>
WEIXIN_ACCOUNT_ID=<ilink_bot_id from result>
WEIXIN_BASE_URL=<baseurl from result, usually https://ilinkai.weixin.qq.com>
WEIXIN_CDN_BASE_URL=https://novac2c.cdn.weixin.qq.com/c2c
WEIXIN_DM_POLICY=open
WEIXIN_ALLOW_ALL_USERS=true
```

### 5. Restart gateway

```bash
hermes gateway restart
```

Verify: `tail -50 ~/.hermes/logs/gateway.log | grep -i weixin`

## Why not `hermes gateway setup`?

Interactive TUI (`hermes gateway setup`) doesn't work well in non-interactive terminal sessions. The programmatic approach above bypasses the TUI entirely.

## ⚠️ TTS Configuration — Critical for WeChat

WeChat requires Chinese text-to-speech. The default `tts.provider: edge` with `edge.voice: en-US-AriaNeural` **cannot speak Chinese** — it will silently hang (微信 shows "对方正在输入..." forever) with error `NoAudioReceived` in gateway.log.

**Symptom:** WeChat shows "对方正在输入..." but no reply ever arrives. Check `~/.hermes/logs/gateway.log` for `NoAudioReceived`.

**Fix:** set `tts.provider: minimax` (needs MINIMAX_API_KEY for TTS, separate from model API key), or use a Chinese edge voice.

## Known Limitations

### Voice Messages (TTS → WeChat)

WeChat does NOT support sending native voice messages via the iLink API. When Hermes sends a TTS audio file to WeChat, it is sent as a **file attachment** — the recipient sees it as a downloadable file, not a playable voice message bubble.

**Root cause:** `gateway/platforms/weixin.py` `_outbound_media_builder()` only handles `image/*`, `video/*`, and generic files. It has no case for `audio/*` MIME types.

To send a TTS-generated voice note, use `text_to_speech()` to create the audio, then send it as a file — the recipient will need to download/open it manually.

### TTS Configuration — Critical for WeChat

WeChat requires Chinese text-to-speech. The default `tts.provider: edge` with `edge.voice: en-US-AriaNeural` **cannot speak Chinese** — it will silently hang (微信 shows "对方正在输入..." forever) with error `NoAudioReceived` in gateway.log.

**Symptom:** WeChat shows "对方正在输入..." but no reply ever arrives. Check `~/.hermes/logs/gateway.log` for `NoAudioReceived`.

**Fix:** set `tts.provider: minimax` (needs MINIMAX_API_KEY for TTS, separate from model API key), or use a Chinese edge voice.

## Token reuse from ClawX/OpenClaw

If OpenClaw already has the WeChat account connected, the tokens are NOT directly reusable — Hermes manages its own WeChat session. You must do a fresh QR login (step 1-3 above) to get new credentials for Hermes.

The OpenClaw WeChat plugin and Hermes WeChat adapter are independent clients connecting to the same Tencent iLink API — each needs its own login/session.
