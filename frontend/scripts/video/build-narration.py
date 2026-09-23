"""把 scenes.json 拆成逐句短语，逐句合成语音并量出时长。

为什么逐句而不是逐场景：字幕按短语出现。整段合成再按字数估边界，
估出来的时间点会与读音差出小半句 —— 观众会以为字幕是错的。

为什么每句起一个新进程：同一个进程里反复 save_to_file / runAndWait，
SAPI 会在第二三次之后不再返回（实测挂在第 3 句上）。这条只有踩过才知道，
所以写在这里。

依赖：Windows + pyttsx3 + 系统语音 Microsoft Huihui（SAPI 离线合成，不联网）。

用法：
    python scripts/video/build-narration.py
产物（写入 VIDEO_DIR，默认系统临时目录下的 vid/）：
    p_<场景>_<序>.wav  每一句的语音
    phrases.json       每场景的短语文本与时长
    narration.json     每场景合计时长——录屏脚本 demo-video.mjs 读它
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import wave

HERE = pathlib.Path(__file__).parent
OUT = pathlib.Path(os.environ.get("VIDEO_DIR") or pathlib.Path(tempfile.gettempdir()) / "vid")
GAP = 0.02  # 短语之间的间隔，compose.py 按同一个常数排字幕


def split_phrases(text: str) -> list[str]:
    """按句号与分号切。切完保留标点，读出来才是完整的一句话。"""
    parts = re.findall(r"[^。；]+[。；]?", text)
    return [p for p in (x.strip() for x in parts) if p]


def measure(path: pathlib.Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    scenes = json.loads((HERE / "scenes.json").read_text(encoding="utf-8"))
    out: list[dict] = []
    narration: dict[str, float] = {}
    for sid, text in scenes:
        items = []
        for i, phrase in enumerate(split_phrases(text)):
            wav = OUT / f"p_{sid}_{i}.wav"
            # 已合成过就跳过：重跑时不必再等一遍，也避免把好文件覆盖成半个
            if not (wav.exists() and wav.stat().st_size > 1000):
                r = subprocess.run(
                    [sys.executable, str(HERE / "tts_one.py"), phrase, str(wav)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=120,
                )
                if r.returncode != 0:
                    print(f"TTS 失败：{sid} #{i} {phrase}\n{r.stdout}{r.stderr}")
                    return 1
            items.append({"text": phrase, "dur": round(measure(wav), 3), "file": wav.name})
        total = round(sum(p["dur"] for p in items) + GAP * (len(items) - 1), 3)
        out.append({"sid": sid, "phrases": items})
        narration[sid] = total
        print(f"{sid}  {total:7.3f}s  {len(items)} 句", flush=True)

    (OUT / "phrases.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (OUT / "narration.json").write_text(
        json.dumps(narration, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    planned = sum(narration.values())
    print(f"\n旁白合计 {planned:.1f}s（题目限制 60–180s）")
    return 0 if 60 <= planned <= 180 else 1


if __name__ == "__main__":
    sys.exit(main())
