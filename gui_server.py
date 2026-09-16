from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from smart_engine import (
    JOBS_ROOT,
    PROJECT_ROOT,
    SMART_OUTPUT_ROOT,
    SmartConfig,
    SmartEngine,
    job_output_target,
)


WEB_ROOT = PROJECT_ROOT / "web"
INPUT_ROOT = PROJECT_ROOT / "input"
TEST_ROOT = PROJECT_ROOT / "test"
CONFIG_PATH = PROJECT_ROOT / "workspace" / "gui-settings.json"
EXPORT_PROFILES_PATH = PROJECT_ROOT / "workspace" / "export-profiles.json"
TRASH_ROOT = PROJECT_ROOT / "workspace" / "trash"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

app = FastAPI(
    title="Sprite Intelligence Workbench",
    version="1.0.0",
    description="Local-first API for AI-generated sprite extraction and review.",
)
engine = SmartEngine()
_export_profiles_lock = threading.Lock()


class AnalyzeRequest(BaseModel):
    source_path: str
    background_mode: str = "auto"
    background_model: str = "isnet-anime"
    provider: str = "auto"
    expected_frames: int = Field(0, ge=0, le=256)
    rows: int = Field(0, ge=0, le=64)
    columns: int = Field(0, ge=0, le=64)
    alpha_threshold: int = Field(24, ge=0, le=255)
    min_component_area: int = Field(20, ge=0, le=1_000_000)
    component_sensitivity: float = Field(0.50, ge=0.05, le=1.0)
    confidence_threshold: float = Field(0.42, ge=0.0, le=1.0)
    padding: int = Field(6, ge=0, le=256)
    output_columns: int = Field(0, ge=0, le=64)
    refine_mode: Literal["off", "conservative", "balanced", "strong"] = "conservative"
    refine_tolerance: float = Field(18.0, ge=3.0, le=60.0)
    matte_width: int = Field(3, ge=0, le=12)
    smart_chroma_enabled: bool = True
    smart_chroma_strength: Literal["conservative", "standard", "strong"] = "standard"


class AssignmentRequest(BaseModel):
    component_id: int = Field(ge=1)
    frame_id: int = Field(ge=0)


class RefineRequest(BaseModel):
    mode: Literal["off", "conservative", "balanced", "strong"] = "balanced"
    tolerance: float = Field(18.0, ge=3.0, le=60.0)
    matte_width: int = Field(3, ge=0, le=12)
    smart_chroma_enabled: bool | None = None
    smart_chroma_strength: Literal["conservative", "standard", "strong"] | None = None


class MaskPointRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    action: Literal["background", "foreground"]
    tolerance: float = Field(18.0, ge=2.0, le=60.0)
    feather: int = Field(2, ge=0, le=12)


class MaskRegionRequest(BaseModel):
    points: list[tuple[int, int]] = Field(min_length=3, max_length=1024)
    feather: int = Field(4, ge=0, le=24)


class LocalRefineRegionRequest(BaseModel):
    points: list[tuple[int, int]] = Field(min_length=3, max_length=1024)
    strength: Literal["gentle", "standard", "strong", "maximum"] = "standard"
    tolerance: float = Field(18.0, ge=3.0, le=60.0)
    matte_width: int = Field(3, ge=0, le=12)
    feather: int = Field(4, ge=0, le=24)
    smart_chroma_enabled: bool | None = None
    smart_chroma_strength: Literal["conservative", "standard", "strong"] | None = None


class SettingsRequest(BaseModel):
    ui_theme: Literal["industrial", "dopamine", "literary"] = "literary"
    http_proxy: str = ""
    https_proxy: str = ""
    vision_base_url: str = ""
    vision_model: str = ""
    vision_api_key: str = ""


ExportArtifact = Literal["aligned", "trimmed", "masks", "sheet", "manifest"]


class ExportRequest(BaseModel):
    output_path: str | None = None
    artifacts: list[ExportArtifact] | None = Field(default=None, min_length=1, max_length=5)
    ai_tag: bool = False


class ProcessRequest(AnalyzeRequest):
    export: bool = True
    output_path: str | None = None
    artifacts: list[ExportArtifact] | None = Field(default=None, min_length=1, max_length=5)
    ai_tag: bool = False


class LibraryTrashRequest(BaseModel):
    source_paths: list[str] = Field(default_factory=list, max_length=500)
    job_ids: list[str] = Field(default_factory=list, max_length=500)
    include_related_jobs: bool = True


class CollectionExportRequest(BaseModel):
    collection: str = Field(min_length=1, max_length=120)
    include_review: bool = False
    output_path: str | None = None
    artifacts: list[ExportArtifact] | None = Field(default=None, min_length=1, max_length=5)
    ai_tag: bool = False


class CollectionOpenRequest(BaseModel):
    collection: str = Field(min_length=1, max_length=120)
    view: Literal["root", "result"] = "result"
    output_path: str = ""


class OutputOpenRequest(BaseModel):
    output_path: str = ""


class FolderDialogRequest(BaseModel):
    initial_path: str = ""


class FolderImportRequest(BaseModel):
    folder_path: str = Field(min_length=1)
    collection: str = Field("", max_length=120)
    overwrite: bool = False


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _output_destination(raw_path: str) -> Path | None:
    value = raw_path.strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, "导出路径必须是绝对路径")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise HTTPException(400, "不能直接导出到磁盘根目录，请选择一个具体文件夹")
    return resolved


def _export_profile_key(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve())).casefold()


def _clean_profile_artifacts(value: Any) -> list[str]:
    allowed = ("aligned", "trimmed", "masks", "sheet", "manifest")
    requested = value if isinstance(value, (list, tuple)) else []
    cleaned = [name for name in allowed if name in requested]
    return cleaned or ["aligned"]


def _read_export_profiles_unlocked() -> dict[str, Any]:
    empty = {"version": 1, "sources": {}, "collections": {}}
    if not EXPORT_PROFILES_PATH.exists():
        return empty
    try:
        stored = json.loads(EXPORT_PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(stored, dict):
        return empty
    return {
        "version": 1,
        "sources": stored.get("sources") if isinstance(stored.get("sources"), dict) else {},
        "collections": stored.get("collections") if isinstance(stored.get("collections"), dict) else {},
    }


def _write_export_profiles_unlocked(profiles: dict[str, Any]) -> None:
    EXPORT_PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = EXPORT_PROFILES_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(EXPORT_PROFILES_PATH)


def _stored_source_export_profile(source_path: str | Path) -> dict[str, Any] | None:
    with _export_profiles_lock:
        record = _read_export_profiles_unlocked()["sources"].get(_export_profile_key(source_path))
    if not isinstance(record, dict):
        return None
    return {
        **record,
        "output_path": str(record.get("output_path") or ""),
        "artifacts": _clean_profile_artifacts(record.get("artifacts")),
        "remembered": True,
    }


def _legacy_source_export_profile(source_path: str | Path) -> dict[str, Any] | None:
    """Read an export made before persistent per-source profiles were introduced."""
    source_key = _export_profile_key(source_path)
    candidates = sorted(
        engine.list_jobs(),
        key=lambda item: float(item.get("modified_at_epoch") or 0),
        reverse=True,
    )
    for summary in candidates:
        if not summary.get("source_path") or _export_profile_key(summary["source_path"]) != source_key:
            continue
        previous = summary.get("last_export")
        if not isinstance(previous, dict) or not previous.get("delivery_dir"):
            continue
        canonical = job_output_target(summary, SMART_OUTPUT_ROOT).resolve()
        delivered = Path(previous["delivery_dir"]).resolve()
        internal_targets = {
            _export_profile_key(canonical),
            _export_profile_key(canonical / "frames_aligned"),
            _export_profile_key(canonical / "frames_trimmed"),
            _export_profile_key(canonical / "masks"),
        }
        return {
            "source_path": str(Path(source_path).resolve()),
            "output_path": "" if _export_profile_key(delivered) in internal_targets else str(delivered),
            "artifacts": _clean_profile_artifacts(previous.get("artifacts")),
            "updated_at": previous.get("exported_at", ""),
            "remembered": True,
            "legacy": True,
        }
    return None


def _source_export_profile(source_path: str | Path) -> dict[str, Any] | None:
    return _stored_source_export_profile(source_path) or _legacy_source_export_profile(source_path)


def _collection_export_profile(collection: str) -> dict[str, Any] | None:
    with _export_profiles_lock:
        record = _read_export_profiles_unlocked()["collections"].get(collection.strip().casefold())
    if not isinstance(record, dict):
        return None
    return {
        **record,
        "output_path": str(record.get("output_path") or ""),
        "artifacts": _clean_profile_artifacts(record.get("artifacts")),
        "remembered": True,
    }


def _remember_export_profile(
    bucket: Literal["sources", "collections"],
    key: str,
    identity: dict[str, str],
    destination: Path | None,
    artifacts: list[str],
) -> dict[str, Any]:
    now = time.time()
    record: dict[str, Any] = {
        **identity,
        "output_path": str(destination.resolve()) if destination is not None else "",
        "artifacts": _clean_profile_artifacts(artifacts),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "updated_at_epoch": now,
    }
    with _export_profiles_lock:
        profiles = _read_export_profiles_unlocked()
        profiles[bucket][key] = record
        _write_export_profiles_unlocked(profiles)
    return {**record, "remembered": True}


def _remember_source_export_profile(
    source_path: str | Path, destination: Path | None, artifacts: list[str]
) -> dict[str, Any]:
    resolved = Path(source_path).resolve()
    return _remember_export_profile(
        "sources",
        _export_profile_key(resolved),
        {"source_path": str(resolved)},
        destination,
        artifacts,
    )


def _remember_collection_export_profile(
    collection: str, destination: Path | None, artifacts: list[str]
) -> dict[str, Any]:
    name = collection.strip()
    return _remember_export_profile(
        "collections",
        name.casefold(),
        {"collection": name},
        destination,
        artifacts,
    )


def _resolve_export_options(
    output_path: str | None,
    artifacts: list[str] | None,
    profile: dict[str, Any] | None,
) -> tuple[Path | None, list[str]]:
    remembered_path = str(profile.get("output_path") or "") if profile else ""
    raw_path = remembered_path if output_path is None else output_path
    requested_artifacts = artifacts if artifacts is not None else (profile or {}).get("artifacts")
    return _output_destination(raw_path), _clean_profile_artifacts(requested_artifacts)


def _select_output_directory(initial_path: str) -> str:
    if os.name != "nt":
        raise OSError("当前系统不支持 Windows 文件夹选择器")
    initial = _output_destination(initial_path) if initial_path.strip() else SMART_OUTPUT_ROOT
    escaped = str(initial).replace("'", "''")
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.WindowState = 'Minimized'
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择精灵素材导出文件夹'
$dialog.ShowNewFolderButton = $true
$dialog.SelectedPath = '{escaped}'
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  Write-Output $dialog.SelectedPath
}}
$owner.Dispose()
"""
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise OSError(completed.stderr.strip() or "文件夹选择器启动失败")
    return completed.stdout.strip()


def _explorer_windows() -> dict[int, str]:
    """Return visible Windows Explorer folder windows keyed by HWND."""
    if os.name != "nt":
        return {}
    user32 = ctypes.windll.user32
    windows: dict[int, str] = {}
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        class_name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if class_name.value not in {"CabinetWClass", "ExploreWClass"}:
            return True
        title_length = max(0, int(user32.GetWindowTextLengthW(hwnd)))
        title = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title, title_length + 1)
        windows[int(hwnd)] = title.value
        return True

    user32.EnumWindows(visit, 0)
    return windows


def _bring_explorer_to_front(hwnd: int) -> bool:
    """Restore and foreground an Explorer window after a browser-triggered launch."""
    if os.name != "nt" or not hwnd:
        return False
    user32 = ctypes.windll.user32
    sw_restore = 9
    swp_no_move = 0x0002
    swp_no_size = 0x0001
    swp_show_window = 0x0040
    hwnd_topmost = -1
    hwnd_not_topmost = -2
    flags = swp_no_move | swp_no_size | swp_show_window
    user32.ShowWindow(hwnd, sw_restore)
    user32.SetWindowPos(hwnd, hwnd_topmost, 0, 0, 0, 0, flags)
    user32.SetWindowPos(hwnd, hwnd_not_topmost, 0, 0, 0, 0, flags)
    user32.BringWindowToTop(hwnd)
    focused = bool(user32.SetForegroundWindow(hwnd))
    if not focused:
        try:
            user32.SwitchToThisWindow(hwnd, True)
            focused = True
        except (AttributeError, OSError):
            pass
    return focused


def _open_folder_foreground(target: Path) -> bool:
    """Open a new Explorer window and make the user-visible folder the foreground."""
    target = target.resolve()
    if os.name != "nt":
        raise OSError("当前系统不支持 Windows 资源管理器")
    before = _explorer_windows()
    subprocess.Popen(
        ["explorer.exe", "/n,", str(target)],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    deadline = time.monotonic() + 3.0
    candidate = 0
    while time.monotonic() < deadline:
        current = _explorer_windows()
        new_handles = [hwnd for hwnd in current if hwnd not in before]
        if new_handles:
            candidate = new_handles[-1]
            break
        matching = [
            hwnd
            for hwnd, title in current.items()
            if target.name.casefold() in title.casefold()
        ]
        if matching:
            candidate = matching[-1]
        time.sleep(0.10)
    return _bring_explorer_to_front(candidate) if candidate else False


def _safe_project_image(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if not _within(path, PROJECT_ROOT):
        raise HTTPException(403, "只能访问项目目录内的文件")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "图片不存在")
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(415, "不支持的图片格式")
    return path


def _safe_upload_relative_path(relative_path: str, fallback_name: str) -> Path:
    raw = (relative_path or fallback_name).replace("\\", "/").strip("/")
    original_parts = raw.split("/")
    if not raw or any(part in {"", ".", ".."} for part in original_parts):
        raise HTTPException(400, "文件夹中包含无效路径")
    if len(original_parts) > 16:
        raise HTTPException(400, "文件夹层级过深，最多支持 16 层")
    safe_parts: list[str] = []
    for part in original_parts:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", part).strip(" .")
        if not cleaned:
            raise HTTPException(400, "文件夹中包含无效名称")
        if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            cleaned = f"_{cleaned}"
        safe_parts.append(cleaned[:120])
    return Path(*safe_parts)


def _safe_collection_name(collection: str) -> str:
    name = collection.strip()
    if (
        not name
        or len(name) > 120
        or name in {".", ".."}
        or name.rstrip(" .") != name
        or "/" in name
        or "\\" in name
        or re.search(r'[<>:"|?*\x00-\x1f]', name)
        or name.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
    ):
        raise HTTPException(400, "无效动画组名称")
    return name


def _same_file_content(first: Path, second: Path) -> bool:
    if not first.is_file() or first.stat().st_size != second.stat().st_size:
        return False
    digests: list[bytes] = []
    for path in (first, second):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digests.append(digest.digest())
    return digests[0] == digests[1]


def _folder_images(folder: Path) -> list[Path]:
    resolved_folder = folder.resolve()
    images: list[Path] = []
    for candidate in folder.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        resolved = candidate.resolve()
        if _within(resolved, resolved_folder):
            images.append(resolved)
    return sorted(
        images,
        key=lambda path: path.relative_to(resolved_folder).as_posix().casefold(),
    )


def _available_import_target(source: Path, target: Path) -> tuple[Path, bool, bool]:
    """Return target, whether it can be reused, and whether its name changed."""
    if not target.exists():
        return target, False, False
    if _same_file_content(source, target):
        return target, True, False
    counter = 1
    while True:
        candidate = target.with_name(f"{target.stem}-{counter}{target.suffix.lower()}")
        if not candidate.exists():
            return candidate, False, True
        if _same_file_content(source, candidate):
            return candidate, True, True
        counter += 1


def _import_folder(request: FolderImportRequest) -> dict[str, Any]:
    raw_folder = Path(request.folder_path).expanduser()
    if not raw_folder.is_absolute():
        raw_folder = PROJECT_ROOT / raw_folder
    folder = raw_folder.resolve()
    if not folder.is_dir():
        raise HTTPException(404, f"动画文件夹不存在：{folder}")
    if folder == Path(folder.anchor):
        raise HTTPException(400, "不能把磁盘根目录作为动画组导入")

    input_root = INPUT_ROOT.resolve()
    if folder == input_root:
        raise HTTPException(400, "请指定 input 下的具体动画组，而不是整个 input 目录")
    if _within(input_root, folder) and not _within(folder, input_root):
        raise HTTPException(400, "导入源不能包含工作流自身的 input 目录")

    source_images = _folder_images(folder)
    if not source_images:
        raise HTTPException(400, f"文件夹中没有支持的图片：{folder}")
    if len(source_images) > 5000:
        raise HTTPException(413, "单次最多导入 5000 张图片")

    if _within(folder, input_root):
        relative_folder = folder.relative_to(input_root)
        collection = relative_folder.parts[0]
        if request.collection and _safe_collection_name(request.collection) != collection:
            raise HTTPException(400, f"该文件夹已属于动画组 {collection}，不能改为其他组")
        return {
            "status": "ready",
            "collection": collection,
            "source_folder": str(folder),
            "imported_root": str((input_root / collection).resolve()),
            "image_count": len(source_images),
            "imported_count": 0,
            "reused_count": len(source_images),
            "renamed_count": 0,
            "sources": [
                {
                    "path": path.relative_to(PROJECT_ROOT).as_posix(),
                    "source_path": str(path),
                    "relative_path": path.relative_to(folder).as_posix(),
                }
                for path in source_images
            ],
        }

    collection = _safe_collection_name(request.collection or folder.name)
    target_root = (input_root / collection).resolve()
    if not _within(target_root, input_root) or target_root.parent != input_root:
        raise HTTPException(403, "动画组路径超出输入目录")

    imported_count = 0
    reused_count = 0
    renamed_count = 0
    imported_sources: list[dict[str, str]] = []
    for source in source_images:
        relative = _safe_upload_relative_path(
            source.relative_to(folder).as_posix(), source.name
        )
        target = (target_root / relative).resolve()
        if not _within(target, target_root):
            raise HTTPException(403, "导入文件路径超出动画组目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        renamed = False
        if target.exists() and request.overwrite:
            if _same_file_content(source, target):
                reused_count += 1
            else:
                shutil.copy2(source, target)
                imported_count += 1
        else:
            target, reused, renamed = _available_import_target(source, target)
            if reused:
                reused_count += 1
            else:
                shutil.copy2(source, target)
                imported_count += 1
        renamed_count += int(renamed)
        imported_sources.append(
            {
                "path": target.relative_to(PROJECT_ROOT).as_posix(),
                "source_path": str(target),
                "relative_path": target.relative_to(target_root).as_posix(),
            }
        )

    return {
        "status": "imported",
        "collection": collection,
        "source_folder": str(folder),
        "imported_root": str(target_root),
        "image_count": len(imported_sources),
        "imported_count": imported_count,
        "reused_count": reused_count,
        "renamed_count": renamed_count,
        "sources": imported_sources,
    }


def _safe_collection_directory(collection: str) -> Path:
    name = collection.strip()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or re.search(r'[<>:"|?*\x00-\x1f]', name)
    ):
        raise HTTPException(400, "无效动画组名称")
    target = (INPUT_ROOT / name).resolve()
    if not _within(target, INPUT_ROOT) or target.parent != INPUT_ROOT.resolve():
        raise HTTPException(403, "动画组路径超出输入目录")
    if not target.is_dir():
        raise HTTPException(404, "动画组不存在")
    return target


def _job_directory(job_id: str) -> Path:
    # Older jobs used the original source stem and can legitimately contain
    # spaces or Unicode. Security comes from treating the ID as exactly one
    # directory name and resolving it below JOBS_ROOT, not from ASCII-only IDs.
    if (
        not job_id
        or job_id in {".", ".."}
        or "/" in job_id
        or "\\" in job_id
        or any(ord(char) < 32 for char in job_id)
    ):
        raise HTTPException(400, "无效任务 ID")
    jobs_root = JOBS_ROOT.resolve()
    target = (jobs_root / job_id).resolve()
    if target.parent != jobs_root or not _within(target, jobs_root):
        raise HTTPException(400, "无效任务 ID")
    if not target.is_dir():
        raise HTTPException(404, f"任务不存在：{job_id}")
    return target


def _safe_job(job_id: str) -> dict[str, Any]:
    _job_directory(job_id)
    try:
        return engine.load_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _load_settings() -> dict[str, str]:
    defaults = {
        "ui_theme": "literary",
        "http_proxy": "",
        "https_proxy": "",
        "vision_base_url": "",
        "vision_model": "",
        "vision_api_key": "",
    }
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            defaults.update({key: str(value) for key, value in stored.items() if key in defaults})
        except (OSError, json.JSONDecodeError):
            pass
    return defaults


def _apply_proxy_settings(settings: dict[str, str]) -> None:
    mapping = {"http_proxy": "HTTP_PROXY", "https_proxy": "HTTPS_PROXY"}
    for key, environment_key in mapping.items():
        value = settings.get(key, "").strip()
        if value:
            os.environ[environment_key] = value
            os.environ[environment_key.lower()] = value
        else:
            os.environ.pop(environment_key, None)
            os.environ.pop(environment_key.lower(), None)


def _to_config(request: AnalyzeRequest) -> SmartConfig:
    return SmartConfig(
        background_mode=request.background_mode,
        background_model=request.background_model,
        provider=request.provider,
        expected_frames=request.expected_frames,
        rows=request.rows,
        columns=request.columns,
        alpha_threshold=request.alpha_threshold,
        min_component_area=request.min_component_area,
        component_sensitivity=request.component_sensitivity,
        confidence_threshold=request.confidence_threshold,
        padding=request.padding,
        output_columns=request.output_columns,
        refine_mode=request.refine_mode,
        refine_tolerance=request.refine_tolerance,
        matte_width=request.matte_width,
        smart_chroma_enabled=request.smart_chroma_enabled,
        smart_chroma_strength=request.smart_chroma_strength,
    )


def _source_library_metadata(source_path: str | Path) -> dict[str, str]:
    """Derive animation-set metadata for both new and legacy jobs."""
    metadata = {"collection": "", "relative_name": ""}
    try:
        source = Path(source_path).expanduser()
        if not source.is_absolute():
            source = PROJECT_ROOT / source
        relative = source.resolve().relative_to(INPUT_ROOT.resolve())
    except (OSError, ValueError):
        return metadata
    metadata["relative_name"] = relative.as_posix()
    if len(relative.parts) > 1:
        metadata["collection"] = relative.parts[0]
    return metadata


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = dict(job)
    result.update(_source_library_metadata(job.get("source_path", "")))
    result["files"] = {
        key: f"/api/jobs/{job['id']}/file/{key}?v={Path(value).stat().st_mtime_ns if Path(value).exists() else 0}"
        for key, value in job["files"].items()
    }
    result["source_url"] = f"/api/image?path={Path(job['source_path']).relative_to(PROJECT_ROOT).as_posix()}"
    result["export_profile"] = _source_export_profile(job["source_path"])
    return result


@app.on_event("startup")
async def startup() -> None:
    for directory in (INPUT_ROOT, TEST_ROOT, JOBS_ROOT, SMART_OUTPUT_ROOT, CONFIG_PATH.parent, EXPORT_PROFILES_PATH.parent, TRASH_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
    _apply_proxy_settings(_load_settings())


@app.get("/api/health")
def health() -> dict[str, Any]:
    providers: list[str] = []
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
    except Exception:
        pass
    return {
        "status": "ok",
        "engine": "local-hybrid-instance-v1",
        "providers": providers,
        "gpu_available": "CUDAExecutionProvider" in providers,
        "model_cached": any((PROJECT_ROOT / ".models").rglob("isnet-anime.onnx")),
    }


@app.get("/api/sources")
def sources() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for group, directory in (("测试样例", TEST_ROOT), ("输入队列", INPUT_ROOT)):
        paths = sorted(
            directory.rglob("*"),
            key=lambda value: value.relative_to(directory).as_posix().casefold(),
        )
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                with Image.open(path) as image:
                    width, height = image.size
            except OSError:
                continue
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            queue_relative = path.relative_to(directory)
            stat = path.stat()
            collection = (
                queue_relative.parts[0]
                if group == "输入队列" and len(queue_relative.parts) > 1
                else ""
            )
            items.append(
                {
                    "name": path.name,
                    "path": relative,
                    "group": group,
                    "collection": collection,
                    "relative_name": queue_relative.as_posix(),
                    "width": width,
                    "height": height,
                    "size_bytes": stat.st_size,
                    "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "modified_at_epoch": stat.st_mtime,
                    "url": f"/api/image?path={relative}",
                }
            )
    return {"sources": items}


@app.get("/api/image")
def project_image(path: str) -> FileResponse:
    return FileResponse(_safe_project_image(path))


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    relative_path: str = Form(""),
) -> dict[str, Any]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise HTTPException(415, "仅支持 PNG/JPG/JPEG/WEBP/BMP")
    filename = Path(file.filename or f"upload{suffix}").name
    safe_relative = _safe_upload_relative_path(relative_path, filename)
    if safe_relative.suffix.lower() not in IMAGE_EXTENSIONS:
        safe_relative = safe_relative.with_name(filename)
    target = (INPUT_ROOT / safe_relative).resolve()
    if not _within(target, INPUT_ROOT):
        raise HTTPException(403, "上传路径超出输入目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    counter = 1
    while target.exists():
        target = target.with_name(f"{target.stem}-{counter}{target.suffix}")
        counter += 1
    with target.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    try:
        with Image.open(target) as image:
            image.verify()
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "上传内容不是有效图片") from exc
    queue_relative = target.relative_to(INPUT_ROOT)
    collection = queue_relative.parts[0] if len(queue_relative.parts) > 1 else ""
    return {
        "name": target.name,
        "path": target.relative_to(PROJECT_ROOT).as_posix(),
        "collection": collection,
        "relative_name": queue_relative.as_posix(),
    }


@app.post("/api/v1/import-folder")
def import_folder(request: FolderImportRequest) -> dict[str, Any]:
    """Import a complete local animation folder while preserving its structure."""
    try:
        return _import_folder(request)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(500, f"导入动画文件夹失败：{exc}") from exc


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    source = _safe_project_image(request.source_path)
    try:
        job = await asyncio.to_thread(engine.analyze, source, _to_config(request))
    except Exception as exc:
        raise HTTPException(500, f"分析失败：{exc}") from exc
    return _public_job(job)


@app.get("/api/jobs")
def jobs() -> dict[str, Any]:
    return {"jobs": [_public_job_summary(job) for job in engine.list_jobs()]}


def _public_job_summary(job: dict[str, Any]) -> dict[str, Any]:
    """Add browser-safe preview URLs to the lightweight history record."""
    result = dict(job)
    result.update(_source_library_metadata(job.get("source_path", "")))
    job_id = str(job.get("id") or "")
    cutout = (JOBS_ROOT / job_id / "cutout.png").resolve()
    if job_id and _within(cutout, JOBS_ROOT) and cutout.is_file():
        result["thumbnail_url"] = (
            f"/api/jobs/{job_id}/file/cutout?v={cutout.stat().st_mtime_ns}"
        )
    else:
        result["thumbnail_url"] = ""
    try:
        source = Path(str(job.get("source_path") or "")).resolve()
        if _within(source, PROJECT_ROOT) and source.is_file():
            relative = source.relative_to(PROJECT_ROOT).as_posix()
            result["source_url"] = f"/api/image?path={relative}&v={source.stat().st_mtime_ns}"
        else:
            result["source_url"] = ""
    except (OSError, ValueError):
        result["source_url"] = ""
    return result


def _move_library_items_to_trash(request: LibraryTrashRequest) -> dict[str, Any]:
    source_paths: set[Path] = set()
    for raw_path in request.source_paths:
        path = _safe_project_image(raw_path)
        if not _within(path, INPUT_ROOT):
            raise HTTPException(403, "只能移除输入素材，测试样例受保护")
        source_paths.add(path)

    job_ids = set(request.job_ids)
    if request.include_related_jobs and source_paths:
        resolved_sources = {path.resolve() for path in source_paths}
        for job in engine.list_jobs():
            try:
                if Path(job.get("source_path", "")).resolve() in resolved_sources:
                    job_ids.add(str(job["id"]))
            except (OSError, KeyError, TypeError):
                continue

    if not source_paths and not job_ids:
        raise HTTPException(400, "没有选择要移入回收站的内容")

    job_directories = {_job_directory(job_id) for job_id in job_ids}
    batch_name = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    batch_root = (TRASH_ROOT / batch_name).resolve()
    if not _within(batch_root, TRASH_ROOT):
        raise HTTPException(500, "无法创建安全回收目录")

    moved_sources = 0
    moved_jobs = 0
    for source in sorted(source_paths, key=lambda value: len(value.parts), reverse=True):
        relative = source.relative_to(INPUT_ROOT)
        destination = batch_root / "sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved_sources += 1
        parent = source.parent
        while parent != INPUT_ROOT and _within(parent, INPUT_ROOT):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    for job_directory in sorted(job_directories, key=lambda value: value.name):
        destination = batch_root / "jobs" / job_directory.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(job_directory), str(destination))
        moved_jobs += 1

    manifest = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": [str(path.relative_to(INPUT_ROOT)).replace("\\", "/") for path in sorted(source_paths)],
        "jobs": sorted(job_ids),
    }
    batch_root.mkdir(parents=True, exist_ok=True)
    (batch_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "trashed",
        "source_count": moved_sources,
        "job_count": moved_jobs,
        "trash_dir": str(batch_root),
        "recoverable": True,
    }


@app.post("/api/library/trash")
async def trash_library_items(request: LibraryTrashRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_move_library_items_to_trash, request)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(500, f"移入回收站失败：{exc}") from exc


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _public_job(_safe_job(job_id))


@app.get("/api/jobs/{job_id}/file/{kind}")
def job_file(job_id: str, kind: str) -> FileResponse:
    job = _safe_job(job_id)
    if kind not in job["files"]:
        raise HTTPException(404, "任务文件不存在")
    path = Path(job["files"][kind]).resolve()
    if not _within(path, JOBS_ROOT) or not path.exists():
        raise HTTPException(404, "任务文件不存在")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.post("/api/jobs/{job_id}/assign")
async def assign(job_id: str, request: AssignmentRequest) -> dict[str, Any]:
    _safe_job(job_id)
    try:
        job = await asyncio.to_thread(
            engine.assign_component, job_id, request.component_id, request.frame_id
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _public_job(job)


@app.post("/api/jobs/{job_id}/refine")
async def refine(job_id: str, request: RefineRequest) -> dict[str, Any]:
    _safe_job(job_id)
    try:
        job = await asyncio.to_thread(
            engine.refine_job,
            job_id,
            request.mode,
            request.tolerance,
            request.matte_width,
            request.smart_chroma_enabled,
            request.smart_chroma_strength,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _public_job(job)


@app.post("/api/jobs/{job_id}/mask-point")
async def mask_point(job_id: str, request: MaskPointRequest) -> dict[str, Any]:
    _safe_job(job_id)
    try:
        job = await asyncio.to_thread(
            engine.edit_mask_point,
            job_id,
            request.x,
            request.y,
            request.action,
            request.tolerance,
            request.feather,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _public_job(job)


@app.post("/api/jobs/{job_id}/mask-region")
async def mask_region(job_id: str, request: MaskRegionRequest) -> dict[str, Any]:
    _safe_job(job_id)
    try:
        job = await asyncio.to_thread(
            engine.restore_initial_region,
            job_id,
            request.points,
            request.feather,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _public_job(job)


@app.post("/api/jobs/{job_id}/refine-region")
async def refine_region(job_id: str, request: LocalRefineRegionRequest) -> dict[str, Any]:
    _safe_job(job_id)
    try:
        job = await asyncio.to_thread(
            engine.refine_local_region,
            job_id,
            request.points,
            request.strength,
            request.tolerance,
            request.matte_width,
            request.feather,
            request.smart_chroma_enabled,
            request.smart_chroma_strength,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"局部精修失败：{exc}") from exc
    return _public_job(job)


@app.post("/api/jobs/{job_id}/export")
async def export(job_id: str, request: ExportRequest | None = None) -> dict[str, Any]:
    job = _safe_job(job_id)
    options = request or ExportRequest()
    destination, artifacts = _resolve_export_options(
        options.output_path,
        options.artifacts,
        _source_export_profile(job["source_path"]),
    )
    try:
        result = await asyncio.to_thread(
            engine.export_job,
            job_id,
            artifacts=artifacts,
            destination=destination,
            ai_tag=options.ai_tag,
        )
        result["export_profile"] = _remember_source_export_profile(
            job["source_path"], destination, artifacts
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _export_collection(request: CollectionExportRequest) -> dict[str, Any]:
    collection_directory = _safe_collection_directory(request.collection)
    destination, artifacts = _resolve_export_options(
        request.output_path,
        request.artifacts,
        _collection_export_profile(request.collection),
    )
    source_paths = sorted(
        (
            path.resolve()
            for path in collection_directory.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: path.relative_to(collection_directory).as_posix().casefold(),
    )
    jobs_by_source: dict[Path, dict[str, Any]] = {}
    for summary in engine.list_jobs():
        try:
            source_path = Path(summary.get("source_path", "")).resolve()
        except (OSError, TypeError):
            continue
        if source_path not in jobs_by_source:
            jobs_by_source[source_path] = summary

    exported: list[dict[str, Any]] = []
    missing: list[str] = []
    skipped_review: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for source_path in source_paths:
        relative_name = source_path.relative_to(collection_directory).as_posix()
        summary = jobs_by_source.get(source_path)
        if not summary:
            missing.append(relative_name)
            continue
        review_count = int(summary.get("review_count", 0))
        if review_count and not request.include_review:
            skipped_review.append(
                {
                    "source": relative_name,
                    "job_id": summary["id"],
                    "review_count": review_count,
                }
            )
            continue
        try:
            result = engine.export_job(
                str(summary["id"]),
                artifacts=artifacts,
                destination=destination,
                destination_subdir=Path(relative_name).with_suffix("") if destination else None,
                ai_tag=request.ai_tag,
            )
            if destination is not None:
                result_directory = destination / "result"
                result_directory.mkdir(parents=True, exist_ok=True)
                preview_name = "__".join(Path(relative_name).with_suffix("").parts) + ".png"
                shutil.copy2(result["sprite_sheet"], result_directory / preview_name)
            _remember_source_export_profile(
                source_path,
                Path(result["delivery_dir"]) if destination is not None else None,
                artifacts,
            )
            exported.append(
                {
                    "source": relative_name,
                    "job_id": summary["id"],
                    **result,
                }
            )
        except (OSError, ValueError, KeyError) as exc:
            errors.append({"source": relative_name, "error": str(exc)})

    export_profile = None
    if exported:
        export_profile = _remember_collection_export_profile(
            request.collection, destination, artifacts
        )
    output_directory = destination or (SMART_OUTPUT_ROOT / request.collection).resolve()
    return {
        "collection": request.collection,
        "source_count": len(source_paths),
        "analyzed_count": len(source_paths) - len(missing),
        "exported_count": len(exported),
        "missing_count": len(missing),
        "skipped_review_count": len(skipped_review),
        "failed_count": len(errors),
        "output_dir": str(output_directory),
        "result_dir": str(output_directory / "result"),
        "export_profile": export_profile,
        "exported": exported,
        "missing": missing,
        "skipped_review": skipped_review,
        "errors": errors,
    }


@app.post("/api/collections/export")
async def export_collection(request: CollectionExportRequest) -> dict[str, Any]:
    return await asyncio.to_thread(_export_collection, request)


@app.get("/api/collections/{collection}/export-profile")
def collection_export_profile(collection: str) -> dict[str, Any]:
    _safe_collection_directory(collection)
    return _collection_export_profile(collection) or {
        "collection": collection,
        "output_path": "",
        "artifacts": ["aligned"],
        "remembered": False,
    }


@app.post("/api/collections/open-output")
def open_collection_output(request: CollectionOpenRequest) -> dict[str, Any]:
    _safe_collection_directory(request.collection)
    root = _output_destination(request.output_path) or (SMART_OUTPUT_ROOT / request.collection).resolve()
    target = root / "result" if request.view == "result" else root
    if not target.is_dir():
        raise HTTPException(404, "请先导出当前动画组")
    try:
        focused = _open_folder_foreground(target)
    except OSError as exc:
        raise HTTPException(500, f"无法打开整组输出文件夹：{exc}") from exc
    return {
        "status": "opened",
        "output_dir": str(target),
        "view": request.view,
        "foreground": focused,
    }


@app.post("/api/jobs/{job_id}/open-output")
def open_output(job_id: str, request: OutputOpenRequest | None = None) -> dict[str, Any]:
    job = _safe_job(job_id)
    requested = request or OutputOpenRequest()
    target = _output_destination(requested.output_path)
    if target is None:
        latest_export = (job.get("exports") or [{}])[-1]
        target = Path(latest_export.get("delivery_dir") or job_output_target(job, SMART_OUTPUT_ROOT)).resolve()
    if not target.is_dir():
        raise HTTPException(404, "请先导出当前任务")
    try:
        focused = _open_folder_foreground(target)
    except OSError as exc:
        raise HTTPException(500, f"无法打开输出文件夹：{exc}") from exc
    return {
        "status": "opened",
        "output_dir": str(target),
        "foreground": focused,
    }


@app.post("/api/dialogs/select-output-folder")
async def select_output_folder(request: FolderDialogRequest) -> dict[str, Any]:
    try:
        selected = await asyncio.to_thread(_select_output_directory, request.initial_path)
    except HTTPException:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, f"无法打开文件夹选择器：{exc}") from exc
    return {"path": selected, "cancelled": not bool(selected)}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    settings = _load_settings()
    return {
        **{key: value for key, value in settings.items() if key != "vision_api_key"},
        "vision_api_key_set": bool(settings.get("vision_api_key")),
    }


@app.put("/api/settings")
def put_settings(request: SettingsRequest) -> dict[str, Any]:
    settings = request.model_dump()
    if settings["vision_api_key"] == "__KEEP__":
        settings["vision_api_key"] = _load_settings().get("vision_api_key", "")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    _apply_proxy_settings(settings)
    return {"status": "saved", "vision_api_key_set": bool(settings["vision_api_key"])}


@app.post("/api/v1/process")
async def process(request: ProcessRequest) -> dict[str, Any]:
    """Stable automation endpoint: analyze a project image and optionally export it."""
    source = _safe_project_image(request.source_path)
    destination, artifacts = _resolve_export_options(
        request.output_path,
        request.artifacts,
        _source_export_profile(source),
    )
    try:
        job = await asyncio.to_thread(engine.analyze, source, _to_config(request))
        export_result = None
        if request.export:
            export_result = await asyncio.to_thread(
                engine.export_job,
                job["id"],
                artifacts=artifacts,
                destination=destination,
                ai_tag=request.ai_tag,
            )
            export_result["export_profile"] = _remember_source_export_profile(
                source, destination, artifacts
            )
            job = await asyncio.to_thread(engine.load_job, job["id"])
        elif request.ai_tag:
            job = await asyncio.to_thread(engine.tag_job_as_ai, job["id"], "analyze")
        return {"job": _public_job(job), "export": export_result}
    except Exception as exc:
        raise HTTPException(500, f"处理失败：{exc}") from exc


if WEB_ROOT.exists():
    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")
