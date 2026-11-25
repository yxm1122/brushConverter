"""172离屏机器验证：真实Qt线程与169事务，不打开窗口/Krita/PS，不代替187人工。"""

from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Formatter
from threading import Event

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
TASK = ROOT / ".tmp" / "172"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QEventLoop,
    QSettings,
    QStandardPaths,
    QThread,
    QTimer,
    Slot,
)
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from brush_converter import convert as conversion  # noqa: E402
from brush_converter import export_transaction as transaction  # noqa: E402
from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import ConvertResult  # noqa: E402
from brush_converter.export_options import (  # noqa: E402
    ProgressEvent,
    source_digest_of_bytes,
    validate_progress_event,
)
from brush_converter.kpp.kpp_writer import read_preset_xml  # noqa: E402
from brush_converter.mapping import BrushPreset  # noqa: E402
from gui import i18n, main_window, workers  # noqa: E402
from gui.main_window import ExportConfirmDialog, MainWindow  # noqa: E402

DIGEST = source_digest_of_bytes(b"task172-synthetic")


def verify_isolation():
    """无法证实隔离就立即失败；先于QApplication和任何配置创建。"""
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(TASK.resolve())
    for name in ("TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "HOME",
                 "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        assert Path(os.environ[name]).resolve().is_relative_to(TASK.resolve()), name
    assert Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation)).resolve().is_relative_to(TASK.resolve())
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
    assert sys.dont_write_bytecode


@pytest.fixture(scope="session")
def app():
    verify_isolation()
    return QApplication.instance() or QApplication([])


@pytest.fixture
def case_dir():
    verify_isolation()
    path = TASK / "samples" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def brush(name="synthetic172", *, missing=False):
    """只用小型合成采样笔尖，不接触商用ABR或旧基准。"""
    return BrushPreset(name, diameter=16, hardness=50, is_computed=False,
        tip_gray=None if missing else np.arange(35, dtype=np.uint8).reshape(5, 7))


def inventory(path):
    """比较字节与mtime；输出目录必须来自本任务，禁止碰其它目录。"""
    path = Path(path).resolve()
    assert path.is_relative_to(TASK.resolve())
    return {item.relative_to(path).as_posix(): (item.read_bytes(), item.stat().st_mtime_ns)
            for item in path.rglob("*") if item.is_file()} if path.exists() else {}


def wait_for(app, predicate):
    """只用于测试同步；计时器不参与产品进度计算，超时必失败而非跳过。"""
    if predicate():
        app.processEvents()
        return
    loop = QEventLoop()
    poll, timeout = QTimer(), QTimer()
    poll.setInterval(1)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    poll.start()
    timeout.start(5000)
    loop.exec()
    poll.stop()
    timeout.stop()
    assert predicate(), "等待真实线程/信号超时，不能冒充通过"
    app.processEvents()


class Gate:
    """用双事件钉住真实边界，不以sleep碰运气制造取消竞态。"""

    def __init__(self):
        self.reached, self.release = Event(), Event()

    def pause(self):
        self.reached.set()
        assert self.release.wait(5), "测试安全闸未释放"


class CheckedWindow(MainWindow):
    """记录真实槽的执行线程；所有控件更新必须仍在QApplication线程。"""

    def __init__(self):
        self.slot_threads = []
        self.seen_progress = []
        self.seen_controls = []
        super().__init__()

    def _set_status(self, *args, **kwargs):
        # 不重写原Qt槽：PySide6重写槽的sender上下文与原生连接不同。
        self.slot_threads.append(QThread.currentThread())
        super()._set_status(*args, **kwargs)

    def _track_worker(self, worker):
        super()._track_worker(worker)
        worker.progress.connect(self._record_progress)
        worker.control_changed.connect(self._record_control)

    @Slot(object)
    def _record_progress(self, progress):
        self.slot_threads.append(QThread.currentThread())
        self.seen_progress.append(progress)

    @Slot(str)
    def _record_control(self, state):
        self.slot_threads.append(QThread.currentThread())
        self.seen_controls.append(state)


@pytest.fixture
def windows(app, case_dir, monkeypatch):
    opened, gates, messages = [], [], []
    def settings(*_args):
        value = QSettings(str(case_dir / (uuid.uuid4().hex + ".ini")), QSettings.Format.IniFormat)
        value.setFallbacksEnabled(False)
        value.setValue("language", "zh")
        return value
    monkeypatch.setattr(main_window, "QSettings", settings)
    monkeypatch.setattr(ExportConfirmDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: messages.append(
        (self.text(), self.informativeText(), self.detailedText())) or 0)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[1:]) or 0)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[1:]) or 0)
    def create():
        win = CheckedWindow()
        win.out_dir_edit.setText(str(case_dir / ("output-" + str(len(opened)))))
        opened.append(win)
        return win
    yield create, gates, messages
    for gate in gates:
        gate.release.set()
    for win in opened:
        for worker in tuple(win._active_workers):
            worker.request_cancel()
            assert worker.wait(5000), "不允许强杀仍在运行的测试线程"
        app.processEvents()
        win._abort_close()
        win.close()
        win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    i18n.set_language("zh")


@pytest.fixture
def window(windows):
    return windows[0]()


def load(win, presets=None):
    win._file_path = "synthetic172.abr"
    win._on_parsed(AbrFile(6, 2, name="synthetic172"),
                   presets if presets is not None else [brush("a"), brush("b"), brush("c")],
                   source_digest=DIGEST)
    return win._selection


def finish(app, win):
    wait_for(app, lambda: not win._active_workers)
    assert win._parse_worker is None and win._convert_worker is None
    assert not win._busy and not win.cancel_btn.isEnabled()
    assert not win._worker_results and win._export_snapshot is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not win.findChildren(QThread)


def render_gate(monkeypatch, gates):
    gate = Gate()
    gates.append(gate)
    original = conversion._render_preset
    def held(*args, **kwargs):
        if not gate.reached.is_set():
            gate.pause()
        return original(*args, **kwargs)
    monkeypatch.setattr(conversion, "_render_preset", held)
    return gate


def worker_run(case_dir, *, bundle=True, standalone=True):
    worker = workers.ConvertWorker(AbrFile(6, 2, name="synthetic172"),
        [brush("a"), brush("b"), brush("c")], str(case_dir / "output"), bundle, standalone)
    results, progress, controls = [], [], []
    worker.done.connect(results.append)
    worker.progress.connect(progress.append)
    worker.control_changed.connect(controls.append)
    return worker, results, progress, controls


def test_isolation_is_proven_before_gui(app):
    verify_isolation()
    assert app.thread() == QThread.currentThread()


@pytest.mark.parametrize("bundle,standalone", [(False, True), (True, False), (True, True)])
def test_real_transaction_progress_and_counts_are_not_global_percent(app, case_dir, bundle, standalone):
    worker, results, progress, _controls = worker_run(case_dir, bundle=bundle, standalone=standalone)
    worker.run()
    assert results[0].status == "committed"
    assert [item.stage for item in progress] == ["prepare", "convert", "convert", "verify", "verified", "deliver", "done"]
    for item in progress:
        validate_progress_event(item.event)
    assert [item.event.completed for item in progress if item.event.phase == "convert"] == [1, 2, 3]
    assert results[0].transaction.cancellable is False
    assert worker.request_cancel() == "finished"
    assert not list((case_dir / "output").glob(transaction.STAGING_PREFIX + "*"))


def test_duplicate_concurrent_requests_are_idempotent(app, case_dir):
    worker, results, _progress, controls = worker_run(case_dir)
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _index: worker.request_cancel(), range(40)))
    assert replies.count("accepted") == 1
    assert replies.count("already_requested") == 39
    worker.run()
    app.processEvents()
    assert results[0].status == "cancelled"
    assert controls.count("cancel_requested") == 1 and "stopping" in controls
    assert inventory(case_dir / "output") == {}


@pytest.mark.parametrize("stage", ["prepare", "convert", "verify", "verified"])
@pytest.mark.parametrize("bundle,standalone", [(False, True), (True, False), (True, True)])
def test_cancel_at_real_cancellable_boundaries_preserves_existing_files(app, case_dir, stage, bundle, standalone):
    out = case_dir / "output"
    (out / "kpp").mkdir(parents=True)
    (out / "old.bundle").write_bytes(b"old-bundle-172")
    (out / "kpp" / "old.kpp").write_bytes(b"old-kpp-172")
    before = inventory(out)
    worker, results, progress, controls = worker_run(case_dir, bundle=bundle, standalone=standalone)
    requested = []
    def cancel(item):
        if item.stage == stage and not requested:
            requested.extend([worker.request_cancel(), worker.request_cancel()])
    worker.progress.connect(cancel)
    worker.run()
    assert requested == ["accepted", "already_requested"]
    assert "stopping" in controls
    result = results[0]
    assert result.status == "cancelled" and result.cancelled
    assert not result.kpp_files and not result.standalone_paths and result.bundle_path is None
    assert all(item.stage != "deliver" for item in progress)
    assert inventory(out) == before


def test_cancel_during_actual_bundle_readback_prevents_publish(app, case_dir, monkeypatch):
    worker, results, _progress, controls = worker_run(case_dir)
    original = transaction.verify_bundle
    def verifying(path):
        assert transaction.STAGING_PREFIX in str(path)
        assert worker.request_cancel() == "accepted"
        return original(path)
    monkeypatch.setattr(transaction, "verify_bundle", verifying)
    worker.run()
    assert results[0].status == "cancelled" and "stopping" in controls
    assert inventory(case_dir / "output") == {}


@pytest.mark.parametrize("stage", ["deliver", "done"])
def test_delivery_and_completion_requests_do_not_relabel_committed_files(app, case_dir, stage):
    worker, results, _progress, controls = worker_run(case_dir)
    replies = []
    worker.progress.connect(lambda item: replies.append(worker.request_cancel()) if item.stage == stage else None)
    worker.run()
    result = results[0]
    assert replies == ["too_late"]
    assert result.status == "committed" and not result.cancelled
    assert "stopping" not in controls
    assert result.transaction.cancel_requested_late is (stage == "deliver")
    assert len(result.standalone_paths) == 3
    for path in result.standalone_paths.values():
        assert read_preset_xml(Path(path).read_bytes())
    assert result.verify.ok


def test_request_accepted_after_last_checkpoint_still_reports_actual_commit(app, case_dir, monkeypatch):
    worker, results, _progress, controls = worker_run(case_dir)
    original = transaction.ExportTransaction._notify_write
    replies = []
    def after_checkpoint(tx, message):
        replies.append(worker.request_cancel())
        original(tx, message)
    monkeypatch.setattr(transaction.ExportTransaction, "_notify_write", after_checkpoint)
    worker.run()
    assert replies == ["accepted"]
    assert controls == ["cancel_requested", "delivering"]
    assert results[0].status == "committed"
    assert results[0].transaction.cancel_requested_late


def test_background_signals_update_widgets_only_in_main_thread(window, windows, app, monkeypatch):
    load(window)
    gate = render_gate(monkeypatch, windows[1])
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    assert window.slot_threads and set(window.slot_threads) == {app.thread()}
    assert window.progress_bar.minimum() == window.progress_bar.maximum() == 0
    for group in (window.file_group, window.list_group, window.process_group, window.out_group):
        assert not group.isEnabled()
    assert not window.lang_combo.isEnabled() and not window.convert_btn.isEnabled()
    assert window.cancel_btn.isEnabled()
    config, ids = window._selection.options.dump_config(), window._selection.selected_ids
    window._set_all(False)
    window._on_checked(ids[0], False)
    window._on_lang_changed(1)
    window._on_switch_changed("rasterize_computed", False)
    assert window._selection.options.dump_config() == config
    assert window._selection.selected_ids == ids and i18n.get_language() == "zh"
    gate.release.set()
    finish(app, window)
    assert set(window.slot_threads) == {app.thread()}
    assert window._last_result.status == "committed"


def test_cancel_received_stopping_cancelled_then_restart_has_no_background_publish(window, windows, app, monkeypatch):
    load(window)
    gate = render_gate(monkeypatch, windows[1])
    out = Path(window.out_dir_edit.text())
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    window.cancel_btn.click()
    assert window._cancel_state == "cancel_requested"
    assert i18n.tr("progress_cancel_requested") in window.status_label.text()
    assert not window.cancel_btn.isEnabled()
    window._on_cancel()
    gate.release.set()
    finish(app, window)
    assert "stopping" in window.seen_controls
    assert window._last_result.status == "cancelled"
    assert window.status_label.text() == i18n.tr("result_cancelled")
    assert inventory(out) == {}
    window._on_convert()
    finish(app, window)
    assert window._last_result.status == "committed" and inventory(out)
    assert window.convert_btn.isEnabled()


def test_done_signal_does_not_unfreeze_before_thread_finished(window, windows, app, monkeypatch):
    load(window)
    gate = Gate()
    windows[1].append(gate)
    class TailWorker(workers.ConvertWorker):
        def run(self):
            super().run()
            gate.pause()
    monkeypatch.setattr(main_window, "ConvertWorker", TailWorker)
    window._on_convert()
    worker = window._convert_worker
    wait_for(app, lambda: gate.reached.is_set() and worker in window._worker_results)
    assert worker.isRunning() and window._busy
    assert not window.convert_btn.isEnabled() and not window.file_group.isEnabled()
    window._on_convert()
    assert window._convert_worker is worker and len(window._active_workers) == 1
    window._on_cancel()
    assert window._cancel_state == "finishing"
    gate.release.set()
    finish(app, window)
    assert not isValid(worker)
    assert window._last_result.status == "committed"


@pytest.mark.parametrize("status", ["empty", "all_skipped"])
def test_no_delivery_statuses_release_thread_and_never_claim_success(window, app, monkeypatch, status):
    load(window, [brush(missing=status == "all_skipped")])
    if status == "empty":
        monkeypatch.setattr(workers, "convert_presets", lambda *args, **kwargs: ConvertResult(status="empty"))
    window._on_convert()
    finish(app, window)
    assert window._last_result.status == status
    assert window.status_label.text() == i18n.tr("result_" + status)
    assert inventory(window.out_dir_edit.text()) == {}


@pytest.mark.parametrize("point", ["stage", "commit"])
def test_real_transaction_failure_cleans_thread_and_keeps_raw_manifest(window, windows, app, monkeypatch, point):
    load(window)
    def failed(*args, **kwargs):
        raise OSError("synthetic172-original-disk-error")
    monkeypatch.setattr(transaction, "_write_bytes" if point == "stage" else "_place_file", failed)
    window._on_convert()
    finish(app, window)
    failure = window._last_result
    assert isinstance(failure, workers.ConvertFailure)
    assert "synthetic172-original-disk-error" in failure.message
    assert failure.summary is not None
    assert failure.summary.status == ("failed" if point == "stage" else "rolled_back")
    assert window.status_label.text() == i18n.tr("convert_failed")
    assert any("synthetic172-original-disk-error" in str(message) for message in windows[2])
    assert window.convert_btn.isEnabled()


@pytest.mark.parametrize("kind", ["parse", "convert"])
def test_start_failure_also_releases_references(window, windows, monkeypatch, kind):
    load(window)
    base = workers.ParseWorker if kind == "parse" else workers.ConvertWorker
    class FailedStart(base):
        def start(self):
            raise RuntimeError("synthetic172-start-failed")
    monkeypatch.setattr(main_window, "ParseWorker" if kind == "parse" else "ConvertWorker", FailedStart)
    if kind == "parse":
        window._load_file("synthetic172.abr")
    else:
        window._on_convert()
    assert not window._active_workers and not window._busy
    assert window._parse_worker is None and window._convert_worker is None
    assert not window.cancel_btn.isEnabled() and window.file_group.isEnabled()
    assert any("synthetic172-start-failed" in str(message) for message in windows[2])


@pytest.mark.parametrize("cancel", [False, True])
def test_parse_real_boundaries_and_cancel_cleanup(window, windows, app, case_dir, monkeypatch, cancel):
    path = case_dir / "synthetic172.abr"
    path.write_bytes(bytes.fromhex("00060002"))
    gate = Gate()
    windows[1].append(gate)
    def mapping(abr):
        gate.pause()
        return [brush()]
    monkeypatch.setattr(workers, "map_presets", mapping)
    window._load_file(str(path))
    wait_for(app, gate.reached.is_set)
    assert [item.stage for item in window.seen_progress] == ["read", "parse", "map"]
    assert window._busy and not window.open_btn.isEnabled()
    if cancel:
        window._on_cancel()
    gate.release.set()
    finish(app, window)
    if cancel:
        assert window._selection is None and window._abr is None
        assert window.status_label.text() == i18n.tr("parse_cancelled")
        assert "stopping" in window.seen_controls
    else:
        assert len(window._selection.selected_ids) == 1
        assert window._selection.options.source_digest == source_digest_of_bytes(path.read_bytes())
    assert inventory(window.out_dir_edit.text()) == {}


def test_parse_failure_is_not_cancelled_and_restores_controls(window, windows, app, case_dir):
    path = case_dir / "bad172.abr"
    path.write_bytes(b"x")
    window._load_file(str(path))
    finish(app, window)
    assert window._selection is None and window._abr is None
    assert window.open_btn.isEnabled()
    assert any("ABR" in str(message) for message in windows[2])


@pytest.mark.parametrize("wait", [False, True])
def test_close_choice_while_generating_waits_or_keeps_window(window, windows, app, monkeypatch, wait):
    load(window)
    gate = render_gate(monkeypatch, windows[1])
    chosen, closed = [], []
    monkeypatch.setattr(window, "_choose_close_action", lambda: chosen.append(True) or wait)
    monkeypatch.setattr(window, "close", lambda: closed.append(not window._active_workers))
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window._close_pending is wait
    if wait:
        assert window._cancel_state == "cancel_requested"
        window.closeEvent(QCloseEvent())
        assert len(chosen) == 1
    else:
        assert window._convert_worker.cancel_state == "running"
    assert not closed
    gate.release.set()
    finish(app, window)
    wait_for(app, lambda: bool(closed) if wait else True)
    assert closed == ([True] if wait else [])
    assert window._last_result.status == ("cancelled" if wait else "committed")


def test_cancel_close_keeps_stop_request_and_allows_next_run(window, windows, app, monkeypatch):
    load(window)
    gate = render_gate(monkeypatch, windows[1])
    monkeypatch.setattr(window, "_choose_close_action", lambda: True)
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    window.closeEvent(QCloseEvent())
    assert window._close_pending
    window._abort_close()
    assert not window._close_pending
    assert window._convert_worker.cancel_state == "cancel_requested"
    gate.release.set()
    finish(app, window)
    assert not closed and window._last_result.status == "cancelled"
    window._on_convert()
    finish(app, window)
    assert window._last_result.status == "committed"


def test_close_in_final_delivery_waits_without_interrupting_transaction(window, windows, app, monkeypatch):
    load(window)
    gate = Gate()
    windows[1].append(gate)
    original = transaction._place_file
    def place(*args):
        if not gate.reached.is_set():
            gate.pause()
        return original(*args)
    monkeypatch.setattr(transaction, "_place_file", place)
    monkeypatch.setattr(window, "_choose_close_action", lambda: True)
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(not window._active_workers))
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    assert window._cancel_state == "delivering" and not window.cancel_btn.isEnabled()
    assert i18n.tr("progress_delivering") in window.status_label.text()
    window.closeEvent(QCloseEvent())
    assert not closed and window._close_pending
    gate.release.set()
    finish(app, window)
    wait_for(app, lambda: bool(closed))
    assert closed == [True]
    assert window._last_result.status == "committed"
    assert window._last_result.transaction.cancel_requested_late
    assert "stopping" not in window.seen_controls


def test_completion_while_close_question_is_open_rechecks_active_set(window, windows, app, monkeypatch):
    load(window)
    gate = render_gate(monkeypatch, windows[1])
    def choose_after_finished():
        gate.release.set()
        finish(app, window)
        return True
    monkeypatch.setattr(window, "_choose_close_action", choose_after_finished)
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() and not window._close_pending
    assert window._last_result.status == "committed"


def test_failure_while_waiting_to_close_keeps_manifest_visible(window, windows, app, monkeypatch):
    load(window)
    gate = render_gate(monkeypatch, windows[1])
    monkeypatch.setattr(window, "_choose_close_action", lambda: True)
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))
    # 在已开始的单支内抛错：取消请求不能把真实失败伪装成成功取消。
    def failed(*args, **kwargs):
        gate.pause()
        raise RuntimeError("synthetic172-render-failed-on-close")
    monkeypatch.setattr(conversion, "_render_preset", failed)
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    window.closeEvent(QCloseEvent())
    gate.release.set()
    finish(app, window)
    assert not closed and not window._close_pending
    assert isinstance(window._last_result, workers.ConvertFailure)
    assert any("synthetic172-render-failed-on-close" in str(message) for message in windows[2])


def test_closed_window_can_be_recreated_without_reusing_cancelled_worker(windows, app, monkeypatch):
    first = windows[0]()
    load(first)
    gate = render_gate(monkeypatch, windows[1])
    monkeypatch.setattr(first, "_choose_close_action", lambda: True)
    first._on_convert()
    old = first._convert_worker
    wait_for(app, gate.reached.is_set)
    first.closeEvent(QCloseEvent())
    gate.release.set()
    finish(app, first)
    app.processEvents()
    assert first._last_result.status == "cancelled" and not isValid(old)
    second = windows[0]()
    load(second)
    second._on_convert()
    finish(app, second)
    assert second._last_result.status == "committed"


def test_stale_worker_signals_cannot_change_next_run(window, windows, app, monkeypatch):
    load(window)
    gate = render_gate(monkeypatch, windows[1])
    stale = workers.ParseWorker("unused172.abr", window)
    stale.progress.connect(window._on_progress)
    stale.control_changed.connect(window._on_control_changed)
    stale.done.connect(window._on_parsed)
    stale_converter = workers.ConvertWorker(AbrFile(6, 2), [], window.out_dir_edit.text(), False, True, window)
    stale_converter.done.connect(window._on_converted)
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    old = window.status_label.text()
    stale.progress.emit(workers.WorkerProgress("deliver", ProgressEvent("write", 3, 3)))
    stale.control_changed.emit("stopping")
    selection = window._selection
    stale.done.emit(AbrFile(6, 2), [brush("must-not-replace172")])
    stale_converter.done.emit(ConvertResult(status="cancelled", cancelled=True))
    assert window.status_label.text() == old and window._cancel_state == "running"
    assert window._selection is selection and window._last_result is None
    stale.deleteLater()
    stale_converter.deleteLater()
    gate.release.set()
    finish(app, window)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_progress_text_counts_and_cancel_states_are_unambiguous(window, language):
    i18n.set_language(language)
    window._on_progress(workers.WorkerProgress("convert", ProgressEvent("convert", 7, 2, "bc1-test172")))
    assert window.progress_bar.maximum() == 7 and window.progress_bar.value() == 2
    assert "2/7" in window.progress_bar.format()
    assert "bc1-test172" in window.status_label.text()
    if language == "en":
        assert not re.search(r"[\u4e00-\u9fff]", window.status_label.text())
    for state in ("cancel_requested", "stopping", "delivering", "finishing"):
        window._cancel_state = state
        window._render_progress()
        assert window.status_label.text() == i18n.tr("progress_" + state)
    assert len({i18n.tr("progress_" + key) for key in
                ("cancel_requested", "stopping", "delivering", "finishing")}) == 4


def test_bilingual_keys_and_placeholders_still_match():
    assert i18n._STRINGS["zh"].keys() == i18n._STRINGS["en"].keys()
    for key, value in i18n._STRINGS["zh"].items():
        def fields(text):
            return {field for _, field, _, _ in Formatter().parse(text) if field}
        assert fields(value) == fields(i18n._STRINGS["en"][key]), key


def test_close_during_parse_waits_for_safe_boundary(window, windows, app, case_dir, monkeypatch):
    path = case_dir / "parse-close172.abr"
    path.write_bytes(bytes.fromhex("00060002"))
    gate = Gate()
    windows[1].append(gate)
    def mapping(abr):
        gate.pause()
        return [brush()]
    monkeypatch.setattr(workers, "map_presets", mapping)
    monkeypatch.setattr(window, "_choose_close_action", lambda: True)
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(not window._active_workers))
    window._load_file(str(path))
    wait_for(app, gate.reached.is_set)
    window.closeEvent(QCloseEvent())
    assert window._close_pending and not closed
    gate.release.set()
    finish(app, window)
    wait_for(app, lambda: bool(closed))
    assert closed == [True] and window._selection is None
    assert inventory(window.out_dir_edit.text()) == {}


def test_gui_cancel_during_real_verification_matches_disk_state(window, windows, app, monkeypatch):
    load(window)
    gate = Gate()
    windows[1].append(gate)
    original = transaction.verify_bundle
    def verify(path):
        gate.pause()
        return original(path)
    monkeypatch.setattr(transaction, "verify_bundle", verify)
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    assert window._progress.stage == "verify"
    assert window.progress_bar.minimum() == window.progress_bar.maximum() == 0
    assert window.cancel_btn.isEnabled()
    window._on_cancel()
    gate.release.set()
    finish(app, window)
    assert window._last_result.status == "cancelled"
    assert inventory(window.out_dir_edit.text()) == {}


def test_cancel_cleanup_retained_files_abort_automatic_close(window, windows, app, monkeypatch):
    load(window)
    window.radio_kpp.setChecked(True)
    gate = Gate()
    windows[1].append(gate)
    original_render, original_remove = conversion._render_preset, transaction._remove_file
    def render(bp, index, **kwargs):
        if index == 1:
            gate.pause()
        return original_render(bp, index, **kwargs)
    def remove(path):
        if Path(path).suffix == ".kpp":
            raise PermissionError("synthetic172-cleanup-locked")
        return original_remove(path)
    monkeypatch.setattr(conversion, "_render_preset", render)
    monkeypatch.setattr(transaction, "_remove_file", remove)
    monkeypatch.setattr(window, "_choose_close_action", lambda: True)
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))
    window._on_convert()
    wait_for(app, gate.reached.is_set)
    window.closeEvent(QCloseEvent())
    gate.release.set()
    finish(app, window)
    result = window._last_result
    assert result.status == "cancelled" and result.transaction.retained
    assert not result.standalone_paths and not closed and not window._close_pending
    assert any(Path(path).exists() for path in result.transaction.retained)
    assert any("synthetic172-cleanup-locked" in str(message) for message in windows[2])
    assert any(i18n.tr("retained_files") in message[1] for message in windows[2] if len(message) == 3)
    out = Path(window.out_dir_edit.text())
    assert all(transaction.STAGING_PREFIX in str(path) for path in out.rglob("*.kpp"))


def test_pending_close_completion_gap_cannot_start_another_export(window, app, monkeypatch):
    load(window)
    window._close_pending = True
    window._set_busy(False)
    assert not window.convert_btn.isEnabled() and not window.file_group.isEnabled()
    window._on_convert()
    assert window._convert_worker is None and not window._active_workers
    window._abort_close()
    assert window.convert_btn.isEnabled() and window.file_group.isEnabled()
    window._on_convert()
    finish(app, window)
    assert window._last_result.status == "committed"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_late_cancel_notice_is_visible_without_expanding_details(window, windows, case_dir, language):
    i18n.set_language(language)
    result = ConvertResult(status="committed")
    result.transaction = transaction.TransactionSummary(str(case_dir), "", "committed", "done", False,
                                                        cancel_requested_late=True)
    window._on_converted(result)
    expected = i18n.tr("result_cancel_late", result=i18n.tr("result_committed"))
    assert window.status_label.text() == expected
    assert windows[2][-1][0] == expected


def test_product_has_no_forced_thread_or_application_shutdown():
    for file in (ROOT / "gui" / "workers.py", ROOT / "gui" / "main_window.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        forbidden = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr in {"terminate", "kill", "wait"}}
        assert forbidden == set(), (file.name, forbidden)
