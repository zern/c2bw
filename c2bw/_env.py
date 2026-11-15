# -*- coding: utf-8 -*-
"""
运行环境初始化辅助模块。
用于在 Nuitka / PyInstaller 单文件打包模式下将临时解压目录加入 sys.path 与 DLL 搜索路径。
"""

import glob
import os
import sys


def setup_bundle_environment(caller_file=None):
    """兼容 Nuitka / PyInstaller 单文件打包运行环境：将临时解压目录加入 sys.path 与 DLL 搜索路径。"""
    bundle_dirs = []

    # 1. PyInstaller 模式
    if hasattr(sys, '_MEIPASS'):
        bundle_dirs.append(getattr(sys, '_MEIPASS'))

    # 2. Nuitka __compiled__ 结构体模式
    compiled_info = getattr(sys.modules.get('__main__'), '__compiled__', None) or globals().get('__compiled__')
    if compiled_info is not None:
        if hasattr(compiled_info, 'containing_dir') and compiled_info.containing_dir:
            bundle_dirs.append(compiled_info.containing_dir)
        elif len(compiled_info) > 4 and compiled_info[4]:
            bundle_dirs.append(compiled_info[4])

    # 3. 环境变量与脚本目录
    if os.environ.get("NUITKA_ONEFILE_DIRECTORY"):
        bundle_dirs.append(os.environ.get("NUITKA_ONEFILE_DIRECTORY"))
    if caller_file:
        bundle_dirs.append(os.path.dirname(os.path.abspath(caller_file)))

    # 4. 可执行文件同级目录
    bundle_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))

    # 5. TEMP 扫描保底：仅当未获得有效解压目录时作为后备，且仅取最新的单个目录
    if not os.environ.get("NUITKA_ONEFILE_DIRECTORY") and not hasattr(sys, '_MEIPASS'):
        temp_dir = os.environ.get('TEMP')
        if temp_dir and os.path.exists(temp_dir):
            matches = sorted(glob.glob(os.path.join(temp_dir, 'onefile_*')), key=os.path.getmtime, reverse=True)
            if matches:
                bundle_dirs.append(matches[0])

    for bdir in bundle_dirs:
        if bdir and os.path.exists(bdir):
            if bdir not in sys.path:
                sys.path.insert(0, bdir)
            mpdir = os.path.join(bdir, 'pymupdf')
            if hasattr(os, 'add_dll_directory'):
                try:
                    os.add_dll_directory(bdir)
                except OSError:
                    pass
                if os.path.exists(mpdir):
                    try:
                        os.add_dll_directory(mpdir)
                    except OSError:
                        pass
            os.environ['PATH'] = bdir + os.pathsep + mpdir + os.pathsep + os.environ.get('PATH', '')
