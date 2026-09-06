# -*- mode: python ; coding: utf-8 -*-
"""macOS 单文件构建配置。需在 macOS runner 上执行。"""
a = Analysis(['c2bw.py'], pathex=[], binaries=[], datas=[('webui', 'webui')], hiddenimports=[
    'PIL.JpegImagePlugin', 'PIL.PdfImagePlugin', 'pypdf', 'webview',
    'webview.platforms.cocoa',
], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[
    'webview.platforms.winforms', 'clr', 'tkinter',
], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='c2bw', debug=False,
         bootloader_ignore_signals=False, strip=False, upx=False, console=False,
         disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
         codesign_identity=None, entitlements_file=None)