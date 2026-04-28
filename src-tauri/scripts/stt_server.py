#!/opt/homebrew/bin/python3
"""
STT 常驻服务：启动一次，保持进程热，通过 stdin/stdout 收发任务
协议（每行一个请求）：
  输入: wav_path|result_path|lang
  输出: DONE  或  ERROR
"""
import sys
import os

try:
    import speech_recognition as sr
except ImportError:
    # 告知 Rust 缺少依赖，然后退出
    print("IMPORT_ERROR", flush=True)
    sys.exit(1)

recognizer = sr.Recognizer()

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

    wav_path   = parts[0]
    result_path = parts[1]
    lang       = parts[2] if len(parts) > 2 else "zh-CN"

    if not os.path.exists(wav_path):
        with open(result_path, "w") as f:
            f.write("ERROR:file_not_found")
        print("DONE", flush=True)
        continue

    try:
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)
        text = recognizer.recognize_google(audio, language=lang)
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(text.strip() if text.strip() else "ERROR:empty_result")
    except sr.UnknownValueError:
        with open(result_path, "w") as f:
            f.write("ERROR:unknown_value")
    except sr.RequestError as e:
        with open(result_path, "w") as f:
            f.write(f"ERROR:google_api:{e}")
    except Exception as e:
        with open(result_path, "w") as f:
            f.write(f"ERROR:{e}")

    print("DONE", flush=True)
