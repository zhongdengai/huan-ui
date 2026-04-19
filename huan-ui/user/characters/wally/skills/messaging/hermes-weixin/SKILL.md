---
name: hermes-weixin
description: Connect Hermes Agent v0.8.0+ to personal WeChat via Tencent iLink Bot API
category: messaging
tags: [wechat, weixin, messaging, china]
---

# Hermes WeChat Integration

Connect Hermes Agent to personal WeChat via Tencent's iLink Bot API.

## Status

Hermes v0.8.0+ has **native WeChat support** via `gateway/platforms/weixin.py`. No plugin installation needed.

## Quick Setup (scan QR)

```bash
hermes gateway configure
# Select "Weixin" → scan QR with WeChat
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WEIXIN_TOKEN` | Yes | Bot token from QR login |
| `WEIXIN_ACCOUNT_ID` | Yes | Bot account ID |
| `WEIXIN_BASE_URL` | No | API base (default: `https://ilinkai.weixin.qq.com`) |
| `WEIXIN_CDN_BASE_URL` | No | CDN URL |
| `WEIXIN_DM_POLICY` | No | `pairing` (default), `open`, or `allowlist` |
| `WEIXIN_ALLOWED_USERS` | No | Comma-separated allowed user IDs |
| `WEIXIN_ALLOW_ALL_USERS` | No | `true` to allow all users |

### Dependencies

```bash
pip install aiohttp cryptography
```

## Reuse ClawX Token (skip re-scanning)

If ClawX already has a connected WeChat account:

```bash
# Find ClawX weixin account data
cat ~/.openclaw/accounts/*.json 2>/dev/null | python3 -c "
import json, sys
for line in sys.stdin:
    try:
        d = json.loads(line)
        if 'token' in d: print('TOKEN:', d['token'])
        if 'accountId' in d: print('ACCOUNT_ID:', d['accountId'])
    except: pass
"

# Also check device accounts
ls ~/.openclaw/devices/
```

Then set the env vars and start the gateway:

```bash
export WEIXIN_TOKEN="<token>"
export WEIXIN_ACCOUNT_ID="<account_id>"
hermes gateway
```

## How It Works

- **Long-polls** `getupdates` endpoint for inbound messages
- **Replies** must include `context_token` from the inbound message
- **Media files**: AES-128-ECB encrypted CDN upload/download
- **API endpoint**: `https://ilinkai.weixin.qq.com`
- **QR flow**: `get_bot_qrcode` → poll `get_qrcode_status` → confirm → save token

## Verify

```bash
hermes gateway status
# Look for "weixin" in connected platforms
```
