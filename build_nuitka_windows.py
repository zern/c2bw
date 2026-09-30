# -*- coding: utf-8 -*-
"""
Windows 原生 Nuitka 独立可执行文件构建与版本元数据校验脚本
彻底解决 Windows 批处理/PowerShell 代码页导致的中文版本信息损坏或丢失问题。
"""

import os
import sys
import subprocess
import shutil
import ctypes
from ctypes import wintypes

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def inspect_pe_version_info(filepath):
    """使用 Windows 原生 Version API 读取 PE 文件的版本元数据信息。"""
    if not os.path.exists(filepath):
        return {}

    size = ctypes.windll.version.GetFileVersionInfoSizeW(filepath, None)
    if size == 0:
        return {}

    res = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(filepath, 0, size, res):
        return {}

    info = {}
    keys = [
        "CompanyName",
        "FileDescription",
        "FileVersion",
        "ProductName",
        "ProductVersion",
        "LegalCopyright",
        "OriginalFilename",
    ]
    # 尝试不同语言代码页：000004b0 (Neutral), 080404b0 (zh-CN), 040904b0 (en-US)
    for key in keys:
        for lang in ["000004b0", "080404b0", "040904b0"]:
            sub_block = f"\\StringFileInfo\\{lang}\\{key}"
            val_ptr = ctypes.c_void_p()
            val_len = wintypes.UINT()
            if (
                ctypes.windll.version.VerQueryValueW(
                    res, sub_block, ctypes.byref(val_ptr), ctypes.byref(val_len)
                )
                and val_len.value > 0
            ):
                info[key] = ctypes.wstring_at(val_ptr.value)
                break
    return info


def build_nuitka():
    print("==========================================================")
    print(" [1/3] Resolving Python dependencies & runtime paths...")
    print("==========================================================")

    import pythonnet
    import clr_loader
    import pymupdf
    import fitz

    pynet_dir = os.path.dirname(pythonnet.__file__)
    clr_dir = os.path.dirname(clr_loader.__file__)
    mupdf_dir = os.path.dirname(pymupdf.__file__)
    fitz_dir = os.path.dirname(fitz.__file__)

    py3_dll = os.path.join(sys.prefix, 'python3.dll')
    if not os.path.exists(py3_dll):
        py3_dll = r"C:\Program Files\python\python3.dll"

    # 准备 Python.Runtime.dll 便携运行时
    pynet_runtime_dll = os.path.join(pynet_dir, "runtime", "Python.Runtime.dll")
    if os.path.exists(pynet_runtime_dll):
        shutil.copy2(pynet_runtime_dll, "Python.Runtime.dll")

    app_version = os.environ.get("APP_VERSION", "v4.1").lstrip("v")
    if not app_version:
        app_version = "4.1"

    four_part_ver = "4.1.0.0"
    company_name = "漢籍合璧"
    product_name = "智能图像预处理工具 v4.1"
    file_desc = "智能图像预处理工具 v4.1"
    copyright_text = "By weiceng © 漢籍合璧"

    print("==========================================================")
    print(" [2/3] Compiling with Nuitka using UTF-16 Unicode API...")
    print("==========================================================")
    print(f" Target Version : {four_part_ver}")
    print(f" Product Name   : {product_name}")
    print(f" Company Name   : {company_name}")
    print(f" Copyright      : {copyright_text}")

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--onefile",
        "--windows-console-mode=disable",
        "--windows-icon-from-ico=hanji.ico",
        f"--company-name={company_name}",
        f"--product-name={product_name}",
        f"--file-version={four_part_ver}",
        f"--product-version={four_part_ver}",
        f"--file-description={file_desc}",
        f"--copyright={copyright_text}",
        "--include-data-dir=webui=webui",
        "--include-data-file=hanji.ico=hanji.ico",
        f"--include-data-dir={os.path.join(pynet_dir, 'runtime')}=pythonnet/runtime",
        f"--include-data-dir={os.path.join(clr_dir, 'ffi', 'dlls')}=clr_loader/ffi/dlls",
        f"--include-data-files={os.path.join(mupdf_dir, '*.*')}=pymupdf/",
        f"--include-data-files={os.path.join(fitz_dir, '*.*')}=fitz/",
        f"--include-data-file={py3_dll}=python3.dll",
        f"--include-data-file={py3_dll}=pymupdf/python3.dll",
        "--nofollow-import-to=fitz",
        "--nofollow-import-to=pymupdf",
        "--no-deployment-flag=excluded-module-usage",
        "--enable-plugin=tk-inter",
        "--nofollow-import-to=jinja2",
        "--nofollow-import-to=markupsafe",
        "--nofollow-import-to=mako",
        "--nofollow-import-to=cryptography",
        "--nofollow-import-to=Crypto",
        "--nofollow-import-to=bcrypt",
        "--nofollow-import-to=gevent",
        "--nofollow-import-to=greenlet",
        "--nofollow-import-to=zope",
        "--nofollow-import-to=psutil",
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=matplotlib",
        "--nofollow-import-to=pandas",
        "--nofollow-import-to=sqlite3",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=doctest",
        "--nofollow-import-to=test",
        "--nofollow-import-to=distutils",
        "--nofollow-import-to=setuptools",
        "--nofollow-import-to=pkg_resources",
        "--nofollow-import-to=pip",
        "--nofollow-import-to=win32ui",
        "--nofollow-import-to=Pythonwin",
        "--nofollow-import-to=IPython",
        "--nofollow-import-to=pydoc",
        "--nofollow-import-to=difflib",
        "--nofollow-import-to=lib2to3",
        "--nofollow-import-to=idlelib",
        "--nofollow-import-to=turtle",
        "--nofollow-import-to=turtledemo",
        "--output-dir=dist_nuitka",
        "--output-filename=c2bw_nuitka.exe",
        "--assume-yes-for-downloads",
        "run_c2bw.py",
    ]

    try:
        ret = subprocess.run(cmd, check=True)
    finally:
        if os.path.exists("Python.Runtime.dll"):
            try:
                os.remove("Python.Runtime.dll")
            except OSError:
                pass

    print("==========================================================")
    print(" [3/3] Verifying PE Version Information in output binary...")
    print("==========================================================")
    built_exe = os.path.join("dist_nuitka", "c2bw_nuitka.exe")
    if not os.path.exists(built_exe):
        print(f"[ERROR] Output executable not found: {built_exe}")
        sys.exit(1)

    pe_info = inspect_pe_version_info(built_exe)
    print("Extracted PE Version Info:")
    for k, v in pe_info.items():
        print(f"  - {k}: {v}")

    # 严格校验版本信息是否成功写入
    if not pe_info.get("FileVersion"):
        print("[ERROR] Verification failed: FileVersion is missing!")
        sys.exit(1)
    if not pe_info.get("ProductVersion"):
        print("[ERROR] Verification failed: ProductVersion is missing!")
        sys.exit(1)
    if not pe_info.get("ProductName"):
        print("[ERROR] Verification failed: ProductName is missing!")
        sys.exit(1)

    print("[SUCCESS] PE Version Information verified successfully!")


if __name__ == "__main__":
    build_nuitka()
