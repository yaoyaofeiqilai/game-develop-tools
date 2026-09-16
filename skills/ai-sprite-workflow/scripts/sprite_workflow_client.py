from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_ROOT = Path(os.environ.get("SPRITE_WORKFLOW_HOME", r"F:\game-tool"))
DEFAULT_BASE_URL = "http://127.0.0.1:7865"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
EXPORT_ARTIFACTS = ("aligned", "trimmed", "masks", "sheet", "manifest")


class ClientError(RuntimeError):
    pass


def request_json(
    base_url: str,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 600,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url.rstrip("/") + endpoint,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except json.JSONDecodeError:
            pass
        raise ClientError(f"API {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ClientError(f"无法连接本地精灵工作流：{exc}") from exc


def ensure_server(base_url: str, project_root: Path) -> None:
    try:
        health = request_json(base_url, "GET", "/api/health", timeout=2)
        if health.get("status") == "ok":
            return
    except ClientError:
        pass

    launcher = project_root / "start_gui.ps1"
    if not launcher.exists():
        raise ClientError(f"启动脚本不存在：{launcher}")
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(launcher),
        "-NoBrowser",
    ]
    try:
        subprocess.run(
            command,
            cwd=project_root,
            check=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClientError(f"启动本地服务失败：{exc}") from exc
    for _ in range(40):
        try:
            if request_json(base_url, "GET", "/api/health", timeout=2).get("status") == "ok":
                return
        except ClientError:
            time.sleep(0.25)
    raise ClientError("本地服务启动后未通过健康检查")


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def same_file_content(first: Path, second: Path) -> bool:
    if not first.exists() or first.stat().st_size != second.stat().st_size:
        return False
    for path in (first, second):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if path == first:
            first_digest = digest.digest()
        else:
            return first_digest == digest.digest()
    return False


def prepare_source(raw_source: str, project_root: Path, import_external: bool) -> Path:
    source = Path(raw_source).expanduser().resolve()
    if not source.is_file():
        raise ClientError(f"输入图片不存在：{source}")
    if source.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ClientError("仅支持 PNG、JPG、JPEG、WEBP 和 BMP")
    if is_within(source, project_root):
        return source
    if not import_external:
        raise ClientError("图片不在项目目录内；确认需要导入后添加 --import-external")

    input_root = project_root / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    target = input_root / source.name
    if target.exists() and same_file_content(source, target):
        return target
    if target.exists():
        target = input_root / f"{source.stem}-{hashlib.sha256(str(source).encode()).hexdigest()[:8]}{source.suffix.lower()}"
    shutil.copy2(source, target)
    return target


def prepare_folder_sources(raw_folder: str, project_root: Path, import_external: bool) -> list[Path]:
    folder = Path(raw_folder).expanduser().resolve()
    if not folder.is_dir():
        raise ClientError(f"输入文件夹不存在：{folder}")
    sources = sorted(
        (path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: path.relative_to(folder).as_posix().casefold(),
    )
    if not sources:
        raise ClientError(f"文件夹中没有支持的图片：{folder}")
    if is_within(folder, project_root):
        return sources
    if not import_external:
        raise ClientError("文件夹不在项目目录内；确认需要导入后添加 --import-external")

    target_root = project_root / "input" / folder.name
    prepared: list[Path] = []
    for source in sources:
        target = target_root / source.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and same_file_content(source, target):
            prepared.append(target)
            continue
        if target.exists():
            suffix = hashlib.sha256(str(source).encode()).hexdigest()[:8]
            target = target.with_name(f"{target.stem}-{suffix}{target.suffix.lower()}")
        shutil.copy2(source, target)
        prepared.append(target)
    return prepared


def local_job_file(project_root: Path, job_id: str, filename: str) -> str:
    return str((project_root / "workspace" / "jobs" / job_id / filename).resolve())


def summarize_job(job: dict[str, Any], project_root: Path) -> dict[str, Any]:
    job_id = job["id"]
    review_ids = job.get("review_component_ids", [])
    return {
        "job_id": job_id,
        "source": job.get("source_path"),
        "status": job.get("status"),
        "frame_count": job.get("layout", {}).get("frame_count"),
        "rows": job.get("layout", {}).get("rows"),
        "columns": job.get("layout", {}).get("columns"),
        "review_count": len(review_ids),
        "review_component_ids": review_ids,
        "refinement": job.get("refinement", {}),
        "initial_cutout_path": local_job_file(project_root, job_id, "cutout_initial.png"),
        "refined_cutout_path": local_job_file(project_root, job_id, "cutout.png"),
        "overlay_path": local_job_file(project_root, job_id, "overlay.png"),
        "job_json_path": local_job_file(project_root, job_id, "job.json"),
        "next_action": (
            "Inspect overlay_path before assigning low-confidence components."
            if review_ids
            else "No low-confidence components remain; the job is ready to export."
        ),
    }


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parse_points(value: str) -> list[list[int]]:
    points: list[list[int]] = []
    try:
        for item in value.split(";"):
            x, y = item.split(",", 1)
            points.append([int(x.strip()), int(y.strip())])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("圈选点格式应为 x,y;x,y;x,y") from exc
    if len(points) < 3:
        raise argparse.ArgumentTypeError("圈选区域至少需要 3 个点")
    return points


def add_processing_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--expected-frames", type=int, default=0)
    command.add_argument("--rows", type=int, default=0)
    command.add_argument("--columns", type=int, default=0)
    command.add_argument("--background-mode", choices=("auto", "rembg", "chroma"), default="auto")
    command.add_argument("--background-model", default="isnet-anime")
    command.add_argument("--provider", choices=("auto", "cuda", "cpu"), default="auto")
    command.add_argument("--refine-mode", choices=("off", "conservative", "balanced", "strong"), default="conservative")
    command.add_argument("--refine-tolerance", type=float, default=18.0)
    command.add_argument("--matte-width", type=int, default=3)
    command.add_argument(
        "--smart-chroma",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically clean saturated green/blue screens (enabled by default)",
    )
    command.add_argument(
        "--chroma-strength",
        choices=("conservative", "standard", "strong"),
        default="standard",
    )
    command.add_argument("--analyze-only", action="store_true")
    command.add_argument("--import-external", action="store_true")
    add_export_arguments(command)


def add_export_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--output",
        type=Path,
        help="Deliver to this directory; omit to reuse this source's remembered destination",
    )
    selection = command.add_mutually_exclusive_group()
    selection.add_argument(
        "--artifact",
        action="append",
        choices=EXPORT_ARTIFACTS,
        help="Export one artifact type; repeat as needed; omit to reuse this source's remembered selection",
    )
    selection.add_argument("--all", action="store_true", help="Export the complete processed bundle")


def requested_artifacts(args: argparse.Namespace) -> list[str]:
    if getattr(args, "all", False):
        return list(EXPORT_ARTIFACTS)
    return list(getattr(args, "artifact", None) or ["aligned"])


def explicit_artifacts(args: argparse.Namespace) -> list[str] | None:
    if getattr(args, "all", False):
        return list(EXPORT_ARTIFACTS)
    selected = getattr(args, "artifact", None)
    return list(selected) if selected else None


def requested_output(args: argparse.Namespace) -> str:
    output = getattr(args, "output", None)
    return str(output.expanduser().resolve()) if output else ""


def process_payload(
    args: argparse.Namespace,
    source: Path,
    output_path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_path": str(source),
        "expected_frames": max(0, args.expected_frames),
        "rows": max(0, args.rows),
        "columns": max(0, args.columns),
        "background_mode": args.background_mode,
        "background_model": args.background_model,
        "provider": args.provider,
        "refine_mode": args.refine_mode,
        "refine_tolerance": max(3.0, min(60.0, args.refine_tolerance)),
        "matte_width": max(0, min(12, args.matte_width)),
        "smart_chroma_enabled": args.smart_chroma,
        "smart_chroma_strength": args.chroma_strength,
        "export": not args.analyze_only,
        "ai_tag": True,
    }
    selected_output = requested_output(args) if output_path is None else output_path
    selected_artifacts = explicit_artifacts(args)
    if selected_output:
        payload["output_path"] = selected_output
    if selected_artifacts is not None:
        payload["artifacts"] = selected_artifacts
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Client for the local AI sprite workflow")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="Analyze and optionally export an image")
    process.add_argument("source")
    add_processing_arguments(process)

    import_folder = subparsers.add_parser(
        "import-folder",
        help="Import a complete local animation folder into the workflow",
    )
    import_folder.add_argument("folder")
    import_folder.add_argument("--collection", default="", help="Optional animation-set name below input")
    import_folder.add_argument("--overwrite", action="store_true", help="Replace conflicting relative paths")

    process_folder = subparsers.add_parser("process-folder", help="Process every image in an animation folder")
    process_folder.add_argument("folder")
    add_processing_arguments(process_folder)
    process_folder.add_argument("--skip-existing", action="store_true", help="Skip sources that already have a saved job")
    process_folder.add_argument("--stop-on-error", action="store_true", help="Stop after the first failed image")

    inspect = subparsers.add_parser("inspect", help="Inspect a saved job")
    inspect.add_argument("job_id")
    inspect.add_argument("--full", action="store_true")

    assign = subparsers.add_parser("assign", help="Assign a component to a frame")
    assign.add_argument("job_id")
    assign.add_argument("component_id", type=int)
    assign.add_argument("frame_id", type=int)

    refine = subparsers.add_parser("refine", help="Rebuild a job mask without rerunning the segmentation model")
    refine.add_argument("job_id")
    refine.add_argument("--mode", choices=("off", "conservative", "balanced", "strong"), default="balanced")
    refine.add_argument("--tolerance", type=float, default=18.0)
    refine.add_argument("--matte-width", type=int, default=3)
    refine.add_argument(
        "--smart-chroma",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable adaptive green/blue-screen cleanup for this job",
    )
    refine.add_argument(
        "--chroma-strength",
        choices=("conservative", "standard", "strong"),
        default=None,
    )

    mask_point = subparsers.add_parser("mask-point", help="Clear or restore one colour-connected mask region")
    mask_point.add_argument("job_id")
    mask_point.add_argument("x", type=int)
    mask_point.add_argument("y", type=int)
    mask_point.add_argument("action", choices=("background", "foreground"))
    mask_point.add_argument("--tolerance", type=float, default=18.0)
    mask_point.add_argument("--feather", type=int, default=2)

    restore_region = subparsers.add_parser("restore-region", help="Restore the preserved initial cutout inside a polygon")
    restore_region.add_argument("job_id")
    restore_region.add_argument("points", type=parse_points, help='Polygon as "x,y;x,y;x,y" in source coordinates')
    restore_region.add_argument("--feather", type=int, default=4)

    refine_region = subparsers.add_parser("refine-region", help="Re-run background removal inside a polygon")
    refine_region.add_argument("job_id")
    refine_region.add_argument("points", type=parse_points, help='Polygon as "x,y;x,y;x,y" in source coordinates')
    refine_region.add_argument("--strength", choices=("gentle", "standard", "strong", "maximum"), default="standard")
    refine_region.add_argument("--tolerance", type=float, default=18.0)
    refine_region.add_argument("--matte-width", type=int, default=3)
    refine_region.add_argument("--feather", type=int, default=4)

    export = subparsers.add_parser("export", help="Export a saved job")
    export.add_argument("job_id")
    add_export_arguments(export)
    export_folder = subparsers.add_parser("export-folder", help="Export analyzed results for an animation collection")
    export_folder.add_argument("collection", help="Top-level folder name below input")
    export_folder.add_argument("--include-review", action="store_true", help="Also export jobs with unresolved component review items")
    add_export_arguments(export_folder)
    library = subparsers.add_parser("library", help="List input assets and processing history")
    library.add_argument("--kind", choices=("all", "sources", "ai", "jobs"), default="all")
    trash = subparsers.add_parser("trash", help="Plan or execute recoverable library cleanup")
    trash.add_argument("--source", dest="source_paths", action="append", default=[])
    trash.add_argument("--job", dest="job_ids", action="append", default=[])
    trash.add_argument("--keep-related-jobs", action="store_true")
    trash.add_argument("--confirm", action="store_true", help="Execute the move; without this flag only print the plan")
    subparsers.add_parser("jobs", help="List saved jobs")
    subparsers.add_parser("health", help="Show service health")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = args.project_root.resolve()
    try:
        ensure_server(args.base_url, project_root)
        if args.command == "health":
            print_json(request_json(args.base_url, "GET", "/api/health"))
        elif args.command == "jobs":
            print_json(request_json(args.base_url, "GET", "/api/jobs"))
        elif args.command == "process":
            source = prepare_source(args.source, project_root, args.import_external)
            result = request_json(
                args.base_url,
                "POST",
                "/api/v1/process",
                process_payload(args, source),
            )
            summary = summarize_job(result["job"], project_root)
            summary["export"] = result.get("export")
            print_json(summary)
        elif args.command == "import-folder":
            result = request_json(
                args.base_url,
                "POST",
                "/api/v1/import-folder",
                {
                    "folder_path": str(Path(args.folder).expanduser().resolve()),
                    "collection": args.collection,
                    "overwrite": args.overwrite,
                },
            )
            print_json(result)
        elif args.command == "process-folder":
            sources = prepare_folder_sources(args.folder, project_root, args.import_external)
            source_folder = Path(args.folder).expanduser().resolve()
            imported_folder = (project_root / "input" / source_folder.name).resolve()
            custom_output = Path(requested_output(args)) if requested_output(args) else None
            existing_sources: set[str] = set()
            if args.skip_existing:
                jobs = request_json(args.base_url, "GET", "/api/jobs").get("jobs", [])
                existing_sources = {str(Path(job.get("source_path", "")).resolve()).casefold() for job in jobs}
            processed: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []
            skipped: list[str] = []
            for source in sources:
                if str(source.resolve()).casefold() in existing_sources:
                    skipped.append(str(source))
                    continue
                try:
                    output_path = None
                    if custom_output is not None:
                        try:
                            relative = source.relative_to(source_folder)
                        except ValueError:
                            relative = source.relative_to(imported_folder)
                        output_path = str((custom_output / relative.with_suffix("")).resolve())
                    result = request_json(args.base_url, "POST", "/api/v1/process", process_payload(args, source, output_path))
                    summary = summarize_job(result["job"], project_root)
                    summary["export"] = result.get("export")
                    processed.append(summary)
                except ClientError as exc:
                    errors.append({"source": str(source), "error": str(exc)})
                    if args.stop_on_error:
                        break
            print_json({
                "folder": str(Path(args.folder).expanduser().resolve()),
                "image_count": len(sources),
                "processed_count": len(processed),
                "skipped_count": len(skipped),
                "failed_count": len(errors),
                "review_job_count": sum(1 for item in processed if item["review_count"] > 0),
                "output_path": str(custom_output) if custom_output else "",
                "artifacts": explicit_artifacts(args),
                "processed": processed,
                "skipped": skipped,
                "errors": errors,
            })
        elif args.command == "inspect":
            job = request_json(args.base_url, "GET", f"/api/jobs/{args.job_id}")
            print_json(job if args.full else summarize_job(job, project_root))
        elif args.command == "assign":
            job = request_json(
                args.base_url,
                "POST",
                f"/api/jobs/{args.job_id}/assign",
                {"component_id": args.component_id, "frame_id": args.frame_id},
            )
            print_json(summarize_job(job, project_root))
        elif args.command == "refine":
            refine_payload: dict[str, Any] = {
                "mode": args.mode,
                "tolerance": max(3.0, min(60.0, args.tolerance)),
                "matte_width": max(0, min(12, args.matte_width)),
            }
            if args.smart_chroma is not None:
                refine_payload["smart_chroma_enabled"] = args.smart_chroma
            if args.chroma_strength is not None:
                refine_payload["smart_chroma_strength"] = args.chroma_strength
            job = request_json(
                args.base_url,
                "POST",
                f"/api/jobs/{args.job_id}/refine",
                refine_payload,
            )
            print_json(summarize_job(job, project_root))
        elif args.command == "mask-point":
            job = request_json(
                args.base_url,
                "POST",
                f"/api/jobs/{args.job_id}/mask-point",
                {
                    "x": args.x,
                    "y": args.y,
                    "action": args.action,
                    "tolerance": max(2.0, min(60.0, args.tolerance)),
                    "feather": max(0, min(12, args.feather)),
                },
            )
            print_json(summarize_job(job, project_root))
        elif args.command == "restore-region":
            job = request_json(
                args.base_url,
                "POST",
                f"/api/jobs/{args.job_id}/mask-region",
                {
                    "points": args.points,
                    "feather": max(0, min(24, args.feather)),
                },
            )
            print_json(summarize_job(job, project_root))
        elif args.command == "refine-region":
            job = request_json(
                args.base_url,
                "POST",
                f"/api/jobs/{args.job_id}/refine-region",
                {
                    "points": args.points,
                    "strength": args.strength,
                    "tolerance": max(3.0, min(60.0, args.tolerance)),
                    "matte_width": max(0, min(12, args.matte_width)),
                    "feather": max(0, min(24, args.feather)),
                },
            )
            print_json(summarize_job(job, project_root))
        elif args.command == "export":
            payload = {"ai_tag": True}
            if requested_output(args):
                payload["output_path"] = requested_output(args)
            if explicit_artifacts(args) is not None:
                payload["artifacts"] = explicit_artifacts(args)
            print_json(request_json(
                args.base_url,
                "POST",
                f"/api/jobs/{args.job_id}/export",
                payload,
            ))
        elif args.command == "export-folder":
            payload = {
                "collection": args.collection,
                "include_review": args.include_review,
                "ai_tag": True,
            }
            if requested_output(args):
                payload["output_path"] = requested_output(args)
            if explicit_artifacts(args) is not None:
                payload["artifacts"] = explicit_artifacts(args)
            print_json(request_json(
                args.base_url,
                "POST",
                "/api/collections/export",
                payload,
            ))
        elif args.command == "library":
            result: dict[str, Any] = {}
            if args.kind in {"all", "sources"}:
                source_items = request_json(args.base_url, "GET", "/api/sources").get("sources", [])
                result["sources"] = [item for item in source_items if item.get("group") == "输入队列"]
            if args.kind in {"all", "jobs"}:
                result["jobs"] = request_json(args.base_url, "GET", "/api/jobs").get("jobs", [])
            if args.kind == "ai":
                jobs = request_json(args.base_url, "GET", "/api/jobs").get("jobs", [])
                result["ai_jobs"] = [item for item in jobs if "ai" in item.get("tags", [])]
            print_json(result)
        elif args.command == "trash":
            if not args.source_paths and not args.job_ids:
                raise ClientError("至少指定一个 --source 或 --job")
            payload = {
                "source_paths": args.source_paths,
                "job_ids": args.job_ids,
                "include_related_jobs": not args.keep_related_jobs,
            }
            if not args.confirm:
                print_json({"executed": False, "recoverable": True, "plan": payload, "next_action": "Review the exact paths, then repeat with --confirm only if the user explicitly requested cleanup."})
            else:
                print_json(request_json(args.base_url, "POST", "/api/library/trash", payload))
        return 0
    except ClientError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
