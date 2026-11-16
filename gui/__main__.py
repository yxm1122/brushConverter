"""brushConverter GUI 入口。

用法:
    python -m gui
"""

from __future__ import annotations

# sys.path 引导在 gui/__init__.py 中完成（包导入时先于本文件执行）
from .main_window import main

if __name__ == "__main__":
    raise SystemExit(main())
