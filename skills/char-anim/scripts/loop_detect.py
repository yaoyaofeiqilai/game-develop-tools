#!/usr/bin/env python3
"""在 PNG 帧序列中找最佳循环区间:缩略灰度图两两差异最小的一对(i,j),j-i 在指定范围内。
用法: loop_detect.py <目录> [最小长度] [最大长度] [起始帧] [结束帧]"""
import sys
import glob
from PIL import Image
import numpy as np

src = sys.argv[1]
min_len = int(sys.argv[2]) if len(sys.argv) > 2 else 24
max_len = int(sys.argv[3]) if len(sys.argv) > 3 else 10**9
lo = int(sys.argv[4]) if len(sys.argv) > 4 else None
hi = int(sys.argv[5]) if len(sys.argv) > 5 else None

files = sorted(glob.glob(src + "/*.png"))
if lo is not None and hi is not None:
    files = files[lo:hi]
n = len(files)
thumbs = []
for f in files:
    im = Image.open(f).convert("L").resize((64, 36))
    thumbs.append(np.asarray(im, dtype=np.float32))

best = None
for i in range(n):
    for j in range(i + min_len, min(i + max_len + 1, n)):
        diff = np.abs(thumbs[i] - thumbs[j]).mean()
        if best is None or diff < best[0]:
            best = (diff, i, j)
print(f"frames: {n} (range {lo}..{hi})")
print(f"best loop: start={best[1]} end={best[2]} diff={best[0]:.2f} len={best[2]-best[1]}")
if lo:
    print(f"absolute: start={best[1]+lo} end={best[2]+lo}")
