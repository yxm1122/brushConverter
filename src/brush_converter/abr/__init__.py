"""ABR（Photoshop 笔刷）读取子包。

布局与算法参考 GIMP 官方实现 app/core/gimpbrush-load.c（GPL），
在 Python 中独立重写。
"""

from .patterns import PatternTexture, parse_patterns
from .reader import AbrFile
from .samples import BrushTip

__all__ = ["AbrFile", "BrushTip", "PatternTexture", "parse_patterns"]
