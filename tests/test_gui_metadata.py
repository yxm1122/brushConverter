"""177机器测试：离屏状态、标签/元信息快照与真实核心组包；实机显示留187。"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from string import Formatter

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
TASK = ROOT / ".tmp" / "177"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QSettings, QStandardPaths  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import ConvertResult  # noqa: E402
from brush_converter.export_metadata import DEFAULT_DESCRIPTION, PresetTagPlan  # noqa: E402
from brush_converter.export_options import source_digest_of_bytes  # noqa: E402
from brush_converter.kpp.bundle import BundleVerifyResult, verify_bundle  # noqa: E402
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402
from gui import i18n, main_window, workers  # noqa: E402
from gui.brush_selection import FILTER_MODES, SORT_MODES, BrushSelection  # noqa: E402
from gui.main_window import (  # noqa: E402
    ExportConfirmDialog,
    MainWindow,
    export_preview_text,
    metadata_result_text,
)
from gui.tag_editor import MetadataExportSnapshot, TagState, tag_plan_text  # noqa: E402


def verify_isolation():
    """先于窗口/配置创建核验落点，不能隔离就失败，不跳过。"""
    for name in ("TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "HOME",
                 "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        assert Path(os.environ[name]).resolve().is_relative_to(TASK.resolve()), name
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(TASK.resolve())
    assert Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation)).resolve().is_relative_to(TASK.resolve())
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
    assert sys.dont_write_bytecode


@pytest.fixture(scope="session")
def app():
    verify_isolation()
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    verify_isolation()
    assert tmp_path.resolve().is_relative_to(TASK.resolve())
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    settings.setValue("language", "zh")
    # 用诱饵证明作者和许可不从已有配置读取；只允许保存语言偏好。
    settings.setValue("author", "不应继承的作者")
    settings.setValue("license", "不应继承的许可")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    win = MainWindow()
    yield win
    assert not win._active_workers, "测试不得遗留后台线程"
    win.close()
    win.deleteLater()
    app.processEvents()
    settings.sync()
    assert set(settings.allKeys()) == {"language", "author", "license"}
    assert settings.value("author") == "不应继承的作者"
    assert settings.value("license") == "不应继承的许可"
    i18n.set_language("zh")


def brush(name="同名合成笔刷", *, missing=False):
    """小数组全在内存合成；不碰原始ABR、旧笔尖或基准图。"""
    return BrushPreset(name, diameter=16, hardness=50, is_computed=not missing,
        texture=TextureSettings("pattern", "synthetic", scale=200, brightness=150,
            contrast=-50, image=np.arange(64, dtype=np.uint8).reshape(8, 8)) if not missing else None)


def load(win, tmp_path, presets=None):
    """固定源字节配合合成已解析对象，测试GUI之后的链路，不冒充解析器测试。"""
    source = tmp_path / "来源 原始.abr"
    source.write_bytes(b"task177-synthetic-source-bytes")
    win._file_path = str(source)
    win._on_parsed(AbrFile(6, 2, name=source.stem),
        presets if presets is not None else [brush(), brush()],
        source_digest=source_digest_of_bytes(source.read_bytes()), show_dialog=False)
    return win._selection


def focus(win, key):
    win.brush_list.setCurrentItem(win._items[key][0])
    assert win._selection.focused_id == key


def run_snapshot(snapshot, tmp_path, *, abr=None):
    """同步执行真实worker.run，不启动系统GUI或Qt主循环。"""
    results = []
    worker = workers.ConvertWorker(abr or AbrFile(6, 2, name="来源 原始"), [],
        str(tmp_path / "output"), snapshot.make_bundle, not snapshot.make_bundle, snapshot=snapshot)
    worker.done.connect(results.append)
    worker.run()
    assert len(results) == 1
    return results[0]


def test_isolation_before_any_configuration(app):
    verify_isolation()


def test_only_requested_fields_and_no_system_or_previous_identity(window, tmp_path):
    assert not window.metadata_group.isEnabled()
    load(window, tmp_path)
    assert set(window.metadata_fields) == {"name", "description", "author", "license", "version"}
    assert window.metadata_fields["author"].text() == ""
    assert window.metadata_fields["license"].toPlainText() == ""
    assert window.metadata_fields["version"].text() == ""
    assert window.metadata_fields["description"].toPlainText() == ""
    snapshot = window._make_export_snapshot()
    assert isinstance(snapshot, MetadataExportSnapshot)
    assert snapshot.metadata.source_filename == "来源 原始.abr"
    assert snapshot.metadata.name == "来源 原始"
    assert snapshot.metadata.author == snapshot.metadata.license == ""
    assert snapshot.metadata.email == snapshot.metadata.website == ""
    assert snapshot.metadata.created == snapshot.metadata.updated == ""
    assert DEFAULT_DESCRIPTION in export_preview_text(snapshot)
    assert "MIT" not in snapshot.metadata.license
    assert window.metadata_source.text() == "来源 原始.abr"
    assert snapshot.preset_tags() == {key: ("来源 原始",) for key in snapshot.option_state().preset_ids}


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_title_preview_uses_same_core_fallback(window, tmp_path, name):
    load(window, tmp_path)
    window.metadata_fields["name"].setText(name)
    snapshot = window._make_export_snapshot()
    assert snapshot.metadata.name == "来源 原始"
    result = run_snapshot(snapshot, tmp_path)
    assert result.verify.ok
    assert result.verify.meta["dc:title"] == snapshot.metadata.name


@pytest.mark.parametrize("field,value,element", [
    ("name", "自定义包 & <标题>", "dc:title"),
    ("description", "合成说明 & <文本>\n下一行", "dc:description"),
    ("author", "合成作者／版权方", "dc:author"),
    ("license", "合成素材许可；非项目MIT", "meta:license"),
    ("version", "素材版2.3", "meta:brushconverter-package-version"),
])
def test_explicit_fields_preview_equal_real_readback(window, tmp_path, field, value, element):
    load(window, tmp_path)
    edit = window.metadata_fields[field]
    edit.setPlainText(value) if hasattr(edit, "toPlainText") else edit.setText(value)
    snapshot = window._make_export_snapshot()
    assert value in export_preview_text(snapshot)
    result = run_snapshot(snapshot, tmp_path)
    assert result.status == "committed" and result.verify.ok
    assert result.verify.meta[element] == value
    assert value in metadata_result_text(result)


def test_source_global_delete_is_not_automatically_reinjected(window, tmp_path):
    load(window, tmp_path)
    window.tag_editor.source_edit.clear()
    snapshot = window._make_export_snapshot()
    assert all(tags == () for tags in snapshot.preset_tags().values())
    assert "←" not in export_preview_text(snapshot)
    result = run_snapshot(snapshot, tmp_path)
    assert result.verify.ok
    assert all(tags == () for tags in result.verify.preset_tags.values())
    assert all(row["tags"] == [] for row in result.verify.trace["presets"])
    assert result.verify.meta["meta:brushconverter-source"] == "来源 原始.abr"


def test_source_rename_shared_and_per_brush_removal_take_priority(window, tmp_path):
    model = load(window, tmp_path)
    first, second = model.selected_ids
    editor = window.tag_editor
    editor.source_edit.setPlainText("新来源")
    editor.common_edit.setPlainText("共享\nInk")
    focus(window, first)
    editor.added_edit.setPlainText("新来源\n单支")
    editor.removed_edit.setPlainText("新来源\nink")
    assert window._tags.plan(first).final() == ("共享", "单支")
    assert window._tags.plan(second).final() == ("Ink", "共享", "新来源")
    text = editor.final_edit.toPlainText()
    assert "共享 ← 公共" in text and "单支 ← 单支追加" in text
    assert "单支已排除：ink、新来源" in text
    assert "新来源" not in window._items[first][1].tags.text()
    result = run_snapshot(window._make_export_snapshot(), tmp_path)
    assert result.verify.ok
    by_id = {row["id"]: tuple(row["tags"]) for row in result.verify.trace["presets"]}
    assert by_id == window._make_export_snapshot().preset_tags()
    assert "来源 原始" not in str(result.verify.preset_tags)


def test_same_tag_can_show_multiple_origins_and_is_normalized():
    state = TagState(("id1", "id2"), "source.abr")
    state.common_text = " Ink \nCafe\u0301\n重复  空白"
    state.source_text = "ink\nCafé"
    state.edits["id1"] = ("INK\n重复 空白", "")
    assert state.plan("id1").final() == ("Café", "INK", "重复 空白")
    i18n.set_language("zh")
    text = tag_plan_text(state.plan("id1"))
    assert "INK ← 公共 + 来源 + 单支追加" in text
    state.edits["id1"] = ("INK", "ink")
    assert "INK" not in state.plan("id1").final()
    assert state.plan("id2").final() == ("Café", "Ink", "重复 空白")
    with pytest.raises(KeyError):
        state.plan("unknown")


@pytest.mark.parametrize("sort", SORT_MODES)
@pytest.mark.parametrize("filter_mode", FILTER_MODES)
def test_view_sort_and_language_never_lose_same_named_edits(window, tmp_path, sort, filter_mode):
    model = load(window, tmp_path)
    first, second = model.selected_ids
    assert first != second
    window.metadata_fields["author"].setText("本次作者")
    editor = window.tag_editor
    editor.common_edit.setPlainText("  共同标签  ")
    focus(window, first)
    editor.added_edit.setPlainText("  甲标签  ")
    editor.removed_edit.setPlainText("来源 原始")
    model.options.set_override(first, "bake_texture_bc", False)
    focus(window, second)
    editor.added_edit.setPlainText("乙标签")
    original = window._tags.freeze(model.selected_ids)
    model.set_view(sort_mode=sort, filter_mode=filter_mode, query=first)
    window._populate()
    assert model.counts["hidden"] >= 1
    window.lang_combo.setCurrentIndex(1)
    window.lang_combo.setCurrentIndex(0)
    model.set_view(query="", filter_mode="all")
    window._populate()
    assert window._tags.freeze(model.selected_ids) == original
    assert editor.common_edit.toPlainText() == "  共同标签  "
    assert window.metadata_fields["author"].text() == "本次作者"
    focus(window, first)
    assert editor.added_edit.toPlainText() == "  甲标签  "
    assert not model.options.enabled(first, "bake_texture_bc")
    focus(window, second)
    assert editor.added_edit.toPlainText() == "乙标签"


def test_hidden_selected_and_original_ordinals_used_for_tag_snapshot(window, tmp_path):
    model = load(window, tmp_path, [brush(), brush(), brush()])
    first, second, third = model.selected_ids
    window.tag_editor.common_edit.setPlainText("共享")
    focus(window, second)
    window.tag_editor.removed_edit.setPlainText("来源 原始")
    window._on_checked(first, False)
    window.search_edit.setText("nothing-visible")
    snapshot = window._make_export_snapshot()
    assert snapshot.hidden_selected == 2
    assert tuple(row.ordinal for row in snapshot.identities) == (2, 3)
    assert set(snapshot.preset_tags()) == {second, third}
    preview = export_preview_text(snapshot)
    assert first not in preview and second in preview and third in preview
    result = run_snapshot(snapshot, tmp_path)
    assert result.verify.ok
    assert [(row["id"], row["ordinal"]) for row in result.verify.trace["presets"]] == [(second, 2), (third, 3)]


def test_bundle_only_inputs_disabled_kpp_does_not_validate_or_write_them(window, tmp_path):
    load(window, tmp_path)
    window.metadata_fields["author"].setText("bundle作者")
    window.tag_editor.common_edit.setPlainText("bundle标签")
    window.radio_kpp.setChecked(True)
    assert not window.metadata_group.isEnabled() and not window.tag_editor.isEnabled()
    assert "不承载" in window.metadata_scope.text()
    # 停用字段即使含坏文字也不能阻止独立kpp，不得偷偷把其内容传核心。
    window.metadata_fields["license"].setPlainText("bad\x01")
    snapshot = window._make_export_snapshot()
    assert snapshot.metadata is None and snapshot.tag_plans == ()
    assert "bundle作者" not in export_preview_text(snapshot)
    assert "bundle标签" not in export_preview_text(snapshot)
    result = run_snapshot(snapshot, tmp_path)
    assert result.status == "committed" and len(result.standalone_paths) == 2
    assert result.bundle_path is None and result.verify is None
    assert metadata_result_text(result) == i18n.tr("metadata_kpp_result")
    assert not any("元信息与标签没有写出" in warning for warning in result.warnings)
    window.radio_bundle.setChecked(True)
    assert window.metadata_fields["author"].text() == "bundle作者"
    assert window.tag_editor.common_edit.toPlainText() == "bundle标签"
    assert window.metadata_group.isEnabled() and window.tag_editor.isEnabled()


def test_reload_recomputes_defaults_and_erases_identity_and_tag_edits(window, tmp_path):
    model = load(window, tmp_path)
    first = model.selected_ids[0]
    window.metadata_fields["author"].setText("上次作者")
    window.metadata_fields["license"].setPlainText("上次许可")
    window.metadata_fields["version"].setText("上次版本")
    window.tag_editor.source_edit.clear()
    window.tag_editor.common_edit.setPlainText("上次标签")
    window.tag_editor.added_edit.setPlainText("单支旧标签")
    model.options.set_override(first, "bake_texture_bc", False)
    load(window, tmp_path)
    assert window._selection.options.preset_ids[0] == first
    snapshot = window._make_export_snapshot()
    assert snapshot.metadata.author == snapshot.metadata.license == snapshot.metadata.package_version == ""
    assert snapshot.preset_tags()[first] == ("来源 原始",)
    assert snapshot.option_state().enabled(first, "bake_texture_bc")
    assert window.tag_editor.added_edit.toPlainText() == ""


def test_load_failure_clears_metadata_and_old_labels(window, tmp_path, monkeypatch):
    load(window, tmp_path)
    window.metadata_fields["author"].setText("不留存")
    errors = []
    def fail(*_args):
        raise RuntimeError("合成解析启动失败")
    monkeypatch.setattr(main_window, "ParseWorker", fail)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    window._load_file(str(tmp_path / "new.abr"))
    assert window._selection is None and window._tags is None
    assert window.metadata_fields["author"].text() == ""
    assert window.tag_editor.source_edit.toPlainText() == ""
    assert not window.metadata_group.isEnabled() and not window.convert_btn.isEnabled()
    assert errors == ["合成解析启动失败"]


def test_freeze_detaches_metadata_tag_input_config_and_arrays(window, tmp_path):
    model = load(window, tmp_path)
    key = model.selected_ids[0]
    window.metadata_fields["description"].setPlainText("确认说明")
    window.tag_editor.common_edit.setPlainText("确认标签")
    snapshot = window._make_export_snapshot()
    preview = export_preview_text(snapshot)
    window.metadata_fields["description"].setPlainText("后来说明")
    window.tag_editor.common_edit.setPlainText("后来标签")
    model.options.set_override(key, "bake_texture_bc", False)
    model.options.preset(key).texture.image[0, 0] = 255
    assert export_preview_text(snapshot) == preview
    assert snapshot.option_state().enabled(key, "bake_texture_bc")
    assert snapshot.option_state().preset(key).texture.image[0, 0] == 0
    copy = snapshot.preset_tags()
    copy.clear()
    assert snapshot.preset_tags()
    with pytest.raises(FrozenInstanceError):
        snapshot.metadata.author = "不能变"


@pytest.mark.parametrize("where", ["author", "tags", "hidden_tags"])
def test_invalid_xml_is_preserved_and_blocks_export_before_worker(window, tmp_path, monkeypatch, where):
    model = load(window, tmp_path)
    bad = "坏文字\x01"
    if where == "author":
        window.metadata_fields["author"].setText(bad)
    else:
        window.tag_editor.added_edit.setPlainText(bad)
        assert "U+0001" in window.tag_editor.final_edit.toPlainText()
        if where == "hidden_tags":
            window.search_edit.setText(model.selected_ids[1])
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(ExportConfirmDialog, "exec", lambda _self: pytest.fail("坏输入不应进入确认"))
    window._on_convert()
    assert len(warnings) == 1 and "U+0001" in warnings[0]
    assert window._convert_worker is None and not window._busy
    if where != "author":
        assert window._tags.edits[model.selected_ids[0]][0] == bad


def test_unselected_invalid_per_brush_does_not_leak_into_selected_snapshot(window, tmp_path):
    model = load(window, tmp_path)
    first, second = model.selected_ids
    window.tag_editor.added_edit.setPlainText("bad\x01")
    model.set_selected(first, False)
    snapshot = window._make_export_snapshot()
    assert set(snapshot.preset_tags()) == {second}


def test_confirmed_unified_snapshot_is_what_worker_receives(window, tmp_path, monkeypatch):
    model = load(window, tmp_path)
    first = model.selected_ids[0]
    window.metadata_fields["author"].setText("确认作者")
    window.tag_editor.common_edit.setPlainText("确认标签")
    window.out_dir_edit.setText(str(tmp_path / "confirmed-output"))
    snapshots = []
    def confirm(dialog):
        assert "确认作者" in dialog.preview.toPlainText()
        assert "确认标签" in dialog.preview.toPlainText()
        window.metadata_fields["author"].setText("后来作者")
        window.tag_editor.common_edit.clear()
        window.radio_kpp.setChecked(True)
        model.options.set_override(first, "bake_texture_bc", False)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ExportConfirmDialog, "exec", confirm)
    original = workers.ConvertWorker
    def capture(*args, **kwargs):
        assert args[3:5] == (True, False)
        snapshot = kwargs["snapshot"]
        snapshots.append(snapshot)
        worker = original(*args, **kwargs)
        # 构造快照和后台参数即可证明接线；不启动线程、不造成竞态。
        monkeypatch.setattr(worker, "start", lambda: None)
        return worker
    monkeypatch.setattr(main_window, "ConvertWorker", capture)
    window._on_convert()
    worker = window._convert_worker
    assert snapshots[0].metadata.author == "确认作者"
    assert "确认标签" in snapshots[0].preset_tags()[first]
    assert worker._metadata_args["metadata"].author == "确认作者"
    assert worker._metadata_args["preset_tags"] == snapshots[0].preset_tags()
    assert worker._option_state.enabled(first, "bake_texture_bc")
    assert not window.process_group.isEnabled()
    window._release_failed_start(worker)


def test_rejected_confirmation_creates_no_worker(window, tmp_path, monkeypatch):
    load(window, tmp_path)
    monkeypatch.setattr(ExportConfirmDialog, "exec", lambda _self: QDialog.DialogCode.Rejected)
    window._on_convert()
    assert window._convert_worker is None and not window._busy


def test_worker_refuses_format_changed_from_confirmed_snapshot(window, tmp_path):
    load(window, tmp_path)
    snapshot = window._make_export_snapshot()
    with pytest.raises(ValueError, match="产物格式不一致"):
        workers.ConvertWorker(window._abr, [], str(tmp_path), False, True, snapshot=snapshot)


def test_source_removal_and_baking_off_together_real_manifest_and_trace(window, tmp_path):
    model = load(window, tmp_path, [brush(), brush(), brush(missing=True)])
    first, second, missing = model.selected_ids
    assert i18n.tr("tags_not_written") in window._items[missing][1].tags.text()
    focus(window, missing)
    assert i18n.tr("tags_not_written") in window.tag_editor.final_edit.toPlainText()
    focus(window, first)
    window.tag_editor.common_edit.setPlainText("共享 & <标签>")
    window.tag_editor.removed_edit.setPlainText("来源 原始")
    window.tag_editor.added_edit.setPlainText("单支标签")
    model.options.set_override(first, "bake_texture_bc", False)
    snapshot = window._make_export_snapshot()
    preview = export_preview_text(snapshot)
    assert "单支标签" in preview and "预计跳过" in preview
    result = run_snapshot(snapshot, tmp_path)
    assert result.status == "committed" and result.verify.ok
    assert result.verify.identity_scheme == "v2" and result.verify.preset_count == 2
    trace = result.verify.trace
    assert trace["source"]["filename"] == "来源 原始.abr"
    assert trace["source"]["digest"] == snapshot.source_digest and trace["source"]["bound"]
    rows = {row["id"]: row for row in trace["presets"]}
    assert set(rows) == {first, second} and missing not in rows
    assert tuple(rows[first]["tags"]) == snapshot.preset_tags()[first]
    assert tuple(rows[second]["tags"]) == snapshot.preset_tags()[second]
    assert rows[first]["switches"]["bake_texture_bc"]["actual"] == "alternative"
    assert rows[second]["switches"]["bake_texture_bc"]["actual"] == "applied"
    assert result.option_plans[first].outcome("bake_texture_bc").actual == "alternative"
    with zipfile.ZipFile(result.bundle_path) as bundle:
        meta = bundle.read("meta.xml").decode()
        assert snapshot.source_path not in meta
        assert str(ROOT) not in meta
        assert "dc:author" not in meta and "meta:license" not in meta
        assert "meta:creation-date" not in meta and "meta:dc-date" not in meta
        for row in trace["presets"]:
            assert result.verify.preset_tags[row["kpp"]] == tuple(row["tags"])
    assert verify_bundle(result.bundle_path).ok


def test_same_snapshot_reuses_and_tag_only_change_new_identity_keeps_old(window, tmp_path):
    load(window, tmp_path)
    snapshot = window._make_export_snapshot()
    first = run_snapshot(snapshot, tmp_path)
    path = Path(first.bundle_path)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    second = run_snapshot(snapshot, tmp_path)
    assert second.status == "reused_only" and second.bundle_reused
    assert second.bundle_path == first.bundle_path
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    window.tag_editor.source_edit.setPlainText("改后来源")
    third = run_snapshot(window._make_export_snapshot(), tmp_path)
    assert third.verify.ok and third.content_hash8 != first.content_hash8
    assert third.bundle_path != first.bundle_path
    assert third.kpp_files == first.kpp_files
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_source_byte_change_after_preview_is_failure_not_stale_export(window, tmp_path):
    load(window, tmp_path)
    snapshot = window._make_export_snapshot()
    Path(snapshot.source_path).write_bytes(b"changed-source-after-preview")
    result = run_snapshot(snapshot, tmp_path)
    assert isinstance(result, workers.ConvertFailure)
    assert "摘要与逐支选项状态不一致" in result.message
    assert not (tmp_path / "output").exists()


def test_source_missing_after_preview_is_failure_without_fallback(window, tmp_path):
    load(window, tmp_path)
    snapshot = window._make_export_snapshot()
    source = Path(snapshot.source_path).resolve()
    assert source.is_relative_to(tmp_path.resolve())
    source.unlink()  # 只删除本用例刚创建的合成源，验证生产路径不能自动跳过核对。
    result = run_snapshot(snapshot, tmp_path)
    assert isinstance(result, workers.ConvertFailure)
    assert "No such file" in result.message or "找不到" in result.message
    assert not (tmp_path / "output").exists()


def test_cancelled_snapshot_does_not_claim_metadata_delivery(window, tmp_path):
    load(window, tmp_path)
    snapshot = window._make_export_snapshot()
    received = []
    worker = workers.ConvertWorker(window._abr, [], str(tmp_path / "output"), True, False, snapshot=snapshot)
    worker.done.connect(received.append)
    assert worker.request_cancel() == "accepted"
    worker.run()
    result = received[0]
    assert result.status == "cancelled" and result.bundle_path is None
    assert metadata_result_text(result) == i18n.tr("metadata_not_verified")
    assert not (tmp_path / "output").exists() or not list((tmp_path / "output").iterdir())


def test_actual_summary_uses_only_verified_readback_not_current_form(window, tmp_path, monkeypatch):
    load(window, tmp_path)
    window.metadata_fields["author"].setText("写出作者")
    window.tag_editor.source_edit.setPlainText("写出标签")
    result = run_snapshot(window._make_export_snapshot(), tmp_path)
    window.metadata_fields["author"].setText("后来作者")
    window.tag_editor.source_edit.setPlainText("后来标签")
    text = metadata_result_text(result)
    assert "写出作者" in text and "写出标签" in text
    assert "后来作者" not in text and "后来标签" not in text
    assert "2 个逐笔标签关联" in text
    messages = []
    monkeypatch.setattr(QMessageBox, "exec", lambda message: messages.append(
        (message.informativeText(), message.detailedText())))
    window._on_converted(result)
    assert "2 个逐笔标签关联" in messages[0][0]
    assert "写出作者" in messages[0][1] and "后来作者" not in messages[0][1]


@pytest.mark.parametrize("status,ok,has_path", [
    ("cancelled", True, True), ("committed", False, True),
    ("committed", True, False), ("all_skipped", True, False),
])
def test_unverified_or_undelivered_never_claim_tags_written(status, ok, has_path):
    result = ConvertResult(status=status, bundle_path="unused.bundle" if has_path else None,
        verify=BundleVerifyResult(ok=ok, meta={"dc:author": "不应显示"},
                                  preset_tags={"x.kpp": ("不应显示",)}))
    assert metadata_result_text(result) == i18n.tr("metadata_not_verified")


def test_busy_freezes_new_controls_until_existing_finished_protocol(window, tmp_path):
    load(window, tmp_path)
    window._set_busy(True)
    assert not window.metadata_group.isEnabled() and not window.tag_editor.isEnabled()
    window._set_busy(False)
    assert window.metadata_group.isEnabled() and window.tag_editor.isEnabled()


def test_snapshot_legacy_callers_remain_compatible(app, tmp_path, monkeypatch):
    model = BrushSelection([brush()], source_digest_of_bytes(b"legacy-gui-state"))
    snapshot = model.snapshot()
    seen = []
    def fake_convert(*args, **kwargs):
        assert "metadata" not in kwargs and "preset_tags" not in kwargs and "source_path" not in kwargs
        seen.append(kwargs["option_state"].preset_ids)
        return ConvertResult(status="empty")
    monkeypatch.setattr(workers, "convert_presets", fake_convert)
    worker = workers.ConvertWorker(AbrFile(6, 2), [], str(tmp_path), True, False, snapshot=snapshot)
    worker.run()
    assert seen == [model.selected_ids]
    assert "包信息预览" not in export_preview_text(snapshot)


def test_real_generated_abr_parse_to_gui_snapshot_and_bundle(window, tmp_path):
    """只读既有生成夹具并复制到本任务；真实解析，不使用商用源或改旧证据。"""
    original = ROOT / "tests" / "abr-generated" / "one-test.abr"
    assert original.is_file(), "生成夹具缺失必须失败，不能跳过当通过"
    source = tmp_path / "177真实解析合成.abr"
    source.write_bytes(original.read_bytes())
    received = []
    parser = workers.ParseWorker(str(source))
    parser.done.connect(lambda abr, presets: received.append((abr, presets)))
    parser.run()
    assert len(received) == 1 and received[0][0] is not None
    abr, presets = received[0]
    window._file_path = str(source)
    window._on_parsed(abr, presets, source_digest=parser.source_digest, show_dialog=False)
    window.tag_editor.source_edit.clear()
    window.tag_editor.common_edit.setPlainText("真实解析标签")
    window.metadata_fields["description"].setPlainText("177合成解析到GUI再组包")
    snapshot = window._make_export_snapshot()
    result = run_snapshot(snapshot, tmp_path, abr=abr)
    assert result.verify.ok and result.verify.identity_scheme == "v2"
    assert result.verify.trace["source"]["filename"] == source.name
    assert result.verify.trace["source"]["digest"] == source_digest_of_bytes(original.read_bytes())
    assert all(tags == ("真实解析标签",) for tags in result.verify.preset_tags.values())
    assert result.verify.meta["dc:description"] == snapshot.metadata.description
    assert result.verify.preset_count > 0


def test_different_source_starts_fresh_metadata_tags_and_ids(window, tmp_path):
    model = load(window, tmp_path)
    ids = model.selected_ids
    window.metadata_fields["author"].setText("旧源作者")
    window.tag_editor.source_edit.setPlainText("旧源标签")
    source = tmp_path / "另一来源.abr"
    source.write_bytes(b"new-task177-synthetic-source")
    window._file_path = str(source)
    window._on_parsed(AbrFile(6, 2, name=source.stem), [brush(), brush()],
        source_digest=source_digest_of_bytes(source.read_bytes()), show_dialog=False)
    snapshot = window._make_export_snapshot()
    assert snapshot.metadata.author == ""
    assert snapshot.metadata.source_filename == "另一来源.abr"
    assert not set(snapshot.preset_tags()).intersection(ids)
    assert set(snapshot.preset_tags().values()) == {("另一来源",)}


def test_new_translation_keys_and_placeholders_match():
    tables = i18n._STRINGS
    assert set(tables["zh"]) == set(tables["en"])
    for key in tables["zh"]:
        def fields(text):
            return {field for _, field, _, _ in Formatter().parse(text) if field is not None}
        assert fields(tables["zh"][key]) == fields(tables["en"][key]), key
    for lang in ("zh", "en"):
        i18n.set_language(lang)
        text = tag_plan_text(PresetTagPlan(common=("shared",), source=("shared",)))
        assert "shared" in text
    i18n.set_language("zh")
