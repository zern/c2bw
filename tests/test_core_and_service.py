# -*- coding: utf-8 -*-
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from c2bw.core import (
    _wrap_g4_stream_as_tiff,
    assign_output_stems,
    calculate_otsu_threshold,
    get_normalized_dpi,
    natural_sort_key,
    wolf_binarize,
)
from c2bw.service import (
    ImageProcessorService,
    compare_versions,
    parse_version_tuple,
)


class TestCoreUtilities(unittest.TestCase):
    def test_otsu_threshold_bimodal(self):
        img = Image.new("L", (20, 10), 20)
        for x in range(10, 20):
            for y in range(10):
                img.putpixel((x, y), 230)
        thresh = calculate_otsu_threshold(img)
        self.assertGreaterEqual(thresh, 20)
        self.assertLessEqual(thresh, 230)

    def test_wolf_binarize_output_mode_and_size(self):
        img = Image.new("L", (32, 32), 240)
        for x in range(8, 24):
            for y in range(8, 24):
                img.putpixel((x, y), 10)
        out = wolf_binarize(img, window=15, k=0.2)
        self.assertEqual(out.mode, "1")
        self.assertEqual(out.size, (32, 32))

    def test_natural_sort_key(self):
        items = ["page_10.png", "page_2.png", "page_1.png"]
        self.assertEqual(
            sorted(items, key=natural_sort_key),
            ["page_1.png", "page_2.png", "page_10.png"],
        )

    def test_get_normalized_dpi_fallback_and_clamping(self):
        img = Image.new("RGB", (10, 10))
        self.assertEqual(get_normalized_dpi(img), (300.0, 300.0))
        img.info["dpi"] = (150.456, 600.2)
        self.assertEqual(get_normalized_dpi(img), (150.46, 600.2))
        img.info["dpi"] = (1, 1)
        self.assertEqual(get_normalized_dpi(img), (300.0, 300.0))

    def test_assign_output_stems_disambiguates_collisions(self):
        tasks = [
            ("/src/a.jpg", "a.jpg", "a.jpg"),
            ("/src/a.png", "a.png", "a.png"),
            ("/src/b.jpg", "b.jpg", "b.jpg"),
        ]
        assigned, collision_groups = assign_output_stems(tasks)
        stems = [item[3] for item in assigned]
        self.assertEqual(collision_groups, 1)
        self.assertEqual(len(set(stems)), 3)
        self.assertIn("b", stems)


class TestCCITTStreamWrapping(unittest.TestCase):
    """CCITT G4 直封 TIFF：必须与重新编码路径解码出完全相同的像素。"""

    def _make_g4_tiff(self, path):
        img = Image.new("1", (64, 48), 255)
        for x in range(8, 24):
            for y in range(6, 40):
                img.putpixel((x, y), 0)
        img.save(path, format="TIFF", compression="group4")
        return path

    def test_wrap_stream_roundtrip_matches_original_pixels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_g4_tiff(os.path.join(tmpdir, "page.tif"))
            with Image.open(path) as src:
                width, height = src.size
                offsets = src.tag_v2[273]
                counts = src.tag_v2[279]
                offsets = offsets[0] if isinstance(offsets, (list, tuple)) else offsets
                counts = counts[0] if isinstance(counts, (list, tuple)) else counts
                with open(path, "rb") as handle:
                    handle.seek(int(offsets))
                    ccitt_data = handle.read(int(counts))
                expected = list(src.convert("L").tobytes())

            wrapped = _wrap_g4_stream_as_tiff(ccitt_data, width, height, photometric=1, dpi=(300, 300))
            with Image.open(io.BytesIO(wrapped)) as out:
                self.assertEqual(out.size, (width, height))
                self.assertEqual(out.tag_v2[259], 4)
                self.assertEqual(out.tag_v2[278], height)
                self.assertEqual(tuple(round(v) for v in out.info["dpi"]), (300, 300))
                self.assertEqual(list(out.convert("L").tobytes()), expected)

    def test_wrap_stream_photometric_zero_inverts_polarity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_g4_tiff(os.path.join(tmpdir, "page.tif"))
            with Image.open(path) as src:
                width, height = src.size
                offsets = src.tag_v2[273]
                counts = src.tag_v2[279]
                offsets = offsets[0] if isinstance(offsets, (list, tuple)) else offsets
                counts = counts[0] if isinstance(counts, (list, tuple)) else counts
                with open(path, "rb") as handle:
                    handle.seek(int(offsets))
                    ccitt_data = handle.read(int(counts))
                normal = list(src.convert("L").tobytes())

            inverted = _wrap_g4_stream_as_tiff(ccitt_data, width, height, photometric=0)
            with Image.open(io.BytesIO(inverted)) as out:
                self.assertEqual(out.tag_v2[262], 0)
                # WhiteIsZero（262=0）与 BlackIsZero 恰好反相，正对应 PDF 的 /BlackIs1 false
                self.assertEqual(list(out.convert("L").tobytes()), [255 - v for v in normal])


class TestServiceStateAndHelpers(unittest.TestCase):
    def test_version_comparison(self):
        self.assertEqual(parse_version_tuple("v4.2.1"), (4, 2, 1))
        self.assertEqual(compare_versions("4.2", "4.2.0"), 0)
        self.assertEqual(compare_versions("4.2", "4.3"), -1)
        self.assertEqual(compare_versions("4.3", "4.2.9"), 1)

    def test_poll_events_does_not_infer_phase_from_status_text(self):
        svc = ImageProcessorService()
        with svc.state_lock:
            svc.is_processing = True
            svc.phase = "extracting_pdf"

        svc.ui_events.put(("status", "正在检查 PDF 水印..."))
        res = svc.poll_events()
        self.assertEqual(res["phase"], "extracting_pdf")

        svc.ui_events.put(("phase", "pdf"))
        res = svc.poll_events()
        self.assertEqual(res["phase"], "pdf")

    def test_start_pdf_extraction_resets_state_on_exception(self):
        svc = ImageProcessorService()
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "sample.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n")
            extract_dir = os.path.join(tmpdir, "out")

            with patch("c2bw.service.clean_pdf_watermarks", side_effect=RuntimeError("boom")):
                res = svc.start_pdf_extraction(pdf_path, extract_dir)
                self.assertTrue(res["ok"])

                # Wait briefly for daemon thread to finish
                event = svc.ui_events.get(timeout=3)
                while event[0] == "status":
                    event = svc.ui_events.get(timeout=3)

                self.assertEqual(event[0], "pdf_extracted")
                self.assertEqual(event[3], 0)
                self.assertIn("boom", event[4])
                self.assertFalse(svc.is_processing)
                self.assertEqual(svc.phase, "idle")

    def test_cancel_processing_with_pending_pdf(self):
        svc = ImageProcessorService()
        with tempfile.TemporaryDirectory() as tmpdir:
            target_dir = os.path.join(tmpdir, "out")
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, "page.png"), "wb") as f:
                f.write(b"data")

            with svc.state_lock:
                svc.is_processing = True
                svc.phase = "awaiting_pdf"
                svc.pending_pdf = ({"target_dir": target_dir, "lang": "zh-CN"}, {})

            res = svc.cancel_processing()
            self.assertTrue(res["ok"])
            self.assertFalse(os.path.exists(target_dir))

            polled = svc.poll_events()
            self.assertFalse(polled["processing"])
            self.assertEqual(polled["phase"], "idle")
            self.assertEqual(polled["events"][-1]["type"], "finish")


if __name__ == "__main__":
    unittest.main()
