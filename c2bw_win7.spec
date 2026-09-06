# -*- mode: python ; coding: utf-8 -*-
# 兼容 PyInstaller 4.10 / CPython 3.8 的 Windows 7 打包配置。

a = Analysis(
    ['c2bw.py'],
    pathex=[],
    binaries=[],
    datas=[('webui', 'webui')],
    hiddenimports=[
        'pkg_resources',
        'PIL.JpegImagePlugin',
        'PIL.PdfImagePlugin',
        'pypdf',
        'webview',
        'webview.platforms.winforms',
        'clr',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
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
    name='c2bw_win7',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='hanji.ico',
    version='version_info.txt',
)
