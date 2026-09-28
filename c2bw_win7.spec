# -*- mode: python ; coding: utf-8 -*-
# 兼容 Windows 7 (CPython 3.8 x64) 的独立单文件打包配置。

import os
import clr_loader
import pythonnet

from PyInstaller.utils.hooks import collect_all

clr_loader_dir = os.path.dirname(clr_loader.__file__)
pythonnet_dir = os.path.dirname(pythonnet.__file__)
os.environ['PATH'] = os.getcwd() + os.pathsep + os.environ.get('PATH', '')

try:
    mupdf_datas, mupdf_binaries, mupdf_hidden = collect_all('pymupdf')
except Exception:
    mupdf_datas, mupdf_binaries, mupdf_hidden = [], [], []

try:
    fitz_datas, fitz_binaries, fitz_hidden = collect_all('fitz')
except Exception:
    fitz_datas, fitz_binaries, fitz_hidden = [], [], []

try:
    import pymupdf
    pymupdf_dir = os.path.dirname(pymupdf.__file__)
    site_packages_dir = os.path.dirname(pymupdf_dir)
    fitz_dir = os.path.join(site_packages_dir, 'fitz')
except Exception:
    pymupdf_dir, fitz_dir = None, None

extra_datas = []
if pymupdf_dir and os.path.exists(pymupdf_dir):
    extra_datas.append((pymupdf_dir, 'pymupdf'))
if fitz_dir and os.path.exists(fitz_dir):
    extra_datas.append((fitz_dir, 'fitz'))

extra_binaries = []
py3_dll = r"C:\Program Files\python\python3.dll"
if os.path.exists(py3_dll):
    extra_binaries.append((py3_dll, '.'))
    extra_binaries.append((py3_dll, 'pymupdf'))

if pymupdf_dir:
    for f in os.listdir(pymupdf_dir):
        if f.lower().endswith(('.dll', '.pyd')):
            fp = os.path.join(pymupdf_dir, f)
            extra_binaries.append((fp, '.'))
            extra_binaries.append((fp, 'pymupdf'))

added_datas = [
    ('webui', 'webui'),
    ('hanji.ico', '.'),
    (os.path.join(pythonnet_dir, 'runtime'), 'pythonnet/runtime'),
    (os.path.join(clr_loader_dir, 'ffi', 'dlls'), 'clr_loader/ffi/dlls'),
] + mupdf_datas + fitz_datas + extra_datas

added_binaries = mupdf_binaries + fitz_binaries + extra_binaries

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
    ] + mupdf_hidden + fitz_hidden,
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
