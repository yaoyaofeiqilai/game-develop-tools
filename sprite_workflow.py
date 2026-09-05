from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL_HOME = PROJECT_ROOT / ".models"


@dataclass(frozen=True)
class FrameResult:
    rgba: Image.Image
    mask: Image.Image
    trimmed: Image.Image
    bbox: tuple[int, int, int, int] | None


class BackgroundRemover:
    def __init__(
        self,
        mode: str,
        model: str,
        provider: str,
        decontaminate: bool,
        alpha_matting: bool,
        chroma_tolerance: float,
        chroma_feather: float,
    ) -> None:
        self.mode = mode
        self.model = model
        self.provider = provider
        self.decontaminate = decontaminate
        self.alpha_matting = alpha_matting
        self.chroma_tolerance = chroma_tolerance
        self.chroma_feather = chroma_feather
        self._session = None
        self.actual_providers: list[str] = []

    def _ensure_session(self):
        if self._session is not None:
            return self._session

        os.environ.setdefault("REMBG_HOME", str(DEFAULT_MODEL_HOME))
        from rembg import new_session

        providers = None
        if self.provider == "cuda":
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif self.provider == "cpu":
            providers = ["CPUExecutionProvider"]
        elif sys.platform == "win32":
            # CUDA EP being listed does not guarantee that its CUDA/cuDNN DLLs
            # are installed. Probe the key DLLs so auto mode fails over cleanly
            # instead of printing a long ONNX Runtime loader error.
            try:
                import onnxruntime as ort

                preload = getattr(ort, "preload_dlls", None)
                if preload is not None:
                    preload()
                ctypes.WinDLL("cublasLt64_13.dll")
                ctypes.WinDLL("cudnn64_9.dll")
            except (OSError, RuntimeError):
                providers = ["CPUExecutionProvider"]
                print(
                    "未检测到完整的 CUDA 13/cuDNN 9 运行库，自动使用 CPU。"
                    "如需 GPU 加速，请参考 README 的可选安装说明。"
                )

        kwargs = {"providers": providers} if providers else {}
        print(f"加载 rembg 模型：{self.model}（首次运行会下载模型）")
        self._session = new_session(self.model, **kwargs)
        inner = getattr(self._session, "inner_session", None)
        if inner is not None:
            self.actual_providers = list(inner.get_providers())
            print("推理后端：" + ", ".join(self.actual_providers))
        return self._session

    def remove(self, image: Image.Image) -> Image.Image:
        selected_mode = self.mode
        if selected_mode == "auto":
            selected_mode = "chroma" if has_uniform_border(image) else "rembg"

        if selected_mode == "chroma":
            return remove_connected_chroma(
                image,
                tolerance=self.chroma_tolerance,
                feather=self.chroma_feather,
            )

        from rembg import remove

        result = remove(
            image.convert("RGB"),
            session=self._ensure_session(),
            decontaminate=self.decontaminate,
            alpha_matting=self.alpha_matting,
        )
        if not isinstance(result, Image.Image):
            raise TypeError("rembg 返回了非 PIL 图像结果")
        return result.convert("RGBA")


def border_pixels(image: Image.Image, width: int = 4) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    width = max(1, min(width, rgb.shape[0] // 4, rgb.shape[1] // 4))
    return np.concatenate(
        [
            rgb[:width, :, :].reshape(-1, 3),
            rgb[-width:, :, :].reshape(-1, 3),
            rgb[:, :width, :].reshape(-1, 3),
            rgb[:, -width:, :].reshape(-1, 3),
        ],
        axis=0,
    )


def has_uniform_border(image: Image.Image, max_channel_std: float = 7.0) -> bool:
    pixels = border_pixels(image)
    return bool(np.max(np.std(pixels, axis=0)) <= max_channel_std)


def remove_connected_chroma(
    image: Image.Image, tolerance: float = 18.0, feather: float = 10.0
) -> Image.Image:
    """Remove only background-coloured pixels connected to the image border.

    Limiting the key to border-connected regions protects white eyes and other
    foreground details that happen to match the background colour.
    """
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    rgb = rgba[:, :, :3].astype(np.float32)
    background = np.median(border_pixels(image), axis=0)
    distance = np.linalg.norm(rgb - background, axis=2)

    candidate = distance <= tolerance + max(feather, 0.0)
    seed = np.zeros(candidate.shape, dtype=bool)
    seed[0, :] = candidate[0, :]
    seed[-1, :] = candidate[-1, :]
    seed[:, 0] = candidate[:, 0]
    seed[:, -1] = candidate[:, -1]
    connected = ndimage.binary_propagation(seed, mask=candidate)

    if feather <= 0:
        alpha = np.where(connected, 0, 255).astype(np.uint8)
    else:
        soft_alpha = np.clip((distance - tolerance) / feather, 0.0, 1.0) * 255.0
        alpha = np.where(connected, soft_alpha, 255.0).astype(np.uint8)
    rgba[:, :, 3] = alpha
    return Image.fromarray(rgba, "RGBA")


def split_grid(
    image: Image.Image, rows: int, columns: int
) -> Iterable[tuple[int, int, tuple[int, int, int, int], Image.Image]]:
    width, height = image.size
    for row in range(rows):
        y0 = round(row * height / rows)
        y1 = round((row + 1) * height / rows)
        for column in range(columns):
            x0 = round(column * width / columns)
            x1 = round((column + 1) * width / columns)
            box = (x0, y0, x1, y1)
            yield row, column, box, image.crop(box)


def clean_alpha(
    rgba: Image.Image,
    min_component_area: int,
    foreground_threshold: int,
) -> Image.Image:
    array = np.asarray(rgba.convert("RGBA"), dtype=np.uint8).copy()
    alpha = array[:, :, 3]
    if min_component_area > 0:
        labels, count = ndimage.label(alpha >= foreground_threshold)
        if count:
            sizes = np.bincount(labels.ravel())
            remove_labels = np.flatnonzero(sizes < min_component_area)
            remove_labels = remove_labels[remove_labels != 0]
            if remove_labels.size:
                alpha[np.isin(labels, remove_labels)] = 0
    array[:, :, 3] = alpha
    return Image.fromarray(array, "RGBA")


def alpha_bbox(
    rgba: Image.Image, crop_threshold: int, padding: int
) -> tuple[int, int, int, int] | None:
    alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    ys, xs = np.where(alpha >= crop_threshold)
    if xs.size == 0:
        return None
    left = max(0, int(xs.min()) - padding)
    top = max(0, int(ys.min()) - padding)
    right = min(rgba.width, int(xs.max()) + 1 + padding)
    bottom = min(rgba.height, int(ys.max()) + 1 + padding)
    return left, top, right, bottom


def checkerboard(size: tuple[int, int], tile: int = 16) -> Image.Image:
    width, height = size
    canvas = Image.new("RGB", size, (238, 238, 238))
    draw = ImageDraw.Draw(canvas)
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            if (x // tile + y // tile) % 2:
                draw.rectangle(
                    (x, y, min(x + tile - 1, width - 1), min(y + tile - 1, height - 1)),
                    fill=(205, 205, 205),
                )
    return canvas


def process_frame(
    frame: Image.Image,
    remover: BackgroundRemover,
    min_component_area: int,
    foreground_threshold: int,
    crop_threshold: int,
    padding: int,
) -> FrameResult:
    rgba = remover.remove(frame)
    rgba = clean_alpha(rgba, min_component_area, foreground_threshold)
    mask = rgba.getchannel("A")
    bbox = alpha_bbox(rgba, crop_threshold, padding)
    if bbox is None:
        trimmed = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    else:
        trimmed = rgba.crop(bbox)
    return FrameResult(rgba=rgba, mask=mask, trimmed=trimmed, bbox=bbox)


def image_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(
            path
            for path in input_path.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
    raise FileNotFoundError(f"输入不存在：{input_path}")


def process_sheet(
    source: Path,
    output_root: Path,
    rows: int,
    columns: int,
    remover: BackgroundRemover,
    min_component_area: int,
    foreground_threshold: int,
    crop_threshold: int,
    padding: int,
) -> Path:
    image = Image.open(source).convert("RGBA")
    job_dir = output_root / source.stem
    full_dir = job_dir / "frames_rgba"
    trimmed_dir = job_dir / "frames_trimmed"
    mask_dir = job_dir / "masks"
    for directory in (full_dir, trimmed_dir, mask_dir):
        directory.mkdir(parents=True, exist_ok=True)

    transparent_sheet = Image.new("RGBA", image.size, (0, 0, 0, 0))
    mask_sheet = Image.new("L", image.size, 0)
    frames: list[dict[str, object]] = []

    for index, (row, column, source_box, frame) in enumerate(
        split_grid(image, rows, columns), start=1
    ):
        print(f"  帧 {index}/{rows * columns}（行 {row + 1}，列 {column + 1}）")
        result = process_frame(
            frame,
            remover,
            min_component_area,
            foreground_threshold,
            crop_threshold,
            padding,
        )
        filename = f"frame_{index:03d}.png"
        result.rgba.save(full_dir / filename)
        result.trimmed.save(trimmed_dir / filename)
        result.mask.save(mask_dir / filename)

        x0, y0, x1, y1 = source_box
        transparent_sheet.alpha_composite(result.rgba, (x0, y0))
        mask_sheet.paste(result.mask, (x0, y0))

        if result.bbox is None:
            trim_box = None
            pivot = None
        else:
            left, top, right, bottom = result.bbox
            trim_width = right - left
            trim_height = bottom - top
            # Bottom-centre pivot expressed in trimmed-image pixels. Engines can
            # use it to keep all trimmed frames aligned as if they were untrimmed.
            pivot = [frame.width / 2 - left, frame.height - top]
            trim_box = [left, top, right, bottom]

        frames.append(
            {
                "index": index,
                "row": row,
                "column": column,
                "file": filename,
                "source_box": list(source_box),
                "cell_size": [frame.width, frame.height],
                "trim_box": trim_box,
                "trimmed_size": list(result.trimmed.size),
                "pivot_bottom_center_px": pivot,
            }
        )

    sheet_path = job_dir / "sprite_sheet.png"
    mask_sheet_path = job_dir / "mask_sheet.png"
    preview_path = job_dir / "preview_checker.png"
    transparent_sheet.save(sheet_path)
    mask_sheet.save(mask_sheet_path)
    preview = checkerboard(transparent_sheet.size)
    preview.paste(transparent_sheet, (0, 0), transparent_sheet)
    preview.save(preview_path)

    manifest = {
        "source": str(source.resolve()),
        "source_size": list(image.size),
        "grid": {"rows": rows, "columns": columns},
        "background_removal": {
            "mode": remover.mode,
            "model": remover.model if remover.mode != "chroma" else None,
            "provider_request": remover.provider,
            "actual_providers": remover.actual_providers,
            "decontaminate": remover.decontaminate,
            "alpha_matting": remover.alpha_matting,
        },
        "frames": frames,
    }
    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return job_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="智能识别 AI 精灵图中的帧实例，并导出透明帧、蒙版和 Sprite Sheet。"
    )
    parser.add_argument("input", type=Path, help="输入图片或图片目录")
    parser.add_argument("--output", type=Path, default=Path("output"), help="输出根目录")
    parser.add_argument("--review", type=Path, default=Path("review"), help="失败任务记录目录")
    parser.add_argument("--watch", action="store_true", help="持续监控输入目录")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="监控扫描间隔（秒）")
    parser.add_argument(
        "--segmentation",
        choices=("smart", "grid"),
        default="smart",
        help="smart=全图实例识别（默认）；grid=旧版固定网格",
    )
    parser.add_argument("--rows", type=int, default=0, help="行数提示，0=自动识别")
    parser.add_argument("--columns", type=int, default=0, help="列数提示，0=自动识别")
    parser.add_argument("--expected-frames", type=int, default=0, help="预期帧数，0=自动识别")
    parser.add_argument(
        "--mode",
        choices=("rembg", "chroma", "auto"),
        default="auto",
        help="rembg=AI抠图；chroma=纯色背景；auto=按边缘颜色自动选择",
    )
    parser.add_argument("--model", default="isnet-anime", help="rembg 模型名")
    parser.add_argument(
        "--provider", choices=("auto", "cuda", "cpu"), default="auto", help="推理后端"
    )
    parser.add_argument(
        "--no-decontaminate", action="store_true", help="关闭边缘背景色去污染"
    )
    parser.add_argument("--alpha-matting", action="store_true", help="启用较慢的 Alpha Matting")
    parser.add_argument("--padding", type=int, default=4, help="紧凑裁剪额外保留像素")
    parser.add_argument(
        "--min-component-area", type=int, default=20, help="忽略小于该面积的前景噪点，0=关闭"
    )
    parser.add_argument(
        "--component-sensitivity", type=float, default=0.50,
        help="智能帧锚点灵敏度，范围 0.05 到 1.0",
    )
    parser.add_argument(
        "--confidence-threshold", type=float, default=0.42,
        help="低于该置信度的组件进入核对队列",
    )
    parser.add_argument(
        "--foreground-threshold", type=int, default=24, help="判定前景连通区域的 Alpha 阈值"
    )
    parser.add_argument("--crop-threshold", type=int, default=8, help="透明边裁剪 Alpha 阈值")
    parser.add_argument("--chroma-tolerance", type=float, default=18.0, help="纯色背景容差")
    parser.add_argument("--chroma-feather", type=float, default=10.0, help="纯色背景羽化宽度")
    return parser


def config_fingerprint(args: argparse.Namespace) -> str:
    values = {
        "segmentation": args.segmentation,
        "rows": args.rows,
        "columns": args.columns,
        "expected_frames": args.expected_frames,
        "mode": args.mode,
        "model": args.model,
        "provider": args.provider,
        "decontaminate": not args.no_decontaminate,
        "alpha_matting": args.alpha_matting,
        "padding": args.padding,
        "min_component_area": args.min_component_area,
        "foreground_threshold": args.foreground_threshold,
        "crop_threshold": args.crop_threshold,
        "chroma_tolerance": args.chroma_tolerance,
        "chroma_feather": args.chroma_feather,
        "component_sensitivity": args.component_sensitivity,
        "confidence_threshold": args.confidence_threshold,
    }
    encoded = json.dumps(values, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def source_signature(source: Path, config_id: str) -> str:
    stat = source.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}:{config_id}"


def load_state(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict[str, dict[str, object]]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def record_failure(source: Path, review_root: Path, error_text: str) -> Path:
    target = review_root / source.stem
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target / source.name)
    (target / "error.txt").write_text(error_text, encoding="utf-8")
    return target


def process_source(
    args: argparse.Namespace,
    source: Path,
    output_root: Path,
    remover: BackgroundRemover | None,
    smart_engine: object | None,
) -> tuple[Path, dict[str, object] | None]:
    if args.segmentation == "smart":
        from smart_engine import SmartConfig

        if smart_engine is None:
            raise RuntimeError("智能分割引擎未初始化")
        config = SmartConfig(
            background_mode=args.mode,
            background_model=args.model,
            provider=args.provider,
            expected_frames=max(0, args.expected_frames),
            rows=max(0, args.rows),
            columns=max(0, args.columns),
            alpha_threshold=args.foreground_threshold,
            min_component_area=max(0, args.min_component_area),
            component_sensitivity=args.component_sensitivity,
            confidence_threshold=args.confidence_threshold,
            padding=max(0, args.padding),
        )
        job = smart_engine.analyze(source, config)
        exported = smart_engine.export_job(job["id"], output_root=output_root)
        return Path(exported["output_dir"]), job

    if remover is None:
        raise RuntimeError("固定网格抠图引擎未初始化")
    if args.rows < 1 or args.columns < 1:
        raise ValueError("grid 模式下 rows 和 columns 必须大于 0")
    job_dir = process_sheet(
        source=source,
        output_root=output_root,
        rows=args.rows,
        columns=args.columns,
        remover=remover,
        min_component_area=max(0, args.min_component_area),
        foreground_threshold=args.foreground_threshold,
        crop_threshold=args.crop_threshold,
        padding=max(0, args.padding),
    )
    return job_dir, None


def process_pending(
    args: argparse.Namespace,
    remover: BackgroundRemover | None,
    smart_engine: object | None,
    output_root: Path,
    review_root: Path,
    state_path: Path,
    state: dict[str, dict[str, object]],
    config_id: str,
) -> int:
    sources = image_inputs(args.input.resolve())
    completed = 0
    for source in sources:
        key = str(source.resolve())
        signature = source_signature(source, config_id)
        previous = state.get(key, {})
        if previous.get("signature") == signature:
            continue

        print(f"检测到待处理图片：{source.name}")
        try:
            job_dir, smart_job = process_source(
                args, source, output_root, remover, smart_engine
            )
            state[key] = {
                "signature": signature,
                "status": "success",
                "output": str(job_dir),
                "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            print(f"完成：{job_dir}")
            if smart_job and smart_job["review_component_ids"]:
                print(
                    f"提示：{len(smart_job['review_component_ids'])} 个低置信组件可在 GUI 中核对。"
                )
            completed += 1
        except Exception:
            error_text = traceback.format_exc()
            review_dir = record_failure(source, review_root, error_text)
            state[key] = {
                "signature": signature,
                "status": "error",
                "review": str(review_dir),
                "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            print(f"处理失败，已记录到：{review_dir}", file=sys.stderr)
            print(error_text, file=sys.stderr)
        save_state(state_path, state)
    return completed


def main() -> int:
    args = build_parser().parse_args()
    if args.rows < 0 or args.columns < 0 or args.expected_frames < 0:
        raise ValueError("rows、columns 和 expected-frames 不能小于 0")
    if not 0.05 <= args.component_sensitivity <= 1.0:
        raise ValueError("component-sensitivity 必须在 0.05 到 1.0 之间")
    if not 0.0 <= args.confidence_threshold <= 1.0:
        raise ValueError("confidence-threshold 必须在 0 到 1.0 之间")
    if not 0 <= args.foreground_threshold <= 255 or not 0 <= args.crop_threshold <= 255:
        raise ValueError("Alpha 阈值必须在 0 到 255 之间")

    input_path = args.input.resolve()
    if args.watch and not input_path.is_dir():
        raise ValueError("--watch 模式的输入必须是目录")
    if not input_path.exists():
        raise FileNotFoundError(f"输入不存在：{input_path}")

    output_root = args.output.resolve()
    review_root = args.review.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    review_root.mkdir(parents=True, exist_ok=True)
    DEFAULT_MODEL_HOME.mkdir(parents=True, exist_ok=True)
    os.environ["REMBG_HOME"] = str(DEFAULT_MODEL_HOME)

    remover = None
    smart_engine = None
    if args.segmentation == "smart":
        from smart_engine import SmartEngine

        smart_engine = SmartEngine()
    else:
        remover = BackgroundRemover(
            mode=args.mode,
            model=args.model,
            provider=args.provider,
            decontaminate=not args.no_decontaminate,
            alpha_matting=args.alpha_matting,
            chroma_tolerance=args.chroma_tolerance,
            chroma_feather=args.chroma_feather,
        )

    state_path = output_root / ".workflow-state.json"
    state = load_state(state_path) if args.watch else {}
    config_id = config_fingerprint(args)

    if args.watch:
        print(f"监控目录：{input_path}")
        print(f"成功输出：{output_root}")
        print(f"失败记录：{review_root}")
        print("按 Ctrl+C 停止监控。")
        try:
            while True:
                process_pending(
                    args,
                    remover,
                    smart_engine,
                    output_root,
                    review_root,
                    state_path,
                    state,
                    config_id,
                )
                time.sleep(max(0.5, args.poll_interval))
        except KeyboardInterrupt:
            print("\n监控已停止。")
            return 0
    else:
        sources = image_inputs(input_path)
        if not sources:
            print("输入目录中没有支持的图片。", file=sys.stderr)
            return 2
        for current, source in enumerate(sources, start=1):
            print(f"处理图片 {current}/{len(sources)}：{source.name}")
            job_dir, smart_job = process_source(
                args, source, output_root, remover, smart_engine
            )
            print(f"完成：{job_dir}")
            if smart_job and smart_job["review_component_ids"]:
                print(
                    f"提示：{len(smart_job['review_component_ids'])} 个低置信组件可在 GUI 中核对。"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
