from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from smart_engine import PROJECT_ROOT, SmartConfig, SmartEngine


class _SyntheticRemover:
    def remove(self, source: Image.Image) -> Image.Image:
        rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8).copy()
        dark_foreground = np.mean(rgba[:, :, :3], axis=2) < 96
        rgba[:, :, 3] = np.where(dark_foreground, 255, 0).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")


class LocalRefineTests(unittest.TestCase):
    def test_semantic_mask_uses_the_same_padded_crop_as_the_source(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "workspace") as temporary:
            jobs_root = Path(temporary)
            job_id = "local-refine-regression"
            job_dir = jobs_root / job_id
            job_dir.mkdir()

            source = Image.new("RGBA", (128, 128), "white")
            draw = ImageDraw.Draw(source)
            draw.rectangle((34, 34, 94, 94), outline="black", width=8)
            source_path = job_dir / "source.png"
            source.save(source_path)

            initial = _SyntheticRemover().remove(source)
            initial_path = job_dir / "cutout_initial.png"
            cutout_path = job_dir / "cutout.png"
            semantic_path = job_dir / "semantic_mask.png"
            foreground_path = job_dir / "foreground_mask.png"
            component_path = job_dir / "component_map.png"
            overlay_path = job_dir / "overlay.png"
            initial.save(initial_path)
            initial.save(cutout_path)
            initial.getchannel("A").save(semantic_path)
            initial.getchannel("A").save(foreground_path)
            Image.new("RGB", source.size).save(component_path)
            source.save(overlay_path)

            config = SmartConfig(background_mode="auto")
            job = {
                "id": job_id,
                "source_name": source_path.name,
                "source_path": str(source_path),
                "config": asdict(config),
                "engine": {},
                "files": {
                    "initial_cutout": str(initial_path),
                    "cutout": str(cutout_path),
                    "semantic_mask": str(semantic_path),
                    "foreground_mask": str(foreground_path),
                    "component_map": str(component_path),
                    "overlay": str(overlay_path),
                },
            }
            (job_dir / "job.json").write_text(
                json.dumps(job, ensure_ascii=False), encoding="utf-8"
            )

            engine = SmartEngine()
            engine._remover = lambda _config: _SyntheticRemover()  # type: ignore[method-assign]
            result = engine.refine_local_region(
                job_id,
                [(38, 38), (90, 38), (90, 90), (38, 90)],
                "standard",
                18.0,
                3,
                4,
                jobs_root=jobs_root,
            )

            self.assertEqual(result["refinement"]["last_local_refine_strength"], "standard")
            self.assertTrue(Path(result["files"]["cutout"]).exists())


if __name__ == "__main__":
    unittest.main()
