"""180组装验收：真实合成ABR→GUI确认快照/CLI同配置→原生关联与实际KPP。

仅用工作区内的离屏控件、独立INI和合成数据；不启动真实窗口、Krita或PS。
复用173的纯合成输入与179的独立读者，不另写转换器或写包器。
机器检查不能替代187的导入、筛选、重启、笔触和图片隐私人工验收。
"""

from __future__ import annotations

import base64
import hashlib
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
# 183A：环境变量优先、原任务目录兜底；隔离断言本身保持不变。
TASK = Path(os.environ.get("BRUSHCONVERTER_TEST_TASK_ROOT", str(ROOT / ".tmp" / "180")))

import test_bundle_interop as native  # noqa: E402
import test_options_integration as assembled  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QStandardPaths  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import cli  # noqa: E402
from brush_converter import __version__  # noqa: E402
from brush_converter import export_transaction as transaction  # noqa: E402
from brush_converter.convert import ConvertResult  # noqa: E402
from brush_converter.export_options import SWITCH_IDS, validate_progress_event  # noqa: E402
from brush_converter.kpp.bundle import BundleVerifyResult, verify_bundle  # noqa: E402
from gui import i18n, main_window, workers  # noqa: E402
from gui.main_window import ExportConfirmDialog, MainWindow, metadata_result_text  # noqa: E402

pytestmark = pytest.mark.verify
COMMON = ("公共180", "INK", "ink", "  中文   标签  ", "cafe\u0301", "<&>")
BASE_TAGS = {"公共180", "INK", "中文 标签", "café", "<&>", "来源180"}


def _isolation():
    """先证实隔离，配置不明就失败而不是跳过或借用真实用户配置。"""
    assert sys.dont_write_bytecode
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(TASK.resolve())
    for key in ("TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE",
                "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        assert Path(os.environ[key]).resolve().is_relative_to(TASK.resolve()), key
    qt_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    assert Path(qt_path).resolve().is_relative_to(TASK.resolve())


@pytest.fixture(scope="module")
def app():
    _isolation()
    return QApplication.instance() or QApplication([])


@pytest.fixture
def scene(app, tmp_path, monkeypatch):
    _isolation()
    assert tmp_path.resolve().is_relative_to(TASK.resolve())
    settings = QSettings(str(tmp_path / "settings180.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    settings.setValue("language", "zh")
    settings.setValue("author", "不得继承的合成作者180")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(ExportConfirmDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: 0)
    window = MainWindow()
    source = tmp_path / "中文路径 有空格" / "合成 原始源180.abr"
    source.parent.mkdir()
    source.write_bytes(assembled.make_source_bytes())
    assembled.load_window(app, window, source)
    assert len(window._selection.options.preset_ids) == 5
    yield window, source
    for worker in tuple(window._active_workers):
        worker.request_cancel()
        assert worker.wait(10000), "只允许协作停止本测试线程，不强杀应用"
    app.processEvents()
    window._abort_close()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settings.sync()
    assert settings.value("author") == "不得继承的合成作者180"
    i18n.set_language("zh")


def _edit(window, field, value):
    widget = window.metadata_fields[field]
    if hasattr(widget, "setPlainText"):
        widget.setPlainText(value)
    else:
        widget.setText(value)


def _configure(window, values=(False, True, False)):
    """经真实控件配置同名笔刷、公共/来源/逐支增删，期望另行手列。"""
    ids = window._selection.options.preset_ids
    assembled.select_switches(window, ids[0], values)
    assembled.select_switches(window, ids[1], tuple(not item for item in values))
    for field, value in {"name": "180 合成资源包", "description": "组装说明 <180 & 对照>",
                         "author": "", "license": "仅供合成验收", "version": "fixture-180.1"}.items():
        _edit(window, field, value)
    editor = window.tag_editor
    editor.common_edit.setPlainText("\n".join(COMMON))
    editor.source_edit.setPlainText("来源180")
    changes = [("第一180", "公共180\n来源180"), ("第二180", "INK"),
               ("", "\n".join(COMMON + ("来源180",)))]
    for key, (added, removed) in zip(ids, changes):
        window.brush_list.setCurrentItem(window._items[key][0])
        editor.added_edit.setPlainText(added)
        editor.removed_edit.setPlainText(removed)
    return {
        ids[0]: {"INK", "中文 标签", "café", "<&>", "第一180"},
        ids[1]: {"公共180", "中文 标签", "café", "<&>", "来源180", "第二180"},
        ids[2]: set(), ids[3]: set(BASE_TAGS), ids[4]: set(BASE_TAGS),
    }


def _files(snapshot, directory):
    """CLI输入只来自已确认快照；不是反过来用写出结果推导输入。"""
    directory.mkdir(parents=True, exist_ok=True)
    options = directory / "逐支开关.json"
    options.write_text(snapshot.config_json, encoding="utf-8")
    metadata = directory / "元信息标签.json"
    if snapshot.make_bundle:
        value = snapshot.metadata
        payload = {"format": "brushconverter.export-metadata", "version": 1,
            "source_digest": snapshot.source_digest,
            "metadata": {key: getattr(value, key) for key in
                         ("name", "description", "author", "license", "package_version")},
            "tags": {"common": [], "source": [], "presets": {}}}
        # 公共与来源由同一份快照保存，逐支只传增删；不传最终集合冒充原始输入。
        for key, plan in snapshot.tag_plans:
            payload["tags"]["common"] = list(plan.common)
            payload["tags"]["source"] = list(plan.source)
            payload["tags"]["presets"][key] = {"added": list(plan.added), "removed": list(plan.removed)}
        metadata.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return options, metadata


def _argv(snapshot, out, directory, *, standalone=False, explicit=True):
    options, metadata = _files(snapshot, directory)
    args = ["convert", snapshot.source_path, "-o", str(out), "--options-file", str(options)]
    for identity in snapshot.identities:
        args += ["--preset-id", identity.preset_id]
    if snapshot.make_bundle:
        if explicit:
            args += ["--metadata-file", str(metadata)]
        if not standalone:
            args += ["--no-standalone"]
    else:
        args += ["--no-bundle"]
    return args


def _cancel(stage, event, writes):
    return ((stage == "convert" and event.phase == "convert" and event.completed == 1)
            or (stage == "verified" and event.phase == "write" and writes == 1)
            or (stage == "deliver" and event.phase == "write" and writes == 2))


def _cli(snapshot, out, directory, monkeypatch, *, standalone=False, cancel_at=None, explicit=True):
    """仅旁观结果或提出协作取消；解析、映射、写包和回读均不打桩。"""
    original, results = cli.convert, []

    def observe(*args, **kwargs):
        progress, writes = kwargs.get("progress"), 0

        def on_progress(event):
            nonlocal writes
            if progress:
                progress(event)
            writes += int(event.phase == "write")
            if _cancel(cancel_at, event, writes):
                kwargs["cancel"].cancel("180合成边界取消")
        kwargs["progress"] = on_progress
        result = original(*args, **kwargs)
        results.append(result)
        return result
    argv = _argv(snapshot, out, directory, standalone=standalone, explicit=explicit)
    with monkeypatch.context() as patch:
        patch.setattr(cli, "convert", observe)
        code = cli.main(argv)
    return code, results[0] if results else None


def _worker(window, snapshot, out, *, standalone=False, cancel_at=None):
    """后台同步执行生产管线，双产物只测API能力，不冒称GUI有同时导出选项。"""
    worker = workers.ConvertWorker(window._abr, [], str(out), snapshot.make_bundle,
        standalone or not snapshot.make_bundle, snapshot=snapshot)
    results, events = [], []
    worker.done.connect(results.append)
    worker.progress.connect(events.append)
    original, writes = worker._on_progress, 0

    def on_progress(event):
        nonlocal writes
        original(event)
        writes += int(event.phase == "write")
        if _cancel(cancel_at, event, writes):
            worker.request_cancel()
    worker._on_progress = on_progress
    worker.run()
    assert len(results) == 1
    for item in events:
        if item.event is not None:
            validate_progress_event(item.event)
    return results[0], events


def _gui(app, window, out):
    window.out_dir_edit.setText(str(out))
    snapshot = window._make_export_snapshot()
    window._on_convert()
    assembled.wait_for(app, lambda: not window._active_workers)
    assert not window._busy and window._export_snapshot is None
    assert isinstance(window._last_result, ConvertResult)
    return snapshot, window._last_result


def _chain(result, snapshot, expected):
    """独立原生读者交叉核对ID、原序号、MD5/SHA256、标签与真实处理原因。"""
    assert result.status in ("committed", "reused_only")
    assert not result.skipped and not result.cancelled
    data = Path(result.bundle_path).read_bytes()
    view = native._native_read(data)
    assert view.would_load is True, view.reason
    assert native._contract_errors(data, view) == []
    trace = json.loads(view.fields["meta:brushconverter-trace"])
    assert trace["identity_format"] == 2 and trace["version"] == 1
    assert trace["source"]["digest"] == "sha256:" + hashlib.sha256(
        Path(snapshot.source_path).read_bytes()).hexdigest()
    assert trace["source"]["filename"] == Path(snapshot.source_path).name
    assert trace["source"]["bound"] is True
    assert trace["producer"]["version"] == __version__
    assert trace["producer"]["release_state"] == "unreleased"
    assert view.fields.get("dc:author", "") == snapshot.metadata.author
    assert view.fields.get("meta:license", "") == snapshot.metadata.license
    assert view.fields["dc:title"] == snapshot.metadata.name
    assert view.fields["dc:description"] == snapshot.metadata.description_or_default()
    assert view.fields.get("meta:brushconverter-package-version", "") == snapshot.metadata.package_version
    assert view.fields["meta:brushconverter-source"] == Path(snapshot.source_path).name
    assert view.fields["meta:bundle-version"] == "1"
    assert view.package_tags == set(), "包级标签不能冒充逐笔原生关联"
    by_id = {row["id"]: row for row in trace["presets"]}
    assert len(by_id) == len(trace["presets"]) == len(expected)
    assert set(by_id) == set(expected)
    assert set(view.resources) == {"paintoppresets/" + row["kpp"] for row in trace["presets"]}
    files, filters = dict(result.kpp_files), {}
    state = snapshot.option_state()
    for identity in snapshot.identities:
        key, ordinal = identity.preset_id, identity.ordinal
        row = by_id[key]
        assert row["ordinal"] == ordinal
        assert row["id"].startswith(f"bc1-{snapshot.source_digest[7:19]}-{ordinal:04d}-")
        assert row["name"] == state.preset(key).name
        path = "paintoppresets/" + row["kpp"]
        assert path in view.resources
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            blob = archive.read(path)
        assert blob == files[row["kpp"]]
        assert row["kpp_sha256"] == hashlib.sha256(blob).hexdigest()
        assert view.resources[path]["md5"] == hashlib.md5(blob).hexdigest()
        assert native._kpp_name(blob) == row["name"]
        assert set(row["tags"]) == set(view.resources[path]["tags"]) == expected[key]
        assert len(row["tags"]) == len(expected[key])
        for tag in expected[key]:
            filters.setdefault(tag, set()).add(row["kpp"])
        for switch in SWITCH_IDS:
            record = row["switches"][switch]
            outcome = result.option_plans[key].outcome(switch)
            for field in ("applicable", "enabled", "actual", "limited"):
                assert record[field] == getattr(outcome, field)
            reason = trace["reason_texts"][record["reason"]]
            assert reason == outcome.actual_reason
            assert hashlib.sha256(reason.encode("utf-8")).hexdigest() == record["reason"]
    assert view.filters() == filters
    verified = verify_bundle(result.bundle_path)
    assert verified.ok and verified.identity_scheme == "v2"
    assert verified.trace == trace
    assert verified.identity_sha256 == result.bundle_identity.sha256
    return trace


def _physical(result, trace, source_state, values):
    """不只比入口自述：按源序号定位同名笔刷，检查实际笔尖/纹理和采样补白。"""
    by_ordinal = {row["ordinal"]: dict(result.kpp_files)[row["kpp"]] for row in trace["presets"]}
    for ordinal, switches in ((1, values), (2, tuple(not item for item in values))):
        root, pixels = assembled.texture_pixels(by_ordinal[ordinal])
        params = {node.get("name"): node.text for node in root.iter("param")}
        definition = ET.fromstring(params["brush_definition"])
        rasterize, prescale, bake = switches
        assert definition.get("type") == ("png_brush" if rasterize else "auto_brush")
        # 同名源直径固定18/22，必须另核实际几何，不能只信追溯名或互比两入口。
        if rasterize:
            resource = next(node for node in root.iter("resource") if node.get("type") == "brushes")
            with Image.open(io.BytesIO(base64.b64decode(resource.text))) as tip:
                stored = np.array(tip)
            # 既有h45足印比1.26，L=2*ceil(1.26*D/2)+2，手算为26/30。
            side = {1: 26, 2: 30}[ordinal]
            assert stored.shape == (side, side)
            assert all(np.all(edge == 255) for edge in
                       (stored[0], stored[-1], stored[:, 0], stored[:, -1]))
        else:
            mask = definition.find("MaskGenerator")
            assert float(mask.get("diameter")) == {1: 18.0, 2: 22.0}[ordinal]
            assert float(mask.get("hfade")) == 0.45
        assert pixels.shape == ((32, 32) if prescale else (16, 16))
        assert float(params["Texture/Pattern/Scale"]) == (1.0 if prescale else 2.0)
        assert float(params["Texture/Pattern/Brightness"]) == (0.0 if bake else -1.0)
        assert float(params["Texture/Pattern/Contrast"]) == (1.0 if bake else 0.5)
        assert params["Texture/Pattern/Invert"] == ("false" if bake else "true")
        preset = source_state.preset(source_state.preset_ids[ordinal - 1])
        expected = preset.texture.image.astype(np.float64)
        if bake:
            expected = 255 - np.clip(np.rint((expected - 127.5) * 0.5 + 127.5) + 150, 0, 255)
        np.testing.assert_array_equal(pixels[::2, ::2] if prescale else pixels, expected.astype(np.uint8))
    assert by_ordinal[1] != by_ordinal[2], "同名不同直径的实际KPP不能串为一支"
    with Image.open(io.BytesIO(by_ordinal[3])) as image:
        root = ET.fromstring(image.text["preset"])
    tip = next(node for node in root.iter("resource") if node.get("type") == "brushes")
    blob = base64.b64decode(tip.text)
    assert hashlib.md5(blob).hexdigest() == tip.get("md5sum")
    with Image.open(io.BytesIO(blob)) as image:
        actual = np.array(image)
    expected = np.full((10, 10), 255, dtype=np.uint8)
    expected[2:8, :] = source_state.preset(source_state.preset_ids[2]).tip_gray
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("values", list(itertools.product((False, True), repeat=3)))
def test_full_gui_cli_native_chain_eight_switch_combinations(app, scene, tmp_path, monkeypatch, values):
    window, source = scene
    expected = _configure(window, values)
    ids = window._selection.options.preset_ids
    before = source.read_bytes(), source.stat().st_mtime_ns
    window.sort_combo.setCurrentIndex(2)
    window.search_edit.setText(ids[0])
    for language in (1, 0):
        window.lang_combo.setCurrentIndex(language)
    assert window._selection.counts["hidden"] == 4
    snapshot, gui_result = _gui(app, window, tmp_path / "GUI 中文输出")
    assert snapshot.hidden_selected == 4
    assert {key: set(tags) for key, tags in snapshot.preset_tags().items()} == expected
    code, cli_result = _cli(snapshot, tmp_path / "CLI 中文输出", tmp_path / "同配置", monkeypatch)
    assert code == 0 and gui_result.status == cli_result.status == "committed"
    assert assembled.delivered_bytes(tmp_path / "GUI 中文输出") == assembled.delivered_bytes(
        tmp_path / "CLI 中文输出")
    assert gui_result.options_summary.to_dict() == cli_result.options_summary.to_dict()
    trace = _chain(gui_result, snapshot, expected)
    assert _chain(cli_result, snapshot, expected) == trace
    _physical(gui_result, trace, window._selection.options, values)
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


@pytest.mark.parametrize("change", ["description", "author", "license", "version", "name",
                                   "common", "source", "added", "removed"])
def test_metadata_or_tag_only_new_identity_repeat_preserves_originals(
        app, scene, tmp_path, monkeypatch, change):
    window, source = scene
    _configure(window)
    first_snapshot, first_gui = _gui(app, window, tmp_path / "gui")
    code, first_cli = _cli(first_snapshot, tmp_path / "cli", tmp_path / "input-a", monkeypatch)
    assert code == 0
    old_gui, old_cli = assembled.inventory(tmp_path / "gui"), assembled.inventory(tmp_path / "cli")
    source_before = source.read_bytes(), source.stat().st_mtime_ns
    if change in ("description", "author", "license", "version", "name"):
        _edit(window, change, "仅变更此字段180")
    elif change == "common":
        window.tag_editor.common_edit.setPlainText("\n".join(COMMON + ("新公共180",)))
    elif change == "source":
        window.tag_editor.source_edit.setPlainText("")
    else:
        first = window._selection.options.preset_ids[0]
        window.brush_list.setCurrentItem(window._items[first][0])
        edit = window.tag_editor.added_edit if change == "added" else window.tag_editor.removed_edit
        edit.setPlainText(edit.toPlainText() + "\n" + ("新单支180" if change == "added" else "INK"))
    snapshot, changed_gui = _gui(app, window, tmp_path / "gui")
    code, changed_cli = _cli(snapshot, tmp_path / "cli", tmp_path / "input-b", monkeypatch)
    assert code == 0
    for first, changed in ((first_gui, changed_gui), (first_cli, changed_cli)):
        assert changed.bundle_identity.sha256 != first.bundle_identity.sha256
        assert changed.bundle_path != first.bundle_path
        assert changed.kpp_files == first.kpp_files
        assert verify_bundle(changed.bundle_path).ok
    for out, old in ((tmp_path / "gui", old_gui), (tmp_path / "cli", old_cli)):
        assert all(assembled.inventory(out)[key] == value for key, value in old.items())
    assert assembled.delivered_bytes(tmp_path / "gui") == assembled.delivered_bytes(tmp_path / "cli")
    before = assembled.inventory(tmp_path / "gui"), assembled.inventory(tmp_path / "cli")
    _, repeated_gui = _gui(app, window, tmp_path / "gui")
    code, repeated_cli = _cli(snapshot, tmp_path / "cli", tmp_path / "input-c", monkeypatch)
    assert code == 0 and repeated_gui.status == repeated_cli.status == "reused_only"
    assert (assembled.inventory(tmp_path / "gui"), assembled.inventory(tmp_path / "cli")) == before
    assert (source.read_bytes(), source.stat().st_mtime_ns) == source_before


@pytest.mark.parametrize("stage", ["convert", "verified", "deliver"])
@pytest.mark.parametrize("standalone", [False, True])
def test_metadata_batch_cancellation_and_late_boundary(scene, tmp_path, monkeypatch, stage, standalone):
    window, _ = scene
    expected = _configure(window)
    snapshot = window._make_export_snapshot()
    for entry in ("gui", "cli"):
        out = tmp_path / entry
        out.mkdir()
        (out / "保留原文件180.txt").write_bytes(b"synthetic-existing-180")
    before = assembled.inventory(tmp_path / "gui"), assembled.inventory(tmp_path / "cli")
    gui_result, events = _worker(window, snapshot, tmp_path / "gui",
                                 standalone=standalone, cancel_at=stage)
    code, cli_result = _cli(snapshot, tmp_path / "cli", tmp_path / "input", monkeypatch,
                           standalone=standalone, cancel_at=stage)
    assert isinstance(gui_result, ConvertResult)
    assert gui_result.status == cli_result.status
    assert gui_result.options_summary.to_dict() == cli_result.options_summary.to_dict()
    assert assembled.delivered_bytes(tmp_path / "gui") == assembled.delivered_bytes(tmp_path / "cli")
    for out, old in zip((tmp_path / "gui", tmp_path / "cli"), before):
        assert all(assembled.inventory(out)[key] == value for key, value in old.items())
        assert not list(out.glob(transaction.STAGING_PREFIX + "*"))
    if stage == "deliver":
        assert code == 0 and gui_result.status == "committed"
        assert gui_result.transaction.cancel_requested_late and cli_result.transaction.cancel_requested_late
        _chain(gui_result, snapshot, expected)
    else:
        assert code == 130 and gui_result.status == "cancelled"
        assert gui_result.bundle_path is None and not gui_result.standalone_paths and not gui_result.kpp_files
        assert cli_result.bundle_path is None and not cli_result.standalone_paths and not cli_result.kpp_files
        assert (assembled.inventory(tmp_path / "gui"), assembled.inventory(tmp_path / "cli")) == before
        assert metadata_result_text(gui_result) == i18n.tr("metadata_not_verified")
        assert "cancelled" in [item.stage for item in events]


def test_same_named_foreign_bundle_and_kpp_are_not_replaced(scene, tmp_path, monkeypatch):
    window, _ = scene
    expected = _configure(window)
    snapshot = window._make_export_snapshot()
    probe, _ = _worker(window, snapshot, tmp_path / "probe", standalone=True)
    for entry in ("gui", "cli"):
        out = tmp_path / entry
        out.mkdir()
        (out / Path(probe.bundle_path).name).write_bytes(b"synthetic-foreign-bundle180")
        # 用真实API返回路径构造碰撞；双产物模式的独立KPP位于kpp子目录。
        relative = Path(probe.standalone_paths[probe.kpp_files[0][0]]).relative_to(tmp_path / "probe")
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic-foreign-kpp180")
    before = assembled.inventory(tmp_path / "gui"), assembled.inventory(tmp_path / "cli")
    gui_result, _ = _worker(window, snapshot, tmp_path / "gui", standalone=True)
    code, cli_result = _cli(snapshot, tmp_path / "cli", tmp_path / "input", monkeypatch, standalone=True)
    assert code == 0
    for result, out, old in zip((gui_result, cli_result), (tmp_path / "gui", tmp_path / "cli"), before):
        assert result.transaction.counts["renamed"] == 2
        assert Path(result.bundle_path).name.endswith("_2.bundle")
        assert all(assembled.inventory(out)[key] == value for key, value in old.items())
        _chain(result, snapshot, expected)
        for name, blob in result.kpp_files:
            assert Path(result.standalone_paths[name]).read_bytes() == blob
    assert assembled.delivered_bytes(tmp_path / "gui") == assembled.delivered_bytes(tmp_path / "cli")


def test_subset_keeps_original_ordinals_and_same_named_tags(app, scene, tmp_path, monkeypatch):
    window, _ = scene
    expected = _configure(window)
    ids = window._selection.options.preset_ids
    window._selection.set_selected(ids[1], False)
    window._selection.set_selected(ids[3], False)
    snapshot = window._make_export_snapshot()
    assert [identity.ordinal for identity in snapshot.identities] == [1, 3, 5]
    result, _ = _worker(window, snapshot, tmp_path / "gui")
    code, other = _cli(snapshot, tmp_path / "cli", tmp_path / "input", monkeypatch)
    assert code == 0
    expected = {key: tags for key, tags in expected.items() if key in {ids[0], ids[2], ids[4]}}
    assert _chain(result, snapshot, expected) == _chain(other, snapshot, expected)
    assert assembled.delivered_bytes(tmp_path / "gui") == assembled.delivered_bytes(tmp_path / "cli")


def test_standalone_kpp_does_not_claim_native_tags(app, scene, tmp_path, monkeypatch, capsys):
    window, _ = scene
    _configure(window)
    window.radio_kpp.setChecked(True)
    snapshot, result = _gui(app, window, tmp_path / "gui")
    assert snapshot.metadata is None and snapshot.tag_plans == ()
    code, other = _cli(snapshot, tmp_path / "cli", tmp_path / "input", monkeypatch)
    assert code == 0 and result.bundle_path is None and other.bundle_path is None
    assert result.verify is None and metadata_result_text(result) == i18n.tr("metadata_kpp_result")
    assert assembled.delivered_bytes(tmp_path / "gui") == assembled.delivered_bytes(tmp_path / "cli")
    for _name, blob in result.kpp_files:
        with Image.open(io.BytesIO(blob)) as image:
            assert "brushconverter-trace" not in image.text
            assert "第一180" not in image.text["preset"]
    capsys.readouterr()
    bad = tmp_path / "不能用于KPP的配置.json"
    bad.write_text(json.dumps({"format": "brushconverter.export-metadata", "version": 1,
                               "metadata": {}}, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["convert", snapshot.source_path, "-o", str(tmp_path / "bad"),
                     "--no-bundle", "--metadata-file", str(bad)]) == 2
    assert not (tmp_path / "bad").exists()
    assert "metadata_requires_bundle" in capsys.readouterr().err


def test_implicit_cli_legacy_is_not_mislabeled_as_gui_same_config(scene, tmp_path, monkeypatch):
    window, _ = scene
    snapshot = window._make_export_snapshot()
    gui_result, _ = _worker(window, snapshot, tmp_path / "gui")
    code, cli_result = _cli(snapshot, tmp_path / "cli", tmp_path / "input", monkeypatch, explicit=False)
    assert code == 0 and gui_result.kpp_files == cli_result.kpp_files
    assert gui_result.verify.identity_scheme == "v2" and cli_result.verify.identity_scheme == "legacy"
    assert cli_result.verify.trace is None
    assert all(not tags for tags in cli_result.verify.preset_tags.values())
    assert Path(gui_result.bundle_path).read_bytes() != Path(cli_result.bundle_path).read_bytes()


def test_confirmed_snapshot_is_detached_from_later_form_edits(scene, tmp_path, monkeypatch):
    window, _ = scene
    expected = _configure(window)
    snapshot = window._make_export_snapshot()
    _edit(window, "author", "确认后另一次输入180")
    window.tag_editor.source_edit.setPlainText("不能混入旧快照180")
    result, _ = _worker(window, snapshot, tmp_path / "gui")
    code, other = _cli(snapshot, tmp_path / "cli", tmp_path / "input", monkeypatch)
    assert code == 0 and snapshot.metadata.author == ""
    assert _chain(result, snapshot, expected) == _chain(other, snapshot, expected)
    assert assembled.delivered_bytes(tmp_path / "gui") == assembled.delivered_bytes(tmp_path / "cli")


def test_verification_failure_publishes_nothing_and_does_not_claim_metadata(scene, tmp_path, monkeypatch):
    window, _ = scene
    _configure(window)
    snapshot = window._make_export_snapshot()
    for entry in ("gui", "cli"):
        out = tmp_path / entry
        out.mkdir()
        (out / "保留180.txt").write_bytes(b"synthetic-existing180")
    before = assembled.inventory(tmp_path / "gui"), assembled.inventory(tmp_path / "cli")
    monkeypatch.setattr(transaction, "verify_bundle", lambda _path: BundleVerifyResult(
        ok=False, errors=["180合成回读故障，不能交付"]))
    result, _ = _worker(window, snapshot, tmp_path / "gui", standalone=True)
    code, other = _cli(snapshot, tmp_path / "cli", tmp_path / "input", monkeypatch, standalone=True)
    assert isinstance(result, workers.ConvertFailure) and result.summary is not None
    assert result.summary.status == "failed" and code == 3 and other is None
    assert (assembled.inventory(tmp_path / "gui"), assembled.inventory(tmp_path / "cli")) == before


def test_real_cli_process_reproduces_gui_confirmed_bundle(app, scene, tmp_path):
    window, _ = scene
    expected = _configure(window)
    snapshot, result = _gui(app, window, tmp_path / "gui")
    argv = _argv(snapshot, tmp_path / "cli", tmp_path / "input")
    run = subprocess.run([sys.executable, "-B", str(ROOT / "cli.py"), *argv],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    (tmp_path / "cli-process.raw.txt").write_bytes(run.stdout)
    assert run.returncode == 0, run.stdout.decode("utf-8", errors="replace")
    assert assembled.delivered_bytes(tmp_path / "gui") == assembled.delivered_bytes(tmp_path / "cli")
    _chain(result, snapshot, expected)
