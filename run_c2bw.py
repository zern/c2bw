# -*- coding: utf-8 -*-
"""
c2bw 桌面应用启动入口
"""
import os
import sys

from c2bw._env import setup_bundle_environment

setup_bundle_environment(__file__ if '__file__' in globals() else None)

import multiprocessing

if __name__ == '__main__':
    multiprocessing.freeze_support()
    
    # 支持单文件自测模式：可执行文件直接执行真实提取检验
    if len(sys.argv) > 1 and sys.argv[1].startswith('--test-extract'):
        deep_mode = '--deep' in sys.argv or sys.argv[1] == '--test-extract-deep'
        pdf_file = 'ssjzs.pdf'
        output_folder = 'test_output_exe'
        args = [a for a in sys.argv[2:] if not a.startswith('--')]
        if len(args) > 0:
            pdf_file = args[0]
        if len(args) > 1:
            output_folder = args[1]
        from c2bw.core import extract_images_from_pdf, fitz, _fitz_import_error
        res = [
            f"[TEST] Python sys.path: {sys.path[:4]}",
            f"[TEST] Fitz module: {fitz}",
            f"[TEST] Fitz version: {getattr(fitz, '__version__', None)}",
            f"[TEST] Import error: {_fitz_import_error}",
            f"[TEST] Deep analysis: {deep_mode}",
        ]
        cnt, err = extract_images_from_pdf(pdf_file, output_folder, deep_analysis=deep_mode)
        res.append(f"[TEST] Extracted count: {cnt}, error: {err}")
        print("\n".join(res))
        with open("test_extract_result.txt", "w", encoding="utf-8") as rf:
            rf.write("\n".join(res) + "\n")
        sys.exit(0)

    from c2bw.desktop import main
    main()
