# -*- mode: python ; coding: utf-8 -*-
# 兼容 Windows 7 (CPython 3.8 x64) 的独立单文件打包配置。

import os
import sys
import re

# 动态确保 version_info.txt 与当前 APP_VERSION 一致，确保 Actions 和本地打包始终包含版本元数据
app_version = os.environ.get("APP_VERSION", "v4.2").lstrip("v")
if not app_version:
    app_version = "4.2"
parts = [int(p) for p in re.findall(r"\d+", app_version)]
while len(parts) < 4:
    parts.append(0)
four_part_tuple = tuple(parts[:4])
four_part_str = ".".join(str(p) for p in parts[:4])

version_info_content = f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={four_part_tuple},
    prodvers={four_part_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'漢籍合璧'),
        StringStruct(u'FileDescription', u'智能图像预处理工具 v{app_version}'),
        StringStruct(u'FileVersion', u'{four_part_str}'),
        StringStruct(u'InternalName', u'c2bw_processor'),
        StringStruct(u'LegalCopyright', u'By weiceng © 漢籍合璧'),
        StringStruct(u'OriginalFilename', u'智能图像预处理工具 v{app_version}.exe'),
        StringStruct(u'ProductName', u'智能图像预处理工具 v{app_version}'),
        StringStruct(u'ProductVersion', u'{four_part_str}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
'''
try:
    with open("version_info.txt", "w", encoding="utf-8") as _vf:
        _vf.write(version_info_content)
except Exception:
    pass
import clr_loader
import pythonnet

from PyInstaller.utils.hooks import collect_all

clr_loader_dir = os.path.dirname(clr_loader.__file__)
pythonnet_dir = os.path.dirname(pythonnet.__file__)
os.environ['PATH'] = os.getcwd() + os.pathsep + os.environ.get('PATH', '')

try:
    import pymupdf
    pymupdf_dir = os.path.dirname(pymupdf.__file__)
    site_packages_dir = os.path.dirname(pymupdf_dir)
    fitz_dir = os.path.join(site_packages_dir, 'fitz')
except Exception:
    pymupdf_dir, fitz_dir = None, None

added_datas = [
    ('webui', 'webui'),
    ('hanji.ico', '.'),
    (os.path.join(pythonnet_dir, 'runtime'), 'pythonnet/runtime'),
    (os.path.join(clr_loader_dir, 'ffi', 'dlls'), 'clr_loader/ffi/dlls'),
]

added_binaries = []
py3_dll = os.path.join(sys.prefix, 'python3.dll')
if not os.path.exists(py3_dll):
    py3_dll = r"C:\Program Files\python\python3.dll"
if os.path.exists(py3_dll):
    added_binaries.append((py3_dll, '.'))

# 严禁重复打包：精准收集 pymupdf 中的二进制库与 Python 模块
if pymupdf_dir and os.path.exists(pymupdf_dir):
    for f in os.listdir(pymupdf_dir):
        fp = os.path.join(pymupdf_dir, f)
        if os.path.isfile(fp):
            if f.lower().endswith(('.dll', '.pyd')):
                added_binaries.append((fp, 'pymupdf'))
            elif f.lower().endswith('.py'):
                added_datas.append((fp, 'pymupdf'))

# 精准收集 fitz 中的 Python 模块
if fitz_dir and os.path.exists(fitz_dir):
    for f in os.listdir(fitz_dir):
        fp = os.path.join(fitz_dir, f)
        if os.path.isfile(fp) and f.lower().endswith('.py'):
            added_datas.append((fp, 'fitz'))

a = Analysis(
    ['run_c2bw.py'],
    pathex=[],
    binaries=added_binaries,
    datas=added_datas,
    hiddenimports=[
        'c2bw',
        'c2bw.core',
        'c2bw.service',
        'c2bw.desktop',
        'PIL.JpegImagePlugin',
        'PIL.PdfImagePlugin',
        'PIL.Jpeg2KImagePlugin',
        'pypdf',
        'fitz',
        'pymupdf',
        'pymupdf._mupdf',
        'pymupdf._extra',
        'pymupdf.mupdf',
        'webview',
        'webview.platforms.winforms',
        'clr',
        'clr_loader',
        'pythonnet',
        'win32com',
        'win32com.client',
        'pythoncom',
    ],
    hookspath=[],
    excludes=[
        'pkg_resources',
        'setuptools',
        'distutils',
        'pip',
        'scipy',
        'matplotlib',
        'pandas',
        'Pythonwin',
        'win32ui',
        'mfc140u',
        'cryptography',
        'Crypto',
        'bcrypt',
        'gevent',
        'greenlet',
        'zope',
        'psutil',
        'jinja2',
        'markupsafe',
        'mako',
        'sqlite3',
        'unittest',
        'pytest',
        'doctest',
        'test',
        'pydoc',
        'difflib',
        'lib2to3',
        'idlelib',
        'turtle',
        'turtledemo',
        'IPython',
        'jupyter',
    ],
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# 过滤并去重 a.binaries 与 a.datas，确保每个源文件（尤其是 21MB 的 mupdfcpp64.dll）绝不重复打包
seen_sources = set()
unique_binaries = []
for item in a.binaries:
    target, src, type_ = item
    src_norm = os.path.normcase(os.path.realpath(os.path.abspath(src)))
    if src_norm not in seen_sources:
        seen_sources.add(src_norm)
        unique_binaries.append(item)
a.binaries = unique_binaries

seen_data_sources = set()
unique_datas = []
for item in a.datas:
    target, src, type_ = item
    src_norm = os.path.normcase(os.path.realpath(os.path.abspath(src)))
    if src_norm not in seen_data_sources:
        seen_data_sources.add(src_norm)
        unique_datas.append(item)
a.datas = unique_datas

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='c2bw_win7',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='hanji.ico',
    version='version_info.txt',
)
