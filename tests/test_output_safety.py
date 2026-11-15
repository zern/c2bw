# -*- coding: utf-8 -*-
"""取消清理不误删既有文件、写锁不串行化编码的回归测试。"""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, TiffImagePlugin

import c2bw.core as core
from c2bw.core import clean_cancelled_output, save_image_atomically
from c2bw.service import ImageProcessorService, _unique_task_dir


class TestUniqueTaskDir(unittest.TestCase):
    def test_reuses_missing_or_empty_dir_and_skips_occupied(self):
        with tempfile.TemporaryDirectory() as base:
            self.assertEqual(_unique_task_dir(base, "book"), os.path.join(base, "book"))
            os.makedirs(os.path.join(base, "book"))
            self.assertEqual(_unique_task_dir(base, "book"), os.path.join(base, "book"))

            with open(os.path.join(base, "book", "old.pdf"), "wb") as f:
                f.write(b"x")
            os.makedirs(os.path.join(base, "book (2)"))
            with open(os.path.join(base, "book (2)", "old.tif"), "wb") as f:
                f.write(b"x")
            with open(os.path.join(base, "book (3)"), "wb") as f:  # 同名普通文件
                f.write(b"x")
            self.assertEqual(_unique_task_dir(base, "book"), os.path.join(base, "book (4)"))

    def test_validate_pdf_settings_never_targets_existing_results(self):
        with tempfile.TemporaryDirectory() as base:
            pdf_path = os.path.join(base, "book.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.5\n")
            service = ImageProcessorService()
            first, err = service.validate_pdf_settings({"pdf_path": pdf_path})
            self.assertIsNone(err)
            os.makedirs(first["clean_dir"])
            keep = os.path.join(first["clean_dir"], "previous_result.pdf")
            with open(keep, "wb") as f:
                f.write(b"old")

            second, err = service.validate_pdf_settings({"pdf_path": pdf_path})
            self.assertIsNone(err)
            self.assertNotEqual(
                os.path.normcase(second["clean_dir"]), os.path.normcase(first["clean_dir"])
            )
            # 模拟第二次任务被取消
            clean_cancelled_output(second["clean_dir"])
            self.assertTrue(os.path.isfile(keep))


class TestCancelCleanup(unittest.TestCase):
    def test_preserve_names_keeps_preexisting_entries(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("mine.jpg", "notes.txt"):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(b"x")
            os.makedirs(os.path.join(d, "mydir"))
            preexisting = set(os.listdir(d))
            with open(os.path.join(d, "page_0001.jpg"), "wb") as f:
                f.write(b"new")
            os.makedirs(os.path.join(d, "new_sub"))

            clean_cancelled_output(d, preserve_names=preexisting)
            self.assertEqual(set(os.listdir(d)), preexisting)

    def test_extraction_cancel_keeps_user_files(self):
        with tempfile.TemporaryDirectory() as d:
            pdf_path = os.path.join(d, "book.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.5\n")
            extract_dir = os.path.join(d, "book")
            os.makedirs(extract_dir)
            user_file = os.path.join(extract_dir, "user.jpg")
            with open(user_file, "wb") as f:
                f.write(b"x")

            service = ImageProcessorService()

            def fake_clean(path, progress_callback=None, cancel_event=None, deep_analysis=False):
                with open(os.path.join(extract_dir, "partial.tmp"), "wb") as f:
                    f.write(b"x")
                cancel_event.set()
                return path, 0, None

            with patch("c2bw.service.clean_pdf_watermarks", side_effect=fake_clean):
                self.assertTrue(service.start_pdf_extraction(pdf_path, extract_dir)["ok"])
                deadline = time.time() + 5
                while service.is_processing and time.time() < deadline:
                    time.sleep(0.01)

            self.assertTrue(os.path.isfile(user_file))
            self.assertFalse(os.path.exists(os.path.join(extract_dir, "partial.tmp")))


class TestWriteLockScope(unittest.TestCase):
    def test_encoding_happens_outside_write_lock(self):
        lock = threading.Lock()
        held_during_encode = []
        original_save = Image.Image.save

        def spy_save(img, fp, *args, **kwargs):
            if not isinstance(fp, (str, bytes, os.PathLike)):
                held_during_encode.append(lock.locked())
            return original_save(img, fp, *args, **kwargs)

        with tempfile.TemporaryDirectory() as d:
            img = Image.new("1", (64, 64), 1)
            out = os.path.join(d, "a.tif")
            info = TiffImagePlugin.ImageFileDirectory_v2()
            info[278] = 64
            with patch.object(Image.Image, "save", spy_save):
                save_image_atomically(img, out, "TIFF", write_lock=lock,
                                      compression="group4", tiffinfo=info)
            self.assertEqual(held_during_encode, [False])
            with Image.open(out) as reread:
                self.assertEqual(reread.info.get("compression"), "group4")
                self.assertEqual(reread.size, (64, 64))
            self.assertEqual(os.listdir(d), ["a.tif"])

    def test_formats_roundtrip_through_buffer(self):
        with tempfile.TemporaryDirectory() as d:
            rgb = Image.new("RGB", (40, 30), (200, 100, 50))
            for name, fmt, opts in (
                ("a.jpg", "JPEG", {"quality": 80, "progressive": True, "optimize": True, "dpi": (300, 300)}),
                ("a.jp2", "JPEG2000", {}),
                ("a.png", "PNG", {}),
            ):
                out = os.path.join(d, name)
                save_image_atomically(rgb, out, fmt, write_lock=threading.Lock(), **opts)
                with Image.open(out) as reread:
                    self.assertEqual(reread.size, (40, 30))


if __name__ == "__main__":
    unittest.main()
