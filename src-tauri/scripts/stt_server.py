#!/usr/bin/env python3
"""
STT 常驻服务：启动一次，保持进程热，通过 stdin/stdout 收发任务
使用 mlx-whisper（本地离线，针对 Apple Silicon 优化）

协议（每行一个请求）：
  输入: wav_path|result_path|lang
  输出: DONE  或  ERROR
"""
import sys
import os

try:
    import mlx_whisper
except ImportError:
    print("IMPORT_ERROR", flush=True)
    sys.exit(1)

# 模型首次使用时自动下载（~75MB），之后缓存到本地
# 使用 base 模型：速度快，中英文识别效果好
MODEL = "mlx-community/whisper-base-mlx"
_model = None

def get_model():
    global _model
    if _model is None:
        # mlx_whisper 内部缓存模型，只下载一次
        _model = True  # 标记已初始化，实际模型在调用时懒加载
    return MODEL

# 告知 Rust 已就绪
print("READY", flush=True)
sys.stdout.flush()

for raw_line in sys.stdin:
    line = raw_line.strip()
    if not line:
        continue

    parts = line.split("|")
    if len(parts) < 2:
        print("ERROR", flush=True)
        continue

    wav_path    = parts[0]
    result_path = parts[1]
    lang        = parts[2] if len(parts) > 2 else "zh"

    # mlx-whisper 用语言代码不带区域（zh 而非 zh-CN）
    if lang.startswith("zh"):
        lang = "zh"
    elif "-" in lang:
        lang = lang.split("-")[0]

    if not os.path.exists(wav_path):
        with open(result_path, "w") as f:
            f.write("ERROR:file_not_found")
        print("DONE", flush=True)
        continue

    try:
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo=get_model(),
            language=lang if lang else None,
            verbose=False,
        )
        text = result.get("text", "").strip()
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(text if text else "ERROR:empty_result")
    except Exception as e:
        with open(result_path, "w") as f:
            f.write(f"ERROR:{e}")

    print("DONE", flush=True)
