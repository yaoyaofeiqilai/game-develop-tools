from __future__ import annotations

import json
import math
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from scipy import ndimage

from sprite_workflow import BackgroundRemover, DEFAULT_MODEL_HOME, border_pixels, has_uniform_border


PROJECT_ROOT = Path(__file__).resolve().parent
JOBS_ROOT = PROJECT_ROOT / "workspace" / "jobs"
SMART_OUTPUT_ROOT = PROJECT_ROOT / "output"
EXPORT_ARTIFACTS = ("aligned", "trimmed", "masks", "sheet", "manifest")


def make_job_id(source_path: Path) -> str:
    """Create a URL- and filesystem-safe ID without depending on source Unicode."""
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", source_path.stem).strip("-_")
    safe_stem = (safe_stem[:48] or "asset").lower()
    return f"{int(time.time())}-{safe_stem}-{uuid.uuid4().hex[:6]}"

FRAME_COLORS = [
    (255, 95, 72),
    (35, 208, 164),
    (255, 191, 64),
    (97, 132, 255),
    (220, 97, 255),
    (55, 196, 232),
    (240, 110, 154),
    (150, 210, 74),
    (255, 139, 56),
    (94, 218, 207),
    (174, 126, 255),
    (228, 205, 82),
]


def job_output_target(
    job: dict[str, Any], output_root: Path = SMART_OUTPUT_ROOT
) -> Path:
    """Keep imported animation-set folders in the exported output tree."""
    source_path = Path(job["source_path"]).resolve()
    input_root = (PROJECT_ROOT / "input").resolve()
    try:
        relative = source_path.relative_to(input_root)
    except ValueError:
        return output_root.resolve() / Path(job["source_name"]).stem
    return output_root.resolve() / relative.parent / relative.stem


def collection_result_target(
    job: dict[str, Any], output_root: Path = SMART_OUTPUT_ROOT
) -> Path | None:
    """Return a flat, human-browsable preview path for animation-set exports."""
    source_path = Path(job["source_path"]).resolve()
    input_root = (PROJECT_ROOT / "input").resolve()
    try:
        relative = source_path.relative_to(input_root)
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None
    stem_parts = relative.with_suffix("").parts[1:]
    preview_name = "__".join(stem_parts) + ".png"
    return output_root.resolve() / relative.parts[0] / "result" / preview_name


def normalize_export_artifacts(artifacts: list[str] | tuple[str, ...] | None) -> list[str]:
    requested = list(artifacts or EXPORT_ARTIFACTS)
    invalid = sorted(set(requested) - set(EXPORT_ARTIFACTS))
    if invalid:
        raise ValueError(f"不支持的导出内容：{', '.join(invalid)}")
    return [name for name in EXPORT_ARTIFACTS if name in requested]


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve()))


def _deliver_export_artifacts(
    target: Path,
    destination: Path | None,
    artifacts: list[str],
) -> tuple[Path, dict[str, Any]]:
    sources: dict[str, Path] = {
        "aligned": target / "frames_aligned",
        "trimmed": target / "frames_trimmed",
        "masks": target / "masks",
        "sheet": target / "sprite_sheet.png",
        "manifest": target / "manifest.json",
    }
    if destination is None:
        if len(artifacts) == 1 and artifacts[0] in {"aligned", "trimmed", "masks"}:
            delivery_dir = sources[artifacts[0]]
        else:
            delivery_dir = target
        return delivery_dir, {name: str(sources[name]) for name in artifacts}

    delivery_dir = destination.resolve()
    delivery_dir.mkdir(parents=True, exist_ok=True)
    delivered: dict[str, Any] = {}
    flatten_directory = len(artifacts) == 1 and artifacts[0] in {"aligned", "trimmed", "masks"}
    for name in artifacts:
        source = sources[name]
        if source.is_dir():
            target_directory = delivery_dir if flatten_directory else delivery_dir / source.name
            target_directory.mkdir(parents=True, exist_ok=True)
            if not _same_path(source, target_directory):
                for item in source.iterdir():
                    if item.is_file():
                        shutil.copy2(item, target_directory / item.name)
            delivered[name] = str(target_directory)
        else:
            target_file = delivery_dir / source.name
            if not _same_path(source, target_file):
                shutil.copy2(source, target_file)
            delivered[name] = str(target_file)
    return delivery_dir, delivered


@dataclass
class SmartConfig:
    background_mode: str = "auto"
    background_model: str = "isnet-anime"
    provider: str = "auto"
    expected_frames: int = 0
    rows: int = 0
    columns: int = 0
    alpha_threshold: int = 24
    min_component_area: int = 20
    component_sensitivity: float = 0.50
    confidence_threshold: float = 0.42
    padding: int = 6
    output_columns: int = 0
    sam_refine: bool = False
    refine_mode: str = "conservative"
    refine_tolerance: float = 18.0
    matte_width: int = 3


def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert an uint8/float RGB image to CIE Lab for perceptual colour distance."""
    value = rgb.astype(np.float32) / 255.0
    value = np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)
    xyz = value @ np.array(
        [[0.4124564, 0.3575761, 0.1804375],
         [0.2126729, 0.7151522, 0.0721750],
         [0.0193339, 0.1191920, 0.9503041]],
        dtype=np.float32,
    ).T
    xyz /= np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    delta = 6 / 29
    transformed = np.where(
        xyz > delta ** 3,
        np.cbrt(xyz),
        xyz / (3 * delta ** 2) + 4 / 29,
    )
    return np.stack(
        [116 * transformed[..., 1] - 16,
         500 * (transformed[..., 0] - transformed[..., 1]),
         200 * (transformed[..., 1] - transformed[..., 2])],
        axis=-1,
    )


def _refine_cutout(
    source: Image.Image,
    initial_cutout: Image.Image,
    mode: str,
    tolerance: float,
    matte_width: int,
    semantic_alpha: np.ndarray | None = None,
) -> tuple[Image.Image, dict[str, Any]]:
    """Remove border-coloured pockets while protecting semantic foreground.

    The initial cutout remains the authority for non-background-coloured pixels.
    Only regions that resemble the sampled canvas background are reconsidered.
    """
    source_rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8)
    initial_rgba = np.asarray(initial_cutout.convert("RGBA"), dtype=np.uint8).copy()
    initial_alpha = initial_rgba[:, :, 3]
    if mode == "off":
        return Image.fromarray(initial_rgba, "RGBA"), {
            "mode": mode,
            "removed_pixels": 0,
            "enclosed_regions_removed": 0,
            "softened_pixels": 0,
            "semantic_assist": semantic_alpha is not None,
        }

    multipliers = {
        "conservative": 0.72,
        "balanced": 1.0,
        "strong": 1.38,
        "aggressive": 1.72,
    }
    effective_tolerance = float(np.clip(tolerance, 3.0, 60.0)) * multipliers.get(mode, 1.0)
    rgb = source_rgba[:, :, :3]
    lab = _srgb_to_lab(rgb)
    background_rgb = np.median(border_pixels(source), axis=0).astype(np.float32)
    background_lab = _srgb_to_lab(background_rgb.reshape(1, 1, 3))[0, 0]
    distance = np.linalg.norm(lab - background_lab, axis=2)
    candidate = distance <= effective_tolerance

    seed = np.zeros(candidate.shape, dtype=bool)
    seed[0, :] = candidate[0, :]
    seed[-1, :] = candidate[-1, :]
    seed[:, 0] = candidate[:, 0]
    seed[:, -1] = candidate[:, -1]
    exterior = ndimage.binary_propagation(seed, mask=candidate)
    enclosed = candidate & ~exterior
    enclosed_labels, enclosed_count = ndimage.label(enclosed, structure=np.ones((3, 3), dtype=np.uint8))

    if semantic_alpha is None:
        semantic_alpha = initial_alpha
        semantic_assist = False
    else:
        semantic_alpha = np.asarray(semantic_alpha, dtype=np.uint8)
        semantic_assist = True

    # Flat canvas-like regions receive a higher background score. Eroded cores
    # avoid judging antialiased outlines as if they were the region interior.
    smooth = ndimage.gaussian_filter(rgb.astype(np.float32), sigma=(1.1, 1.1, 0))
    local_detail = np.linalg.norm(rgb.astype(np.float32) - smooth, axis=2)
    remove_core = exterior.copy()
    accepted_regions = 0
    image_area = candidate.size
    minimum_region = max(3, round(image_area * 0.0000015))
    thresholds = {
        "conservative": 0.72,
        "balanced": 0.58,
        "strong": 0.43,
        "aggressive": 0.30,
    }
    semantic_limits = {
        "conservative": 0.28,
        "balanced": 0.12,
        "strong": 0.0,
        "aggressive": 0.0,
    }

    for region_id in range(1, enclosed_count + 1):
        region = enclosed_labels == region_id
        area = int(region.sum())
        if area < minimum_region:
            continue
        core = ndimage.binary_erosion(region, iterations=1)
        sample = core if core.any() else region
        colour_score = float(np.clip(1.0 - np.mean(distance[sample]) / max(effective_tolerance, 1.0), 0.0, 1.0))
        semantic_background = float(1.0 - np.mean(semantic_alpha[sample]) / 255.0)
        flatness = float(np.clip(1.0 - np.mean(local_detail[sample]) / 16.0, 0.0, 1.0))
        # A semantic model is the main protection for white clothing/eyes. In
        # strong mode users deliberately permit colour evidence to dominate.
        score = 0.48 * colour_score + 0.38 * semantic_background + 0.14 * flatness
        if semantic_background >= semantic_limits.get(mode, 0.12) and score >= thresholds.get(mode, 0.58):
            remove_core |= region
            accepted_regions += 1

    final_alpha = initial_alpha.astype(np.float32)
    final_alpha[remove_core] = 0.0
    matte_width = int(np.clip(matte_width, 0, 12))
    softened = np.zeros_like(remove_core)
    if matte_width > 0 and remove_core.any():
        distance_from_background = ndimage.distance_transform_edt(~remove_core)
        softened = (~remove_core) & (distance_from_background <= matte_width)
        ramp = np.clip(distance_from_background / (matte_width + 0.35), 0.0, 1.0) * 255.0
        final_alpha[softened] = np.minimum(final_alpha[softened], ramp[softened])

    final_alpha_u8 = np.clip(final_alpha, 0, 255).astype(np.uint8)
    result = source_rgba.copy()
    result[:, :, 3] = final_alpha_u8

    # Unmix the sampled background from partially transparent edge RGB. This
    # prevents pale halos after scaling or compositing onto a dark game scene.
    edge = (final_alpha_u8 > 8) & (final_alpha_u8 < 247)
    if edge.any():
        alpha_float = np.maximum(final_alpha_u8[edge, None].astype(np.float32) / 255.0, 0.08)
        foreground_rgb = (
            rgb[edge].astype(np.float32) - (1.0 - alpha_float) * background_rgb[None, :]
        ) / alpha_float
        result[edge, :3] = np.clip(foreground_rgb, 0, 255).astype(np.uint8)

    removed_pixels = int(np.count_nonzero((initial_alpha >= 8) & (final_alpha_u8 < 8)))
    return Image.fromarray(result, "RGBA"), {
        "mode": mode,
        "tolerance": round(effective_tolerance, 2),
        "matte_width": matte_width,
        "removed_pixels": removed_pixels,
        "enclosed_regions_removed": accepted_regions,
        "softened_pixels": int(softened.sum()),
        "semantic_assist": semantic_assist,
        "background_rgb": [int(round(value)) for value in background_rgb],
    }


def _point_mask_edit(
    source: Image.Image,
    cutout: Image.Image,
    x: int,
    y: int,
    action: Literal["background", "foreground"],
    tolerance: float,
    feather: int,
) -> tuple[Image.Image, dict[str, Any]]:
    source_rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8)
    result = np.asarray(cutout.convert("RGBA"), dtype=np.uint8).copy()
    height, width = result.shape[:2]
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("点击位置超出图片范围")
    lab = _srgb_to_lab(source_rgba[:, :, :3])
    seed_lab = lab[y, x]
    distance = np.linalg.norm(lab - seed_lab, axis=2)
    candidate = distance <= float(np.clip(tolerance, 2.0, 60.0))
    seed = np.zeros(candidate.shape, dtype=bool)
    seed[y, x] = True
    region = ndimage.binary_propagation(seed, mask=candidate)
    if not region.any():
        raise ValueError("没有找到可修改的连通区域")
    alpha = result[:, :, 3].astype(np.float32)
    feather = int(np.clip(feather, 0, 12))
    if action == "background":
        alpha[region] = 0
        if feather:
            outside_distance = ndimage.distance_transform_edt(~region)
            band = (~region) & (outside_distance <= feather)
            ramp = np.clip(outside_distance / (feather + 0.35), 0.0, 1.0) * 255.0
            alpha[band] = np.minimum(alpha[band], ramp[band])
    else:
        alpha[region] = 255
        result[region, :3] = source_rgba[region, :3]
        if feather:
            inside_distance = ndimage.distance_transform_edt(region)
            band = region & (inside_distance <= feather)
            ramp = np.clip(inside_distance / (feather + 0.35), 0.0, 1.0) * 255.0
            alpha[band] = np.maximum(result[:, :, 3][band], ramp[band])
    result[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    return Image.fromarray(result, "RGBA"), {
        "action": action,
        "x": x,
        "y": y,
        "pixels": int(region.sum()),
        "tolerance": tolerance,
        "feather": feather,
    }


def _polygon_region(
    size: tuple[int, int], points: list[tuple[int, int]]
) -> tuple[np.ndarray, list[tuple[int, int]], list[int]]:
    if len(points) < 3:
        raise ValueError("圈选区域至少需要 3 个点")
    width, height = size
    safe_points = [
        (int(np.clip(x, 0, width - 1)), int(np.clip(y, 0, height - 1)))
        for x, y in points[:1024]
    ]
    polygon_image = Image.new("L", (width, height), 0)
    ImageDraw.Draw(polygon_image).polygon(safe_points, fill=255)
    region = np.asarray(polygon_image, dtype=np.uint8) > 0
    if not region.any():
        raise ValueError("圈选区域为空")
    xs = [point[0] for point in safe_points]
    ys = [point[1] for point in safe_points]
    return region, safe_points, [min(xs), min(ys), max(xs) + 1, max(ys) + 1]


def _restore_initial_region(
    current: Image.Image,
    initial: Image.Image,
    points: list[tuple[int, int]],
    feather: int,
) -> tuple[Image.Image, dict[str, Any]]:
    """Blend the preserved pre-refinement cutout back inside a polygon."""
    if current.size != initial.size:
        raise ValueError("初抠图与当前精修图尺寸不一致")
    region, safe_points, bbox = _polygon_region(current.size, points)

    feather = int(np.clip(feather, 0, 24))
    if feather:
        inside_distance = ndimage.distance_transform_edt(region)
        weight = np.where(region, np.clip(inside_distance / feather, 0.0, 1.0), 0.0)
    else:
        weight = region.astype(np.float32)
    weight = weight[:, :, None].astype(np.float32)
    current_array = np.asarray(current.convert("RGBA"), dtype=np.uint8)
    initial_array = np.asarray(initial.convert("RGBA"), dtype=np.uint8)
    blended = np.clip(
        current_array.astype(np.float32) * (1.0 - weight)
        + initial_array.astype(np.float32) * weight,
        0,
        255,
    ).astype(np.uint8)
    restored = region & (
        initial_array[:, :, 3].astype(np.int16)
        > current_array[:, :, 3].astype(np.int16) + 2
    )
    return Image.fromarray(blended, "RGBA"), {
        "action": "restore_initial_region",
        "points": len(safe_points),
        "bbox": bbox,
        "region_pixels": int(region.sum()),
        "restored_pixels": int(restored.sum()),
        "feather": feather,
    }


def _apply_local_refinement(
    current: Image.Image,
    source: Image.Image,
    local_refined: Image.Image,
    crop_box: tuple[int, int, int, int],
    points: list[tuple[int, int]],
    strength: str,
    tolerance: float,
    feather: int,
    local_metrics: dict[str, Any],
    background_mode: str,
    background_model: str,
    semantic_alpha: np.ndarray | None = None,
) -> tuple[Image.Image, dict[str, Any]]:
    """Apply only locally proposed pixels that also have strong background evidence.

    A remover working on a small crop can mistake a partial character feature for
    the crop background. Its alpha is therefore a proposal, never the authority:
    the full source canvas colour, local flatness, and the full-image semantic mask
    must support the removal before anything is written back.
    """
    if current.size != source.size:
        raise ValueError("源图与当前精修图尺寸不一致")
    region, safe_points, bbox = _polygon_region(current.size, points)
    left, top, right, bottom = crop_box
    expected_size = (right - left, bottom - top)
    if local_refined.size != expected_size:
        raise ValueError("局部精修结果与圈选裁图尺寸不一致")

    current_array = np.asarray(current.convert("RGBA"), dtype=np.uint8).copy()
    target = current_array.copy()
    local_array = np.asarray(local_refined.convert("RGBA"), dtype=np.uint8).copy()
    crop_current = current_array[top:bottom, left:right]
    full_source = np.asarray(source.convert("RGBA"), dtype=np.uint8)
    crop_source = full_source[top:bottom, left:right]
    crop_region = region[top:bottom, left:right]

    background_rgb = np.median(border_pixels(source), axis=0).astype(np.float32)
    background_lab = _srgb_to_lab(background_rgb.reshape(1, 1, 3))[0, 0]
    source_rgb = crop_source[:, :, :3]
    full_colour_distance = np.linalg.norm(
        _srgb_to_lab(full_source[:, :, :3]) - background_lab, axis=2
    )
    colour_distance = full_colour_distance[top:bottom, left:right]
    smooth = ndimage.gaussian_filter(source_rgb.astype(np.float32), sigma=(1.05, 1.05, 0))
    local_detail = np.linalg.norm(source_rgb.astype(np.float32) - smooth, axis=2)

    evidence_settings = {
        "gentle": (0.72, 80, 8.0),
        "standard": (1.0, 140, 12.0),
        "strong": (1.30, 190, 18.0),
        "maximum": (1.60, 224, 26.0),
    }
    tolerance_multiplier, semantic_limit, detail_limit = evidence_settings.get(
        strength, evidence_settings["standard"]
    )
    evidence_tolerance = float(np.clip(tolerance, 3.0, 60.0)) * tolerance_multiplier
    colour_evidence = colour_distance <= evidence_tolerance
    full_canvas_candidate = full_colour_distance <= evidence_tolerance
    exterior_seed = np.zeros(full_canvas_candidate.shape, dtype=bool)
    exterior_seed[0, :] = full_canvas_candidate[0, :]
    exterior_seed[-1, :] = full_canvas_candidate[-1, :]
    exterior_seed[:, 0] = full_canvas_candidate[:, 0]
    exterior_seed[:, -1] = full_canvas_candidate[:, -1]
    exterior_evidence = ndimage.binary_propagation(
        exterior_seed, mask=full_canvas_candidate
    )[top:bottom, left:right]
    flat_evidence = local_detail <= detail_limit
    # Very close, flat canvas pixels are reliable even when the semantic model
    # assigned middling confidence to a narrow enclosed background pocket.
    canvas_core = (
        (colour_distance <= max(3.0, evidence_tolerance * 0.32))
        & (local_detail <= 5.0)
    )

    if semantic_alpha is None:
        semantic_evidence = np.ones(crop_region.shape, dtype=bool)
    else:
        semantic_array = np.asarray(semantic_alpha, dtype=np.uint8)
        if semantic_array.shape != region.shape:
            raise ValueError("语义蒙版与源图尺寸不一致")
        semantic_evidence = semantic_array[top:bottom, left:right] <= semantic_limit

    proposed = (
        local_array[:, :, 3].astype(np.int16) + 2
        < crop_current[:, :, 3].astype(np.int16)
    ) & crop_region
    direct_exterior_background = crop_region & exterior_evidence
    model_supported_background = (
        proposed
        & colour_evidence
        & (flat_evidence | canvas_core)
        & (semantic_evidence | canvas_core)
    )
    background_evidence = direct_exterior_background | model_supported_background
    protected_foreground = proposed & ~background_evidence

    crop_target = target[top:bottom, left:right]
    crop_target[background_evidence, :3] = local_array[background_evidence, :3]
    proposed_alpha = local_array[:, :, 3].copy()
    proposed_alpha[direct_exterior_background] = 0
    crop_target[background_evidence, 3] = np.minimum(
        crop_current[background_evidence, 3], proposed_alpha[background_evidence]
    )

    feather = int(np.clip(feather, 0, 24))
    if feather:
        inside_distance = ndimage.distance_transform_edt(region)
        weight = np.where(region, np.clip(inside_distance / feather, 0.0, 1.0), 0.0)
    else:
        weight = region.astype(np.float32)
    weight = weight[:, :, None].astype(np.float32)
    blended = np.clip(
        current_array.astype(np.float32) * (1.0 - weight)
        + target.astype(np.float32) * weight,
        0,
        255,
    ).astype(np.uint8)

    current_alpha = current_array[:, :, 3]
    final_alpha = blended[:, :, 3]
    refined = region & (
        final_alpha.astype(np.int16) + 2 < current_alpha.astype(np.int16)
    )
    removed = region & (current_alpha >= 8) & (final_alpha < 8)
    return Image.fromarray(blended, "RGBA"), {
        "action": "refine_local_region",
        "points": len(safe_points),
        "bbox": bbox,
        "crop_box": [left, top, right, bottom],
        "region_pixels": int(region.sum()),
        "proposed_pixels": int(proposed.sum()),
        "background_evidence_pixels": int(background_evidence.sum()),
        "exterior_background_pixels": int(direct_exterior_background.sum()),
        "protected_foreground_pixels": int(protected_foreground.sum()),
        "refined_pixels": int(refined.sum()),
        "removed_pixels": int(removed.sum()),
        "strength": strength,
        "evidence_tolerance": round(evidence_tolerance, 2),
        "semantic_protection_limit": semantic_limit,
        "feather": feather,
        "background_mode": background_mode,
        "background_model": background_model,
        "local_refinement": local_metrics,
    }


def _encode_ids(labels: np.ndarray) -> Image.Image:
    value = labels.astype(np.uint32)
    rgb = np.stack(
        [value & 255, (value >> 8) & 255, (value >> 16) & 255], axis=2
    ).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def decode_ids(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint32)
    return rgb[:, :, 0] + (rgb[:, :, 1] << 8) + (rgb[:, :, 2] << 16)


def _bbox_distance(
    box: tuple[int, int, int, int], anchor: tuple[int, int, int, int]
) -> float:
    left, top, right, bottom = box
    aleft, atop, aright, abottom = anchor
    dx = max(aleft - right, left - aright, 0)
    dy = max(atop - bottom, top - abottom, 0)
    return math.hypot(dx, dy)


def _component_records(
    alpha: np.ndarray, config: SmartConfig
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    foreground = alpha >= config.alpha_threshold
    structure = np.ones((3, 3), dtype=np.uint8)
    raw_labels, count = ndimage.label(foreground, structure=structure)
    objects = ndimage.find_objects(raw_labels)
    image_area = alpha.shape[0] * alpha.shape[1]
    minimum = max(config.min_component_area, round(image_area * 0.000008))

    labels = np.zeros_like(raw_labels, dtype=np.int32)
    records: list[dict[str, Any]] = []
    next_id = 1
    for raw_id in range(1, count + 1):
        section = objects[raw_id - 1]
        if section is None:
            continue
        local = raw_labels[section] == raw_id
        area = int(local.sum())
        if area < minimum:
            continue
        ys, xs = np.where(local)
        top, left = section[0].start, section[1].start
        x0, y0 = left + int(xs.min()), top + int(ys.min())
        x1, y1 = left + int(xs.max()) + 1, top + int(ys.max()) + 1
        global_mask = raw_labels == raw_id
        weights = alpha[global_mask].astype(np.float64) + 1.0
        global_y, global_x = np.where(global_mask)
        cx = float(np.average(global_x, weights=weights))
        cy = float(np.average(global_y, weights=weights))
        labels[global_mask] = next_id
        records.append(
            {
                "id": next_id,
                "area": area,
                "bbox": [x0, y0, x1, y1],
                "centroid": [round(cx, 2), round(cy, 2)],
                "width": x1 - x0,
                "height": y1 - y0,
            }
        )
        next_id += 1
    return labels, records


def _choose_anchor_components(
    components: list[dict[str, Any]],
    image_size: tuple[int, int],
    expected: int,
    sensitivity: float,
) -> tuple[list[dict[str, Any]], float, str]:
    if not components:
        return [], 0.0, "no_foreground"

    ordered = sorted(components, key=lambda item: item["area"], reverse=True)
    largest_area = float(ordered[0]["area"])
    largest_height = max(item["height"] for item in ordered[: min(20, len(ordered))])
    area_ratio = 0.045 - 0.03 * max(0.0, min(1.0, sensitivity))
    area_floor = max(20.0, largest_area * area_ratio)
    height_floor = largest_height * 0.18
    candidates = [
        item
        for item in ordered
        if item["area"] >= area_floor and item["height"] >= height_floor
    ]
    scale_items = candidates or ordered[: min(8, len(ordered))]
    widths = [item["width"] for item in scale_items]
    heights = [item["height"] for item in scale_items]
    radius_x = max(10.0, float(np.median(widths)) * 0.45)
    radius_y = max(10.0, float(np.median(heights)) * 0.75)

    selected: list[dict[str, Any]] = []
    for item in candidates:
        cx, cy = item["centroid"]
        collision = any(
            abs(cx - other["centroid"][0]) < radius_x
            and abs(cy - other["centroid"][1]) < radius_y
            for other in selected
        )
        if not collision:
            selected.append(item)

    if expected > 0:
        if len(selected) < expected:
            for item in ordered:
                if item in selected:
                    continue
                cx, cy = item["centroid"]
                collision = any(
                    abs(cx - other["centroid"][0]) < radius_x * 0.65
                    and abs(cy - other["centroid"][1]) < radius_y * 0.65
                    for other in selected
                )
                if not collision:
                    selected.append(item)
                if len(selected) >= expected:
                    break
        if len(selected) > expected:
            # Keep spatial coverage while favouring larger components.
            coverage: list[dict[str, Any]] = []
            for item in sorted(selected, key=lambda it: it["area"], reverse=True):
                if len(coverage) >= expected:
                    break
                coverage.append(item)
            selected = coverage
        confidence = 0.94 if len(selected) == expected else 0.52
        reason = "expected_frame_count"
    else:
        confidence = 0.82
        reason = "component_significance"
        if len(selected) < 2:
            selected = ordered[:1]
            confidence = 0.48

    return selected, confidence, reason


def _sort_anchors(
    anchors: list[dict[str, Any]], rows_hint: int
) -> tuple[list[dict[str, Any]], int, int]:
    if not anchors:
        return [], 0, 0
    if rows_hint > 0:
        row_count = min(rows_hint, len(anchors))
    else:
        by_y = sorted(anchors, key=lambda item: item["centroid"][1])
        heights = [item["height"] for item in anchors]
        gap_limit = max(20.0, float(np.median(heights)) * 0.9)
        row_count = 1
        for previous, current in zip(by_y, by_y[1:]):
            if current["centroid"][1] - previous["centroid"][1] > gap_limit:
                row_count += 1
        row_count = min(row_count, len(anchors))

    if row_count == 1:
        ordered = sorted(anchors, key=lambda item: item["centroid"][0])
        return ordered, 1, len(ordered)

    # Lightweight 1-D k-means on Y to form rows.
    ys = np.array([item["centroid"][1] for item in anchors], dtype=float)
    centres = np.linspace(float(ys.min()), float(ys.max()), row_count)
    assignment = np.zeros(len(anchors), dtype=int)
    for _ in range(20):
        new_assignment = np.argmin(abs(ys[:, None] - centres[None, :]), axis=1)
        if np.array_equal(new_assignment, assignment):
            break
        assignment = new_assignment
        for index in range(row_count):
            values = ys[assignment == index]
            if values.size:
                centres[index] = float(values.mean())

    row_order = np.argsort(centres)
    ordered: list[dict[str, Any]] = []
    max_columns = 0
    for raw_row in row_order:
        row_items = [
            item for index, item in enumerate(anchors) if assignment[index] == raw_row
        ]
        row_items.sort(key=lambda item: item["centroid"][0])
        max_columns = max(max_columns, len(row_items))
        ordered.extend(row_items)
    return ordered, row_count, max_columns


def _assign_components(
    components: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    confidence_threshold: float,
) -> tuple[dict[int, int], dict[int, float], list[int]]:
    if not anchors:
        return {}, {}, []
    anchor_ids = {item["id"]: index + 1 for index, item in enumerate(anchors)}
    assignments: dict[int, int] = {}
    confidences: dict[int, float] = {}
    review: list[int] = []

    anchor_width = max(1.0, float(np.median([item["width"] for item in anchors])))
    anchor_height = max(1.0, float(np.median([item["height"] for item in anchors])))
    anchor_area = max(1.0, float(np.median([item["area"] for item in anchors])))

    for component in components:
        component_id = component["id"]
        if component_id in anchor_ids:
            assignments[component_id] = anchor_ids[component_id]
            confidences[component_id] = 1.0
            continue

        cx, cy = component["centroid"]
        scores: list[tuple[float, int]] = []
        for frame_id, anchor in enumerate(anchors, start=1):
            ax, ay = anchor["centroid"]
            centre_cost = math.hypot(
                (cx - ax) / anchor_width,
                0.55 * (cy - ay) / anchor_height,
            )
            edge_cost = _bbox_distance(
                tuple(component["bbox"]), tuple(anchor["bbox"])
            ) / max(anchor_width, anchor_height)
            scores.append((0.72 * centre_cost + 0.28 * edge_cost, frame_id))
        scores.sort()
        best_score, best_frame = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else best_score + 1.0
        margin = (second_score - best_score) / max(second_score, 1e-6)
        size_signal = min(1.0, component["area"] / (anchor_area * 0.08))
        confidence = float(np.clip(0.72 * margin + 0.28 * size_signal, 0.0, 1.0))
        assignments[component_id] = best_frame
        confidences[component_id] = round(confidence, 4)
        if confidence < confidence_threshold:
            review.append(component_id)
    return assignments, confidences, review


def _frame_summaries(
    anchors: list[dict[str, Any]],
    components: list[dict[str, Any]],
    assignments: dict[int, int],
    confidences: dict[int, float],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    component_by_id = {item["id"]: item for item in components}
    for frame_id, anchor in enumerate(anchors, start=1):
        component_ids = [key for key, value in assignments.items() if value == frame_id]
        frame_confidences = [confidences.get(key, 0.0) for key in component_ids]
        x0 = min(component_by_id[key]["bbox"][0] for key in component_ids)
        y0 = min(component_by_id[key]["bbox"][1] for key in component_ids)
        x1 = max(component_by_id[key]["bbox"][2] for key in component_ids)
        y1 = max(component_by_id[key]["bbox"][3] for key in component_ids)
        summaries.append(
            {
                "id": frame_id,
                "anchor_component": anchor["id"],
                "component_ids": component_ids,
                "bbox": [x0, y0, x1, y1],
                "confidence": round(float(np.mean(frame_confidences)), 4),
                "color": "#%02x%02x%02x" % FRAME_COLORS[(frame_id - 1) % len(FRAME_COLORS)],
            }
        )
    return summaries


def _render_overlay(
    source: Image.Image,
    labels: np.ndarray,
    assignments: dict[int, int],
    frames: list[dict[str, Any]],
    review_ids: list[int],
) -> Image.Image:
    base = ImageEnhance.Brightness(source.convert("RGB")).enhance(0.72).convert("RGBA")
    tint = np.zeros((labels.shape[0], labels.shape[1], 4), dtype=np.uint8)
    for component_id, frame_id in assignments.items():
        color = FRAME_COLORS[(frame_id - 1) % len(FRAME_COLORS)]
        mask = labels == component_id
        tint[mask, :3] = color
        tint[mask, 3] = 92 if component_id not in review_ids else 145
        boundary = ndimage.binary_dilation(mask, iterations=1) & ~ndimage.binary_erosion(mask, iterations=1)
        tint[boundary, :3] = (255, 255, 255) if component_id not in review_ids else (255, 65, 65)
        tint[boundary, 3] = 235
    overlay = Image.alpha_composite(base, Image.fromarray(tint, "RGBA"))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    for frame in frames:
        x0, y0, x1, y1 = frame["bbox"]
        color = tuple(int(frame["color"][index : index + 2], 16) for index in (1, 3, 5))
        draw.rounded_rectangle((x0, y0, x1, y1), radius=8, outline=color + (255,), width=3)
        label = f"F{frame['id']}  {round(frame['confidence'] * 100)}%"
        text_box = draw.textbbox((0, 0), label, font=font)
        tw, th = text_box[2] - text_box[0], text_box[3] - text_box[1]
        label_y = max(0, y0 - th - 10)
        draw.rounded_rectangle((x0, label_y, x0 + tw + 12, label_y + th + 8), radius=4, fill=color + (240,))
        draw.text((x0 + 6, label_y + 4), label, font=font, fill=(12, 16, 19, 255))
    return overlay


class SmartEngine:
    def __init__(self) -> None:
        self._removers: dict[tuple[str, str, str], BackgroundRemover] = {}
        JOBS_ROOT.mkdir(parents=True, exist_ok=True)
        DEFAULT_MODEL_HOME.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("REMBG_HOME", str(DEFAULT_MODEL_HOME))

    def _remover(self, config: SmartConfig, mode: str | None = None) -> BackgroundRemover:
        selected_mode = mode or config.background_mode
        key = (selected_mode, config.background_model, config.provider)
        if key not in self._removers:
            self._removers[key] = BackgroundRemover(
                mode=selected_mode,
                model=config.background_model,
                provider=config.provider,
                decontaminate=True,
                alpha_matting=False,
                chroma_tolerance=18.0,
                chroma_feather=8.0,
            )
        return self._removers[key]

    @staticmethod
    def _derive_layout(
        source: Image.Image, cutout: Image.Image, config: SmartConfig
    ) -> dict[str, Any]:
        alpha = np.asarray(cutout.getchannel("A"), dtype=np.uint8)
        labels, components = _component_records(alpha, config)
        expected = config.expected_frames
        if config.rows > 0 and config.columns > 0:
            expected = config.rows * config.columns
        anchors, layout_confidence, inference_reason = _choose_anchor_components(
            components,
            source.size,
            expected,
            config.component_sensitivity,
        )
        anchors, inferred_rows, inferred_columns = _sort_anchors(anchors, config.rows)
        assignments, confidences, review_ids = _assign_components(
            components, anchors, config.confidence_threshold
        )
        frames = _frame_summaries(
            anchors, components, assignments, confidences
        ) if anchors else []
        return {
            "alpha": alpha,
            "labels": labels,
            "components": components,
            "assignments": assignments,
            "confidences": confidences,
            "review_ids": review_ids,
            "frames": frames,
            "layout_confidence": layout_confidence,
            "inference_reason": inference_reason,
            "rows": inferred_rows,
            "columns": inferred_columns,
        }

    def _save_analysis_state(
        self,
        job: dict[str, Any],
        source: Image.Image,
        cutout: Image.Image,
        config: SmartConfig,
        job_dir: Path,
    ) -> dict[str, Any]:
        state = self._derive_layout(source, cutout, config)
        cutout.save(job["files"]["cutout"])
        Image.fromarray(state["alpha"], "L").save(job["files"]["foreground_mask"])
        _encode_ids(state["labels"]).save(job["files"]["component_map"])
        _render_overlay(
            source,
            state["labels"],
            state["assignments"],
            state["frames"],
            state["review_ids"],
        ).save(job["files"]["overlay"])
        job["config"] = asdict(config)
        job["components"] = state["components"]
        job["assignments"] = {str(key): value for key, value in state["assignments"].items()}
        job["component_confidence"] = {str(key): value for key, value in state["confidences"].items()}
        job["review_component_ids"] = state["review_ids"]
        job["frames"] = state["frames"]
        job["layout"] = {
            "frame_count": len(state["frames"]),
            "rows": state["rows"],
            "columns": state["columns"],
        }
        job["engine"]["inference_reason"] = state["inference_reason"]
        job["engine"]["layout_confidence"] = round(state["layout_confidence"], 4)
        job["status"] = "needs_review" if state["review_ids"] else "ready"
        self._write_job(job_dir, job)
        return job

    def _ensure_initial_cutout(
        self,
        job: dict[str, Any],
        source: Image.Image,
        config: SmartConfig,
        job_dir: Path,
    ) -> Image.Image:
        initial_path_value = job.get("files", {}).get("initial_cutout")
        if initial_path_value and Path(initial_path_value).exists():
            return Image.open(initial_path_value).convert("RGBA")

        # Jobs created before mask refinement did not preserve this file. Re-run
        # only their configured background remover once, then persist the result
        # so all later regional edits are deterministic.
        initial_cutout = self._remover(config).remove(source)
        initial_path = job_dir / "cutout_initial.png"
        semantic_path = job_dir / "semantic_mask.png"
        initial_cutout.save(initial_path)
        initial_cutout.getchannel("A").save(semantic_path)
        job.setdefault("files", {})["initial_cutout"] = str(initial_path)
        job["files"]["semantic_mask"] = str(semantic_path)
        return initial_cutout

    def analyze(
        self,
        source_path: Path,
        config: SmartConfig,
        jobs_root: Path = JOBS_ROOT,
    ) -> dict[str, Any]:
        source_path = source_path.resolve()
        source = Image.open(source_path).convert("RGBA")
        started = time.perf_counter()
        remover = self._remover(config)
        initial_cutout = remover.remove(source)
        semantic_alpha = None
        semantic_remover = None
        if (
            config.refine_mode != "off"
            and config.background_mode in {"auto", "chroma"}
            and has_uniform_border(source)
        ):
            semantic_remover = self._remover(config, "rembg")
            semantic_cutout = semantic_remover.remove(source)
            semantic_alpha = np.asarray(semantic_cutout.getchannel("A"), dtype=np.uint8)
        cutout, refinement = _refine_cutout(
            source,
            initial_cutout,
            config.refine_mode,
            config.refine_tolerance,
            config.matte_width,
            semantic_alpha,
        )
        analysis = self._derive_layout(source, cutout, config)

        job_id = make_job_id(source_path)
        job_dir = jobs_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        initial_cutout_path = job_dir / "cutout_initial.png"
        cutout_path = job_dir / "cutout.png"
        semantic_mask_path = job_dir / "semantic_mask.png"
        label_map_path = job_dir / "component_map.png"
        overlay_path = job_dir / "overlay.png"
        mask_path = job_dir / "foreground_mask.png"
        initial_cutout.save(initial_cutout_path)
        cutout.save(cutout_path)
        if semantic_alpha is None:
            semantic_alpha = np.asarray(initial_cutout.getchannel("A"), dtype=np.uint8)
        Image.fromarray(semantic_alpha, "L").save(semantic_mask_path)
        _encode_ids(analysis["labels"]).save(label_map_path)
        Image.fromarray(analysis["alpha"], "L").save(mask_path)
        _render_overlay(
            source,
            analysis["labels"],
            analysis["assignments"],
            analysis["frames"],
            analysis["review_ids"],
        ).save(overlay_path)

        job = {
            "id": job_id,
            "source_path": str(source_path),
            "source_name": source_path.name,
            "source_size": list(source.size),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "config": asdict(config),
            "engine": {
                "name": "local-hybrid-instance-v1",
                "background_mode": config.background_mode,
                "providers": (semantic_remover or remover).actual_providers,
                "inference_reason": analysis["inference_reason"],
                "layout_confidence": round(analysis["layout_confidence"], 4),
            },
            "layout": {
                "frame_count": len(analysis["frames"]),
                "rows": analysis["rows"],
                "columns": analysis["columns"],
            },
            "components": analysis["components"],
            "assignments": {str(key): value for key, value in analysis["assignments"].items()},
            "component_confidence": {str(key): value for key, value in analysis["confidences"].items()},
            "review_component_ids": analysis["review_ids"],
            "frames": analysis["frames"],
            "refinement": refinement,
            "manual_edits": [],
            "files": {
                "initial_cutout": str(initial_cutout_path),
                "cutout": str(cutout_path),
                "semantic_mask": str(semantic_mask_path),
                "component_map": str(label_map_path),
                "foreground_mask": str(mask_path),
                "overlay": str(overlay_path),
            },
            "status": "needs_review" if analysis["review_ids"] else "ready",
        }
        self._write_job(job_dir, job)
        return job

    def refine_job(
        self,
        job_id: str,
        mode: str,
        tolerance: float,
        matte_width: int,
        jobs_root: Path = JOBS_ROOT,
    ) -> dict[str, Any]:
        job_dir = jobs_root / job_id
        job = self.load_job(job_id, jobs_root)
        config = SmartConfig(**job.get("config", {}))
        config.refine_mode = mode
        config.refine_tolerance = tolerance
        config.matte_width = matte_width
        source = Image.open(job["source_path"]).convert("RGBA")
        initial_cutout = self._ensure_initial_cutout(job, source, config, job_dir)
        semantic_path = job.get("files", {}).get("semantic_mask")
        semantic_alpha = None
        if semantic_path and Path(semantic_path).exists():
            semantic_alpha = np.asarray(Image.open(semantic_path).convert("L"), dtype=np.uint8)
        cutout, refinement = _refine_cutout(
            source, initial_cutout, mode, tolerance, matte_width, semantic_alpha
        )
        job["refinement"] = refinement
        job["manual_edits"] = []
        return self._save_analysis_state(job, source, cutout, config, job_dir)

    def edit_mask_point(
        self,
        job_id: str,
        x: int,
        y: int,
        action: Literal["background", "foreground"],
        tolerance: float,
        feather: int,
        jobs_root: Path = JOBS_ROOT,
    ) -> dict[str, Any]:
        job_dir = jobs_root / job_id
        job = self.load_job(job_id, jobs_root)
        config = SmartConfig(**job.get("config", {}))
        source = Image.open(job["source_path"]).convert("RGBA")
        current = Image.open(job["files"]["cutout"]).convert("RGBA")
        edited, record = _point_mask_edit(
            source, current, x, y, action, tolerance, feather
        )
        job.setdefault("manual_edits", []).append(record)
        job.setdefault("refinement", {})["manual_edit_count"] = len(job["manual_edits"])
        return self._save_analysis_state(job, source, edited, config, job_dir)

    def restore_initial_region(
        self,
        job_id: str,
        points: list[tuple[int, int]],
        feather: int,
        jobs_root: Path = JOBS_ROOT,
    ) -> dict[str, Any]:
        job_dir = jobs_root / job_id
        job = self.load_job(job_id, jobs_root)
        config = SmartConfig(**job.get("config", {}))
        source = Image.open(job["source_path"]).convert("RGBA")
        initial = self._ensure_initial_cutout(job, source, config, job_dir)
        current = Image.open(job["files"]["cutout"]).convert("RGBA")
        restored, record = _restore_initial_region(current, initial, points, feather)
        job.setdefault("manual_edits", []).append(record)
        refinement = job.setdefault("refinement", {})
        refinement["manual_edit_count"] = len(job["manual_edits"])
        refinement["last_region_restored_pixels"] = record["restored_pixels"]
        return self._save_analysis_state(job, source, restored, config, job_dir)

    def refine_local_region(
        self,
        job_id: str,
        points: list[tuple[int, int]],
        strength: Literal["gentle", "standard", "strong", "maximum"],
        tolerance: float,
        matte_width: int,
        feather: int,
        jobs_root: Path = JOBS_ROOT,
    ) -> dict[str, Any]:
        """Re-run the configured background remover on a selected source crop."""
        if strength not in {"gentle", "standard", "strong", "maximum"}:
            raise ValueError("不支持的局部精修强度")
        job_dir = jobs_root / job_id
        job = self.load_job(job_id, jobs_root)
        config = SmartConfig(**job.get("config", {}))
        source = Image.open(job["source_path"]).convert("RGBA")
        current = Image.open(job["files"]["cutout"]).convert("RGBA")
        _, _, bbox = _polygon_region(source.size, points)

        # Give the original remover context around the lasso. The write-back is
        # still clipped to the exact polygon, so neighbouring frames are safe.
        left, top, right, bottom = bbox
        span = max(right - left, bottom - top)
        padding = int(np.clip(round(span * 0.22), 24, 160))
        crop_box = (
            max(0, left - padding),
            max(0, top - padding),
            min(source.width, right + padding),
            min(source.height, bottom + padding),
        )
        local_source = source.crop(crop_box)
        local_initial = self._remover(config).remove(local_source)

        semantic_alpha = None
        semantic_full = None
        semantic_path = job.get("files", {}).get("semantic_mask")
        if semantic_path and Path(semantic_path).exists():
            semantic_image = Image.open(semantic_path).convert("L")
            semantic_full = np.asarray(semantic_image, dtype=np.uint8)
            if semantic_full.shape != (source.height, source.width):
                raise ValueError("语义蒙版与源图尺寸不一致，请重新运行智能分析")
            crop_left, crop_top, crop_right, crop_bottom = crop_box
            semantic_alpha = semantic_full[
                crop_top:crop_bottom,
                crop_left:crop_right,
            ]

        mode = {
            "gentle": "conservative",
            "standard": "balanced",
            "strong": "strong",
            "maximum": "aggressive",
        }[strength]
        local_refined, local_metrics = _refine_cutout(
            local_source,
            local_initial,
            mode,
            tolerance,
            matte_width,
            semantic_alpha,
        )
        edited, record = _apply_local_refinement(
            current,
            source,
            local_refined,
            crop_box,
            points,
            strength,
            tolerance,
            feather,
            local_metrics,
            config.background_mode,
            config.background_model,
            semantic_full,
        )
        job.setdefault("manual_edits", []).append(record)
        refinement = job.setdefault("refinement", {})
        refinement["manual_edit_count"] = len(job["manual_edits"])
        refinement["last_region_refined_pixels"] = record["refined_pixels"]
        refinement["last_region_removed_pixels"] = record["removed_pixels"]
        refinement["last_region_protected_pixels"] = record["protected_foreground_pixels"]
        refinement["last_local_refine_strength"] = strength
        return self._save_analysis_state(job, source, edited, config, job_dir)

    def _write_job(self, job_dir: Path, job: dict[str, Any]) -> None:
        path = job_dir / "job.json"
        temporary = job_dir / "job.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def load_job(self, job_id: str, jobs_root: Path = JOBS_ROOT) -> dict[str, Any]:
        path = jobs_root / job_id / "job.json"
        if not path.exists():
            raise FileNotFoundError(f"任务不存在：{job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_jobs(self, jobs_root: Path = JOBS_ROOT) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not jobs_root.exists():
            return results
        for path in sorted(jobs_root.glob("*/job.json"), reverse=True):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                modified_at = path.stat().st_mtime
                results.append(
                    {
                        "id": job["id"],
                        "source_name": job["source_name"],
                        "created_at": job["created_at"],
                        "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(modified_at)),
                        "modified_at_epoch": modified_at,
                        "status": job["status"],
                        "frame_count": job["layout"]["frame_count"],
                        "source_path": job.get("source_path", ""),
                        "review_count": len(job.get("review_component_ids", [])),
                        "tags": job.get("tags", []),
                        "last_export": (job.get("exports") or [None])[-1],
                    }
                )
            except (OSError, KeyError, json.JSONDecodeError):
                continue
        return results

    def assign_component(
        self, job_id: str, component_id: int, frame_id: int, jobs_root: Path = JOBS_ROOT
    ) -> dict[str, Any]:
        job_dir = jobs_root / job_id
        job = self.load_job(job_id, jobs_root)
        if frame_id < 0 or frame_id > job["layout"]["frame_count"]:
            raise ValueError("frame_id 超出范围")
        component_ids = {item["id"] for item in job["components"]}
        if component_id not in component_ids:
            raise ValueError("component_id 不存在")
        anchor_frame = next(
            (
                frame["id"]
                for frame in job["frames"]
                if frame["anchor_component"] == component_id
            ),
            None,
        )
        if anchor_frame is not None and frame_id != anchor_frame:
            raise ValueError(
                f"组件 #{component_id} 是 F{anchor_frame} 的主体锚点，不能移动或忽略"
            )
        job["assignments"][str(component_id)] = frame_id
        job["component_confidence"][str(component_id)] = 1.0
        job["review_component_ids"] = [
            value for value in job["review_component_ids"] if value != component_id
        ]
        assignments = {int(key): value for key, value in job["assignments"].items() if value > 0}
        confidences = {int(key): value for key, value in job["component_confidence"].items()}
        anchors = []
        component_by_id = {item["id"]: item for item in job["components"]}
        for frame in job["frames"]:
            anchors.append(component_by_id[frame["anchor_component"]])
        job["frames"] = _frame_summaries(
            anchors, job["components"], assignments, confidences
        )
        source = Image.open(job["source_path"]).convert("RGBA")
        labels = decode_ids(Image.open(job["files"]["component_map"]))
        _render_overlay(
            source,
            labels,
            assignments,
            job["frames"],
            job["review_component_ids"],
        ).save(job["files"]["overlay"])
        job["status"] = "needs_review" if job["review_component_ids"] else "ready"
        self._write_job(job_dir, job)
        return job

    def export_job(
        self,
        job_id: str,
        output_root: Path = SMART_OUTPUT_ROOT,
        jobs_root: Path = JOBS_ROOT,
        artifacts: list[str] | tuple[str, ...] | None = None,
        destination: Path | None = None,
        destination_subdir: Path | None = None,
        ai_tag: bool = False,
    ) -> dict[str, Any]:
        job = self.load_job(job_id, jobs_root)
        requested_artifacts = normalize_export_artifacts(artifacts)
        if not requested_artifacts:
            raise ValueError("至少选择一种导出内容")
        source_rgba = Image.open(job["files"]["cutout"]).convert("RGBA")
        alpha = np.asarray(source_rgba.getchannel("A"), dtype=np.uint8)
        labels = decode_ids(Image.open(job["files"]["component_map"]))
        assignments = {int(key): int(value) for key, value in job["assignments"].items()}
        frame_count = job["layout"]["frame_count"]
        if frame_count < 1:
            raise ValueError("没有可导出的帧")

        target = job_output_target(job, output_root)
        trimmed_dir = target / "frames_trimmed"
        aligned_dir = target / "frames_aligned"
        masks_dir = target / "masks"
        for directory in (trimmed_dir, aligned_dir, masks_dir):
            directory.mkdir(parents=True, exist_ok=True)

        source_array = np.asarray(source_rgba, dtype=np.uint8).copy()
        frame_data: list[dict[str, Any]] = []
        trimmed_images: list[Image.Image] = []
        frame_masks: list[Image.Image] = []
        padding = max(0, int(job["config"].get("padding", 6)))
        for frame_id in range(1, frame_count + 1):
            component_ids = [key for key, value in assignments.items() if value == frame_id]
            frame_mask = np.isin(labels, component_ids)
            effective_alpha = np.where(frame_mask, alpha, 0).astype(np.uint8)
            ys, xs = np.where(effective_alpha > 0)
            if xs.size:
                bbox = (
                    max(0, int(xs.min()) - padding),
                    max(0, int(ys.min()) - padding),
                    min(source_rgba.width, int(xs.max()) + 1 + padding),
                    min(source_rgba.height, int(ys.max()) + 1 + padding),
                )
                frame_array = source_array.copy()
                frame_array[:, :, 3] = effective_alpha
                trimmed = Image.fromarray(frame_array, "RGBA").crop(bbox)
            else:
                bbox = (0, 0, 1, 1)
                trimmed = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            mask_image = Image.fromarray(effective_alpha, "L")
            filename = f"frame_{frame_id:03d}.png"
            trimmed.save(trimmed_dir / filename)
            mask_image.save(masks_dir / filename)
            trimmed_images.append(trimmed)
            frame_masks.append(mask_image)
            frame_data.append(
                {
                    "id": frame_id,
                    "file": filename,
                    "bbox": list(bbox),
                    "trimmed_size": list(trimmed.size),
                    "component_ids": component_ids,
                }
            )

        common_width = max(image.width for image in trimmed_images)
        common_height = max(image.height for image in trimmed_images)
        aligned_images: list[Image.Image] = []
        for index, trimmed in enumerate(trimmed_images, start=1):
            canvas = Image.new("RGBA", (common_width, common_height), (0, 0, 0, 0))
            x = (common_width - trimmed.width) // 2
            y = common_height - trimmed.height
            canvas.alpha_composite(trimmed, (x, y))
            canvas.save(aligned_dir / f"frame_{index:03d}.png")
            frame_data[index - 1]["aligned_offset"] = [x, y]
            frame_data[index - 1]["pivot_bottom_center_px"] = [common_width / 2, common_height]
            aligned_images.append(canvas)

        columns = int(job["config"].get("output_columns", 0))
        if columns <= 0:
            columns = frame_count if frame_count <= 12 else 8
        rows = math.ceil(frame_count / columns)
        sheet = Image.new(
            "RGBA", (common_width * columns, common_height * rows), (0, 0, 0, 0)
        )
        for index, frame in enumerate(aligned_images):
            x = (index % columns) * common_width
            y = (index // columns) * common_height
            sheet.alpha_composite(frame, (x, y))
        sheet_path = target / "sprite_sheet.png"
        sheet.save(sheet_path)
        result_preview = collection_result_target(job, output_root)
        if result_preview is not None:
            result_preview.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(result_preview)

        manifest = {
            "source": job["source_path"],
            "job_id": job_id,
            "engine": job["engine"],
            "status_at_export": job["status"],
            "frame_count": frame_count,
            "canvas_size": [common_width, common_height],
            "sheet": {"columns": columns, "rows": rows, "file": "sprite_sheet.png"},
            "collection_preview": str(result_preview) if result_preview is not None else None,
            "frames": frame_data,
            "unresolved_components": job["review_component_ids"],
        }
        manifest_path = target / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        requested_destination = destination.resolve() if destination is not None else None
        if requested_destination is not None and destination_subdir is not None:
            requested_destination = requested_destination / destination_subdir
        delivery_dir, delivered = _deliver_export_artifacts(
            target,
            requested_destination,
            requested_artifacts,
        )
        exported_at = time.strftime("%Y-%m-%d %H:%M:%S")
        export_record = {
            "exported_at": exported_at,
            "delivery_dir": str(delivery_dir),
            "artifacts": requested_artifacts,
            "ai_tag": bool(ai_tag),
        }
        job.setdefault("exports", []).append(export_record)
        job["exports"] = job["exports"][-50:]
        if ai_tag:
            job["tags"] = sorted(set(job.get("tags", [])) | {"ai"})
        self._write_job(jobs_root / job_id, job)
        return {
            "output_dir": str(target),
            "delivery_dir": str(delivery_dir),
            "artifacts": requested_artifacts,
            "delivered": delivered,
            "sprite_sheet": str(sheet_path),
            "manifest": str(manifest_path),
            "result_preview": str(result_preview) if result_preview is not None else None,
            "frame_count": frame_count,
            "status": job["status"],
            "tags": job.get("tags", []),
        }

    def tag_job_as_ai(
        self,
        job_id: str,
        action: str = "analyze",
        jobs_root: Path = JOBS_ROOT,
    ) -> dict[str, Any]:
        job = self.load_job(job_id, jobs_root)
        job["tags"] = sorted(set(job.get("tags", [])) | {"ai"})
        job.setdefault("ai_activity", []).append(
            {"action": action, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        )
        job["ai_activity"] = job["ai_activity"][-50:]
        self._write_job(jobs_root / job_id, job)
        return job
