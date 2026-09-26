# -*- coding: utf-8 -*-
"""
从 version_info.txt 提取版本元数据，供 Nuitka 构建命令或 CI 动态调用
"""
import os
import re
import sys

# 保证在 Windows 环境下正确输出 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def parse_version_info(filepath="version_info.txt"):
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    entries = dict(re.findall(r"StringStruct\(\s*u?['\"]([^'\"]+)['\"]\s*,\s*u?['\"]([^'\"]+)['\"]\s*\)", content))
    return entries

def get_nuitka_flags(filepath="version_info.txt"):
    info = parse_version_info(filepath)
    company = info.get("CompanyName", "漢籍合璧")
    prod_name = info.get("ProductName", "智能图像预处理工具 v4.0")
    file_desc = info.get("FileDescription", "智能图像预处理工具 v4.0")
    copyright_text = info.get("LegalCopyright", "By weiceng © 漢籍合璧")
    file_ver = info.get("FileVersion", "4.0.0.0")
    prod_ver = info.get("ProductVersion", "4.0.0.0")

    return [
        f'--company-name={company}',
        f'--product-name={prod_name}',
        f'--file-version={file_ver}',
        f'--product-version={prod_ver}',
        f'--file-description={file_desc}',
        f'--copyright={copyright_text}',
    ]

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--nuitka-flags":
        print("\n".join(get_nuitka_flags()))
    elif len(sys.argv) > 1 and sys.argv[1] == "--nuitka-cmd":
        # 适用于直接拼接到命令行
        print(" ".join(f'"{f}"' for f in get_nuitka_flags()))
    else:
        info = parse_version_info()
        for k, v in info.items():
            print(f"{k}: {v}")
