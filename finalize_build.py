# -*- coding: utf-8 -*-
import os
import shutil
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "win11"

if mode == "win11":
    src = os.path.join("dist", "c2bw_win11.exe")
    targets = [
        "智能图像预处理工具 v3.9.exe",
    ]
elif mode == "win7":
    src = os.path.join("dist", "c2bw_win7.exe")
    targets = [
        "智能图像预处理工具 v3.9_Win7.exe",
    ]
elif mode == "nuitka":
    src = os.path.join("dist_nuitka", "c2bw_nuitka.exe")
    targets = [
        "智能图像预处理工具 v3.9_Nuitka.exe",
    ]
else:
    raise ValueError(f"Unknown mode: {mode}")

if not os.path.exists(src):
    raise FileNotFoundError("Source not found: " + src)

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

# 清理中间生成产物
if os.path.exists(src):
    try:
        os.remove(src)
    except Exception:
        pass
