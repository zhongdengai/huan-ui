#!/usr/bin/env python3
"""
MiniMax image-01 生图脚本
用法: python3 minimax_image_gen.py "提示词" [aspect_ratio]
aspect_ratio: 1:1(默认) | 16:9 | 9:16 | 4:3 | 3:2
"""
import sys, json, base64, urllib.request, datetime, subprocess
from pathlib import Path

def get_key():
    cfg_path = Path.home() / "Library/Application Support/huanhuan/user/config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg["llm"]["api_key"], cfg["llm"].get("base_url", "https://api.minimaxi.com/v1")
    except Exception as e:
        print(f"ERROR: 读取 API Key 失败: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("用法: minimax_image_gen.py <prompt> [aspect_ratio]")
        sys.exit(1)

    prompt = sys.argv[1]
    aspect = sys.argv[2] if len(sys.argv) > 2 else "1:1"

    valid = {"1:1", "16:9", "9:16", "4:3", "3:2", "2:3"}
    if aspect not in valid:
        aspect = "1:1"

    api_key, base_url = get_key()
    host = base_url.rstrip("/")
    if host.endswith("/v1"):
        host = host[:-3]

    payload = json.dumps({
        "model": "image-01",
        "prompt": prompt,
        "response_format": "base64",
        "n": 1,
        "aspect_ratio": aspect,
    }).encode()

    req = urllib.request.Request(
        host + "/v1/image_generation",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    print(f"🎨 正在生成图片：{prompt[:50]}...")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"ERROR: 请求失败: {e}")
        sys.exit(1)

    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code", 0) != 0:
        print(f"ERROR: MiniMax 返回错误: {base_resp.get('status_msg')}")
        sys.exit(1)

    imgs = data.get("data", {}).get("image_base64", [])
    if not imgs:
        print("ERROR: 没有返回图片数据")
        sys.exit(1)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = Path.home() / "Downloads" / f"huanhuan_{ts}.png"
    save_path.write_bytes(base64.b64decode(imgs[0]))

    print(f"✅ 图片已保存：{save_path}")

    # macOS 打开预览
    try:
        subprocess.Popen(["open", str(save_path)])
    except Exception:
        pass

if __name__ == "__main__":
    main()
