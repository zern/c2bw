# -*- coding: utf-8 -*-
"""
c2bw 桌面应用启动入口
"""
import os
import sys

def _setup_bundle_environment():
    """兼容 Nuitka / PyInstaller 单文件打包运行环境：将临时解压目录加入 sys.path 与 DLL 搜索路径。"""
    bundle_dirs = []
    
    # 1. PyInstaller 模式
    if hasattr(sys, '_MEIPASS'):
        bundle_dirs.append(getattr(sys, '_MEIPASS'))
        
    # 2. Nuitka __compiled__ 结构体模式（Nuitka 官方标准解压目录位置）
    compiled_info = getattr(sys.modules.get('__main__'), '__compiled__', None) or globals().get('__compiled__')
    if compiled_info is not None:
        if hasattr(compiled_info, 'containing_dir') and compiled_info.containing_dir:
            bundle_dirs.append(compiled_info.containing_dir)
        elif len(compiled_info) > 4 and compiled_info[4]:
            bundle_dirs.append(compiled_info[4])
            
    # 3. 环境变量与当前脚本目录
    if os.environ.get("NUITKA_ONEFILE_DIRECTORY"):
        bundle_dirs.append(os.environ.get("NUITKA_ONEFILE_DIRECTORY"))
    if '__file__' in globals():
        bundle_dirs.append(os.path.dirname(os.path.abspath(__file__)))
        
    # 4. 可执行文件同级目录
    bundle_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    
    # 5. TEMP 扫描保底：仅当未获得有效解压目录时作为后备，且仅取最新的单个目录
    if not os.environ.get("NUITKA_ONEFILE_DIRECTORY") and not hasattr(sys, '_MEIPASS'):
        temp_dir = os.environ.get('TEMP')
        if temp_dir and os.path.exists(temp_dir):
            import glob
            matches = sorted(glob.glob(os.path.join(temp_dir, 'onefile_*')), key=os.path.getmtime, reverse=True)
            if matches:
                bundle_dirs.append(matches[0])

    for _bdir in bundle_dirs:
        if _bdir and os.path.exists(_bdir):
            if _bdir not in sys.path:
                sys.path.insert(0, _bdir)
            _mpdir = os.path.join(_bdir, 'pymupdf')
            if hasattr(os, 'add_dll_directory'):
                try:
                    os.add_dll_directory(_bdir)
                except Exception:
                    pass
                if os.path.exists(_mpdir):
                    try:
                        os.add_dll_directory(_mpdir)
                    except Exception:
                        pass
            os.environ['PATH'] = _bdir + os.pathsep + _mpdir + os.pathsep + os.environ.get('PATH', '')

_setup_bundle_environment()

import multiprocessing

if __name__ == '__main__':
    multiprocessing.freeze_support()
    
    # 支持单文件自测模式：可执行文件直接执行真实提取检验
    if len(sys.argv) > 1 and sys.argv[1] == '--test-extract':
        pdf_file = sys.argv[2] if len(sys.argv) > 2 else 'GJ2312405.pdf'
        output_folder = sys.argv[3] if len(sys.argv) > 3 else 'test_output_exe'
        from c2bw.core import extract_images_from_pdf, fitz, _fitz_import_error
        res = [
            f"[TEST] Python sys.path: {sys.path[:4]}",
            f"[TEST] Fitz module: {fitz}",
            f"[TEST] Fitz version: {getattr(fitz, '__version__', None)}",
            f"[TEST] Import error: {_fitz_import_error}",
        ]
        cnt, err = extract_images_from_pdf(pdf_file, output_folder)
        res.append(f"[TEST] Extracted count: {cnt}, error: {err}")
        print("\n".join(res))
        with open("test_extract_result.txt", "w", encoding="utf-8") as rf:
            rf.write("\n".join(res) + "\n")
        sys.exit(0)

    from c2bw.desktop import main
    main()
