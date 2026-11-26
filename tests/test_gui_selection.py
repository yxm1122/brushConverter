"""170定向测试：纯状态机、离屏控件和GUI后台接口；不启动真实窗口或应用。"""

from __future__ import annotations

import itertools
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from string import Formatter
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
# 183A：全量运行时由 tests/conftest.py 发布统一任务根；单独运行时仍固定 170 目录。
TASK = Path(os.environ.get("BRUSHCONVERTER_TEST_TASK_ROOT", str(ROOT / ".tmp" / "170")))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QEventLoop,
    QSettings,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import ConvertResult  # noqa: E402
from brush_converter.export_options import (  # noqa: E402
    SWITCH_IDS,
    OptionConfigError,
    source_digest_of_bytes,
    switch_applicability,
)
from brush_converter.export_transaction import ExportTransactionError, TransactionSummary  # noqa: E402
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402
from gui import i18n, main_window, workers  # noqa: E402
from gui.brush_selection import (  # noqa: E402
    FILTER_MODES,
    SORT_MODES,
    BrushSelection,
    is_renderable,
    state_plan,
)
from gui.main_window import ExportConfirmDialog, MainWindow, export_preview_text  # noqa: E402

DIGEST = source_digest_of_bytes(b"task170-synthetic-source")


def brush(name="same", *, computed=True, texture=True):
    """只合成小数组，不使用商用ABR、旧基准图或系统临时目录。"""
    return BrushPreset(name, diameter=16, hardness=50, is_computed=computed,
        tip_gray=None if computed else np.full((8, 12), 128, dtype=np.uint8),
        texture=TextureSettings("pattern", "synthetic", scale=200, brightness=150,
            contrast=-50, image=np.arange(256, dtype=np.uint8).reshape(16, 16))
        if texture else None)


@pytest.fixture(scope="session")
def app():
    """仅离屏QApplication，不调用show、不运行真实主循环。"""
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    """每个窗口只读写工作区内独立INI，绝不写Windows注册表。"""
    assert tmp_path.resolve().is_relative_to(TASK.resolve())
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    settings.setValue("language", "zh")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    win = MainWindow()
    yield win
    win._parse_worker = None
    win._convert_worker = None
    win.close()
    win.deleteLater()
    application = QApplication.instance()
    application.processEvents()
    settings.sync()
    i18n.set_language("zh")


def load(win, presets, digest=DIGEST):
    win._parse_worker = SimpleNamespace(source_digest=digest, isRunning=lambda: False)
    win._file_path = "synthetic.abr"
    win._on_parsed(AbrFile(6, 2, name="synthetic"), presets)
    return win._selection


def test_isolation_is_verified(app):
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(TASK.resolve())
    for name in ("TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "HOME",
                 "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        assert Path(os.environ[name]).resolve().is_relative_to(TASK.resolve()), name
    assert Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation)).resolve().is_relative_to(TASK.resolve())


def test_duplicate_names_and_identical_content_keep_independent_ids():
    source = brush()
    model = BrushSelection([source, deepcopy(source)], DIGEST)
    first, second = model.options.preset_ids
    assert first != second
    model.options.set_override(first, SWITCH_IDS[0], False)
    model.set_selected(second, False)
    assert not model.options.enabled(first, SWITCH_IDS[0])
    assert model.options.enabled(second, SWITCH_IDS[0])
    assert model.selected_ids == (first,)
    assert model.options.identity(second).ordinal == 2


@pytest.mark.parametrize("sort", SORT_MODES)
@pytest.mark.parametrize("filter_mode", FILTER_MODES)
def test_view_changes_never_mutate_state(sort, filter_mode):
    model = BrushSelection([brush("z"), brush("a", computed=False, texture=False)], DIGEST)
    first, second = model.options.preset_ids
    model.options.set_override(first, SWITCH_IDS[0], False)
    model.set_selected(second, False)
    config = model.options.dump_config()
    model.set_view(query="a", filter_mode=filter_mode, sort_mode=sort)
    assert model.options.dump_config() == config
    assert model.selected_ids == (first,)
    model.set_view(query="", filter_mode="all", sort_mode="source")
    assert model.visible_ids == (first, second)


def test_hidden_selection_and_visible_bulk_actions():
    model = BrushSelection([brush("alpha"), brush("beta"), brush("gamma")], DIGEST)
    first, second, third = model.selected_ids
    model.set_view(query="beta")
    assert model.counts == dict(total=3, visible=1, sel=3, hidden=2)
    model.set_visible_selected(False)
    assert model.selected_ids == (first, third)
    assert model.counts["hidden"] == 2
    model.set_view(query="missing")
    model.set_visible_selected(False)
    assert model.selected_ids == (first, third)
    model.set_view(query="", filter_mode="selected")
    model.set_visible_selected(False)
    assert model.selected_ids == ()
    model.set_view(filter_mode="all")
    model.set_visible_selected(True)
    assert model.selected_ids == (first, second, third)


def test_search_by_id_casefold_and_ordinal():
    model = BrushSelection([brush("Alpha"), brush("Beta")], DIGEST)
    first, second = model.selected_ids
    model.set_view(query="  ALPHA  ")
    assert model.visible_ids == (first,)
    model.set_view(query=second.upper())
    assert model.visible_ids == (second,)
    model.set_view(query="0002-")
    assert model.visible_ids == (second,)
    with pytest.raises(ValueError):
        model.set_view(filter_mode="bad")
    with pytest.raises(ValueError):
        model.set_view(sort_mode="bad")


@pytest.mark.parametrize("scale,expected", [(100, False), (103.125, False), (103.2, True), (200, True)])
def test_prescale_default_uses_contract_rounding(scale, expected):
    preset = brush()
    preset.texture.scale = scale
    model = BrushSelection([preset], DIGEST)
    key = model.selected_ids[0]
    assert model.options.enabled(key, "prescale_texture") is expected
    assert state_plan(model.options, key).prescale_effective is expected


@pytest.mark.parametrize("brightness,expected", [(-128, True), (-127, False), (127, False), (128, True)])
def test_baking_default_uses_production_boundary(brightness, expected):
    preset = brush()
    preset.texture.brightness = brightness
    model = BrushSelection([preset], DIGEST)
    assert model.options.enabled(model.selected_ids[0], "bake_texture_bc") is expected


def test_missing_texture_is_inapplicable_and_cannot_enable():
    preset = brush(computed=False)
    preset.texture.image = None
    model = BrushSelection([preset], DIGEST)
    key = model.selected_ids[0]
    for switch in SWITCH_IDS:
        assert not model.options.enabled(key, switch)
        with pytest.raises(OptionConfigError, match="inapplicable_on"):
            model.options.set_override(key, switch, True)


@pytest.mark.parametrize("values", list(itertools.product([False, True], repeat=3)))
@pytest.mark.parametrize("language", ["zh", "en"])
def test_forecast_uses_actual_production_plan_not_contract_baseline(values, language):
    i18n.set_language(language)
    preset = brush("synthetic")
    model = BrushSelection([preset], DIGEST)
    key = model.selected_ids[0]
    for switch, value in zip(SWITCH_IDS, values):
        model.options.set_override(key, switch, value)
    plan = state_plan(model.options, key)
    assert [item.actual for item in plan.outcomes] == ["applied" if value else "alternative" for value in values]
    text = export_preview_text(model.snapshot())
    assert "pending_alternative" not in text
    assert "尚未接入" not in text
    assert key in text
    assert "{" not in text
    expected = i18n.tr("export_counts", applied=sum(values), alternative=3-sum(values),
                       limited=sum(item.limited for item in plan.outcomes), skipped=0)
    assert expected in text
    if language == "en":
        assert not re.search(r"[\u4e00-\u9fff]", text)
    i18n.set_language("zh")


def test_forecast_keeps_hidden_duplicate_names_and_excludes_unselected():
    model = BrushSelection([brush("same"), brush("same"), brush("excluded")], DIGEST)
    first, second, third = model.selected_ids
    model.set_selected(third, False)
    model.set_view(query="not found")
    snapshot = model.snapshot()
    text = export_preview_text(snapshot)
    assert snapshot.hidden_selected == 2
    assert first in text and second in text and third not in text
    assert [item.ordinal for item in snapshot.identities] == [1, 2]


def test_missing_tip_never_counts_as_expected_processing():
    preset = brush(computed=False)
    preset.tip_gray = None
    model = BrushSelection([preset], DIGEST)
    assert not is_renderable(preset)
    text = export_preview_text(model.snapshot())
    assert i18n.tr("skipped_tip") in text
    assert i18n.tr("export_counts", applied=0, alternative=0, limited=0, skipped=1) in text


def test_snapshot_detaches_config_selection_and_mutable_arrays():
    preset = brush()
    model = BrushSelection([preset], DIGEST)
    key = model.selected_ids[0]
    snapshot = model.snapshot()
    model.options.set_override(key, SWITCH_IDS[0], False)
    model.set_selected(key, False)
    preset.texture.image[:] = 99
    state = snapshot.option_state()
    assert state.enabled(key, SWITCH_IDS[0])
    assert state.preset(key).texture.image[0, 0] == 0
    state.preset(key).texture.image[:] = 88
    assert snapshot.option_state().preset(key).texture.image[0, 0] == 0
    assert json.loads(snapshot.config_json)["source_digest"] == DIGEST


def test_empty_window_and_inapplicable_controls(window):
    assert not window.convert_btn.isEnabled()
    assert not window.reload_btn.isEnabled()
    model = load(window, [brush("sample", computed=False, texture=False)])
    for switch in SWITCH_IDS:
        assert not window.switch_checks[switch].isEnabled()
        assert not window.switch_checks[switch].isChecked()
        assert window.switch_checks[switch].toolTip()
    assert model.counts["sel"] == 1
    assert window.convert_btn.isEnabled()


def test_language_sort_rebuild_preserve_id_selection_switches_and_focus(window):
    model = load(window, [brush("same"), brush("same"), brush("aaa")])
    first, second, third = model.selected_ids
    window.brush_list.setCurrentItem(window._items[second][0])
    window.switch_checks[SWITCH_IDS[0]].setChecked(False)
    window._items[first][1].checkbox.setChecked(False)
    window.search_edit.setText("same")
    window.sort_combo.setCurrentIndex(2)
    old_config = model.options.dump_config()
    for language in (1, 0, 1, 0):
        window.lang_combo.setCurrentIndex(language)
        window._populate()
        assert model.options.dump_config() == old_config
        assert model.selected_ids == (second, third)
        assert model.focused_id == second
        assert window.brush_list.currentItem().data(Qt.ItemDataRole.UserRole) == second
        assert not window.switch_checks[SWITCH_IDS[0]].isChecked()
        assert window._items[third][0].isHidden()
        assert window.search_edit.text() == "same"
    assert set(window._settings.allKeys()) == {"language"}


def test_ui_bulk_actions_only_change_visible_items(window):
    model = load(window, [brush("alpha"), brush("beta")])
    first, second = model.selected_ids
    window.search_edit.setText("beta")
    window._set_all(False)
    assert model.selected_ids == (first,)
    assert model.counts["hidden"] == 1
    assert window._items[first][1].checkbox.isChecked()
    assert not window._items[second][1].checkbox.isChecked()
    assert "1" in window.count_label.text()
    assert window.convert_btn.isEnabled()
    window.search_edit.clear()
    window._set_all(True)
    window.filter_combo.setCurrentIndex(1)
    window._set_all(False)
    assert model.visible_ids == ()
    assert not window.convert_btn.isEnabled()


@pytest.mark.parametrize("new_source", [False, True])
def test_reload_or_new_file_resets_all_manual_values(window, monkeypatch, new_source):
    old = load(window, [brush()])
    key = old.selected_ids[0]
    old.options.set_override(key, SWITCH_IDS[0], False)
    window._set_all(False)
    class FakeParser:
        def __init__(self, path, parent=None):
            self.source_digest = source_digest_of_bytes(b"different") if new_source else DIGEST
            self.done = SimpleNamespace(connect=lambda callback: None)
            self.finished = SimpleNamespace(connect=lambda callback: None)
        def start(self):
            pass
        def isRunning(self):
            return False
    monkeypatch.setattr(main_window, "ParseWorker", FakeParser)
    window._load_file("other.abr" if new_source else "synthetic.abr")
    assert window._selection is None
    assert not window.convert_btn.isEnabled()
    window._on_parsed(AbrFile(6, 2), [brush()])
    current = window._selection
    new_key = current.selected_ids[0]
    assert current.options.enabled(new_key, SWITCH_IDS[0])
    assert new_key != key if new_source else new_key == key
    assert current.counts == dict(total=1, visible=1, sel=1, hidden=0)


def test_parse_failure_clears_previous_state_and_missing_digest_blocks(window, monkeypatch):
    load(window, [brush()])
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    window._on_parsed(None, "original parse error")
    assert window._selection is None and not window._presets
    assert window.brush_list.count() == 0 and not window.convert_btn.isEnabled()
    assert errors == ["original parse error"]
    window._parse_worker.source_digest = None
    window._on_parsed(AbrFile(6, 2), [brush()])
    assert i18n.tr("missing_digest") in errors[-1]
    assert window._selection is None


def test_confirmation_is_full_readonly_and_rejection_starts_no_worker(window, monkeypatch):
    model = load(window, [brush("same"), brush("same")])
    window.search_edit.setText("nothing")
    dialog = ExportConfirmDialog(model.snapshot(), "synthetic", ".bundle", window)
    assert dialog.preview.isReadOnly()
    assert all(key in dialog.preview.toPlainText() for key in model.selected_ids)
    dialog.deleteLater()
    monkeypatch.setattr(ExportConfirmDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    window._on_convert()
    assert window._convert_worker is None
    assert not window._busy


def test_export_passes_confirmed_snapshot_even_if_live_model_changes(window, monkeypatch, tmp_path):
    model = load(window, [brush("same"), brush("same")])
    first, second = model.selected_ids
    model.set_selected(first, False)
    window.out_dir_edit.setText(str(tmp_path / "output"))
    def confirm(dialog):
        assert second in dialog.preview.toPlainText()
        model.options.set_override(second, SWITCH_IDS[0], False)
        model.set_selected(second, False)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ExportConfirmDialog, "exec", confirm)
    captured = []
    class FakeConverter:
        def __init__(self, *args, snapshot, **kwargs):
            captured.append(snapshot)
            self.done = SimpleNamespace(connect=lambda callback: None)
            self.finished = SimpleNamespace(connect=lambda callback: None)
        def start(self):
            pass
        def isRunning(self):
            return False
    monkeypatch.setattr(main_window, "ConvertWorker", FakeConverter)
    window._on_convert()
    assert len(captured) == 1
    snapshot = captured[0]
    assert tuple(item.preset_id for item in snapshot.identities) == (second,)
    assert snapshot.option_state().enabled(second, SWITCH_IDS[0])
    assert window._busy and not window.list_group.isEnabled()
    assert not window.reload_btn.isEnabled()


def test_worker_keeps_original_ordinals_and_independent_same_content_choices(app, tmp_path, monkeypatch):
    preset = brush()
    model = BrushSelection([deepcopy(preset), deepcopy(preset), deepcopy(preset)], DIGEST)
    first, second, third = model.selected_ids
    model.set_selected(first, False)
    model.options.set_override(second, SWITCH_IDS[0], False)
    snapshot = model.snapshot()
    seen = []
    def fake_convert(abr, presets, out_dir, **kwargs):
        state = kwargs["option_state"]
        assert [x.ordinal for x in state.identities] == [2, 3]
        assert state.preset_ids == (second, third)
        assert not state.enabled(second, SWITCH_IDS[0])
        assert state.enabled(third, SWITCH_IDS[0])
        assert all(state.preset(key) is value for key, value in zip(state.preset_ids, presets))
        seen.append(out_dir)
        return ConvertResult(status="empty")
    monkeypatch.setattr(workers, "convert_presets", fake_convert)
    worker = workers.ConvertWorker(AbrFile(6, 2), [], str(tmp_path), False, True, snapshot=snapshot)
    model.options.set_override(second, SWITCH_IDS[0], True)
    received = []
    worker.done.connect(received.append)
    worker.run()
    assert seen == [str(tmp_path)] and received[0].status == "empty"


def test_worker_real_conversion_uses_snapshot_and_169_safe_reuse(app, tmp_path):
    model = BrushSelection([brush("same"), brush("same")], DIGEST)
    first, second = model.selected_ids
    model.set_selected(first, False)
    model.options.set_override(second, SWITCH_IDS[0], False)
    snapshot = model.snapshot()
    results = []
    for _ in range(2):
        worker = workers.ConvertWorker(AbrFile(6, 2, name="synthetic"), [],
            str(tmp_path / "output"), False, True, snapshot=snapshot)
        worker.done.connect(results.append)
        worker.run()
    assert all(not isinstance(result, str) for result in results), results
    first_run, reused = results
    assert first_run.status == "committed" and reused.status == "reused_only"
    assert first_run.options_summary.evidence == "conversion-run"
    assert first_run.options_summary.items[0].identity.ordinal == 2
    assert first_run.option_plans[second].native_tip
    assert first_run.option_plans[second].outcome(SWITCH_IDS[0]).actual == "alternative"
    assert first not in first_run.option_plans
    assert all(Path(path).resolve().is_relative_to(tmp_path.resolve())
               for path in first_run.standalone_paths.values())


def test_parser_digest_and_presets_use_same_raw_bytes(app, tmp_path, monkeypatch):
    path = tmp_path / "synthetic.abr"
    initial = bytes.fromhex("00060002")
    path.write_bytes(initial)
    def mapping(abr):
        path.write_bytes(b"changed-after-read")
        return [brush()]
    monkeypatch.setattr(workers, "map_presets", mapping)
    worker = workers.ParseWorker(str(path))
    received = []
    worker.done.connect(lambda *args: received.append(args))
    worker.run()
    assert worker.source_digest == source_digest_of_bytes(initial)
    assert isinstance(received[0][0], AbrFile)
    assert received[0][1][0].name == "same"


def test_parser_preserves_original_exception(app, tmp_path, capsys):
    path = tmp_path / "broken.abr"
    path.write_bytes(b"x")
    worker = workers.ParseWorker(str(path))
    received = []
    worker.done.connect(lambda *args: received.append(args))
    worker.run()
    assert received[0][0] is None
    assert "ABR" in received[0][1]
    assert "ValueError" in capsys.readouterr().err


@pytest.mark.parametrize("status", ["committed", "reused_only", "all_skipped", "empty", "cancelled"])
def test_169_status_and_delivery_details_are_honest(window, monkeypatch, status):
    model = load(window, [brush()])
    window._export_snapshot = model.snapshot()
    messages = []
    def no_real_dialog(message):
        messages.append((message.windowTitle(), message.text(), message.detailedText()))
        return 0
    monkeypatch.setattr(QMessageBox, "exec", no_real_dialog)
    result = ConvertResult(status=status, cancelled=status == "cancelled")
    result.transaction = SimpleNamespace(items=[SimpleNamespace(state="reused", renamed_from="", final_path="synthetic.kpp")])
    window._on_converted(result)
    assert window.status_label.text() == i18n.tr(f"result_{status}")
    assert messages[0][1] == i18n.tr(f"result_{status}")
    assert i18n.tr("run_missing") in messages[0][2]
    assert i18n.tr("reused_notice", path="synthetic.kpp") in messages[0][2]
    if status not in ("committed", "reused_only"):
        assert messages[0][0] != i18n.tr("done_title")


def test_duplicate_object_reference_still_gets_independent_snapshot_objects():
    source = brush()
    model = BrushSelection([source, source], DIGEST)
    first, second = model.selected_ids
    model.options.set_override(first, SWITCH_IDS[0], False)
    state = model.snapshot().option_state()
    assert state.preset(first) is not state.preset(second)
    assert not state.enabled(first, SWITCH_IDS[0])
    assert state.enabled(second, SWITCH_IDS[0])


def test_output_paths_warnings_and_verify_errors_remain_visible(window, monkeypatch):
    model = load(window, [brush()])
    assert i18n.tr("computed_brush") in window._items[model.selected_ids[0]][1].source_info.text()
    messages = []
    monkeypatch.setattr(QMessageBox, "exec", lambda message: messages.append(
        (message.informativeText(), message.detailedText())) or 0)
    result = ConvertResult(status="committed", standalone_paths={"synthetic.kpp": "synthetic_2.kpp"},
        warnings=["original transaction warning"], verify=SimpleNamespace(ok=False, errors=["verify detail"]))
    result.transaction = SimpleNamespace(items=[SimpleNamespace(
        state="committed", renamed_from="synthetic.kpp", final_path="synthetic_2.kpp")])
    window._on_converted(result)
    assert "synthetic_2.kpp" in messages[0][0]
    assert "original transaction warning" in messages[0][1]
    assert "verify detail" in messages[0][1]
    assert i18n.tr("renamed_notice", path="synthetic_2.kpp") in messages[0][1]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_worker_failure_keeps_blocked_retained_and_rollback_manifest(window, monkeypatch, tmp_path, language, capsys):
    i18n.set_language(language)
    retained = str(tmp_path / "retained.kpp")
    rolled_back = str(tmp_path / "removed.kpp")
    summary = TransactionSummary(str(tmp_path), str(tmp_path / "staging"),
        "partial_blocked", "staging", False, retained=[retained], rolled_back=[rolled_back],
        blocked=[{"path": retained, "reason": "synthetic locked file"}])
    def failed(*args, **kwargs):
        raise ExportTransactionError("original staging failure", phase="staging", summary=summary)
    monkeypatch.setattr(workers, "convert_presets", failed)
    worker = workers.ConvertWorker(AbrFile(6, 2), [brush()], str(tmp_path), False, True)
    received = []
    worker.done.connect(received.append)
    worker.run()
    failure = received[0]
    assert isinstance(failure, workers.ConvertFailure)
    assert failure.phase == "staging" and failure.summary is summary
    assert "ExportTransactionError" in capsys.readouterr().err
    messages = []
    monkeypatch.setattr(QMessageBox, "exec", lambda message: messages.append(
        (message.text(), message.informativeText(), message.detailedText())) or 0)
    window._on_converted(failure)
    assert messages[0][0] == i18n.tr("convert_failed")
    assert retained in messages[0][1] and rolled_back in messages[0][1]
    assert i18n.tr("transaction_partial_blocked") in messages[0][1]
    assert "original staging failure" in messages[0][2]
    assert "synthetic locked file" in messages[0][2]
    i18n.set_language("zh")


def test_cancelled_planned_suffix_is_not_reported_as_delivered(window, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "exec", lambda message: messages.append(message.detailedText()) or 0)
    result = ConvertResult(status="cancelled", cancelled=True)
    result.transaction = SimpleNamespace(items=[SimpleNamespace(state="verified",
        renamed_from="synthetic.kpp", final_path="synthetic_2.kpp")])
    window._on_converted(result)
    assert i18n.tr("renamed_notice", path="synthetic_2.kpp") not in messages[0]
    assert window.status_label.text() == i18n.tr("result_cancelled")


def test_real_thread_start_finished_releases_parse_and_repeated_export(window, app, monkeypatch, tmp_path):
    """真实QThread生命周期，仍离屏且不启动真实主窗口；finished后清引用并释放。"""
    class Parser(QThread):
        done = Signal(object, object)
        source_digest = DIGEST
        def __init__(self, path, parent=None):
            super().__init__(parent)
        def run(self):
            self.done.emit(AbrFile(6, 2), [brush()])
    monkeypatch.setattr(main_window, "ParseWorker", Parser)
    monkeypatch.setattr(ExportConfirmDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(workers, "convert_presets", lambda *args, **kwargs: ConvertResult(status="empty"))
    original_track = window._track_worker
    def cycle(start, attribute):
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        captured = []
        def track(worker):
            original_track(worker)
            captured.append(worker)
            worker.finished.connect(loop.quit)
        monkeypatch.setattr(window, "_track_worker", track)
        timer.start(3000)
        start()
        loop.exec()
        timer.stop()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert len(captured) == 1
        assert getattr(window, attribute) is None
        assert not window._active_workers
        assert not isValid(captured[0])
        assert not window.findChildren(QThread)
    cycle(lambda: window._load_file("synthetic.abr"), "_parse_worker")
    window.out_dir_edit.setText(str(tmp_path))
    cycle(window._on_convert, "_convert_worker")
    cycle(window._on_convert, "_convert_worker")


def test_bilingual_keys_placeholders_and_all_applicability_reasons():
    assert i18n._STRINGS["zh"].keys() == i18n._STRINGS["en"].keys()
    for key, text in i18n._STRINGS["zh"].items():
        zh_fields = {field for _, field, _, _ in Formatter().parse(text) if field}
        en_fields = {field for _, field, _, _ in Formatter().parse(i18n._STRINGS["en"][key]) if field}
        assert zh_fields == en_fields, key
    reasons = set()
    for scale in (0, 100, 103.125, 200, float("nan"), float("inf"), 1e308):
        for image in (None, np.zeros((16, 16), dtype=np.uint8), np.zeros((0, 0)), np.zeros(3)):
            preset = brush()
            preset.texture.scale = scale
            preset.texture.image = image
            for switch in SWITCH_IDS:
                reasons.add(switch_applicability(preset, switch).reason)
    reasons.add(switch_applicability(brush(computed=False), SWITCH_IDS[0]).reason)
    normal = brush()
    normal.texture.brightness = 0
    reasons.add(switch_applicability(normal, SWITCH_IDS[2]).reason)
    i18n.set_language("en")
    for reason in reasons:
        assert not re.search(r"[\u4e00-\u9fff]", i18n.tr_applicability(reason)), reason
    i18n.set_language("zh")
