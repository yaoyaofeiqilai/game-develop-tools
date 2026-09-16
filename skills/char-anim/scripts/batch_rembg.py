#!/usr/bin/env python3
"""批量 rembg 抠图(isnet-anime 模型): <输入目录> <输出目录>
输入 PNG 序列(任意背景),输出同名透明 PNG。"""
import sys
import glob
import os
from rembg import remove, new_session
from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)
session = new_session("isnet-anime")
files = sorted(glob.glob(os.path.join(src, "*.png")))
for k, f in enumerate(files):
    im = Image.open(f).convert("RGB")
    out = remove(im, session=session)
    out.save(os.path.join(dst, os.path.basename(f)))
    if (k + 1) % 10 == 0 or k + 1 == len(files):
        print(f"{k+1}/{len(files)}", flush=True)
print("done ->", dst)
