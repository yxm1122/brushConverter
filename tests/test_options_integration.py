"""173组装验收：真实合成ABR→离屏GUI快照/CLI→生产转换→安全交付。

不读取商用笔刷、不启动真实窗口/Krita/PS。临时目录必须显式位于工作区，
QSettings只用独立INI；文件/字节/实际摘要检查不等于187人工效果通过。
"""

from __future__ import annotations

import base64
import io
import itertools
import json
import os
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QEventLoop,
    QSettings,
    QStandardPaths,
    QTimer,
)
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import cli  # noqa: E402
from brush_converter import convert as conversion  # noqa: E402
from brush_converter import export_transaction as tx  # noqa: E402
from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.export_options import (  # noqa: E402
    SWITCH_IDS,
    ExportOptionState,
    OptionConfigError,
    source_digest_of_bytes,
    validate_progress_event,
)
from brush_converter.kpp.bundle import verify_bundle  # noqa: E402
from brush_converter.kpp.kpp_writer import read_preset_xml  # noqa: E402
from brush_converter.mapping import BrushPreset, map_presets  # noqa: E402
from gui import i18n, main_window, workers  # noqa: E402
from gui.brush_selection import BrushSelection, state_plan  # noqa: E402
from gui.main_window import ExportConfirmDialog, MainWindow, export_preview_text  # noqa: E402
from tools import make_test_abr as abr_writer  # noqa: E402

pytestmark = pytest.mark.verify


def make_source_bytes(*, scale=200.0, brightness=150) -> bytes:
    """复用既有纯编码器造五支完整源；同名不同直径不触发既有去重规则。"""
    tip_id = "b1730000-0000-4000-8000-000000000001"
    pattern_id = "b1730000-0000-4000-8000-000000000002"
    texture = dict(name="自造纹理173", uuid=pattern_id, scale=scale, depth=60, blend="Mltp",
                   invert=True)

    def computed(name, diameter, spec, value):
        preset = abr_writer.build_computed_preset(
            name, diameter, 45, 25, 13, roundness=75, texture=spec)
        abr_writer._set_key(preset.items, "textureBrightness", abr_writer.integer(value))
        abr_writer._set_key(preset.items, "textureContrast", abr_writer.integer(-50))
        return preset

    first = computed("同名计算173", 18, texture, brightness)
    second = computed("同名计算173", 22, texture, brightness)
    sampled = abr_writer.build_preset("非方采样173", 20, 70, 25, 0, tip_id, "自造笔尖173")
    missing = abr_writer.build_preset("缺图案173", 20, 70, 25, 0, tip_id, "自造笔尖173",
        texture=texture | {"uuid": "b1730000-0000-4000-8000-000000000099"})
    boundary = computed("边界计算173", 16, texture | {"scale": 100}, 127)
    presets = [first, second, sampled, missing, boundary]
    pixels = abr_writer.make_ellipse_tip(10, 6, 70)
    sections = [
        abr_writer.encode_section("samp", abr_writer.encode_samp_item(tip_id, 10, 6, pixels)),
        abr_writer.encode_section("patt", abr_writer.encode_pattern_record(
            texture["name"], pattern_id, 16, 16,
            abr_writer.make_noise_pattern(16, "task173-original"))),
        abr_writer.encode_section("desc", abr_writer.encode_desc_section([
            ("Brsh", abr_writer.vlls(presets))])),
        abr_writer.encode_section("phry", abr_writer.build_phry(
            "自造验收173", "b1730000-0000-4000-8000-000000000003", len(presets))),
    ]
    return struct.pack(">HH", 6, 2) + b"".join(sections)


def verify_isolation():
    """先证实隔离再创建Qt应用/配置；不静默转向系统临时目录。"""
    temporary = ROOT / ".tmp"
    assert sys.dont_write_bytecode
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(temporary)
    for key in ("TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "HOME",
                "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        assert Path(os.environ[key]).resolve().is_relative_to(temporary), key
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    assert Path(location).resolve().is_relative_to(temporary)


@pytest.fixture(scope="module")
def app():
    verify_isolation()
    return QApplication.instance() or QApplication([])


@pytest.fixture
def source(tmp_path):
    assert tmp_path.resolve().is_relative_to(ROOT / ".tmp")
    path = tmp_path / "自造 源173.abr"
    path.write_bytes(make_source_bytes())
    abr = AbrFile.parse(path)
    presets = map_presets(abr)
    assert len(presets) == 5
    assert [preset.is_computed for preset in presets] == [True, True, False, False, True]
    assert presets[2].tip_gray.shape == (6, 10)
    assert presets[3].texture.image is None
    state = ExportOptionState.create(presets, source_digest_of_bytes(path.read_bytes()))
    return path, abr, state


def wait_for(app, predicate):
    """测试同步只用真实信号与有界事件循环，不用sleep或伪造进度。"""
    if not predicate():
        loop = QEventLoop()
        poll, timeout = QTimer(), QTimer()
        poll.setInterval(1)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        poll.start()
        timeout.start(10000)
        loop.exec()
        poll.stop()
        timeout.stop()
    assert predicate(), "组装线程或信号未在期限内结束，不能计为通过"
    app.processEvents()


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    verify_isolation()
    settings = QSettings(str(tmp_path / "settings173.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    settings.setValue("language", "zh")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(ExportConfirmDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    messages = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: messages.append(
        (self.text(), self.informativeText(), self.detailedText())) or 0)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[1:]) or 0)
    win = MainWindow()
    win.test_messages = messages
    yield win
    for worker in tuple(win._active_workers):
        worker.request_cancel()
        assert worker.wait(10000), "只允许等待本测试的协作停止，不强杀线程"
    app.processEvents()
    win._abort_close()
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settings.sync()
    i18n.set_language("zh")


def load_window(app, window, path):
    window._load_file(str(path))
    wait_for(app, lambda: not window._active_workers)
    assert window._selection is not None and window._abr is not None
    return window._selection


def select_switches(window, key, values):
    """经真实勾选控件改值，不直接修改后台或生产计划。"""
    window.brush_list.setCurrentItem(window._items[key][0])
    for switch, value in zip(SWITCH_IDS, values, strict=True):
        assert window.switch_checks[switch].isEnabled()
        window.switch_checks[switch].setChecked(value)


def inventory(path):
    path = Path(path)
    assert path.resolve().is_relative_to(ROOT / ".tmp")
    return {item.relative_to(path).as_posix(): (item.read_bytes(), item.stat().st_mtime_ns)
            for item in path.rglob("*") if item.is_file()} if path.exists() else {}


def delivered_bytes(path):
    return {name: value[0] for name, value in inventory(path).items()}


def cli_run(monkeypatch, source, out, *, config=None, keys=None, bundle=True,
            standalone=True, cancel_at=None):
    """旁观真实CLI函数的结果；解析/映射/转换/写出不打桩，也不发宿主信号。"""
    original = cli.convert
    results = []
    writes = 0

    def observe(*args, **kwargs):
        progress = kwargs.get("progress")
        token = kwargs["cancel"]

        def event_hook(event):
            nonlocal writes
            if progress:
                progress(event)
            if event.phase == "write":
                writes += 1
            if ((cancel_at == "convert" and event.phase == "convert" and event.completed == 1)
                    or (cancel_at == "verified" and event.phase == "write" and writes == 1)
                    or (cancel_at == "deliver" and event.phase == "write" and writes == 2)):
                token.cancel("合成边界取消173")
        kwargs["progress"] = event_hook
        result = original(*args, **kwargs)
        results.append(result)
        return result

    argv = ["convert", str(source), "-o", str(out)]
    if config is not None:
        argv += ["--options-file", str(config)]
    for key in keys or ():
        argv += ["--preset-id", key]
    if not bundle:
        argv += ["--no-bundle"]
    if not standalone:
        argv += ["--no-standalone"]
    with monkeypatch.context() as patch:
        patch.setattr(cli, "convert", observe)
        code = cli.main(argv)
    return code, results[0] if results else None


def snapshot_worker(source, out, *, bundle=True, standalone=True, cancel_at=None):
    """工作线程使用真实不可变快照与生产事务；同步run便于钉住取消检查点。"""
    _path, abr, state = source
    model = BrushSelection([state.preset(key) for key in state.preset_ids], state.source_digest)
    worker = workers.ConvertWorker(abr, [], str(out), bundle, standalone, snapshot=model.snapshot())
    results, events = [], []
    worker.done.connect(results.append)
    worker.progress.connect(events.append)
    original = worker._on_progress
    writes = 0

    def event_hook(event):
        nonlocal writes
        original(event)
        if event.phase == "write":
            writes += 1
        if ((cancel_at == "convert" and event.phase == "convert" and event.completed == 1)
                or (cancel_at == "verified" and event.phase == "write" and writes == 1)
                or (cancel_at == "deliver" and event.phase == "write" and writes == 2)):
            worker.request_cancel()
    worker._on_progress = event_hook
    worker.run()
    assert len(results) == 1
    for event in events:
        if event.event is not None:
            validate_progress_event(event.event)
    return results[0], events


def texture_pixels(blob):
    root = ET.fromstring(read_preset_xml(blob))
    pattern = next(node for node in root.iter("resource") if node.get("type") == "patterns")
    with Image.open(io.BytesIO(base64.b64decode(pattern.text))) as image:
        pixels = np.array(image)
    return root, pixels


def test_isolation_and_real_id_inventory(app, source, window, capsys):
    path, _abr, state = source
    model = load_window(app, window, path)
    assert model.options.identities == state.identities
    assert cli.main(["convert", str(path), "--list-presets"]) == 0
    captured = capsys.readouterr()
    listing = json.loads(captured.out)
    assert [entry["id"] for entry in listing["presets"]] == list(state.preset_ids)
    assert listing["source_digest"] == state.source_digest
    assert len(set(state.preset_ids)) == 5
    assert state.identities[0].name == state.identities[1].name
    assert [entry["defaults"] for entry in listing["presets"]] == [
        dict.fromkeys(SWITCH_IDS, True), dict.fromkeys(SWITCH_IDS, True),
        dict.fromkeys(SWITCH_IDS, False), dict.fromkeys(SWITCH_IDS, False),
        dict(zip(SWITCH_IDS, [True, False, False], strict=True))]
    assert not captured.err


@pytest.mark.parametrize("bundle", [False, True], ids=["kpp", "bundle"])
@pytest.mark.parametrize("values", list(itertools.product([False, True], repeat=3)))
def test_eight_combinations_mixed_same_name_hidden_gui_cli_bytes(
        app, source, window, tmp_path, monkeypatch, bundle, values):
    path, _abr, _state = source
    model = load_window(app, window, path)
    first, second, *_ = model.options.preset_ids
    select_switches(window, first, values)
    select_switches(window, second, tuple(not value for value in values))
    window.sort_combo.setCurrentIndex(2)
    window.search_edit.setText(first)
    assert model.counts == dict(total=5, visible=1, sel=5, hidden=4)
    snapshot = model.snapshot()
    preview = export_preview_text(snapshot)
    for key in model.selected_ids:
        assert preview.count(key) == 1
    assert tuple(item.preset_id for item in snapshot.identities) == model.selected_ids
    assert [state_plan(snapshot.option_state(), first).outcome(key).enabled
            for key in SWITCH_IDS] == list(values)
    for lang_index in (1, 0):
        window.lang_combo.setCurrentIndex(lang_index)
        assert model.snapshot().config_json == snapshot.config_json
        assert model.counts["hidden"] == 4
    config = tmp_path / "同配置173.json"
    config.write_text(snapshot.config_json, encoding="utf-8")
    gui_out, cli_out = tmp_path / "gui", tmp_path / "cli"
    window.out_dir_edit.setText(str(gui_out))
    (window.radio_bundle if bundle else window.radio_kpp).setChecked(True)
    window._on_convert()
    wait_for(app, lambda: not window._active_workers)
    gui_result = window._last_result
    assert isinstance(gui_result, conversion.ConvertResult)
    code, cli_result = cli_run(monkeypatch, path, cli_out, config=config,
                              bundle=bundle, standalone=not bundle)
    assert code == 0 and gui_result.status == cli_result.status == "committed"
    assert gui_result.options_summary.to_dict() == cli_result.options_summary.to_dict()
    assert delivered_bytes(gui_out) == delivered_bytes(cli_out)
    assert len(gui_result.kpp_files) == 5
    # 不只比较两入口或相信计划自述：独立回读实际内嵌定义、字段与源像素锚点。
    root, pixels = texture_pixels(gui_result.kpp_files[0][1])
    params = {node.get("name"): node.text for node in root.iter("param")}
    definition = ET.fromstring(params["brush_definition"])
    rasterize, prescale, bake = values
    assert definition.get("type") == ("png_brush" if rasterize else "auto_brush")
    if not rasterize:
        assert float(definition.find("MaskGenerator").get("hfade")) == pytest.approx(0.45)
    assert pixels.shape == ((32, 32) if prescale else (16, 16))
    assert float(params["Texture/Pattern/Scale"]) == (1.0 if prescale else 2.0)
    # C=-50、PS亮度+150的反解为0.5/(510*0.5)-150/(255*0.5)，截断为-1。
    assert float(params["Texture/Pattern/Brightness"]) == (0.0 if bake else -1.0)
    assert float(params["Texture/Pattern/Contrast"]) == (1.0 if bake else 0.5)
    assert params["Texture/Pattern/Invert"] == ("false" if bake else "true")
    expected = model.options.preset(first).texture.image.astype(np.float64)
    if bake:
        expected = 255 - np.clip(np.rint((expected - 127.5) * 0.5 + 127.5) + 150, 0, 255)
    np.testing.assert_array_equal(pixels[::2, ::2] if prescale else pixels, expected.astype(np.uint8))
    for key in model.selected_ids:
        assert gui_result.option_plans[key].to_dict() == state_plan(snapshot.option_state(), key).to_dict()
    if bundle:
        assert verify_bundle(gui_result.bundle_path).ok
    assert not list(gui_out.glob(tx.STAGING_PREFIX + "*"))
    assert not window._busy and window._export_snapshot is None


@pytest.mark.parametrize("scale,brightness,prescale,bake", [
    (100, 127, False, False), (103.125, 127, False, False),
    (103.2, 128, True, True), (200, -128, True, True),
])
def test_applicability_boundaries_reach_both_entries(
        app, window, tmp_path, monkeypatch, scale, brightness, prescale, bake):
    path = tmp_path / "边界源173.abr"
    path.write_bytes(make_source_bytes(scale=scale, brightness=brightness))
    model = load_window(app, window, path)
    first = model.selected_ids[0]
    assert [model.options.enabled(first, key) for key in SWITCH_IDS] == [True, prescale, bake]
    window.brush_list.setCurrentItem(window._items[model.selected_ids[3]][0])
    assert all(not check.isEnabled() and not check.isChecked()
               for check in window.switch_checks.values())
    gui_result, _events = snapshot_worker((path, window._abr, model.options), tmp_path / "gui")
    code, cli_result = cli_run(monkeypatch, path, tmp_path / "cli")
    assert code == 0
    assert gui_result.options_summary.to_dict() == cli_result.options_summary.to_dict()
    assert delivered_bytes(tmp_path / "gui") == delivered_bytes(tmp_path / "cli")


def test_visible_bulk_subset_reload_and_changed_source(app, source, window, tmp_path, monkeypatch):
    path, _abr, _state = source
    model = load_window(app, window, path)
    first, second, *rest = model.selected_ids
    select_switches(window, first, (False, False, False))
    window.search_edit.setText(second)
    window._set_all(False)
    assert model.selected_ids == (first, *rest)
    assert model.counts["hidden"] == 4
    config = tmp_path / "隐藏选中173.json"
    config.write_text(model.snapshot().config_json, encoding="utf-8")
    window.radio_kpp.setChecked(True)
    window.out_dir_edit.setText(str(tmp_path / "gui"))
    window._on_convert()
    wait_for(app, lambda: not window._active_workers)
    code, result = cli_run(monkeypatch, path, tmp_path / "cli", config=config,
                           keys=list(reversed(model.selected_ids)), bundle=False)
    assert code == 0
    assert window._last_result.options_summary.to_dict() == result.options_summary.to_dict()
    assert delivered_bytes(tmp_path / "gui") == delivered_bytes(tmp_path / "cli")
    window._on_reload()
    wait_for(app, lambda: not window._active_workers)
    reset = window._selection.options
    assert window._selection.counts == dict(total=5, visible=5, sel=5, hidden=0)
    assert all(reset.enabled(first, switch) for switch in SWITCH_IDS)
    other = tmp_path / "另一源173.abr"
    other.write_bytes(make_source_bytes(scale=150))
    load_window(app, window, other)
    assert first not in window._selection.options.preset_ids
    assert window._selection.snapshot().config_json != config.read_text(encoding="utf-8")


def test_snapshot_copies_arrays_and_switch_values(source, tmp_path):
    path, abr, state = source
    model = BrushSelection([state.preset(key) for key in state.preset_ids], state.source_digest)
    first = model.selected_ids[0]
    model.options.set_override(first, SWITCH_IDS[0], False)
    snapshot = model.snapshot()
    worker = workers.ConvertWorker(abr, [], str(tmp_path / "gui"), True, True, snapshot=snapshot)
    original = worker._option_state.dump_config()
    model.options.reset()
    # 解析得到的图案可能只读；替换源侧数组仍必须与已交后台的快照隔离。
    model.options.preset(first).texture.image = np.zeros_like(model.options.preset(first).texture.image)
    snapshot.presets[0].texture.image = np.full_like(snapshot.presets[0].texture.image, 255)
    assert worker._option_state.dump_config() == original
    assert not np.all(worker._presets[0].texture.image == 0)
    assert not np.all(worker._presets[0].texture.image == 255)
    assert not worker._option_state.enabled(first, SWITCH_IDS[0])
    assert path.read_bytes() == make_source_bytes()


def test_default_bytes_and_sampled_padding_unchanged(source, tmp_path, monkeypatch):
    path, abr, state = source
    core = conversion.convert_presets(abr, [state.preset(key) for key in state.preset_ids],
                                     str(tmp_path / "core"))
    gui_result, _events = snapshot_worker(source, tmp_path / "gui")
    code, cli_result = cli_run(monkeypatch, path, tmp_path / "cli")
    assert code == 0
    assert core.kpp_files == gui_result.kpp_files == cli_result.kpp_files
    assert delivered_bytes(tmp_path / "core") == delivered_bytes(tmp_path / "gui")
    assert delivered_bytes(tmp_path / "core") == delivered_bytes(tmp_path / "cli")
    blob = core.kpp_files[2][1]
    root = ET.fromstring(read_preset_xml(blob))
    tip = next(node for node in root.iter("resource") if node.get("type") == "brushes")
    with Image.open(io.BytesIO(base64.b64decode(tip.text))) as image:
        pixels = np.array(image)
    assert pixels.shape == (10, 10)
    np.testing.assert_array_equal(pixels[2:8], state.preset(state.preset_ids[2]).tip_gray)
    assert np.all(pixels[:2] == 255) and np.all(pixels[8:] == 255)


@pytest.mark.parametrize("change,expected", [
    (lambda p: p.update(version=9), "version_mismatch"),
    (lambda p: p.update(source_digest="sha256:" + "0" * 64), "source_mismatch"),
    # 序列化省略全不适用项；最后一条是边界计算笔刷，预缩放明确不适用。
    (lambda p: p["presets"][-1]["switches"].update(prescale_texture=True), "inapplicable_on"),
    (lambda p: p["presets"].__setitem__(0, {"name": "同名计算173", "switches": {}}), "duplicate_name"),
    (lambda p: p["presets"][0].update(id="bc1-unknown"), "unknown_id"),
    (lambda p: p["presets"][0]["switches"].update(unknown=True), "unknown_switch"),
    (lambda p: p["presets"][0]["switches"].update(rasterize_computed=1), "bad_field"),
    (lambda p: p["presets"].append(deepcopy(p["presets"][0])), "duplicate_id"),
])
def test_same_bad_config_fails_before_output_on_both_adapters(source, tmp_path, change, expected):
    path, _abr, state = source
    model = BrushSelection([state.preset(key) for key in state.preset_ids], state.source_digest)
    snapshot = model.snapshot()
    payload = json.loads(snapshot.config_json)
    change(payload)
    invalid = replace(snapshot, config_json=json.dumps(payload))
    config = tmp_path / "坏配置173.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OptionConfigError) as gui_error:
        workers.ConvertWorker(source[1], [], str(tmp_path / "gui"), True, True, snapshot=invalid)
    with pytest.raises(OptionConfigError) as cli_error:
        cli.convert(str(path), str(tmp_path / "cli"), options_file=str(config))
    assert [(i.code, i.location) for i in gui_error.value.issues] == [
        (i.code, i.location) for i in cli_error.value.issues]
    assert expected in [i.code for i in cli_error.value.issues]
    assert cli.main(["convert", str(path), "-o", str(tmp_path / "cli"),
                     "--options-file", str(config)]) == 2
    assert not (tmp_path / "gui").exists() and not (tmp_path / "cli").exists()


@pytest.mark.parametrize("stage", ["convert", "verified", "deliver"])
@pytest.mark.parametrize("bundle,standalone", [(False, True), (True, False), (True, True)])
def test_cancellation_boundary_status_and_outputs_match(source, window, tmp_path, monkeypatch,
                                                       stage, bundle, standalone):
    gui_out, cli_out = tmp_path / "gui", tmp_path / "cli"
    for out in (gui_out, cli_out):
        out.mkdir()
        (out / "保留原件173.txt").write_bytes(b"old173")
    old_gui, old_cli = inventory(gui_out), inventory(cli_out)
    gui_result, events = snapshot_worker(source, gui_out, bundle=bundle,
                                         standalone=standalone, cancel_at=stage)
    code, cli_result = cli_run(monkeypatch, source[0], cli_out, bundle=bundle,
                               standalone=standalone, cancel_at=stage)
    assert isinstance(gui_result, conversion.ConvertResult)
    assert gui_result.options_summary.to_dict() == cli_result.options_summary.to_dict()
    assert gui_result.status == cli_result.status
    window._apply_converted(gui_result, show_dialog=False)
    assert window._status[0] == ("result_cancel_late" if stage == "deliver" else "result_cancelled")
    assert delivered_bytes(gui_out) == delivered_bytes(cli_out)
    assert all(inventory(gui_out)[name] == value for name, value in old_gui.items())
    assert all(inventory(cli_out)[name] == value for name, value in old_cli.items())
    if stage == "deliver":
        assert code == 0 and gui_result.status == "committed" and not gui_result.cancelled
        assert gui_result.transaction.cancel_requested_late
        assert cli_result.transaction.cancel_requested_late
        assert "deliver" in [event.stage for event in events]
    else:
        assert code == 130 and gui_result.status == "cancelled" and gui_result.cancelled
        assert inventory(gui_out) == old_gui and inventory(cli_out) == old_cli
        assert not gui_result.kpp_files and not gui_result.standalone_paths
        assert gui_result.bundle_path is None
        assert gui_result.options_summary.cancelled
        assert "cancelled" in [event.stage for event in events]
    assert not list(gui_out.glob(tx.STAGING_PREFIX + "*"))


def test_conflicts_reuse_and_alternative_reconversion_preserve_both_entries(
        source, tmp_path, monkeypatch):
    path, _abr, state = source
    results = []
    for entry in ("gui", "cli"):
        out = tmp_path / entry
        def run():
            if entry == "gui":
                return snapshot_worker(source, out)[0]
            code, result = cli_run(monkeypatch, path, out)
            assert code == 0
            return result
        first = run()
        before = inventory(out)
        repeated = run()
        assert repeated.status == "reused_only" and inventory(out) == before
        assert repeated.transaction.counts["reused"] == 6
        target = Path(first.standalone_paths[first.kpp_files[0][0]])
        target.write_bytes(b"synthetic-foreign-kpp173")
        bundle = Path(first.bundle_path)
        bundle.write_bytes(b"synthetic-foreign-bundle173")
        foreign = inventory(out)
        renamed = run()
        assert renamed.transaction.counts["renamed"] == 2
        assert all(inventory(out)[name] == value for name, value in foreign.items())
        assert Path(renamed.bundle_path).name.endswith("_2.bundle")
        assert verify_bundle(renamed.bundle_path).ok
        results.append(delivered_bytes(out))
    assert results[0] == results[1]
    config = tmp_path / "可编辑173.json"
    for key in state.preset_ids:
        for switch in SWITCH_IDS:
            if state.applicability(key, switch).applicable:
                state.set_override(key, switch, False)
    config.write_text(state.dump_config(), encoding="utf-8")
    before = inventory(tmp_path / "cli")
    code, editable = cli_run(monkeypatch, path, tmp_path / "cli", config=config)
    assert code == 0 and editable.transaction.counts["renamed"] > 0
    assert all(inventory(tmp_path / "cli")[name] == value for name, value in before.items())


def test_render_failure_retained_scene_reaches_both_adapters(source, tmp_path, monkeypatch, capsys):
    """渲染错误叠加未知暂存文件：清理不得删外来文件，两个入口都必须保留清单。"""
    original_cleanup = tx.ExportTransaction._cleanup_staging
    def foreign_cleanup(transaction):
        (transaction._staging / "foreign173.txt").write_bytes(b"foreign173")
        original_cleanup(transaction)
    def fail_render(*_args, **_kwargs):
        raise RuntimeError("合成渲染失败173")
    monkeypatch.setattr(tx.ExportTransaction, "_cleanup_staging", foreign_cleanup)
    monkeypatch.setattr(conversion, "_render_preset", fail_render)
    failure, _events = snapshot_worker(source, tmp_path / "gui")
    code, _result = cli_run(monkeypatch, source[0], tmp_path / "cli")
    captured = capsys.readouterr()
    assert isinstance(failure, workers.ConvertFailure)
    assert failure.summary is not None, "GUI丢失了清理受阻的真实事务清单"
    assert failure.summary.retained
    assert code == 3, "有受阻现场时CLI必须报安全写出失败，不压成内部错误1"
    assert "retained=1" in captured.err
    assert "转换完成" not in captured.out
    assert not list((tmp_path / "gui").glob("*.bundle"))
    for entry in ("gui", "cli"):
        foreign = list((tmp_path / entry).rglob("foreign173.txt"))
        assert len(foreign) == 1 and foreign[0].read_bytes() == b"foreign173"


def test_ordinary_render_error_keeps_internal_exit_code_and_cleans_scene(source, tmp_path, monkeypatch):
    """清理无阻碍的普通渲染异常仍为内部错误1，不把全部异常升级成安全失败3。"""
    def fail_render(*_args, **_kwargs):
        raise RuntimeError("普通合成错误173")
    monkeypatch.setattr(conversion, "_render_preset", fail_render)
    failure, _events = snapshot_worker(source, tmp_path / "gui")
    code, _result = cli_run(monkeypatch, source[0], tmp_path / "cli")
    assert isinstance(failure, workers.ConvertFailure) and failure.summary is None
    assert code == 1
    assert not inventory(tmp_path / "gui") and not inventory(tmp_path / "cli")


def test_skipped_selection_is_not_reported_as_applied(app, window, tmp_path):
    """缺笔尖的映射对象是显式边界夹具，不冒称真实ABR解析可产生它。"""
    presets = [BrushPreset("可处理173", is_computed=True, diameter=16),
               BrushPreset("缺笔尖173", is_computed=False, tip_gray=None)]
    state = ExportOptionState.create(presets, source_digest_of_bytes(b"explicit-skip173"))
    model = BrushSelection(presets, state.source_digest)
    snapshot = model.snapshot()
    assert i18n.tr("skipped_tip") in export_preview_text(snapshot)
    worker = workers.ConvertWorker(AbrFile(6, 2, name="显式夹具173"), [],
                                  str(tmp_path / "skip"), False, True, snapshot=snapshot)
    results = []
    worker.done.connect(results.append)
    worker.run()
    result = results[0]
    assert len(result.kpp_files) == 1 and len(result.skipped) == 1
    assert [item.identity.preset_id for item in result.options_summary.items] == [state.preset_ids[0]]
    assert state.preset_ids[1] not in result.option_plans
    window._apply_converted(result, show_dialog=False)
    assert window._status[0] == "result_committed"
