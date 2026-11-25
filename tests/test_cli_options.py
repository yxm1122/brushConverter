"""171命令行逐支配置测试：只用小数组和虚构名称，保留真实解析、转换与事务。

测试不创建Qt应用或窗口，不读取真实ABR，不发送宿主信号；运行期文件只写本任务
.tmp/171/test-author/。本文件由测试代理编写，交主会话串行运行。
"""

from __future__ import annotations

import builtins
import importlib
import itertools
import json
import os
import re
import signal
import struct
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cli  # noqa: E402
from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import ConvertResult, convert_presets  # noqa: E402
from brush_converter.export_options import (  # noqa: E402
    CONFIG_FORMAT,
    CONFIG_VERSION,
    SWITCH_IDS,
    CancelToken,
    ExportOptionState,
    OptionConfigError,
    source_digest_of_bytes,
)
from brush_converter.export_transaction import (  # noqa: E402
    STAGING_PREFIX,
    ExportTransaction,
    ExportTransactionError,
)
from brush_converter.kpp.bundle import verify_bundle  # noqa: E402
from brush_converter.mapping import BrushPreset, TextureSettings, map_presets  # noqa: E402

pytestmark = pytest.mark.verify
TASK = ROOT / ".tmp" / "171" / "test-author"


@pytest.fixture
def case_dir(monkeypatch):
    """每例使用任务内新目录，既不复用旧证据，也不依赖系统临时目录。"""
    path = TASK / "cases" / uuid4().hex
    path.mkdir(parents=True)
    scratch = path / "temporary"
    scratch.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(key, str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.chdir(path)
    assert path.resolve().is_relative_to((ROOT / ".tmp" / "171").resolve())
    return path


def _minimal_abr_bytes() -> bytes:
    """直接编码一支4×6采样笔尖的v1 ABR，经过生产解析器而非伪造解析对象。"""
    mask = np.array([
        [0, 0, 80, 160, 0, 0],
        [0, 90, 180, 255, 120, 0],
        [0, 20, 130, 240, 60, 0],
        [0, 0, 30, 50, 0, 0],
    ], dtype=np.uint8)
    height, width = mask.shape
    body = (struct.pack(">IH", 0, 25) + b"\x01"
            + struct.pack(">4h", 0, 0, height, width)
            + struct.pack(">4i", 0, 0, height, width)
            + struct.pack(">HB", 8, 0) + (255 - mask).tobytes())
    return struct.pack(">HHHI", 1, 1, 2, len(body)) + body


def _computed(name: str = "虚构计算171", *, diameter: float = 16.0) -> BrushPreset:
    """三项开关均适用；纹理只有8×8像素，名称与元信息均为虚构。"""
    return BrushPreset(
        name=name, diameter=diameter, hardness=45.0, roundness=75.0,
        angle=13.0, is_computed=True, uuid="synthetic-tip-171",
        texture=TextureSettings(
            name="虚构纹理171", uuid="synthetic-texture-171", scale=200.0,
            brightness=150, contrast=-50, invert=True, blend_mode="Mltp",
            image=(np.arange(64, dtype=np.uint8).reshape(8, 8) * 4),
        ),
        meta={"author": "虚构作者171", "private_note": "虚构元数据171"},
    )


def _sampled(name: str = "虚构采样171") -> BrushPreset:
    """没有纹理或计算笔刷开关的4×6采样笔尖。"""
    return BrushPreset(name=name, uuid="synthetic-sampled-171",
                       tip_gray=np.arange(24, dtype=np.uint8).reshape(4, 6) * 10,
                       meta={"author": "虚构采样作者171"})


@dataclass
class SyntheticSource:
    """同一源字节对应的解析结果、映射夹具与稳定身份生成器。"""

    path: Path
    data: bytes
    abr: AbrFile
    presets: list[BrushPreset]

    def state(self) -> ExportOptionState:
        """每次新建独立状态，避免测试自身沿用手动覆盖。"""
        return ExportOptionState.create(deepcopy(self.presets), source_digest_of_bytes(self.data))


def _source(case_dir, monkeypatch, presets=None, *, inject_mapping=True) -> SyntheticSource:
    """只替换公开映射边界，ABR解析、KPP转换及169事务仍走生产实现。"""
    path = case_dir / "虚构 源171.abr"
    data = _minimal_abr_bytes()
    path.write_bytes(data)
    abr = AbrFile._from_bytes(data, path.stem)
    assert len(abr.tips) == 1 and abr.tips[0].gray.shape == (4, 6)
    chosen = ([_computed("虚构甲171"), _sampled("虚构乙171"),
               _computed("虚构丙171", diameter=24.0)] if presets is None else presets)
    if inject_mapping:
        def mapped(parsed):
            """验证CLI确实先解析了同一份最小ABR，再返回全新的合成预设。"""
            assert isinstance(parsed, AbrFile)
            assert (parsed.version, parsed.subversion) == (1, 1)
            assert len(parsed.tips) == 1
            np.testing.assert_array_equal(parsed.tips[0].gray, abr.tips[0].gray)
            return deepcopy(chosen)

        monkeypatch.setattr(cli, "map_presets", mapped)
    else:
        chosen = map_presets(abr)
    return SyntheticSource(path, data, abr, deepcopy(chosen))


@pytest.fixture
def source(case_dir, monkeypatch):
    """默认提供计算、采样、另一支计算的混合来源。"""
    return _source(case_dir, monkeypatch)


def _payload(state, entries=None):
    """显式配置只写冻结字段；省略条目与省略开关均留给167计算条件默认。"""
    return {"format": CONFIG_FORMAT, "version": CONFIG_VERSION,
            "source_digest": state.source_digest,
            "presets": [] if entries is None else entries}


def _config(case_dir, payload, *, name="逐支选项171.json", bom=False):
    """使用UTF-8或UTF-8 BOM保存配置，路径留在本例目录内。"""
    path = case_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8-sig" if bom else "utf-8")
    return path


def _kpps(out):
    """从最终独立目录读取KPP字节；比较完整字节，不只比较XML字段。"""
    return {path.name: path.read_bytes() for path in sorted((out / "kpp").glob("*.kpp"))}


def _core(source, out, state=None, preset_ids=None):
    """核心对照沿用原身份和源顺序，不重新给筛选子集编号。"""
    presets = deepcopy(source.presets) if state is None else [
        state.preset(key) for key in state.preset_ids
        if preset_ids is None or key in preset_ids
    ]
    return convert_presets(source.abr, presets, str(out), make_bundle=False,
                           option_state=state)


def _inventory(root):
    """记录目录形状、全部旧字节及纳秒mtime，连意外新增暂存目录也能发现。"""
    directories = tuple(sorted(str(path.relative_to(root)) for path in root.rglob("*")
                               if path.is_dir())) if root.exists() else ()
    files = {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
             for path in root.rglob("*") if path.is_file()} if root.exists() else {}
    return directories, files


def _old_output(case_dir):
    """预置两个旧文件，后续失败/取消必须同时保住内容与mtime。"""
    out = case_dir / "旧输出171"
    (out / "kpp").mkdir(parents=True)
    for index, path in enumerate((out / "保留171.bundle", out / "kpp" / "保留171.kpp")):
        path.write_bytes(f"虚构旧文件171-{index}".encode("utf-8"))
        os.utime(path, ns=(1_700_000_000_000_000_000 + index,
                          1_700_000_000_000_000_000 + index))
    return out


def _private_text_absent(text, source=None, extra=()):
    """日志不能泄露真实仓库绝对路径或夹具中的原始名称、UUID、元信息。"""
    tokens = [str(ROOT), ROOT.as_posix(), *extra]
    if source is not None:
        tokens.extend((str(source.path), source.path.as_posix(), source.path.stem))
        for preset in source.presets:
            tokens.extend((preset.name, preset.uuid or ""))
            tokens.extend(str(value) for value in preset.meta.values())
            if preset.texture is not None:
                tokens.extend((preset.texture.name, preset.texture.uuid))
    for token in tokens:
        if token:
            assert token not in text, f"输出泄露了不应回显的字段：{token!r}"
    assert "Traceback (most recent call last)" not in text
    assert 'File "' not in text


def _bad_for_both_outputs(case_dir, source, capsys, argv_tail, issue):
    """坏输入对全新输出零创建，对已有输出零改动，并返回明确配置错误码。"""
    old = _old_output(case_dir)
    old_before = _inventory(old)
    new = case_dir / "绝不创建171"
    for out in (new, old):
        capsys.readouterr()
        code = cli.main(["convert", str(source.path), "-o", str(out), *argv_tail])
        captured = capsys.readouterr()
        text = captured.out + captured.err
        assert type(code) is int and code == 2
        assert f"[{issue}]" in text
        assert "转换完成" not in text
        _private_text_absent(text, source)
        assert not new.exists()
        assert _inventory(old) == old_before


@pytest.fixture
def fake_signals(monkeypatch):
    """用内存登记表替代标准库信号注册，不向宿主发送或真正安装任何处理器。"""
    break_signal = getattr(signal, "SIGBREAK", 17199)
    monkeypatch.setattr(signal, "SIGBREAK", break_signal, raising=False)
    original = {signal.SIGINT: object(), break_signal: object()}
    current = dict(original)
    registrations = []

    def get_handler(signum):
        """读回当前内存处理器，供CLI保存与恢复。"""
        return current[signum]

    def install(signum, handler):
        """记录安装/恢复动作并返回上一处理器，与signal.signal语义一致。"""
        previous = current[signum]
        current[signum] = handler
        registrations.append((signum, handler))
        return previous

    monkeypatch.setattr(signal, "getsignal", get_handler)
    monkeypatch.setattr(signal, "signal", install)
    return SimpleNamespace(current=current, original=original, registrations=registrations,
                           sigint=signal.SIGINT, sigbreak=break_signal)


def _assert_handlers_restored(fake_signals):
    """成功、取消和异常路径都必须恢复两种中断的原处理器。"""
    assert fake_signals.current == fake_signals.original
    for signum in fake_signals.original:
        changes = [handler for sig, handler in fake_signals.registrations if sig == signum]
        assert len(changes) >= 2
        assert callable(changes[0])
        assert changes[-1] is fake_signals.original[signum]


def test_minimal_real_abr_converts_without_mapping_patch(case_dir, monkeypatch, capsys):
    """最小ABR从生产解析、生产映射一路进入真实转换与bundle回读校验。"""
    source = _source(case_dir, monkeypatch, inject_mapping=False)
    assert len(source.presets) == 1 and not source.presets[0].is_computed
    expected = _core(source, case_dir / "核心171")
    out = case_dir / "实际171"
    assert cli.main(["convert", str(source.path), "-o", str(out)]) == 0
    assert _kpps(out) == dict(expected.kpp_files)
    bundles = list(out.glob("*.bundle"))
    assert len(bundles) == 1 and verify_bundle(bundles[0]).ok
    _private_text_absent(capsys.readouterr().out, source)


@pytest.mark.parametrize("mode", ("list", "convert"))
def test_source_is_read_once_and_digest_matches_parser_bytes(mode, source, case_dir, monkeypatch, capsys):
    """第一次读取后改写源文件，列ID和带配置转换仍绑定实际解析的原字节。"""
    state = source.state()
    payload = _payload(state, [{"id": state.preset_ids[0],
                               "switches": {SWITCH_IDS[0]: False}}])
    config = _config(case_dir, payload)
    state.apply_config(payload)
    expected = _core(source, case_dir / "核心快照171", state)
    original_read = Path.read_bytes
    reads = []

    def read_and_change(path):
        """只对本例源文件计数；第二次读取会得到另一份合法但不同的源。"""
        data = original_read(path)
        if path.resolve() == source.path.resolve():
            reads.append(data)
            path.write_bytes(struct.pack(">HH", 1, 0))
        return data

    monkeypatch.setattr(Path, "read_bytes", read_and_change)
    out = case_dir / "单次读取171"
    tail = ["--list-presets"] if mode == "list" else ["--options-file", str(config)]
    assert cli.main(["convert", str(source.path), "-o", str(out), *tail]) == 0
    text = capsys.readouterr().out
    assert reads == [source.data]
    if mode == "list":
        listed = json.loads(text)
        assert listed["source_digest"] == source_digest_of_bytes(source.data)
        assert [row["id"] for row in listed["presets"]] == list(source.state().preset_ids)
        assert not out.exists()
    else:
        assert _kpps(out) == dict(expected.kpp_files)
        assert state.preset_ids[0] in text


def test_list_presets_is_json_only_and_contains_no_metadata(source, case_dir, capsys):
    """ID清单只含冻结字段与条件默认值，不能顺带导出或泄露显示元信息。"""
    state = source.state()
    out = case_dir / "只列出171"
    code = cli.main(["convert", str(source.path), "--list-presets", "-o", str(out)])
    captured = capsys.readouterr()
    assert type(code) is int and code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert set(payload) == {"format", "version", "source_digest", "presets"}
    assert payload["format"] == "brushconverter.preset-list"
    assert type(payload["version"]) is int and payload["version"] == 1
    assert payload["source_digest"] == state.source_digest
    assert len(payload["presets"]) == len(state.preset_ids)
    for row, identity in zip(payload["presets"], state.identities):
        assert set(row) == {"id", "ordinal", "applicable", "defaults"}
        assert row["id"] == identity.preset_id and row["ordinal"] == identity.ordinal
        for key in ("applicable", "defaults"):
            assert set(row[key]) == set(SWITCH_IDS)
            assert all(type(value) is bool for value in row[key].values())
        assert row["applicable"] == {key: state.applicability(identity.preset_id, key).applicable
                                     for key in SWITCH_IDS}
        assert row["defaults"] == {key: state.default_enabled(identity.preset_id, key)
                                   for key in SWITCH_IDS}
    assert not out.exists() and not (case_dir / "converted").exists()
    _private_text_absent(captured.out, source)


@pytest.mark.parametrize("combination", ("options", "selection", "both"))
def test_list_presets_rejects_options_or_selection(combination, source, case_dir, capsys):
    """清单模式拒绝配置与选择混用，不能忽略参数后悄悄输出或转换。"""
    state = source.state()
    config = _config(case_dir, _payload(state))
    tail = ["--list-presets"]
    if combination in ("options", "both"):
        tail += ["--options-file", str(config)]
    if combination in ("selection", "both"):
        tail += ["--preset-id", state.preset_ids[0]]
    _bad_for_both_outputs(case_dir, source, capsys, tail, "bad_field")


@pytest.mark.parametrize("switch_values", tuple(itertools.product((False, True), repeat=3)))
def test_each_switch_and_combination_match_core_bytes(switch_values, source, case_dir, capsys):
    """三项单项/组合的全部布尔组合，CLI结果必须与真实核心KPP逐字节一致。"""
    state = source.state()
    key = state.preset_ids[0]
    config_payload = _payload(state, [{"id": key, "switches": dict(zip(SWITCH_IDS, switch_values))}])
    state.apply_config(config_payload)
    config = _config(case_dir, config_payload)
    expected = _core(source, case_dir / "核心171", state)
    out = case_dir / "实际171"
    assert cli.main(["convert", str(source.path), "-o", str(out),
                     "--options-file", str(config)]) == 0
    assert _kpps(out) == dict(expected.kpp_files)
    text = capsys.readouterr().out
    assert "conversion-run" in text and "实际" in text
    assert "alternative" in text if not all(switch_values) else "applied" in text
    _private_text_absent(text, source)


def test_default_command_matches_stateless_core_and_selects_all(source, case_dir, capsys):
    """旧用法不带新参数与-o时仍转换全部，默认目录只落在当前任务工作区。"""
    expected = _core(source, case_dir / "核心默认171")
    assert cli.main(["convert", str(source.path)]) == 0
    out = case_dir / "converted"
    assert len(_kpps(out)) == len(source.presets)
    assert _kpps(out) == dict(expected.kpp_files)
    _private_text_absent(capsys.readouterr().out, source)


@pytest.mark.parametrize("bom", (False, True))
def test_valid_utf8_config_uses_shared_apply_and_omission_defaults(bom, source, case_dir,
                                                                monkeypatch, capsys):
    """UTF-8及BOM配置均交167完整校验，省略项保持条件默认而非全部关闭。"""
    state = source.state()
    payload = _payload(state, [{"id": state.preset_ids[0],
                               "switches": {SWITCH_IDS[0]: False}}])
    state.apply_config(payload)
    config = _config(case_dir, payload, bom=bom)
    expected = _core(source, case_dir / "核心171", state)
    original_apply = ExportOptionState.apply_config
    observed = []

    def shared_apply(current, config_payload):
        """记录整份源状态确实收到了原配置，再调用真正的167校验。"""
        observed.append((current.preset_ids, deepcopy(config_payload)))
        return original_apply(current, config_payload)

    monkeypatch.setattr(ExportOptionState, "apply_config", shared_apply)
    out = case_dir / "实际171"
    assert cli.main(["convert", str(source.path), "-o", str(out),
                     "--options-file", str(config)]) == 0
    assert (source.state().preset_ids, payload) in observed
    assert _kpps(out) == dict(expected.kpp_files)
    assert state.enabled(state.preset_ids[0], SWITCH_IDS[1])
    assert state.enabled(state.preset_ids[2], SWITCH_IDS[0])
    _private_text_absent(capsys.readouterr().out, source)


def test_selected_ids_keep_source_order_and_omit_unselected_export(source, case_dir, capsys):
    """命令行把第三支写在前也不改变源顺序，未选中配置可校验但不能导出。"""
    state = source.state()
    first, omitted, third = state.preset_ids
    payload = _payload(state, [
        {"id": omitted, "switches": {SWITCH_IDS[0]: False}},
        {"id": third, "switches": {SWITCH_IDS[2]: False}},
    ])
    state.apply_config(payload)
    expected = _core(source, case_dir / "核心171", state, [first, third])
    config = _config(case_dir, payload)
    out = case_dir / "实际171"
    assert cli.main(["convert", str(source.path), "-o", str(out),
                     "--options-file", str(config), "--preset-id", third,
                     "--preset-id", first]) == 0
    assert _kpps(out) == dict(expected.kpp_files)
    text = capsys.readouterr().out
    assert text.index(first) < text.index(third)
    assert omitted not in text and len(_kpps(out)) == 2


def test_gui_snapshot_of_second_identical_duplicate_matches_cli(case_dir, monkeypatch, capsys):
    """只选同名同内容第二支时，纯状态GUI快照和CLI必须保留原ID并逐字节一致。"""
    # gui包的既有初始化会导入main_window/Qt；导入库不等于创建应用或真实窗口。
    qt_before = sys.modules.get("PySide6.QtCore")
    app_before = qt_before.QCoreApplication.instance() if qt_before is not None else None
    selection_module = importlib.import_module("gui.brush_selection")
    qt_core = sys.modules["PySide6.QtCore"]
    assert qt_core.QCoreApplication.instance() is app_before
    twin = _computed("虚构同名171")
    source = _source(case_dir, monkeypatch, [twin, deepcopy(twin), _sampled()])
    model = selection_module.BrushSelection(deepcopy(source.presets), source_digest_of_bytes(source.data))
    first, second, third = model.options.preset_ids
    assert model.options.identity(first).content_digest == model.options.identity(second).content_digest
    model.options.set_override(second, SWITCH_IDS[0], False)
    model.options.set_override(second, SWITCH_IDS[2], False)
    model.set_selected(first, False)
    model.set_selected(third, False)
    model.set_view(query="完全隐藏171", sort_mode="name_desc")
    snapshot = model.snapshot()
    assert snapshot.hidden_selected == 1
    assert [identity.preset_id for identity in snapshot.identities] == [second]
    assert snapshot.identities[0].ordinal == 2
    state = snapshot.option_state()
    expected = convert_presets(source.abr, [state.preset(second)], str(case_dir / "GUI核心171"),
                               make_bundle=False, option_state=state)
    config = case_dir / "GUI快照171.json"
    config.write_text(snapshot.config_json, encoding="utf-8")
    out = case_dir / "CLI实际171"
    assert cli.main(["convert", str(source.path), "-o", str(out),
                     "--options-file", str(config), "--preset-id", second]) == 0
    assert _kpps(out) == dict(expected.kpp_files)
    wrong_defaults = convert_presets(source.abr, [deepcopy(twin)], str(case_dir / "默认反例171"),
                                     make_bundle=False)
    assert list(_kpps(out).values()) != [data for _, data in wrong_defaults.kpp_files]
    text = capsys.readouterr().out
    assert second in text and first not in text and third not in text
    assert qt_core.QCoreApplication.instance() is app_before
    if app_before is not None:
        # 若组装回归已有应用，快照用例也不能增开任何窗口。
        assert not any(widget.isVisible() for widget in app_before.topLevelWidgets())


@pytest.mark.parametrize("kind", ("unknown", "duplicate", "display_name"))
def test_invalid_preset_selection_fails_without_guessing(kind, source, case_dir, capsys):
    """未知ID、重复ID或把显示名当ID均失败，不按名称猜测，也不部分导出。"""
    first = source.state().preset_ids[0]
    keys = {"unknown": [first, "bc1-unknown-171"], "duplicate": [first, first],
            "display_name": [source.presets[0].name]}[kind]
    tail = list(itertools.chain.from_iterable(("--preset-id", key) for key in keys))
    _bad_for_both_outputs(case_dir, source, capsys, tail,
                          "duplicate_id" if kind == "duplicate" else "unknown_id")


def test_unselected_invalid_configuration_is_still_validated(source, case_dir, capsys):
    """只导出第一支时，第二支配置中的错误仍须阻断整批，不能藏在筛选后。"""
    first, second, _third = source.state().preset_ids
    payload = _payload(source.state(), [
        {"id": first, "switches": {SWITCH_IDS[0]: False}},
        {"id": second, "switches": {"not_a_switch_171": False}},
    ])
    config = _config(case_dir, payload)
    _bad_for_both_outputs(case_dir, source, capsys,
                          ["--options-file", str(config), "--preset-id", first], "unknown_switch")


@pytest.mark.parametrize("issue", (
    "bad_field", "bad_json", "unreadable", "version_mismatch", "source_mismatch",
    "unknown_id", "unknown_name", "duplicate_id", "duplicate_name", "unknown_switch",
    "inapplicable_on",
))
def test_all_eleven_config_errors_fail_before_writing(issue, case_dir, monkeypatch, fake_signals, capsys):
    """167冻结的十一类错误均非零退出，零创建新目录并保护旧文件字节和mtime。"""
    duplicate = _computed("虚构同名171")
    source = _source(case_dir, monkeypatch, [duplicate, deepcopy(duplicate), _sampled()])
    state = source.state()
    first, _second, sampled = state.preset_ids
    payload = _payload(state, [{"id": first, "switches": {SWITCH_IDS[0]: False}}])
    if issue == "bad_field":
        payload["unexpected_171"] = True
    elif issue == "version_mismatch":
        payload["version"] = 2
    elif issue == "source_mismatch":
        payload["source_digest"] = source_digest_of_bytes(b"another fictional source171")
    elif issue == "unknown_id":
        payload["presets"][0]["id"] = "bc1-unknown-171"
    elif issue == "unknown_name":
        payload["presets"][0] = {"name": "不存在的虚构笔刷171", "switches": {}}
    elif issue == "duplicate_id":
        payload["presets"] *= 2
    elif issue == "duplicate_name":
        payload["presets"][0] = {"name": duplicate.name, "switches": {}}
    elif issue == "unknown_switch":
        payload["presets"][0]["switches"] = {"not_a_switch_171": False}
    elif issue == "inapplicable_on":
        payload["presets"][0] = {"id": sampled, "switches": {SWITCH_IDS[0]: True}}
    config = case_dir / "错误配置171.json"
    if issue == "bad_json":
        config.write_bytes(b'{"format":')
    elif issue != "unreadable":
        _config(case_dir, payload, name=config.name)
    _bad_for_both_outputs(case_dir, source, capsys, ["--options-file", str(config)], issue)
    _assert_handlers_restored(fake_signals)


def test_shared_validation_reports_every_issue(source, case_dir, capsys):
    """一份配置同时含多处错误时，CLI必须保留167给出的全部诊断。"""
    state = source.state()
    payload = _payload(state, [
        {"id": state.preset_ids[0], "switches": {SWITCH_IDS[0]: "not_boolean_171"}},
        {"id": state.preset_ids[0], "switches": {"unknown_switch_171": False}},
        {"id": "bc1-unknown-171", "switches": {}},
    ])
    payload["version"] = 2
    payload["source_digest"] = source_digest_of_bytes(b"wrong synthetic source171")
    with pytest.raises(OptionConfigError) as caught:
        state.apply_config(payload)
    issues = caught.value.issues
    config = _config(case_dir, payload)
    out = case_dir / "不写出171"
    assert cli.main(["convert", str(source.path), "-o", str(out),
                     "--options-file", str(config)]) == 2
    captured = capsys.readouterr()
    text = captured.out + captured.err
    for code in {issue.code for issue in issues}:
        assert text.count(f"[{code}]") == sum(issue.code == code for issue in issues)
    for location in ("$.version", "$.source_digest",
                     "$.presets[0].switches.rasterize_computed",
                     "$.presets[1].id", "$.presets[2].id"):
        assert location in text
    assert not out.exists()
    _private_text_absent(text, source)


@pytest.mark.parametrize("level", ("root", "entry", "switch"))
def test_duplicate_json_keys_are_strictly_rejected(level, source, case_dir, capsys):
    """即使重复键的值完全相同，也不准JSON解码器覆盖前值后继续转换。"""
    key = json.dumps(source.state().preset_ids[0])
    switch = json.dumps(SWITCH_IDS[0])
    entries = f'{{"id":{key},"switches":{{{switch}:false}}}}'
    version = '"version":1'
    if level == "root":
        version += ',"version":1'
    elif level == "entry":
        entries = f'{{"id":{key},"id":{key},"switches":{{{switch}:false}}}}'
    else:
        entries = f'{{"id":{key},"switches":{{{switch}:false,{switch}:false}}}}'
    text = f'{{"format":{json.dumps(CONFIG_FORMAT)},{version},"presets":[{entries}]}}'
    config = case_dir / "重复键171.json"
    config.write_text(text, encoding="utf-8")
    _bad_for_both_outputs(case_dir, source, capsys, ["--options-file", str(config)], "bad_json")


@pytest.mark.parametrize("encoding", ("invalid_utf8", "utf16", "nan", "infinity"))
def test_invalid_encoding_or_nonstandard_json_is_rejected(encoding, source, case_dir, capsys):
    """非UTF-8字节与JSON标准外常量均明示失败，不能当默认配置继续。"""
    raw = json.dumps(_payload(source.state())).encode("utf-8")
    if encoding == "invalid_utf8":
        raw = b"\xff" + raw
    elif encoding == "utf16":
        raw = b"\xff\xfe" + raw.decode("utf-8").encode("utf-16-le")
    else:
        constant = "NaN" if encoding == "nan" else "Infinity"
        raw = ('{"format":' + json.dumps(CONFIG_FORMAT) + ',"version":' + constant
               + ',"presets":[]}').encode("utf-8")
    config = case_dir / "编码错误171.json"
    config.write_bytes(raw)
    _bad_for_both_outputs(case_dir, source, capsys, ["--options-file", str(config)], "bad_json")


@pytest.mark.parametrize("same_path", (False, True))
def test_repeated_options_file_is_rejected_even_if_identical(same_path, source, case_dir, capsys):
    """多份配置无论路径相同与否都失败，不合并也不采用最后一次参数。"""
    config = _config(case_dir, _payload(source.state()))
    second = config if same_path else _config(case_dir, _payload(source.state()), name="另一份171.json")
    _bad_for_both_outputs(case_dir, source, capsys,
                          ["--options-file", str(config), "--options-file", str(second)], "bad_field")


def test_help_explains_defaults_errors_escaping_cancel_and_unselected(capsys):
    """帮助需足以发现ID、单项覆盖、条件默认、转义和取消边界，不靠内部示例猜。"""
    code = cli.main(["convert", "--help"])
    captured = capsys.readouterr()
    assert type(code) is int and code == 0
    help_text = captured.out
    for option in ("--options-file", "--preset-id", "--list-presets", *SWITCH_IDS):
        assert option in help_text
    for word in ("默认", "未选中", "未知", "重复", "转义", "取消"):
        assert word in help_text
    assert re.search(r"Ctrl\+C|SIGINT|中断", help_text)
    assert "不可取消" in help_text
    assert "--select-none" not in help_text


def test_no_select_none_switch_is_introduced(source, case_dir, capsys):
    """不新增select-none语义；未知开关按参数错误返回2且不创建输出。"""
    out = case_dir / "不创建171"
    code = cli.main(["convert", str(source.path), "-o", str(out), "--select-none"])
    assert type(code) is int and code == 2
    assert not out.exists()
    assert "参数错误" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ("empty", "all_skipped"))
def test_zero_source_or_all_skipped_returns_two_without_directory(kind, case_dir, monkeypatch, capsys):
    """零源与缺笔尖全跳过分别说明原因，均返回2而不是“成功转换0支”。"""
    presets = [] if kind == "empty" else [BrushPreset("虚构无笔尖171")]
    source = _source(case_dir, monkeypatch, presets)
    out = case_dir / "空输出171"
    code = cli.main(["convert", str(source.path), "-o", str(out)])
    assert type(code) is int and code == 2
    text = capsys.readouterr().out
    assert ("零" in text if kind == "empty" else "跳过" in text)
    assert "转换完成" not in text and not out.exists()
    _private_text_absent(text, source)


def test_success_and_reuse_return_zero_without_rewriting(source, case_dir, fake_signals, capsys):
    """正常写出与同内容复用都返回0，第二次调用不改变任何产物mtime。"""
    out = case_dir / "复用输出171"
    argv = ["convert", str(source.path), "-o", str(out)]
    assert cli.main(argv) == 0
    first = _inventory(out)
    capsys.readouterr()
    assert cli.main(argv) == 0
    assert _inventory(out) == first
    assert "复用" in capsys.readouterr().out
    _assert_handlers_restored(fake_signals)


@pytest.mark.parametrize("signal_name", ("SIGINT", "SIGBREAK"))
@pytest.mark.parametrize("phase,completed", (("prepare", 0), ("convert", 1), ("write", 3)))
def test_real_progress_interrupt_cleans_transaction_and_restores_handlers(
        signal_name, phase, completed, source, case_dir, monkeypatch, fake_signals, capsys):
    """真实进度触发两次登记处理器，取消幂等、旧文件不变、暂存清完且可重新转换。"""
    out = _old_output(case_dir)
    before = _inventory(out)
    original_convert = cli.convert
    results = []
    events = []
    cancelled = []
    signum = fake_signals.sigint if signal_name == "SIGINT" else fake_signals.sigbreak

    def convert_with_interrupt(*args, **kwargs):
        """包装公开适配函数的进度订阅，继续执行真正的解析、渲染与事务。"""
        token = kwargs["cancel"]
        assert isinstance(token, CancelToken)
        subscribed = kwargs["progress"]

        def progress(event):
            """只在指定协作检查点请求取消，绝不向宿主发信号。"""
            events.append(event)
            if not cancelled and event.phase == phase and event.completed == completed:
                handler = fake_signals.current[signum]
                assert callable(handler)
                handler(signum, None)
                reason = token.reason
                handler(signum, None)
                assert token.cancelled and token.reason == reason
                cancelled.append(True)
            if subscribed is not None:
                subscribed(event)

        kwargs["progress"] = progress
        result = original_convert(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(cli, "convert", convert_with_interrupt)
    code = cli.main(["convert", str(source.path), "-o", str(out)])
    assert type(code) is int and code == 130
    assert cancelled and any(event.phase == "cancelled" for event in events)
    result = results[0]
    assert result.cancelled and result.status == "cancelled"
    assert result.kpp_files == [] and result.bundle_path is None and result.standalone_paths == {}
    assert result.rendered_in_memory == completed
    assert result.transaction.status == "cancelled"
    assert result.options_summary.cancelled
    assert result.options_summary.evidence == "conversion-run"
    assert _inventory(out) == before
    assert not list(out.rglob(f"{STAGING_PREFIX}*"))
    text = capsys.readouterr().out
    assert "已取消" in text and "事务" in text
    assert not any(line.startswith("转换完成") for line in text.splitlines())
    _private_text_absent(text, source)
    _assert_handlers_restored(fake_signals)

    monkeypatch.setattr(cli, "convert", original_convert)
    assert cli.main(["convert", str(source.path), "-o", str(out)]) == 0
    assert len(_kpps(out)) == len(source.presets) + 1
    for path, old in before[1].items():
        assert _inventory(out)[1][path] == old
    _assert_handlers_restored(fake_signals)


def test_cancel_after_commit_started_finishes_delivery_and_returns_zero(
        source, case_dir, monkeypatch, fake_signals, capsys):
    """第一项已交付后才请求取消，真实169事务必须完整交付并提示迟到取消。"""
    expected = _core(source, case_dir / "核心171")
    out = case_dir / "迟到取消171"
    real_checkpoint = ExportTransaction.checkpoint
    real_convert = cli.convert
    fired = []
    results = []

    def checkpoint(transaction):
        """通过公开cancellable观察真正commit阶段，再调用CLI登记的处理器。"""
        if not transaction.cancellable and not fired:
            assert list((out / "kpp").glob("*.kpp"))
            handler = fake_signals.current[fake_signals.sigint]
            handler(fake_signals.sigint, None)
            handler(fake_signals.sigint, None)
            fired.append(True)
        return real_checkpoint(transaction)

    def record(*args, **kwargs):
        """保留真实适配结果以核对迟到取消没有改写成功状态。"""
        result = real_convert(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(ExportTransaction, "checkpoint", checkpoint)
    monkeypatch.setattr(cli, "convert", record)
    assert cli.main(["convert", str(source.path), "-o", str(out)]) == 0
    assert fired and results[0].transaction.cancel_requested_late
    assert not results[0].cancelled and results[0].status == "committed"
    assert _kpps(out) == dict(expected.kpp_files)
    assert all(item.state == "committed" for item in results[0].transaction.items)
    assert all(verify_bundle(bundle).ok for bundle in out.glob("*.bundle"))
    assert not list(out.rglob(f"{STAGING_PREFIX}*"))
    text = capsys.readouterr().out
    assert "不可取消" in text and "完整交付" in text
    assert not any(line.startswith("已取消：") for line in text.splitlines())
    _assert_handlers_restored(fake_signals)


@pytest.mark.parametrize("print_phase", ("result", "summary"))
def test_interrupt_while_printing_committed_result_keeps_zero(
        print_phase, source, case_dir, monkeypatch, fake_signals, capsys):
    """结果或真实摘要正在输出时中断仍只置令牌，已交付批次不误报130且恢复处理器。"""
    out = case_dir / "打印期中断171"
    expected = _core(source, case_dir / "核心打印171")
    real_convert = cli.convert
    real_print = builtins.print
    results = []
    tokens = []
    fired = []
    marker = "转换完成：" if print_phase == "result" else "逐支处理（真实运行"

    def record(*args, **kwargs):
        """记录已经真实提交的结果及信号处理器绑定的令牌。"""
        result = real_convert(*args, **kwargs)
        results.append(result)
        tokens.append(kwargs["cancel"])
        return result

    def print_with_interrupt(*values, **kwargs):
        """在已提交结果的指定输出行调用登记处理器，不发送真正系统信号。"""
        if not fired and any(marker in str(value) for value in values):
            assert results[0].status == "committed"
            handler = fake_signals.current[fake_signals.sigint]
            assert callable(handler), "已交付结果打印完成前不得恢复默认中断处理器"
            handler(fake_signals.sigint, None)
            reason = tokens[0].reason
            handler(fake_signals.sigint, None)
            assert tokens[0].cancelled and tokens[0].reason == reason
            fired.append(True)
        return real_print(*values, **kwargs)

    monkeypatch.setattr(cli, "convert", record)
    monkeypatch.setattr(builtins, "print", print_with_interrupt)
    code = cli.main(["convert", str(source.path), "-o", str(out)])
    assert type(code) is int and code == 0
    assert fired and results[0].status == "committed" and not results[0].cancelled
    assert _kpps(out) == dict(expected.kpp_files)
    text = capsys.readouterr().out
    assert not any(line.startswith("已取消：") for line in text.splitlines())
    _private_text_absent(text, source)
    _assert_handlers_restored(fake_signals)


@pytest.mark.parametrize("error_type", (ValueError, OSError, RuntimeError, struct.error, IndexError))
def test_general_failure_is_private_nonzero_and_restores_handlers(
        error_type, source, case_dir, monkeypatch, fake_signals, capsys):
    """一般解析/运行错误返回1，不打印异常原文、源码绝对路径或traceback。"""
    secret = f"虚构异常哨兵171 {ROOT / 'cli.py'} 虚构作者171 synthetic-tip-171"

    def fail(*_args, **_kwargs):
        """公开适配边界抛出带隐私哨兵的合成异常。"""
        raise error_type(secret)

    monkeypatch.setattr(cli, "convert", fail)
    out = case_dir / "错误输出171"
    code = cli.main(["convert", str(source.path), "-o", str(out)])
    captured = capsys.readouterr()
    assert type(code) is int and code == 1
    _private_text_absent(captured.out + captured.err, source, ("虚构异常哨兵171", secret))
    assert not out.exists()
    _assert_handlers_restored(fake_signals)


@pytest.mark.parametrize("phase,expected_status", (
    ("staging", "failed"), ("commit", "rolled_back"), ("retained", "partial_blocked"),
))
def test_real_transaction_failure_reports_counts_and_redacts_scene(
        phase, expected_status, source, case_dir, monkeypatch, fake_signals, capsys):
    """真实169失败清单含回滚/受阻/保留计数；原始异常、路径及元信息不回显。"""
    out = _old_output(case_dir)
    before = _inventory(out)
    real_convert = cli.convert
    real_checkpoint = ExportTransaction.checkpoint
    failures = []
    injected = []
    secret = f"虚构磁盘错误哨兵171 {ROOT / 'cli.py'} 虚构元数据171"

    def stage_error(_transaction, _name, _data):
        """暂存阶段通过公开接口模拟磁盘错误，清理仍交真实转换管线执行。"""
        raise OSError(secret)

    def checkpoint_error(transaction):
        """交付首项后制造错误；受阻例改变本次文件以验证不会误删现场。"""
        if not transaction.cancellable and not injected:
            created = [path for path in (out / "kpp").glob("*.kpp")
                       if str(path.relative_to(out)) not in before[1]]
            assert created
            if phase == "retained":
                created[0].write_bytes(b"fictional scene changed171")
            injected.append(True)
            raise OSError(secret)
        return real_checkpoint(transaction)

    def record_failure(*args, **kwargs):
        """仅记录真实事务异常，再原样交给CLI的错误分发。"""
        try:
            return real_convert(*args, **kwargs)
        except ExportTransactionError as exc:
            failures.append(exc)
            raise

    if phase == "staging":
        monkeypatch.setattr(ExportTransaction, "stage_kpp", stage_error)
    else:
        monkeypatch.setattr(ExportTransaction, "checkpoint", checkpoint_error)
    monkeypatch.setattr(cli, "convert", record_failure)
    code = cli.main(["convert", str(source.path), "-o", str(out)])
    captured = capsys.readouterr()
    assert type(code) is int and code == 3
    assert len(failures) == 1 and failures[0].summary is not None
    summary = failures[0].summary
    assert summary.status == expected_status
    text = captured.out + captured.err
    assert "事务" in text
    for key in ("new", "reused", "renamed", "blocked", "rolled_back", "retained"):
        assert re.search(rf'{key}\s*[=:]\s*{summary.counts[key]}\b', text)
    _private_text_absent(text, source, ("虚构磁盘错误哨兵171", secret))
    assert "转换完成" not in text
    after = _inventory(out)
    for name, value in before[1].items():
        assert after[1][name] == value
    if phase == "retained":
        assert summary.retained and summary.blocked
        assert set(after[1]) - set(before[1]) == {
            str(Path(path).relative_to(out)) for path in summary.retained if Path(path).is_file()
        }
    else:
        assert after == before
    assert not list(out.rglob(f"{STAGING_PREFIX}*"))
    _assert_handlers_restored(fake_signals)


def test_output_validation_failure_returns_three_without_raw_reasons(
        source, case_dir, monkeypatch, fake_signals, capsys):
    """兼容旧测试的convert替换接口，输出校验失败也必须返回3且不回显原始原因。"""
    secret = f"虚构校验原因哨兵171 {ROOT / 'cli.py'} synthetic-tip-171"
    fake = ConvertResult(bundle_path=str(case_dir / "虚构未交付171.bundle"),
                         verify=SimpleNamespace(ok=False, errors=[secret]))
    monkeypatch.setattr(cli, "convert", lambda *_args, **_kwargs: fake)
    assert cli.main(["convert", str(source.path), "-o", str(case_dir / "校验171")]) == 3
    captured = capsys.readouterr()
    assert "校验" in captured.out + captured.err
    _private_text_absent(captured.out + captured.err, source, ("虚构校验原因哨兵171", secret))
    _assert_handlers_restored(fake_signals)


def test_bad_config_with_private_value_keeps_codes_and_hides_raw_value(source, case_dir, capsys):
    """167错误含用户输入值时仍保留代码/位置，但不泄露值中的路径与元数据。"""
    state = source.state()
    secret = f"虚构配置值哨兵171 {ROOT / 'cli.py'} 虚构作者171"
    payload = _payload(state, [{"id": state.preset_ids[0],
                               "switches": {SWITCH_IDS[0]: secret}}])
    config = _config(case_dir, payload)
    out = case_dir / "隐私失败171"
    assert cli.main(["convert", str(source.path), "-o", str(out),
                     "--options-file", str(config)]) == 2
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "[bad_field]" in text and "$.presets[0].switches" in text
    _private_text_absent(text, source, ("虚构配置值哨兵171", secret))
    assert not out.exists()


@pytest.mark.parametrize("signal_name", ("SIGINT", "SIGBREAK"))
def test_interrupt_while_printing_transaction_failure_keeps_three(
        signal_name, source, case_dir, monkeypatch, fake_signals, capsys):
    """失败清单打印期间重复中断仍只置令牌，保留退出码3与完整明细，不泄露traceback。"""
    import builtins

    out = _old_output(case_dir)
    before = _inventory(out)
    real_print = builtins.print
    fired = []
    signum = fake_signals.sigint if signal_name == "SIGINT" else fake_signals.sigbreak

    def fail_stage(_transaction, _name, _data):
        """真实事务暂存失败，错误清理由169执行。"""
        raise OSError("虚构失败清单中断171")

    def interrupting_print(*values, **kwargs):
        """在主入口的异常摘要中调用已登记处理器，不向宿主发送信号。"""
        if not fired and any(str(value).startswith("事务明细：") for value in values):
            handler = fake_signals.current[signum]
            assert callable(handler), "打印失败清单时协作处理器被过早恢复"
            handler(signum, None)
            handler(signum, None)
            fired.append(True)
        return real_print(*values, **kwargs)

    monkeypatch.setattr(ExportTransaction, "stage_kpp", fail_stage)
    monkeypatch.setattr(builtins, "print", interrupting_print)
    assert cli.main(["convert", str(source.path), "-o", str(out)]) == 3
    assert fired
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "安全写出失败" in text and "事务明细：" in text
    assert "rolled_back=" in text and "retained=" in text
    assert "转换完成" not in text
    _private_text_absent(text, source)
    assert _inventory(out) == before
    _assert_handlers_restored(fake_signals)
