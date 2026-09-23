"""把「录屏 + 逐句旁白」合成成片：narration.wav + sub.ass + demo.mp4。

上一版是手工拼的，用的命令没有留下来，于是这次只能从头重建一遍。写成脚本的
理由不是省事，而是**上一版的合成过程无法复现**——一个只能被人工重现一次的
交付步骤，等于没有交付步骤。

对齐原理：画面里每段的真实起始秒由录制脚本写进 timing.json（它是量出来的，
不是算出来的）。这里让每段的旁白**也从那个秒数开始**，中间的空档用静音补齐。
于是「这一段画面在讲这句话」是算出来的，不靠掐秒。

为什么字幕按短语切：整段合成再按字数估边界，估出来的时间点会与读音差出小半句，
观众会以为字幕是错的。短语的时长由 build-narration.py 逐句量出，这里只做排布。

前置：先跑 build-narration.py 再跑 demo-video.mjs，三者共用 VIDEO_DIR。
用法：VIDEO_DIR=... python scripts/video/compose.py [输出路径]
输出默认直接落在仓库根目录的「演示视频.mp4」——成品就是交付物本身，
中间不要再插一步手工拷贝：多一步手工，就多一次「这次拷的是哪一版」的机会。

编码参数是量出来的，不是抄的：同一段录屏，crf 20/medium 出 16.1 MiB，
crf 24/slow 出 12.1 MiB，crf 28/slow 出 9.1 MiB。逐帧看过
滚动途中（最难压的一段）与静止画面两种情况下的小字，crf 28 与 crf 20 无肉眼差别——
画面绝大部分是静止的，贵的只有滚动那几秒。压到 9 MiB 之后，整包才留得住余量。
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import wave

import imageio_ffmpeg

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent.parent.parent  # video/ → scripts/ → frontend/ → 仓库根
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
OUT = pathlib.Path(os.environ.get("VIDEO_DIR") or pathlib.Path(tempfile.gettempdir()) / "vid")
GAP = 0.02  # 与 build-narration.py 里那个常数必须一致：它已经算进每段旁白的总长

ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: zh,Microsoft YaHei,30,&H00FFFFFF,&H00FFFFFF,&H40101014,&H00000000,0,0,0,0,100,100,0,0,3,9,0,2,60,60,40,134

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def wav_params(path: pathlib.Path) -> tuple[int, int, int]:
    with wave.open(str(path)) as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth()


def silence(seconds: float, rate: int, ch: int, out: pathlib.Path) -> None:
    subprocess.run(
        [
            FFMPEG, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl={'mono' if ch == 1 else 'stereo'}",
            "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(out),
        ],
        check=True,
    )


def main() -> int:
    phrases = json.loads((OUT / "phrases.json").read_text(encoding="utf-8"))
    timing = json.loads((OUT / "timing.json").read_text(encoding="utf-8"))

    rate, ch, _ = wav_params(OUT / phrases[0]["phrases"][0]["file"])
    print(f"旁白采样：{rate} Hz / {ch} 声道")

    gap_wav = OUT / "gap.wav"
    silence(GAP, rate, ch, gap_wav)

    parts: list[pathlib.Path] = []
    dialogue: list[str] = []
    cursor = 0.0
    drift: list[str] = []

    for scene in phrases:
        sid = scene["sid"]
        want = float(timing[sid]["start"])
        if want - cursor > 0.005:  # 画面这一段比旁白晚开始 → 补静音，把旁白按到画面上
            sil = OUT / f"sil_{sid}_lead.wav"
            silence(want - cursor, rate, ch, sil)
            parts.append(sil)
            cursor = want
        elif want < cursor - 0.05:
            drift.append(f"{sid} 旁白比画面早 {cursor - want:.2f}s")
        for i, p in enumerate(scene["phrases"]):
            if i:
                parts.append(gap_wav)
                cursor += GAP
            start = cursor
            parts.append(OUT / p["file"])
            cursor += p["dur"]
            dialogue.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(cursor)},zh,,0,0,0,,{p['text']}"
            )

    (OUT / "sub.ass").write_text(ASS_HEAD + "\n".join(dialogue) + "\n", encoding="utf-8")
    (OUT / "narration.txt").write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8"
    )
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(OUT / "narration.txt"), "-c", "copy", str(OUT / "narration.wav")],
        check=True,
    )
    print(f"旁白拼接完成：{cursor:.2f}s，字幕 {len(dialogue)} 条")
    for d in drift:
        print(f"  注意：{d}")

    raw = pathlib.Path((OUT / "raw-path.txt").read_text(encoding="utf-8").strip())
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "演示视频.mp4"
    #
    # 字幕路径里的反斜杠与冒号会被 filtergraph 当成语法，必须先转义再传给 ass=
    escaped = (OUT / "sub.ass").as_posix().replace(":", r"\:")
    subprocess.run(
        [
            FFMPEG, "-y", "-v", "error",
            "-i", str(raw), "-i", str(OUT / "narration.wav"),
            "-vf", f"ass='{escaped}'",
            "-c:v", "libx264", "-preset", "slow", "-crf", "28",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
            "-shortest", str(out),
        ],
        check=True,
    )
    print(f"成片：{out}  {out.stat().st_size / 1024 / 1024:.2f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
