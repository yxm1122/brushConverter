"""brushConverter GUI 入口。

用法:
    python -m gui
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让 gui 包能 import src/brush_converter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from .main_window import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
