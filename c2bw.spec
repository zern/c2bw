# -*- mode: python ; coding: utf-8 -*-


import os
import clr_loader
import pythonnet

clr_loader_dir = os.path.dirname(clr_loader.__file__)
pythonnet_dir = os.path.dirname(pythonnet.__file__)
os.environ['PATH'] = os.getcwd() + os.pathsep + os.environ.get('PATH', '')

added_datas = [
    ('webui', 'webui'),
    ('hanji.ico', '.'),
    (os.path.join(pythonnet_dir, 'runtime'), 'pythonnet/runtime'),
    (os.path.join(clr_loader_dir, 'ffi', 'dlls'), 'clr_loader/ffi/dlls'),
]

a = Analysis(
    ['c2bw.py'],
    pathex=[],
    binaries=[],
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
    runtime_hooks=[],
    excludes=['pkg_resources'],
    win_no_prefer_redirects=False,
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
    name='智能图像预处理工具 v3.5',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='hanji.ico',
    version='version_info.txt',
)
