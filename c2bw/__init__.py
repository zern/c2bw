"""
c2bw - 智能图像预处理与重构工具包
"""

__version__ = "3.6"
__author__ = "weiceng © 漢籍合璧"

from c2bw.core import (
    calculate_otsu_threshold,
    process_single_image,
    save_image,
    build_single_pdf,
    extract_images_from_pdf,
)
from c2bw.service import ImageProcessorService
from c2bw.desktop import main

__all__ = [
    "__version__",
    "__author__",
    "calculate_otsu_threshold",
    "process_single_image",
    "save_image",
    "build_single_pdf",
    "extract_images_from_pdf",
    "ImageProcessorService",
    "main",
]


