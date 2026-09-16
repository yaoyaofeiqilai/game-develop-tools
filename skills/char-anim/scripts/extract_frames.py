# -*- coding: utf-8 -*-
"""视频均匀抽帧:PyAV 解码,等间隔采样 N 帧导出 PNG 序列。

必须用 ComfyUI 自带 python 跑(带 PyAV,零安装):
  <COMFYUI_ROOT>/python_embeded/python.exe extract_frames.py \
      --src video.mp4 --dst <帧目录> --frames 8 [--start 0.0] [--end -1]

--start/--end 单位秒,--end -1 = 到片尾。输出 frame_00.png ... 升序。
位移/单次类动作建议先全量抽帧(--frames 0 表示逐帧全抽)再人工圈区间。
"""
import argparse, sys
from pathlib import Path

import av


def extract(src: Path, dst: Path, n_frames: int, start: float, end: float) -> list[Path]:
    dst.mkdir(parents=True, exist_ok=True)
    container = av.open(str(src))
    stream = container.streams.video[0]
    fps = float(stream.average_rate)
    total = stream.frames or int((stream.duration or 0) * fps / stream.time_base.denominator * stream.time_base.numerator)
    # 解码全部帧索引后再采样(视频短,内存可控;长视频请先 --start/--end 截段)
    picked, saved = [], []
    lo, hi = int(start * fps), (int(end * fps) if end >= 0 else 10**9)
    idx = -1
    frames_buf: list = []
    for frame in container.decode(stream):
        idx += 1
        if idx < lo:
            continue
        if idx > hi:
            break
        frames_buf.append(frame.to_image())  # PIL.Image
    container.close()
    if not frames_buf:
        sys.exit(f"[extract] 未抽到任何帧(src={src}, start={start}, end={end}, fps={fps})")
    n = len(frames_buf) if n_frames <= 0 else min(n_frames, len(frames_buf))
    if n == len(frames_buf):
        idxs = range(len(frames_buf))
    else:
        idxs = sorted({round(i * (len(frames_buf) - 1) / (n - 1)) for i in range(n)}) if n > 1 else [0]
    for i, fi in enumerate(idxs):
        p = dst / f"frame_{i:02d}.png"
        frames_buf[fi].save(p)
        saved.append(p)
    print(f"[extract] {src.name}: 有效帧 {len(frames_buf)}(fps={fps:.2f}) → 采样 {len(saved)} 帧 → {dst}")
    return saved


def main() -> None:
    ap = argparse.ArgumentParser(description="视频均匀抽帧(PyAV)")
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--frames", type=int, default=8, help="采样帧数;0=逐帧全抽")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=-1.0)
    args = ap.parse_args()
    extract(Path(args.src), Path(args.dst), args.frames, args.start, args.end)


if __name__ == "__main__":
    main()
