#!/bin/bash
# analyze_image.sh — 直接调 MiniMax Vision API，绕过 URL 访问限制
# 用法: analyze_image.sh <图片路径> <问题>

set -e

IMAGE_PATH="$1"
QUESTION="${2:-请描述这张图片的内容}"

if [ -z "$IMAGE_PATH" ]; then
    echo "Usage: $0 <image_path> [question]" >&2
    exit 1
fi

if [ ! -f "$IMAGE_PATH" ]; then
    echo "Error: File not found: $IMAGE_PATH" >&2
    exit 1
fi

# 读取 API key 和 base_url
SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CONFIG_YAML="$HOME/.hermes/config.yaml"

# 用 grep 提取 vision api_key（只取非空、非注释的行）
API_KEY=$(grep -A5 "^auxiliary:" "$CONFIG_YAML" 2>/dev/null | grep "vision:" -A4 | grep "api_key:" | head -1 | sed 's/.*api_key: *//' | tr -d ' ')
BASE_URL="https://api.minimaxi.com/anthropic/v1"
MODEL="MiniMax-M2.7"

if [ -z "$API_KEY" ] || [ "$API_KEY" = "sk-cp-..." ]; then
    echo "Error: Could not find valid API key in $CONFIG_YAML" >&2
    exit 1
fi

# 转成 base64，移除换行
IMAGE_B64=$(base64 -b 0 < "$IMAGE_PATH")

# 构建 JSON payload（miniMax 原生 vision 格式）
cat << EOF > /tmp/vision_payload.json
{
  "model": "$MODEL",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "$QUESTION"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,${IMAGE_B64}"}}
    ]
  }],
  "max_tokens": 2000,
  "stream": false
}
EOF

# 发请求
RESPONSE=$(curl -s -X POST "$BASE_URL/messages" \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d @/tmp/vision_payload.json)

rm -f /tmp/vision_payload.json

# 解析结果
echo "$RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if 'type' in data and data['type'] == 'error':
        print('Error:', data.get('error', {}).get('message', data))
    else:
        content = data.get('content', [])
        for block in content:
            if block.get('type') == 'text':
                print(block['text'])
            elif block.get('type') == 'image_url':
                print('[image]')
except:
    print('Raw response:', sys.stdin.read()[:500])
" 2>/dev/null || echo "$RESPONSE"
