"""GUI 后台线程：只发送信号，以协作检查点停止，不接触控件或强杀线程。"""

from __future__ import annotations

import traceback
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from PySide6.QtCore import QThread, Signal

from brush_converter.abr import AbrFile
from brush_converter.convert import convert_presets
from brush_converter.export_options import (
    CancelToken,
    ConversionCancelled,
    ProgressEvent,
    source_digest_of_bytes,
)
from brush_converter.export_transaction import TransactionSummary
from brush_converter.mapping import BrushPreset, map_presets

from .brush_selection import ExportSnapshot


@dataclass(frozen=True)
class WorkerProgress:
    """GUI 阶段信封；原始事件以支计数，绝不换算成虚构的整批百分比。"""

    stage: str
    event: ProgressEvent | None = None


@dataclass(frozen=True)
class ParseCancelled:
    """解析在安全边界停止；与解析失败分别显示，不提供可导出的旧数据。"""

    reason: str


class CooperativeWorker(QThread):
    """GUI 可直接调用的线程安全请求入口；耗时工作仍只在 run 内执行。"""

    progress = Signal(object)
    control_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = RLock()
        self._token = CancelToken()
        self._control = "ready"

    @property
    def cancel_state(self) -> str:
        with self._lock:
            return self._control

    def request_cancel(self) -> str:
        """幂等请求而非停止承诺；越过交付边界的请求只记录，最终结果优先。"""
        with self._lock:
            if self._control == "finished":
                return "finished"
            if self._control == "delivering":
                self._token.cancel()
                return "too_late"
            if self._token.cancelled:
                return "already_requested"
            self._token.cancel()
            self._control = "cancel_requested"
            self.control_changed.emit(self._control)
            return "accepted"

    def _begin(self) -> None:
        with self._lock:
            if not self._token.cancelled:
                self._control = "running"

    def _poll_cancel(self) -> bool:
        """由生产事务在真实检查点调用，只有此时才报告“正在停止”。"""
        with self._lock:
            requested = self._token.cancelled
            if requested and self._control not in ("stopping", "delivering", "finished"):
                self._control = "stopping"
                self.control_changed.emit(self._control)
            return requested

    def _checkpoint(self) -> None:
        if self._poll_cancel():
            raise ConversionCancelled("用户取消")

    def _complete(self) -> None:
        with self._lock:
            self._control = "finished"


class ParseWorker(CooperativeWorker):
    """后台解析 ABR；单次解析不可中断，前后边界可协作停止。"""

    # (abr, presets) 或 (None, 原始错误/ParseCancelled)，主线程负责展示。
    done = Signal(object, object)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self.source_digest: str | None = None

    def run(self) -> None:
        self._begin()
        try:
            self._checkpoint()
            self.progress.emit(WorkerProgress("read"))
            # 同一份原始字节同时用于解析与身份摘要，避免换文件竞态串配置。
            data = Path(self._path).read_bytes()
            self._checkpoint()
            self.progress.emit(WorkerProgress("parse"))
            abr = AbrFile._from_bytes(data, Path(self._path).stem)
            self._checkpoint()
            self.source_digest = source_digest_of_bytes(data)
            self.progress.emit(WorkerProgress("map"))
            presets = map_presets(abr)
            self._checkpoint()
            self.progress.emit(WorkerProgress("parse_complete"))
            self._complete()
            self.done.emit(abr, presets)
        except ConversionCancelled as exc:
            self.source_digest = None
            self._complete()
            self.done.emit(None, ParseCancelled(exc.reason))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._complete()
            self.done.emit(None, str(exc))


@dataclass(frozen=True)
class ConvertFailure:
    """保留169失败现场，不把受阻/回滚/保留清单压成一句字符串。"""

    message: str
    phase: str = ""
    summary: TransactionSummary | None = None


class ConvertWorker(CooperativeWorker):
    """后台转换不可变快照；取消轮询直接接169事务而不是只停止界面。"""

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
        self._write_events = 0

    def _on_progress(self, event: ProgressEvent) -> None:
        """只转发真实阶段：169先报验证完成，再在最后检查点后报最终交付。

        冻结接口没有 verify 枚举，故保留原事件并在GUI信封中区分两个有序
        write 通知；不解析中文文案、不改事务/CLI，也不假定验证耗时百分比。
        """
        stage = event.phase
        with self._lock:
            if event.phase == "write":
                self._write_events += 1
                stage = "verified" if self._write_events == 1 else "deliver"
                if stage == "deliver":
                    self._control = "delivering"
                    self.control_changed.emit(self._control)
            elif event.phase == "convert" and event.completed == event.total:
                # 逐支生成已结束；后续还要组包、暂存验证，不能称“整批完成”。
                stage = "verify"
            self.progress.emit(WorkerProgress(stage, event))

    def run(self) -> None:
        self._begin()
        try:
            result = convert_presets(
                self._abr, self._presets, self._out_dir,
                make_bundle=self._make_bundle,
                make_standalone=self._make_standalone,
                option_state=self._option_state,
                # 回调在检查点报告stopping；令牌读写均受本类锁保护。
                cancel_callback=self._poll_cancel,
                progress=self._on_progress,
            )
            self._complete()
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._complete()
            self.done.emit(ConvertFailure(str(exc), getattr(exc, "phase", ""),
                                          getattr(exc, "summary", None)))
