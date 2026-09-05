from __future__ import annotations

import importlib.util
import shutil
import unittest
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = PROJECT_ROOT / "skills" / "ai-sprite-workflow" / "scripts" / "sprite_workflow_client.py"
SPEC = importlib.util.spec_from_file_location("sprite_workflow_client", CLIENT_PATH)
assert SPEC and SPEC.loader
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


class SkillClientTests(unittest.TestCase):
    def runtime_root(self) -> Path:
        root = PROJECT_ROOT / "tests" / "_runtime" / uuid.uuid4().hex
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def test_process_folder_uses_stable_recursive_image_order(self) -> None:
        root = self.runtime_root()
        project = root / "project"
        folder = project / "input" / "hero"
        nested = folder / "attack"
        nested.mkdir(parents=True)
        (folder / "b.png").write_bytes(b"b")
        (folder / "a.png").write_bytes(b"a")
        (nested / "c.jpg").write_bytes(b"c")
        (nested / "ignore.txt").write_text("ignore", encoding="utf-8")

        result = CLIENT.prepare_folder_sources(str(folder), project, False)

        self.assertEqual(
            [path.relative_to(folder).as_posix() for path in result],
            ["a.png", "attack/c.jpg", "b.png"],
        )

    def test_external_folder_requires_explicit_import(self) -> None:
        root = self.runtime_root()
        project = root / "project"
        external = root / "external"
        external.mkdir()
        (external / "frame.png").write_bytes(b"image")

        with self.assertRaises(CLIENT.ClientError):
            CLIENT.prepare_folder_sources(str(external), project, False)

    def test_cleanup_is_plan_only_without_confirm(self) -> None:
        args = CLIENT.build_parser().parse_args(
            ["trash", "--source", "input/hero/frame.png"]
        )
        self.assertFalse(args.confirm)
        self.assertEqual(args.source_paths, ["input/hero/frame.png"])

    def test_export_defaults_to_aligned_and_accepts_custom_output(self) -> None:
        root = self.runtime_root()
        destination = root / "game" / "hero"
        args = CLIENT.build_parser().parse_args(
            ["export", "job-1", "--output", str(destination)]
        )
        self.assertEqual(CLIENT.requested_artifacts(args), ["aligned"])
        self.assertEqual(CLIENT.requested_output(args), str(destination.resolve()))

    def test_export_all_selects_complete_bundle(self) -> None:
        args = CLIENT.build_parser().parse_args(["export-folder", "hero", "--all"])
        self.assertEqual(CLIENT.requested_artifacts(args), list(CLIENT.EXPORT_ARTIFACTS))

    def test_process_payload_marks_ai_jobs(self) -> None:
        args = CLIENT.build_parser().parse_args(["process", "sheet.png", "--analyze-only"])
        payload = CLIENT.process_payload(args, Path("sheet.png"))
        self.assertTrue(payload["ai_tag"])
        self.assertNotIn("artifacts", payload)
        self.assertNotIn("output_path", payload)

    def test_process_payload_only_overrides_remembered_export_when_requested(self) -> None:
        root = self.runtime_root()
        destination = root / "game" / "hero"
        args = CLIENT.build_parser().parse_args(
            ["process", "sheet.png", "--output", str(destination), "--artifact", "trimmed"]
        )
        payload = CLIENT.process_payload(args, Path("sheet.png"))
        self.assertEqual(payload["output_path"], str(destination.resolve()))
        self.assertEqual(payload["artifacts"], ["trimmed"])

    def test_import_folder_command_is_explicit_and_configurable(self) -> None:
        args = CLIENT.build_parser().parse_args(
            ["import-folder", r"F:\generated\hero", "--collection", "hero-v2", "--overwrite"]
        )
        self.assertEqual(args.command, "import-folder")
        self.assertEqual(args.collection, "hero-v2")
        self.assertTrue(args.overwrite)


if __name__ == "__main__":
    unittest.main()
