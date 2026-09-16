#!/usr/bin/env python3
"""精灵帧后处理管线:AI 生成的帧图 → 游戏可用资产。

流程:边缘泛洪去白底(保留角色身上的白色衣物)→ 紧致裁剪 →
脚底基线对齐 → 统一帧尺寸 → 输出处理后的帧序列 + 预览 GIF。

为什么用边缘泛洪而不是全局色键:角色有白色围裙/女仆头饰,
全局去白会把衣服吃掉;只有与图像边缘连通的白色区域才是背景。

用法:
    python sprite_pipeline.py --src <帧目录> --dst <输出目录> [--height 256] [--gif]
"""
import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image


def white_bg_to_alpha(img: Image.Image, tol: int = 24) -> Image.Image:
    """把与边缘连通的白底转透明(容差 tol),边缘外扩 1px 收缩去白边。"""
    a = np.array(img.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    white = (a.sum(axis=2) >= 765 - tol * 3)

    visited = np.zeros((h, w), dtype=bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if white[y, x] and not visited[y, x]:
                visited[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if white[y, x] and not visited[y, x]:
                visited[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and white[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = True
                q.append((ny, nx))

    alpha = np.where(visited, 0, 255).astype(np.uint8)

    # 去孤岛:固定槽位切割会把相邻帧的尾巴/裙角切进来形成小碎片,
    # 只保留与最大连通域面积比超过 3% 的前景块,其余置透明
    fg = alpha > 0
    labels = np.zeros(fg.shape, dtype=np.int32)
    cur = 0
    areas: dict[int, int] = {}
    for sy in range(fg.shape[0]):
        for sx in range(fg.shape[1]):
            if fg[sy, sx] and labels[sy, sx] == 0:
                cur += 1
                area = 0
                q2 = deque([(sy, sx)])
                labels[sy, sx] = cur
                while q2:
                    cy, cx = q2.popleft()
                    area += 1
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if (0 <= ny < fg.shape[0] and 0 <= nx < fg.shape[1]
                                and fg[ny, nx] and labels[ny, nx] == 0):
                            labels[ny, nx] = cur
                            q2.append((ny, nx))
                areas[cur] = area
    if areas:
        main_area = max(areas.values())
        for label, area in areas.items():
            if area < main_area * 0.03:
                alpha[labels == label] = 0

    # 收缩 1px:与透明区相邻的前景像素 alpha 减半(去白边光晕)
    fg = alpha > 0
    edge = np.zeros_like(fg)
    edge[1:, :] |= fg[:-1, :] != fg[1:, :]
    edge[:-1, :] |= fg[:-1, :] != fg[1:, :]
    edge[:, 1:] |= fg[:, :-1] != fg[:, 1:]
    edge[:, :-1] |= fg[:, :-1] != fg[:, 1:]
    edge &= fg
    alpha[edge] = 200

    rgba = np.dstack([a.astype(np.uint8), alpha])
    return Image.fromarray(rgba, "RGBA")


def tight_crop(img: Image.Image) -> Image.Image:
    """按透明通道紧致裁剪。"""
    bbox = img.getchannel("A").getbbox()
    return img.crop(bbox) if bbox else img


def process_frames(src: Path, dst: Path, height: int = 256, has_alpha: bool = False,
                   ref_frame: int | None = None, tol: int = 24) -> list[Path]:
    frames = sorted((p for p in src.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")),
                    key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem)
    if not frames:
        raise SystemExit(f"目录无帧图: {src}")

    # 第一遍:去底 + 逐帧内容包围盒
    # has_alpha=True 时输入帧自带透明通道(如 Animated Drawings 渲染输出),跳过白底泛洪
    # 包围盒用阈值化 alpha(>32)计算:rembg 输出的半透明噪点/淡阴影会把 getbbox() 撑大,
    # 导致缩放基准失真(角色被缩小)
    # tol 是白底判定容差:默认 24;浅肉色手部高光可能被判成白底遭泛洪吃掉
    # (attack_up/4、attack_down/13 踩过),此时调小 tol(如 12)收紧判定
    alphas, boxes = [], []
    for f in frames:
        img = Image.open(f).convert("RGBA") if has_alpha else white_bg_to_alpha(Image.open(f), tol)
        alphas.append(img)
        boxes.append(img.getchannel("A").point(lambda v: 255 if v > 32 else 0).getbbox())

    # 联合包围盒对齐(关键!):所有帧按各自内容在统一画布上的绝对位置放置,
    # 躯干位置不再因逐帧裁剪而漂移——消除播放时的位置跳变/卡顿
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    unioned = [img.crop((x0, y0, x1, y1)) for img in alphas]

    # 缩放基准:默认用联合包围盒高(循环动作,各帧内容高度一致)。
    # 跳跃等有位移的动作联合框含运动幅度,会误把角色缩小——
    # 此时用 --ref-frame 指定一帧站立姿势,以该帧内容高度为角色身高基准,
    # 保证与其他动作的精灵尺寸一致。
    if ref_frame is not None:
        rb = boxes[ref_frame]
        scale = height / (rb[3] - rb[1])
    else:
        scale = height / (y1 - y0)
    cell_w = int((x1 - x0) * scale) + 8
    cell_h = int((y1 - y0) * scale) + 8

    dst.mkdir(parents=True, exist_ok=True)
    outs = []
    for i, img in enumerate(unioned):
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        cell = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
        cell.paste(img, ((cell_w - img.width) // 2, cell_h - 4 - img.height), img)
        out = dst / f"{i}.png"
        cell.save(out)
        outs.append(out)
    return outs


def make_gif(frames: list[Path], out: Path, fps: int = 8) -> None:
    imgs = [Image.open(f).convert("RGBA") for f in frames]
    bg = Image.new("RGBA", imgs[0].size, (245, 248, 250, 255))
    frames_rgb = []
    for im in imgs:
        canvas = bg.copy()
        canvas.paste(im, (0, 0), im)
        frames_rgb.append(canvas.convert("P", palette=Image.ADAPTIVE))
    frames_rgb[0].save(out, save_all=True, append_images=frames_rgb[1:],
                       duration=int(1000 / fps), loop=0)


def main() -> None:
    ap = argparse.ArgumentParser(description="精灵帧后处理:去底/对齐/统一尺寸")
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--height", type=int, default=256, help="帧内容高度(px)")
    ap.add_argument("--gif", action="store_true", help="同时输出预览 GIF")
    ap.add_argument("--has-alpha", action="store_true", help="输入帧自带透明通道,跳过白底泛洪")
    ap.add_argument("--ref-frame", type=int, default=None,
                    help="以该帧(站立姿势)的内容高度为角色身高基准;跳跃等有位移的动作用")
    ap.add_argument("--tol", type=int, default=24,
                    help="白底泛洪容差,默认 24;浅肉色肢体被误吃时调小(如 12)")
    args = ap.parse_args()

    outs = process_frames(Path(args.src), Path(args.dst), args.height, args.has_alpha,
                          args.ref_frame, args.tol)
    print(f"[sprite] 处理 {len(outs)} 帧 -> {args.dst}")
    if args.gif:
        gif = Path(args.dst) / "preview.gif"
        make_gif(outs, gif)
        print(f"[sprite] 预览: {gif}")


if __name__ == "__main__":
    main()
