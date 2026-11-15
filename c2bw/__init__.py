"""
c2bw - 智能图像预处理与重构工具包
"""

__version__ = "4.3"
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


def main():
    """延迟导入桌面宿主：多进程工作进程会导入本包，避免其加载 pywebview/tkinter 等界面依赖。"""
    from c2bw.desktop import main as _desktop_main
    return _desktop_main()

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


