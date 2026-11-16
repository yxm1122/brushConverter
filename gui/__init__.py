"""brushConverter 图形界面。"""

from __future__ import annotations

import sys
from pathlib import Path

# `python -m gui` 会先执行本文件，再执行 __main__.py；而 main_window 在导入时
# 就依赖 brush_converter，所以 src/ 的 sys.path 引导必须放在这里，
# 放在 __main__.py 里已经太晚（那时包导入失败早已抛出 ModuleNotFoundError）。
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from .main_window import MainWindow  # noqa: E402

__all__ = ["MainWindow"]
