"""GUI 后台线程：解析 ABR 与执行转换，避免阻塞界面。"""

from __future__ import annotations

import traceback
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from brush_converter.abr import AbrFile
from brush_converter.convert import convert_presets
from brush_converter.export_options import source_digest_of_bytes
from brush_converter.export_transaction import TransactionSummary
from brush_converter.mapping import BrushPreset, map_presets

from .brush_selection import ExportSnapshot


class ParseWorker(QThread):
    """后台解析 ABR 文件。"""

    # (abr, presets) 或 (None, 原始异常信息)；标题/前缀由 GUI 按当前语言添加
    done = Signal(object, object)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self.source_digest: str | None = None

    def run(self) -> None:
        """线程体：解析 ABR + 映射预设，异常通过 done 信号回传。"""
        try:
            # 同一份原始字节同时用于解析与身份摘要，避免换文件竞态串配置。
            data = Path(self._path).read_bytes()
            abr = AbrFile._from_bytes(data, Path(self._path).stem)
            self.source_digest = source_digest_of_bytes(data)
            presets = map_presets(abr)
            self.done.emit(abr, presets)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.done.emit(None, str(e))


@dataclass(frozen=True)
class ConvertFailure:
    """保留169失败现场，不把受阻/回滚/保留清单压成一句字符串。"""

    message: str
    phase: str = ""
    summary: TransactionSummary | None = None


class ConvertWorker(QThread):
    """后台转换选中预设。"""

    # ConvertResult 或 ConvertFailure；原始报错与结构化事务清单都保留。
    done = Signal(object)

    def __init__(self, abr: AbrFile, presets: list[BrushPreset], out_dir: str,
                 make_bundle: bool, make_standalone: bool, parent=None,
                 *, snapshot: ExportSnapshot | None = None):
        super().__init__(parent)
        self._abr = deepcopy(abr)
        # 快照自身再次复制；后台不读取控件、QSettings或仍可变化的GUI状态。
        self._option_state = snapshot.option_state() if snapshot is not None else None
        self._presets = ([self._option_state.preset(key)
                          for key in self._option_state.preset_ids]
                         if self._option_state is not None else deepcopy(presets))
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
                option_state=self._option_state,
            )
            self.done.emit(result)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.done.emit(ConvertFailure(str(e), getattr(e, 'phase', ''),
                                          getattr(e, 'summary', None)))
