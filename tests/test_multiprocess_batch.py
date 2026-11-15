# -*- coding: utf-8 -*-
"""图像批处理多进程调度的回归测试。"""
import concurrent.futures
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from c2bw.core import process_single_image
from c2bw.service import ImageProcessorService


def _crash_worker(*_args):
    os._exit(3)  # 模拟工作进程崩溃（如内存耗尽被系统杀掉）


def _make_tasks(src_dir, count, size=(400, 300)):
    tasks = []
    for i in range(count):
        name = f"p{i:03d}.png"
        path = os.path.join(src_dir, name)
        img = Image.effect_noise(size, 60).convert("L")
        img.save(path)
        tasks.append((path, name, name, f"p{i:03d}"))
    return tasks


def _settings(target_dir, **extra):
    settings = {
        "target_dir": target_dir,
        "max_threads": 4,
        "enable_binarize": True,
        "invert_binarize": False,
        "bin_method": "0",
        "enable_crop": True,
        "crop_percent": 50,
        "crop_direction": "R2L",
        "exclude_ratio": 0.8,
        "size_opt_mode": "original",
    }
    settings.update(extra)
    return settings


class TestImageBatch(unittest.TestCase):
    def test_cpu_heavy_batch_runs_in_processes(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            tasks = _make_tasks(src, 6)
            service = ImageProcessorService()
            stats = service._run_image_batch(tasks, _settings(dst))
            self.assertTrue(stats["used_processes"])
            self.assertEqual(stats["succeeded"], 6)
            self.assertEqual(stats["errors"], [])
            self.assertEqual(stats["cropped_double_count"], 6)
            outputs = sorted(os.listdir(dst))
            self.assertEqual(len(outputs), 12)
            self.assertTrue(all(name.endswith(".tif") for name in outputs))

    def test_copy_only_batch_stays_on_threads(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            tasks = _make_tasks(src, 6)
            service = ImageProcessorService()
            stats = service._run_image_batch(
                tasks, _settings(dst, enable_binarize=False, enable_crop=False)
            )
            self.assertFalse(stats["used_processes"])
            self.assertEqual(stats["succeeded"], 6)

    def test_env_switch_disables_processes(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            tasks = _make_tasks(src, 6)
            with patch.dict(os.environ, {"C2BW_DISABLE_MULTIPROCESS": "1"}):
                stats = ImageProcessorService()._run_image_batch(tasks, _settings(dst))
            self.assertFalse(stats["used_processes"])
            self.assertEqual(stats["succeeded"], 6)

    def test_broken_process_pool_falls_back_to_threads(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            tasks = _make_tasks(src, 6)
            service = ImageProcessorService()

            def crashing_executor(settings, task_count):
                return (
                    concurrent.futures.ProcessPoolExecutor(max_workers=2),
                    _crash_worker, True, 2,
                )

            with patch.object(service, "_create_image_executor", side_effect=crashing_executor):
                stats = service._run_image_batch(tasks, _settings(dst))
            self.assertFalse(stats["used_processes"])
            self.assertEqual(stats["processed"], 6)
            self.assertEqual(stats["succeeded"], 6)
            self.assertEqual(len(os.listdir(dst)), 12)

    def test_cancel_stops_dispatching(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            tasks = _make_tasks(src, 20, size=(40, 30))
            service = ImageProcessorService()
            calls = []
            lock = threading.Lock()

            def worker(*args):
                with lock:
                    calls.append(args[1])
                    if len(calls) == 3:
                        service.cancel_event.set()
                return process_single_image(*args, cancel_event=service.cancel_event)

            with patch.dict(os.environ, {"C2BW_DISABLE_MULTIPROCESS": "1"}), \
                    patch.object(service, "process_single_image", side_effect=worker):
                stats = service._run_image_batch(tasks, _settings(dst, max_threads=1))
            self.assertLess(len(calls), 20)
            self.assertLess(stats["processed"], 20)

    def test_pause_holds_dispatch_until_resumed(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            tasks = _make_tasks(src, 8, size=(40, 30))
            service = ImageProcessorService()
            service.pause_event.clear()
            timer = threading.Timer(0.5, service.pause_event.set)
            timer.start()
            try:
                stats = service._run_image_batch(tasks, _settings(dst))
            finally:
                timer.cancel()
            self.assertEqual(stats["succeeded"], 8)


if __name__ == "__main__":
    unittest.main()
