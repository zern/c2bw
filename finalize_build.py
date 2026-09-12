# -*- coding: utf-8 -*-
import os
import shutil

import sys

mode = sys.argv[1] if len(sys.argv) > 1 else "win11"

if mode == "win11":
    src = os.path.join("dist", "c2bw_win11.exe")
    targets = [
        "c2bw-windows-x64.exe",
        "智能图像预处理工具 v3.6_Win11.exe",
        "智能图像预处理工具 v3.6.exe",
    ]
elif mode == "win7":
    src = os.path.join("dist", "c2bw_win7.exe")
    targets = [
        "c2bw_v3.6_win7.exe",
        "智能图像预处理工具 v3.6_Win7.exe",
    ]
elif mode == "nuitka":
    src = os.path.join("dist_nuitka", "c2bw_nuitka.exe")
    targets = [
        "c2bw_nuitka.exe",
        "智能图像预处理工具 v3.6_Nuitka.exe",
    ]
else:
    raise ValueError(f"Unknown mode: {mode}")

if not os.path.exists(src):
    raise FileNotFoundError("Source not found: " + src)

import time

for t in targets:
    dst = os.path.join("dist", t)
    for attempt in range(5):
        try:
            shutil.copyfile(src, dst)
            print(f"Generated: {dst} ({os.path.getsize(dst)} bytes)")
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(1)



