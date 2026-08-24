# -*- mode: python ; coding: utf-8 -*-
"""brushConverter GUI 打包配置（PyInstaller --onedir）。

用法（在项目根目录）:
    pyinstaller brushconverter-gui.spec

入口 brushConverter_gui_entry.py 避免了 gui/__main__.py 里的
sys.path 注入（打包后路径已无效，但保留无害）。
"""
block_cipher = None

a = Analysis(
    ['brushConverter_gui_entry.py'],
    pathex=['.', 'src'],
    binaries=[],
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
