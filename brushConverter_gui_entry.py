"""brushConverter GUI 打包入口（PyInstaller 专用）。

直接调用 gui.main_window.main，避开 gui/__main__.py 的开发期
sys.path hack（运行 `python -m gui` 时仍用 __main__.py）。
"""
from gui.main_window import main

if __name__ == "__main__":
    raise SystemExit(main())
