# -*- coding: utf-8 -*-
import os
import shutil
import sys
import time

# 确保在各平台（尤其是 GitHub Actions Windows runner 的 cp1252 编码环境）输出中文字符时不报错
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

mode = sys.argv[1] if len(sys.argv) > 1 else "win11"

if mode == "win11":
    src = os.path.join("dist", "c2bw_win11.exe")
    targets = [
        "智能图像预处理工具 v4.1.exe",
        "c2bw-v4.1.exe",
    ]
elif mode == "win7":
    src = os.path.join("dist", "c2bw_win7.exe")
    targets = [
        "智能图像预处理工具 v4.1_Win7.exe",
        "c2bw-v4.1_Win7.exe",
    ]
elif mode == "nuitka":
    src = os.path.join("dist_nuitka", "c2bw_nuitka.exe")
    targets = [
        "智能图像预处理工具 v4.1.exe",
        "c2bw-v4.1.exe",
    ]
else:
    raise ValueError(f"Unknown mode: {mode}")

if not os.path.exists(src):
    raise FileNotFoundError("Source not found: " + src)

os.makedirs("dist", exist_ok=True)

for t in targets:
    dst = os.path.join("dist", t)
    for attempt in range(5):
        try:
            shutil.copyfile(src, dst)
            try:
                print(f"Generated: {dst} ({os.path.getsize(dst)} bytes)")
                if sys.platform == "win32" and dst.lower().endswith(".exe"):
                    try:
                        import ctypes
                        from ctypes import wintypes
                        size = ctypes.windll.version.GetFileVersionInfoSizeW(dst, None)
                        if size > 0:
                            buf = ctypes.create_string_buffer(size)
                            if ctypes.windll.version.GetFileVersionInfoW(dst, 0, size, buf):
                                for key in ["FileDescription", "FileVersion", "ProductName", "ProductVersion", "CompanyName", "LegalCopyright"]:
                                    for lang in ["000004b0", "080404b0", "040904b0"]:
                                        val_ptr = ctypes.c_void_p()
                                        val_len = wintypes.UINT()
                                        if ctypes.windll.version.VerQueryValueW(buf, f"\\StringFileInfo\\{lang}\\{key}", ctypes.byref(val_ptr), ctypes.byref(val_len)) and val_len.value > 0:
                                            print(f"  * {key}: {ctypes.wstring_at(val_ptr.value)}")
                                            break
                    except Exception as ve:
                        print(f"  * Version check warning: {ve}")
            except Exception:
                pass
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(1)

# 清理中间生成产物
if os.path.exists(src):
    try:
        os.remove(src)
    except Exception:
        pass
