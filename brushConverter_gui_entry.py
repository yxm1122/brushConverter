"""brushConverter GUI 打包入口（PyInstaller 专用）。

直接调用 gui.main_window.main；gui/__init__.py 里的开发期 sys.path
引导在打包后指向不存在的 src/，但只是无害的多余路径（导入已由
PyInstaller 解析）。运行 `python -m gui` 时仍走 gui/__main__.py。
"""
from gui.main_window import main

if __name__ == "__main__":
    raise SystemExit(main())
