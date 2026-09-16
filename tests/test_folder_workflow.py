from __future__ import annotations

import json
import unittest
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException
from PIL import Image

from gui_server import (
    CollectionExportRequest,
    FolderImportRequest,
    LibraryTrashRequest,
    _collection_export_profile,
    _job_directory,
    _export_collection,
    _import_folder,
    _move_library_items_to_trash,
    _public_job_summary,
    _remember_collection_export_profile,
    _remember_source_export_profile,
    _resolve_export_options,
    _safe_upload_relative_path,
    _source_export_profile,
)
from smart_engine import PROJECT_ROOT, SmartEngine, _deliver_export_artifacts, _encode_ids, collection_result_target, job_output_target, make_job_id


class FolderWorkflowTests(unittest.TestCase):
    def test_folder_import_preserves_structure_and_reuses_identical_files(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        source = root / "generated" / "hero"
        input_root = root / "input"
        try:
            (source / "attack").mkdir(parents=True)
            (source / "idle.png").write_bytes(b"idle")
            (source / "attack" / "01.webp").write_bytes(b"attack")
            (source / "notes.txt").write_text("ignore", encoding="utf-8")
            with patch("gui_server.INPUT_ROOT", input_root):
                first = _import_folder(FolderImportRequest(folder_path=str(source)))
                second = _import_folder(FolderImportRequest(folder_path=str(source)))

            self.assertEqual(first["collection"], "hero")
            self.assertEqual(first["imported_count"], 2)
            self.assertEqual(first["reused_count"], 0)
            self.assertEqual(second["imported_count"], 0)
            self.assertEqual(second["reused_count"], 2)
            self.assertEqual(
                [item["relative_path"] for item in first["sources"]],
                ["attack/01.webp", "idle.png"],
            )
            self.assertTrue((input_root / "hero" / "attack" / "01.webp").is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_folder_import_registers_existing_input_collection_without_copying(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        input_root = root / "input"
        source = input_root / "hero" / "attack"
        try:
            source.mkdir(parents=True)
            (source / "01.png").write_bytes(b"frame")
            with patch("gui_server.INPUT_ROOT", input_root):
                result = _import_folder(FolderImportRequest(folder_path=str(source)))

            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["collection"], "hero")
            self.assertEqual(result["imported_count"], 0)
            self.assertEqual(result["reused_count"], 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_job_history_summary_exposes_processed_thumbnail_with_source_fallback(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        jobs_root = root / "jobs"
        source = root / "input" / "hero.png"
        cutout = jobs_root / "job-1" / "cutout.png"
        try:
            source.parent.mkdir(parents=True)
            cutout.parent.mkdir(parents=True)
            Image.new("RGB", (8, 8), "white").save(source)
            Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(cutout)
            with patch("gui_server.JOBS_ROOT", jobs_root):
                summary = _public_job_summary({
                    "id": "job-1",
                    "source_path": str(source),
                    "source_name": "hero.png",
                })
            self.assertIn("/api/jobs/job-1/file/cutout", summary["thumbnail_url"])
            self.assertIn("/api/image?path=tests/_runtime/", summary["source_url"])
            self.assertIn("&v=", summary["source_url"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_job_history_summary_recovers_animation_collection_from_source_path(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        input_root = root / "input"
        source = input_root / "hero-walk" / "nested" / "frame_002.png"
        try:
            source.parent.mkdir(parents=True)
            Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(source)
            with patch("gui_server.INPUT_ROOT", input_root):
                summary = _public_job_summary({
                    "id": "legacy-folder-job",
                    "source_path": str(source),
                    "source_name": source.name,
                })
            self.assertEqual(summary["collection"], "hero-walk")
            self.assertEqual(
                summary["relative_name"],
                "hero-walk/nested/frame_002.png",
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_export_profile_is_bound_to_source_not_last_global_choice(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        profile_path = root / "workspace" / "export-profiles.json"
        first = root / "input" / "hero.png"
        second = root / "input" / "enemy.png"
        destination = root / "game" / "assets" / "hero"
        try:
            with patch("gui_server.EXPORT_PROFILES_PATH", profile_path), patch(
                "gui_server.engine.list_jobs", return_value=[]
            ):
                saved = _remember_source_export_profile(first, destination, ["aligned"])
                self.assertEqual(saved["output_path"], str(destination.resolve()))
                self.assertEqual(_source_export_profile(first)["output_path"], str(destination.resolve()))
                self.assertIsNone(_source_export_profile(second))
                resolved_path, artifacts = _resolve_export_options(
                    None, None, _source_export_profile(first)
                )
                self.assertEqual(resolved_path, destination.resolve())
                self.assertEqual(artifacts, ["aligned"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_collection_export_profile_is_independent_and_can_reset_to_default(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        profile_path = root / "workspace" / "export-profiles.json"
        destination = root / "delivery" / "hero"
        try:
            with patch("gui_server.EXPORT_PROFILES_PATH", profile_path):
                _remember_collection_export_profile("hero", destination, ["sheet"])
                profile = _collection_export_profile("hero")
                self.assertEqual(profile["output_path"], str(destination.resolve()))
                self.assertEqual(profile["artifacts"], ["sheet"])
                resolved_path, artifacts = _resolve_export_options("", ["trimmed"], profile)
                self.assertIsNone(resolved_path)
                self.assertEqual(artifacts, ["trimmed"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_previous_job_export_is_used_as_legacy_source_profile(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        source = root / "input" / "hero.png"
        destination = root / "godot" / "assets" / "hero"
        jobs = [{
            "id": "old-job",
            "source_name": "hero.png",
            "source_path": str(source),
            "modified_at_epoch": 42,
            "last_export": {
                "delivery_dir": str(destination),
                "artifacts": ["aligned"],
                "exported_at": "2026-09-01 10:00:00",
            },
        }]
        try:
            with patch("gui_server.EXPORT_PROFILES_PATH", root / "missing-profiles.json"), patch(
                "gui_server.engine.list_jobs", return_value=jobs
            ):
                profile = _source_export_profile(source)
            self.assertEqual(profile["output_path"], str(destination.resolve()))
            self.assertEqual(profile["artifacts"], ["aligned"])
            self.assertTrue(profile["legacy"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_folder_upload_keeps_safe_relative_structure(self) -> None:
        relative = _safe_upload_relative_path(
            "hero-pack/attack/front/frame 01.png", "frame 01.png"
        )
        self.assertEqual(
            relative.as_posix(), "hero-pack/attack/front/frame 01.png"
        )

    def test_folder_upload_rejects_parent_traversal(self) -> None:
        with self.assertRaises(HTTPException):
            _safe_upload_relative_path("hero-pack/../outside.png", "outside.png")

    def test_export_target_preserves_animation_set_folders(self) -> None:
        job = {
            "source_path": str(
                PROJECT_ROOT / "input" / "hero-pack" / "attack" / "sheet.png"
            ),
            "source_name": "sheet.png",
        }
        target = job_output_target(job, PROJECT_ROOT / "output")
        self.assertEqual(
            target,
            (PROJECT_ROOT / "output" / "hero-pack" / "attack" / "sheet").resolve(),
        )

    def test_collection_result_preview_is_flat_and_readable(self) -> None:
        temporary_project = PROJECT_ROOT / "tests" / "preview-layout"
        job = {
            "source_path": str(
                temporary_project / "input" / "batch_a" / "attack" / "sheet.png"
            ),
            "source_name": "sheet.png",
        }
        with patch("smart_engine.PROJECT_ROOT", temporary_project):
            target = collection_result_target(job, temporary_project / "output")
        self.assertEqual(
            target,
            (temporary_project / "output" / "batch_a" / "result" / "attack__sheet.png").resolve(),
        )

    def test_aligned_only_delivery_flattens_frames_into_requested_directory(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        canonical = root / "canonical"
        aligned = canonical / "frames_aligned"
        destination = root / "delivery"
        try:
            aligned.mkdir(parents=True)
            (aligned / "frame_001.png").write_bytes(b"frame")
            delivery_dir, delivered = _deliver_export_artifacts(
                canonical, destination, ["aligned"]
            )
            self.assertEqual(delivery_dir, destination.resolve())
            self.assertTrue((destination / "frame_001.png").is_file())
            self.assertEqual(delivered["aligned"], str(destination.resolve()))
            self.assertFalse((destination / "frames_aligned").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_real_export_records_ai_tag_and_custom_delivery(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        jobs_root, canonical, delivery = root / "jobs", root / "canonical", root / "delivery"
        job_id = "synthetic-job"
        job_directory = jobs_root / job_id
        try:
            job_directory.mkdir(parents=True)
            cutout = job_directory / "cutout.png"
            component_map = job_directory / "component_map.png"
            Image.new("RGBA", (6, 8), (40, 120, 220, 255)).save(cutout)
            _encode_ids(np.ones((8, 6), dtype=np.int32)).save(component_map)
            job = {
                "id": job_id,
                "source_path": str(root / "input" / "hero.png"),
                "source_name": "hero.png",
                "engine": {"name": "test"},
                "status": "ready",
                "layout": {"frame_count": 1},
                "config": {"padding": 0, "output_columns": 0},
                "assignments": {"1": 1},
                "review_component_ids": [],
                "files": {"cutout": str(cutout), "component_map": str(component_map)},
            }
            (job_directory / "job.json").write_text(json.dumps(job), encoding="utf-8")

            result = SmartEngine().export_job(
                job_id,
                output_root=canonical,
                jobs_root=jobs_root,
                artifacts=["aligned"],
                destination=delivery,
                ai_tag=True,
            )
            saved = json.loads((job_directory / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(result["delivery_dir"], str(delivery.resolve()))
            self.assertTrue((delivery / "frame_001.png").is_file())
            self.assertEqual(saved["tags"], ["ai"])
            self.assertEqual(saved["exports"][-1]["artifacts"], ["aligned"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_library_trash_is_recoverable_and_keeps_exports(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            input_root, jobs_root, trash_root = root / "input", root / "jobs", root / "trash"
            source = input_root / "hero" / "frame.png"
            job_directory = jobs_root / "job-1"
            exported = root / "output" / "hero" / "frame.png"
            source.parent.mkdir(parents=True)
            job_directory.mkdir(parents=True)
            exported.parent.mkdir(parents=True)
            Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(source)
            (job_directory / "job.json").write_text("{}", encoding="utf-8")
            Image.new("RGBA", (4, 4), (0, 255, 0, 255)).save(exported)

            with patch("gui_server.INPUT_ROOT", input_root), patch("gui_server.JOBS_ROOT", jobs_root), patch("gui_server.TRASH_ROOT", trash_root), patch("gui_server.engine.list_jobs", return_value=[]):
                result = _move_library_items_to_trash(
                    LibraryTrashRequest(source_paths=[str(source)], job_ids=["job-1"])
                )

            self.assertEqual(result["source_count"], 1)
            self.assertEqual(result["job_count"], 1)
            self.assertFalse(source.exists())
            self.assertFalse(job_directory.exists())
            self.assertTrue(exported.exists())
            self.assertTrue((Path(result["trash_dir"]) / "sources" / "hero" / "frame.png").exists())
            self.assertTrue((Path(result["trash_dir"]) / "jobs" / "job-1").is_dir())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_library_trash_accepts_legacy_unicode_related_job_id(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        input_root, jobs_root, trash_root = root / "input", root / "jobs", root / "trash"
        source = input_root / "角色 图.png"
        legacy_job_id = "1788175826-角色 图-3a5acd"
        job_directory = jobs_root / legacy_job_id
        try:
            source.parent.mkdir(parents=True)
            job_directory.mkdir(parents=True)
            Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(source)
            (job_directory / "job.json").write_text("{}", encoding="utf-8")
            jobs = [{"id": legacy_job_id, "source_path": str(source)}]

            with patch("gui_server.INPUT_ROOT", input_root), patch("gui_server.JOBS_ROOT", jobs_root), patch("gui_server.TRASH_ROOT", trash_root), patch("gui_server.engine.list_jobs", return_value=jobs):
                result = _move_library_items_to_trash(
                    LibraryTrashRequest(source_paths=[str(source)], include_related_jobs=True)
                )

            self.assertEqual(result["source_count"], 1)
            self.assertEqual(result["job_count"], 1)
            self.assertTrue((Path(result["trash_dir"]) / "jobs" / legacy_job_id).is_dir())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_job_directory_rejects_path_traversal(self) -> None:
        with self.assertRaises(HTTPException):
            _job_directory("../outside")

    def test_new_job_ids_are_ascii_and_path_safe(self) -> None:
        job_id = make_job_id(Path("角色 动画 01.png"))
        self.assertRegex(job_id, r"^[a-z0-9_-]+$")
        self.assertNotIn("角色", job_id)

    def test_collection_export_uses_latest_analyzed_results_and_reports_gaps(self) -> None:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        collection = root / "input" / "hero"
        collection.mkdir(parents=True)
        for name in ("a.png", "b.png", "c.png"):
            (collection / name).write_bytes(b"image")
        jobs = [
            {"id": "ready-job", "source_path": str(collection / "a.png"), "review_count": 0},
            {"id": "review-job", "source_path": str(collection / "b.png"), "review_count": 2},
        ]
        export_result = {
            "output_dir": str(root / "output" / "hero" / "a"),
            "delivery_dir": str(root / "output" / "hero" / "a" / "frames_aligned"),
            "artifacts": ["aligned"],
            "sprite_sheet": "sheet.png",
            "manifest": "manifest.json",
            "frame_count": 6,
            "status": "ready",
        }
        try:
            with patch("gui_server.INPUT_ROOT", root / "input"), patch("gui_server.SMART_OUTPUT_ROOT", root / "output"), patch("gui_server.EXPORT_PROFILES_PATH", root / "profiles.json"), patch("gui_server.engine.list_jobs", return_value=jobs), patch("gui_server.engine.export_job", return_value=export_result) as export_job:
                result = _export_collection(CollectionExportRequest(collection="hero"))

            self.assertEqual(result["exported_count"], 1)
            self.assertEqual(result["missing"], ["c.png"])
            self.assertEqual(result["skipped_review_count"], 1)
            export_job.assert_called_once_with(
                "ready-job",
                artifacts=["aligned"],
                destination=None,
                destination_subdir=None,
                ai_tag=False,
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
