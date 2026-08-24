"""GUI 后台线程：解析 ABR 与执行转换，避免阻塞界面。"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal

from brush_converter.abr import AbrFile
from brush_converter.mapping import BrushPreset, map_presets
from brush_converter.convert import convert_presets, ConvertResult


class ParseWorker(QThread):
    """后台解析 ABR 文件。"""

    done = Signal(object, object)   # (abr, presets) 或 (None, 错误信息字符串)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self) -> None:
        """线程体：解析 ABR + 映射预设，异常通过 done 信号回传。"""
        try:
            abr = AbrFile.parse(self._path)
            presets = map_presets(abr)
            self.done.emit(abr, presets)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.done.emit(None, f"解析失败：{e}")


class ConvertWorker(QThread):
    """后台转换选中预设。"""

    done = Signal(object)    # ConvertResult 或 错误信息字符串

    def __init__(self, abr: AbrFile, presets: list[BrushPreset], out_dir: str,
                 make_bundle: bool, make_standalone: bool, parent=None):
        super().__init__(parent)
        self._abr = abr
        self._presets = presets
        self._out_dir = out_dir
        self._make_bundle = make_bundle
        self._make_standalone = make_standalone

    def run(self) -> None:
        """线程体：转换选中预设，异常通过 done 信号回传。"""
        try:
            result = convert_presets(
                self._abr, self._presets, self._out_dir,
                make_bundle=self._make_bundle,
                make_standalone=self._make_standalone,
            )
            self.done.emit(result)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.done.emit(f"转换失败：{e}")
