---
name: minimax-vision-setup
description: MiniMax 图片理解（vision）配置 — 区分 Token Plan API Key 与按量付费 API Key
category: mlops
---

# MiniMax Vision 图片理解配置

## 核心问题
MiniMax 有两套 API key 系统，**只有 Token Plan API Key 才支持图片理解**。

| API Key 类型 | 图片理解 | 获取方式 |
|--|--|--|
| 按量付费 API Key | ❌ 不支持 | platform.minimaxi.com 按量计费 |
| **Token Plan API Key** | ✅ 支持 | platform.minimaxi.com/subscribe/token-plan |

## 关键发现（踩坑过程）

1. MiniMax 的 Anthropic 兼容端点 **明确不支持** `image_url` content type（报错 2013）
2. MiniMax 开放平台文档里"图片模型"只有 image-01（图像生成），没有图片理解
3. **Banner 提示**："订阅 Token Plan，尽享全模态模型"
4. Token Plan 提供专属 **MCP 工具**：`understand_image` 和 `web_search`
5. 安装命令：`claude mcp add -s user MiniMax --env MINIMAX_API_KEY=xxx --env MINIMAX_API_HOST=https://api.minimaxi.com -- uvx minimax-coding-plan-mcp -y`
6. ClawX 能做 vision 是因为：内置了 OpenCode AI 网关路由，或 Token Plan MCP

## 解决方案

1. 订阅 Token Plan：https://platform.minimaxi.com/subscribe/token-plan
2. 获取 Token Plan API Key：https://platform.minimaxi.com/user-center/payment/token-plan
3. 更新 `~/.hermes/.env` 中的 `MINIMAX_CN_API_KEY` 为 Token Plan API Key
4. 重启 Hermes Agent gateway

## 验证方式

```bash
# 检查当前 API key 类型
# Token Plan key 和按量付费 key 格式不同
```

## 关键发现（续）：Hermes Agent 的 vision 路由机制

**vision_analyze 工具默认不走 MiniMax！**

查看源码 `agent/auxiliary_client.py`:
```python
_VISION_AUTO_PROVIDER_ORDER = (
    "openrouter",   # ← 第一优先级
    "nous",         # ← 第二优先级
    # MiniMax 不在列表里！
)
```

当主 provider 是 MiniMax 时，vision_analyze 会：
1. 检查 MiniMax 是否在 `_VISION_AUTO_PROVIDER_ORDER` → 不在
2. Fall back 到 OpenRouter（第一候补）
3. OpenRouter 也需要 API key → 没配 → **404 Not Found**

**ClawX 能做 vision 的真正原因**：
- ClawX 内置了 `opencode-go` provider，路由到 `https://opencode.ai/zen/go`
- OpenCode AI 网关背后接了多个 vision 模型（Claude/GPT-4o 等）
- 或者 ClawX 内置了 Token Plan MCP (`understand_image` 工具)

**所以 Hermes Agent 的 vision 问题不是 MiniMax 不支持，而是 vision_analyze 根本没路由到 MiniMax！**

解决方案：
1. **方案A**：配置 OpenRouter API key（或其他 vision provider）
2. **方案B**：修改 Hermes Agent 代码，把 MiniMax 加入 `_VISION_AUTO_PROVIDER_ORDER`
3. **方案C**：接 ClawX 的 OpenCode AI 网关（需要 API key）

## 坑点
- Hermes Agent 的 vision_analyze 工具在 MiniMax 上不工作是因为用了按量付费 API key
- Token Plan API Key ≠ 按量付费 API Key
- MiniMax 开放平台文档的 Anthropic API 部分明确写了"不支持图像输入"

## 关键实测发现（2025-04-12）

## 坑点
- MiniMax REST API 无论哪个端点都不支持图片理解（即使返回 200，图片也被静默丢弃）
- Token Plan API Key ≠ 按量付费 API Key
- vision_analyze 工具**默认不走 MiniMax**，走 OpenRouter

## 关键实测发现（2025-04-12）

### MiniMax REST API 行为（按量付费 key）
- `/v1/chat/completions` + `image_url` base64 → **200 但图片被静默丢弃**，模型回复"没有图片"
- `/anthropic/v1/messages` + `image` base64 → **200 但图片被静默丢弃**，模型回复"没有图片"
- `/anthropic/v1/chat/completions` → **404 Not Found**

### MiniMax 图片相关能力分布
| 能力 | 端点 | 备注 |
|--|--|--|
| 图片生成 | `POST /v1/image_generation` model=image-01 | 常规 API key 可用 |
| 图片理解（vision） | Token Plan MCP `understand_image` 工具 | 仅 Token Plan 可用 |
| 视频/语音/音乐生成 | 各专用端点 | 常规 API key 可用 |

### 确认方式
- 文档 Banner：**"订阅 Token Plan，尽享全模态模型"**
- "图像"板块只有图片生成文档，无图片理解文档

### 结论
**MiniMax REST API 完全不支持图片理解**，必须走以下方案之一：
1. **OpenRouter** + vision 模型（如 `nvidia/nemotron-nano-12b-v2-vl`、`google/gemini-3-flash-preview`）
2. Token Plan MCP 工具 `understand_image`

### 已验证可行的 vision 配置
```yaml
auxiliary:
  vision:
    provider: openrouter
    model: nvidia/nemotron-nano-12b-v2-vl  # 或 google/gemini-3-flash-preview
    base_url: https://openrouter.ai/api/v1
    api_key: sk-or-...   # OpenRouter API Key
    timeout: 30
    download_timeout: 30
```
vision 能力的唯一出口是 **Token Plan MCP** 工具 `understand_image`。

### 实际解决方案（2025-04-12）
| 方案 | 难度 | 效果 |
|------|------|------|
| 方案A：配置 OpenRouter API key | 易 | vision_analyze 自动走 OpenRouter Gemini Flash |
| 方案B：配置 MiniMax MCP `understand_image` | 烦 | 需要 MCPorter 配置，工具名 `understand_image` |
