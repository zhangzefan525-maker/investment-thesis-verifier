"""合成一句话。由 build-narration.py 逐句 spawn，不单独使用。

必须**一句话一个进程**。同一个进程里反复 save_to_file / runAndWait，
SAPI 会在第二次或第三次之后不再返回（实测挂在第 3 句上，进程一直不动）。
这里不设超时也不重试：调用方是逐句 spawn 的，卡住就是卡住，看得见。

用法：python tts_one.py "要读的句子" 输出.wav
成功时向 stdout 打印这句的秒数，失败时打印 FAILED 并以非零码退出。
"""
import pathlib
import sys
import wave

import pyttsx3

text, out = sys.argv[1], sys.argv[2]
engine = pyttsx3.init()
engine.setProperty(
    "voice",
    r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_ZH-CN_HUIHUI_11.0",
)
engine.setProperty("rate", 170)
engine.save_to_file(text, out)
engine.runAndWait()

p = pathlib.Path(out)
if not p.exists() or p.stat().st_size < 1000:
    print("FAILED")
    sys.exit(1)
with wave.open(str(p)) as w:
    print(f"{w.getnframes() / w.getframerate():.3f}")
