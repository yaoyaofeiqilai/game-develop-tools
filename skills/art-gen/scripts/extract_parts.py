#!/usr/bin/env python3
"""部件拆分图 → 独立部件 PNG 序列。

对拆分图做边缘泛洪去底后,按连通域提取每个部件(面积过滤),
输出 part_00.png ... 及部件清单(位置/尺寸),供人工或 AI 做部件映射。

用法:
    python extract_parts.py --src <拆分图> --dst <输出目录> [--min-area-ratio 0.01]
"""
import argparse
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from sprite_pipeline import white_bg_to_alpha  # noqa: E402


def extract(src: Path, dst: Path, min_area_ratio: float = 0.01) -> None:
    img = white_bg_to_alpha(Image.open(src))
    a = np.array(img)
    fg = a[:, :, 3] > 0
    h, w = fg.shape

    labels = np.zeros((h, w), dtype=np.int32)
    parts: list[dict] = []
    cur = 0
    for sy in range(h):
        for sx in range(w):
            if fg[sy, sx] and labels[sy, sx] == 0:
                cur += 1
                xs, ys, area = [sx, sx], [sy, sy], 0
                q = deque([(sy, sx)])
                labels[sy, sx] = cur
                while q:
                    cy, cx = q.popleft()
                    area += 1
                    xs[0] = min(xs[0], cx); xs[1] = max(xs[1], cx)
                    ys[0] = min(ys[0], cy); ys[1] = max(ys[1], cy)
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and fg[ny, nx] and labels[ny, nx] == 0:
                            labels[ny, nx] = cur
                            q.append((ny, nx))
                parts.append({"label": cur, "area": area,
                              "bbox": (xs[0], ys[0], xs[1] + 1, ys[1] + 1)})

    main_area = max(p["area"] for p in parts)
    parts = [p for p in parts if p["area"] >= main_area * min_area_ratio]
    parts.sort(key=lambda p: (p["bbox"][0]))  # 按 x 排序

    dst.mkdir(parents=True, exist_ok=True)
    print(f"[parts] 提取 {len(parts)} 个部件(总面积 {main_area}):")
    for i, p in enumerate(parts):
        x0, y0, x1, y1 = p["bbox"]
        piece = img.crop((x0, y0, x1, y1))
        out = dst / f"part_{i:02d}.png"
        piece.save(out)
        ratio = p["area"] / main_area
        print(f"  part_{i:02d}.png  {x1-x0}x{y1-y0}  面积比{ratio:.2f}  位置({x0},{y0})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--min-area-ratio", type=float, default=0.01)
    args = ap.parse_args()
    extract(Path(args.src), Path(args.dst), args.min_area_ratio)


if __name__ == "__main__":
    main()
