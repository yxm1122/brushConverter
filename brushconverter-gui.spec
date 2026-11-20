# -*- mode: python ; coding: utf-8 -*-
"""brushConverter GUI 打包配置（PyInstaller --onedir）。

用法（在项目根目录）:
    pyinstaller brushconverter-gui.spec

入口 brushConverter_gui_entry.py 直接调用 gui.main_window.main；
sys.path 引导现位于 gui/__init__.py（打包后指向不存在的 src/，无害）。
"""
block_cipher = None

# v1.3.1 打包修复：同 brushconverter-cli.spec —— 显式收集 libexpat.dll，
# 否则打包后 GUI/CLI 的转换路径（xml.etree.ElementTree）会因 pyexpat 加载失败而崩溃。
import sys as _sys
from pathlib import Path as _Path

_EXPAT_DLL_CANDIDATES = [
    _Path(_sys.prefix) / "Library" / "bin" / "libexpat.dll",
    _Path(_sys.prefix) / "DLLs" / "libexpat.dll",
]
binaries = [(str(p), ".") for p in _EXPAT_DLL_CANDIDATES if p.is_file()]

a = Analysis(
    ['brushConverter_gui_entry.py'],
    pathex=['.', 'src'],
    binaries=binaries,
    datas=[],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='brushConverter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/brushConverter.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='brushConverter',
)
