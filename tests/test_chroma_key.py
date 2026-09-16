from __future__ import annotations

import unittest

import numpy as np
from PIL import Image

from smart_engine import _adaptive_chroma_cleanup, _detect_chroma_screen


class AdaptiveChromaTests(unittest.TestCase):
    @staticmethod
    def green_screen_fixture() -> tuple[Image.Image, Image.Image, np.ndarray]:
        source = np.full((96, 96, 4), (12, 236, 16, 255), dtype=np.uint8)
        source[18:78, 18:78, :3] = (31, 27, 34)
        source[44:52, 44:52, :3] = (14, 232, 18)

        current = source.copy()
        current[:, :, 3] = 0
        current[18:78, 18:78, 3] = 255
        # Simulate a semantic model that incorrectly filled the enclosed hole.
        semantic = np.zeros((96, 96), dtype=np.uint8)
        semantic[18:78, 18:78] = 255
        return Image.fromarray(source, "RGBA"), Image.fromarray(current, "RGBA"), semantic

    def test_detects_uniform_saturated_green_screen(self) -> None:
        source, _, _ = self.green_screen_fixture()
        profile = _detect_chroma_screen(source)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["kind"], "green")

    def test_removes_enclosed_green_even_when_semantic_mask_fills_it(self) -> None:
        source, current, semantic = self.green_screen_fixture()
        result, metrics = _adaptive_chroma_cleanup(
            source, current, True, "standard", semantic
        )
        alpha = np.asarray(result.getchannel("A"), dtype=np.uint8)

        self.assertTrue(metrics["applied"])
        self.assertGreaterEqual(metrics["enclosed_regions_removed"], 1)
        self.assertEqual(int(alpha[48, 48]), 0)
        self.assertEqual(int(alpha[30, 30]), 255)

    def test_disabled_mode_preserves_original_alpha(self) -> None:
        source, current, semantic = self.green_screen_fixture()
        result, metrics = _adaptive_chroma_cleanup(
            source, current, False, "standard", semantic
        )
        self.assertFalse(metrics["applied"])
        np.testing.assert_array_equal(
            np.asarray(result.getchannel("A")), np.asarray(current.getchannel("A"))
        )

    def test_plain_white_background_does_not_activate_chroma_key(self) -> None:
        source = Image.new("RGBA", (64, 64), "white")
        current = Image.new("RGBA", (64, 64), (20, 30, 40, 255))
        result, metrics = _adaptive_chroma_cleanup(source, current, True, "strong")
        self.assertFalse(metrics["detected"])
        np.testing.assert_array_equal(np.asarray(result), np.asarray(current))

    def test_region_scope_does_not_change_pixels_outside_lasso(self) -> None:
        source, current, semantic = self.green_screen_fixture()
        region = np.zeros((96, 96), dtype=bool)
        region[38:58, 38:58] = True
        result, _ = _adaptive_chroma_cleanup(
            source, current, True, "standard", semantic, region
        )
        alpha = np.asarray(result.getchannel("A"), dtype=np.uint8)
        self.assertEqual(int(alpha[48, 48]), 0)
        self.assertEqual(int(alpha[30, 30]), 255)


if __name__ == "__main__":
    unittest.main()
