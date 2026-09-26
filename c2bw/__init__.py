"""
c2bw - 智能图像预处理与重构工具包
"""

__version__ = "4.0"
__author__ = "weiceng © 漢籍合璧"

from c2bw.core import (
    calculate_otsu_threshold,
    wolf_binarize,
    WOLF_PRESETS,
    process_single_image,
    save_image,
    get_jp2_save_options,
    build_single_pdf,
    clean_pdf_watermarks,
    extract_images_from_pdf,
    extract_pdf_bookmarks,
    build_pdf_page_mapping,
    apply_bookmarks_to_writer,
)
from c2bw.service import ImageProcessorService
from c2bw.desktop import main

__all__ = [
    "__version__",
    "__author__",
    "calculate_otsu_threshold",
    "wolf_binarize",
    "WOLF_PRESETS",
    "process_single_image",
    "save_image",
    "get_jp2_save_options",
    "build_single_pdf",
    "clean_pdf_watermarks",
    "extract_images_from_pdf",
    "extract_pdf_bookmarks",
    "build_pdf_page_mapping",
    "apply_bookmarks_to_writer",
    "ImageProcessorService",
    "main",
]


