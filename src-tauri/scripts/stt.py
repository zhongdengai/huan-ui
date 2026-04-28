#!/usr/bin/env python3
"""
语音识别脚本
用法: python3 stt.py <音频文件路径> <结果输出文件路径> [语言代码]
依赖: pip3 install SpeechRecognition
"""
import sys
import os

audio_file  = sys.argv[1] if len(sys.argv) > 1 else ""
result_file = sys.argv[2] if len(sys.argv) > 2 else "/tmp/huanhuan_stt_result.txt"
lang        = sys.argv[3] if len(sys.argv) > 3 else "zh-CN"

def write_result(text):
    with open(result_file, "w", encoding="utf-8") as f:
        f.write(text)

if not audio_file or not os.path.exists(audio_file):
    write_result("ERROR:file_not_found")
    sys.exit(1)

try:
    import speech_recognition as sr
except ImportError:
    write_result("ERROR:no_speech_recognition_package")
    sys.exit(1)

r = sr.Recognizer()
try:
    with sr.AudioFile(audio_file) as source:
        audio = r.record(source)
    text = r.recognize_google(audio, language=lang)
    write_result(text.strip() if text.strip() else "ERROR:empty_result")
except sr.UnknownValueError:
    write_result("ERROR:unknown_value")
except sr.RequestError as e:
    write_result(f"ERROR:google_api:{e}")
except Exception as e:
    write_result(f"ERROR:{e}")
