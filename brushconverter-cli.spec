# -*- mode: python ; coding: utf-8 -*-
"""brushconverter-cli 打包配置（PyInstaller --onedir）。

用法（在项目根目录）:
    pyinstaller brushconverter-cli.spec
"""
block_cipher = None

# v1.3.1 打包修复：convert.py 新增 xml.etree.ElementTree 的运行时用法（输出校验/真实纹理计数），
# xml.parsers.expat 依赖 pyexpat.pyd，后者又依赖 libexpat.dll。该 DLL 在 conda 环境里位于
# Library/bin，不在 PyInstaller 的默认二进制解析路径上（旧构建日志会警告
# "Library not found: could not resolve 'libexpat.dll'"），结果是打包后 convert 直接抛
# ImportError: DLL load failed while importing pyexpat。这里按 sys.prefix 派生路径显式收集，
# 不写死本机绝对路径；找不到时退化为空列表（与旧行为一致，不会让构建失败）。
import sys as _sys
from pathlib import Path as _Path

_EXPAT_DLL_CANDIDATES = [
    _Path(_sys.prefix) / "Library" / "bin" / "libexpat.dll",
    _Path(_sys.prefix) / "DLLs" / "libexpat.dll",
]
binaries = [(str(p), ".") for p in _EXPAT_DLL_CANDIDATES if p.is_file()]

a = Analysis(
    ['cli.py'],
    pathex=['src', '.'],
    binaries=binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'PySide6', 'PyQt5', 'PyQt6', 'tkinter',
        'matplotlib', 'notebook', 'IPython',
        'PIL.ImageTk',
    ],
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
    name='brushconverter-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
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
    name='brushconverter-cli',
)
