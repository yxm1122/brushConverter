"""178 命令行元信息标签测试：只用合成 ABR 与中性虚构文字，不启动 Qt/Krita。

运行期只写本任务 .tmp/178/test-author/；不读商用 ABR，不联网，不启动渲染。
每条用例都走真实 ABR 解析、真实 175/176 归一与身份、真实 169 事务和安全写出。
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cli  # noqa: E402
from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import convert_presets  # noqa: E402
from brush_converter.export_metadata import (  # noqa: E402
    DEFAULT_DESCRIPTION,
    BundleMetadata,
    PresetTagPlan,
    normalize_tags,
)
from brush_converter.export_options import ExportOptionState, source_digest_of_bytes  # noqa: E402
from brush_converter.kpp.bundle import (  # noqa: E402
    ELEM_PACKAGE_VERSION,
    ELEM_SOURCE_FILE,
    verify_bundle,
)
from brush_converter.mapping import BrushPreset  # noqa: E402

pytestmark = pytest.mark.verify
TASK = ROOT / ".tmp" / "178" / "test-author"
FORMAT = "brushconverter.export-metadata"


@pytest.fixture
def case_dir(monkeypatch):
    """每例独立目录；TEMP/TMP/TMPDIR 都限定在本任务内，不写系统临时目录。"""
    path = TASK / "cases" / uuid4().hex
    path.mkdir(parents=True)
    scratch = path / "temporary"
    scratch.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(key, str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.chdir(path)
    assert path.resolve().is_relative_to(TASK.resolve())
    return path


def _minimal_abr_bytes() -> bytes:
    """直接编码一支 4×6 采样笔尖的 v1 ABR；经过生产解析器而不是伪造解析对象。"""
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


def _computed(name: str = "合成甲178") -> BrushPreset:
    """计算笔刷：走真实硬度剖面生成采样笔尖，不依赖额外纹理。"""
    return BrushPreset(
        name=name, diameter=16.0, hardness=45.0, roundness=75.0,
        angle=13.0, is_computed=True, uuid="synthetic-tip-178",
    )


def _sampled(name: str = "合成乙178") -> BrushPreset:
    """采样笔刷：自带 4×6 位图，用于验证逐支标签按稳定 ID 落到第二支。"""
    return BrushPreset(
        name=name, uuid="synthetic-sampled-178",
        tip_gray=np.arange(24, dtype=np.uint8).reshape(4, 6) * 10,
    )


def _source(case_dir, monkeypatch):
    """同一份源字节对应解析结果、映射夹具与稳定身份；名称含中文和 &。"""
    path = case_dir / "合成 源&178.abr"
    data = _minimal_abr_bytes()
    path.write_bytes(data)
    abr = AbrFile._from_bytes(data, path.stem)
    assert len(abr.tips) == 1 and abr.tips[0].gray.shape == (4, 6)
    chosen = [_computed("合成甲178"), _sampled("合成乙178")]

    def mapped(parsed):
        assert isinstance(parsed, AbrFile)
        assert (parsed.version, parsed.subversion) == (1, 1)
        assert len(parsed.tips) == 1
        return deepcopy(chosen)

    monkeypatch.setattr(cli, "map_presets", mapped)
    state = ExportOptionState.create(deepcopy(chosen), source_digest_of_bytes(data))
    return path, abr, chosen, state


def _payload(state, *, metadata=None, tags=None):
    """显式配置只写契约字段；不塞任何真实个人信息。"""
    data = {"format": FORMAT, "version": 1, "source_digest": state.source_digest}
    if metadata is not None:
        data["metadata"] = metadata
    if tags is not None:
        data["tags"] = tags
    return data


def _config(case_dir, payload, name="元信息标签178.json", *, bom=False):
    path = case_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8-sig" if bom else "utf-8")
    return path


def _run_bundle(source, out, *extra):
    """只生成 .bundle；新参数必须用 --no-standalone 走同一事务。"""
    return cli.main(["convert", str(source), "-o", str(out),
                     "--no-standalone", *extra])


def _bundles(out):
    return sorted(Path(out).glob("*.bundle"))


def _bundle(out):
    found = _bundles(out)
    assert len(found) == 1, [path.name for path in found]
    return found[0]


def _files(root):
    """目录内全部文件的相对名与完整字节；不比较 mtime 就不算复用。"""
    root = Path(root)
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def _xml_texts(bundle):
    """读取包内 meta.xml / META-INF/manifest.xml 与全部 kpp，用来查转义和泄露。"""
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        meta = archive.read("meta.xml").decode("utf-8")
        manifest_name = next(name for name in names if name.endswith("manifest.xml"))
        manifest = archive.read(manifest_name).decode("utf-8")
        kpps = {name: archive.read(name) for name in names if name.endswith(".kpp")}
    return meta, manifest, kpps


def _trace_by_id(verified):
    return {entry["id"]: entry for entry in verified.trace["presets"]}


def _expected(common=(), source=(), added=(), removed=()):
    """与 GUI 共用的 175 生产规则；测试不另写一套标签排序。"""
    return PresetTagPlan(common=common, source=source,
                         added=added, removed=removed).final()


def _assert_safe_log(text):
    assert "Traceback (most recent call last)" not in text
    assert 'File "' not in text


def test_help_documents_contract_and_parseable_neutral_example(capsys):
    """帮助必须给出可复制的完整示例，且示例本身是合法 JSON、全是中性合成值。"""
    code = cli.main(["convert", "--help"])
    captured = capsys.readouterr()
    assert type(code) is int and code == 0
    text = captured.out
    for token in ("--metadata-file", "brushconverter.export-metadata", "--list-presets",
                  "source_digest", "name", "description", "author", "license",
                  "package_version", "MIT", "只写 .bundle", "旧口径", "未知/未选中 ID",
                  '"metadata":{}', '"tags":{}',
                  "source=[]",
                  ".tmp/178/样本.abr", ".tmp/178/元信息标签.json"):
        assert token in text, token
    sample = next(line.strip() for line in text.splitlines()
                  if line.strip().startswith('{"format":"brushconverter.export-metadata"'))
    data = json.loads(sample)
    assert data["format"] == FORMAT and type(data["version"]) is int
    assert data["metadata"]["author"] == "" and data["metadata"]["license"] == ""
    assert data["metadata"]["name"] == "合成示例 & <包>"
    assert data["metadata"]["description"] == "中文 & <说明>"
    assert data["tags"]["common"] == ["共享 & <标签>"]
    assert data["tags"]["source"] == []
    assert "替换为本源完整稳定ID" in data["tags"]["presets"]
    assert "MIT" not in json.dumps(data, ensure_ascii=False)


def test_without_metadata_config_keeps_legacy_bundle(case_dir, monkeypatch, capsys):
    """不给 --metadata-file 时保持旧口径：无追溯/逐支标签，字节与直接核心调用一致。"""
    source, abr, _presets, state = _source(case_dir, monkeypatch)
    out = case_dir / "旧口径输出"
    assert _run_bundle(source, out) == 0
    bundle = _bundle(out)
    verified = verify_bundle(bundle)
    assert verified.ok and verified.identity_scheme == "legacy"
    assert verified.trace is None
    assert all(not values for values in verified.preset_tags.values())
    assert f"meta:{ELEM_PACKAGE_VERSION}" not in verified.meta
    assert f"meta:{ELEM_SOURCE_FILE}" not in verified.meta
    assert "dc:author" not in verified.meta and "meta:license" not in verified.meta
    core_out = case_dir / "核心旧口径"
    core = convert_presets(abr, [state.preset(key) for key in state.preset_ids],
                           str(core_out), make_bundle=True, make_standalone=False,
                           option_state=state)
    assert core.status == "committed"
    assert _files(core_out) == _files(out)
    text = capsys.readouterr().out
    assert "包信息与标签回读" not in text
    _assert_safe_log(text)


def test_empty_explicit_config_matches_gui_snapshot_defaults(case_dir, monkeypatch, capsys):
    """显式空配置=GUI 空表单：标题/默认说明/来源标签/追溯逐字节一致。"""
    source, abr, presets, state = _source(case_dir, monkeypatch)
    config = _config(case_dir, _payload(state, metadata={}, tags={}),
                     name="空配置178.json")
    out = case_dir / "显式空配置输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 0
    bundle = _bundle(out)
    verified = verify_bundle(bundle)
    assert verified.ok and verified.identity_scheme == "v2"
    stem = source.stem
    assert verified.meta["dc:title"] == stem
    assert verified.meta["dc:description"] == DEFAULT_DESCRIPTION
    assert "dc:author" not in verified.meta and "meta:license" not in verified.meta
    assert verified.meta[f"meta:{ELEM_SOURCE_FILE}"] == source.name
    assert verified.trace["source"]["filename"] == source.name
    assert verified.trace["source"]["digest"] == state.source_digest
    trace = _trace_by_id(verified)
    assert set(trace) == set(state.preset_ids)
    for entry in trace.values():
        assert tuple(entry["tags"]) == (stem,)
        assert verified.preset_tags[entry["kpp"]] == (stem,)
    meta_xml, manifest_xml, _ = _xml_texts(bundle)
    assert "合成 源&amp;178" in manifest_xml  # 来源标签里的 & 必须转义
    assert str(case_dir) not in meta_xml and str(case_dir) not in manifest_xml

    # 用与 GUI 生产快照相同的输入直接调用核心：字节应完全一致，而不是只看状态。
    gui_out = case_dir / "GUI等价输出"
    gui_state = ExportOptionState.create(deepcopy(presets), state.source_digest)
    gui_plans = {key: PresetTagPlan(source=(stem,)).final()
                 for key in gui_state.preset_ids}
    gui = convert_presets(
        abr, [gui_state.preset(key) for key in gui_state.preset_ids], str(gui_out),
        make_bundle=True, make_standalone=False, option_state=gui_state,
        metadata=BundleMetadata(name=abr.name, source_filename=source.name),
        preset_tags=gui_plans, source_path=str(source))
    assert gui.status == "committed"
    assert _files(gui_out) == _files(out)

    text = capsys.readouterr().out
    assert "包信息与标签回读" in text
    assert "逐支标签 2/2 支" in text
    assert str(case_dir) not in text and str(source) not in text
    _assert_safe_log(text)


def test_explicit_metadata_and_tags_roundtrip(case_dir, monkeypatch, capsys):
    """显式中文/XML 特殊字符逐字段回读；逐支增删与共享/来源/单支关系正确。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    first, second = state.preset_ids
    common = ["共享 & <标签>", "共享 178"]
    payload = _payload(
        state,
        metadata={
            "name": "合成包 & <178>",
            "description": "中文 <说明> & 符号",
            "author": "合成作者 & <匿名>",
            "license": "CC0-合成-1.0",
            "package_version": "9.9-合成",
        },
        tags={
            "common": common,
            "source": ["改后来源 & 178"],
            "presets": {
                first: {"added": ["单支 <甲>"], "removed": ["共享 178"]},
                second: {"added": [], "removed": ["共享 & <标签>"]},
            },
        },
    )
    config = _config(case_dir, payload)
    out = case_dir / "显式输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 0
    bundle = _bundle(out)
    verified = verify_bundle(bundle)
    assert verified.ok
    assert verified.meta["dc:title"] == "合成包 & <178>"
    assert verified.meta["dc:description"] == "中文 <说明> & 符号"
    assert verified.meta["dc:author"] == "合成作者 & <匿名>"
    assert verified.meta["meta:license"] == "CC0-合成-1.0"
    assert verified.meta[f"meta:{ELEM_PACKAGE_VERSION}"] == "9.9-合成"
    assert verified.meta[f"meta:{ELEM_SOURCE_FILE}"] == source.name
    assert verified.meta["meta:license"] != "MIT"

    expected_first = _expected(common=common, source=("改后来源 & 178",),
                               added=("单支 <甲>",), removed=("共享 178",))
    expected_second = _expected(common=common, source=("改后来源 & 178",),
                                added=(), removed=("共享 & <标签>",))
    trace = _trace_by_id(verified)
    assert tuple(trace[first]["tags"]) == expected_first
    assert tuple(trace[second]["tags"]) == expected_second
    assert "共享 178" not in expected_first
    assert "共享 & <标签>" in expected_first
    assert "共享 & <标签>" not in expected_second
    assert "共享 178" in expected_second
    assert verified.preset_tags[trace[first]["kpp"]] == expected_first
    assert verified.preset_tags[trace[second]["kpp"]] == expected_second

    meta_xml, manifest_xml, _ = _xml_texts(bundle)
    for xml in (meta_xml, manifest_xml):
        assert "&amp;" in xml and "&lt;" in xml
        assert "合成包 & <178>" not in xml
        assert "共享 & <标签>" not in xml
        assert str(case_dir) not in xml
    assert "合成 源&amp;178" not in manifest_xml  # 来源标签已被改名，不再自动加回

    text = capsys.readouterr().out
    assert "包信息与标签回读" in text
    assert "逐支标签 2/2 支（5 个逐笔关联）" in text
    for marker in ("合成包 & <178>", "中文 <说明> & 符号", "合成作者 & <匿名>",
                   "CC0-合成-1.0", str(case_dir), str(source)):
        assert marker not in text, marker
    _assert_safe_log(text)


@pytest.mark.parametrize("mode, tags_part, expected_source", [
    ("默认", None, ["合成 源&178"]),
    ("禁用", [], []),
    ("改名", ["改名 来源 178"], ["改名 来源 178"]),
])
def test_source_tags_default_disabled_and_renamed(
        mode, tags_part, expected_source, case_dir, monkeypatch, capsys):
    """来源标签：省略=文件名主干，[]=禁用，其它=改名；都不自动加回旧标签。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    tags = {"common": ["公共 178"]}
    if tags_part is not None:
        tags["source"] = tags_part
    config = _config(case_dir, _payload(state, metadata={"author": ""}, tags=tags))
    out = case_dir / ("来源标签" + mode)
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 0
    verified = verify_bundle(_bundle(out))
    assert verified.ok
    expected = _expected(common=("公共 178",), source=tuple(expected_source))
    for entry in _trace_by_id(verified).values():
        assert tuple(entry["tags"]) == expected
        assert verified.preset_tags[entry["kpp"]] == expected
    stem = source.stem
    if mode == "禁用":
        assert stem not in expected
    elif mode == "改名":
        assert stem not in expected and "改名 来源 178" in expected
    else:
        assert stem in expected
    _assert_safe_log(capsys.readouterr().out)


def test_metadata_only_config_keeps_gui_default_source_tag(case_dir, monkeypatch, capsys):
    """只给 metadata 不给 tags 时，仍按 GUI 空表单写来源标签，而不是无标签。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    config = _config(case_dir, _payload(state, metadata={"author": "合成作者甲"}))
    out = case_dir / "只有元信息输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 0
    verified = verify_bundle(_bundle(out))
    assert verified.ok
    assert verified.meta["dc:author"] == "合成作者甲"
    assert verified.meta["dc:title"] == source.stem
    for values in verified.preset_tags.values():
        assert values == (source.stem,)
    assert "dc:author" in verified.meta
    _assert_safe_log(capsys.readouterr().out)


def test_unknown_author_and_license_default_empty_and_no_mit(case_dir, monkeypatch, capsys):
    """未知作者默认空；不自动套项目 MIT、不写邮箱/网站/日期。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    config = _config(case_dir, _payload(state, metadata={}, tags={}),
                     name="空作者178.json")
    out = case_dir / "默认空输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 0
    bundle = _bundle(out)
    verified = verify_bundle(bundle)
    assert verified.ok
    for key in ("dc:author", "meta:license", "meta:email", "meta:website",
                "meta:creation-date", "meta:dc-date"):
        assert key not in verified.meta, key
    meta_xml, manifest_xml, _ = _xml_texts(bundle)
    assert "MIT" not in meta_xml and "MIT" not in manifest_xml
    assert "author" not in json.dumps(verified.preset_tags, ensure_ascii=False)
    _assert_safe_log(capsys.readouterr().out)


def _bad_cases():
    """坏字段组合：全部必须在写盘前失败，且至少命中对应错误码。"""
    return [
        ("metadata非对象", lambda base, first: {**base, "metadata": []}, "bad_field"),
        ("metadata未知字段", lambda base, first: {**base, "metadata": {"email": "x@y"}},
         "unknown_field"),
        ("metadata字段类型", lambda base, first: {**base, "metadata": {"author": 123}},
         "bad_field"),
        ("metadata源文件名", lambda base, first: {
            **base, "metadata": {"source_filename": "x.abr"}}, "unknown_field"),
        ("tags非对象", lambda base, first: {**base, "tags": []}, "bad_field"),
        ("tags未知键", lambda base, first: {**base, "tags": {"other": []}},
         "unknown_field"),
        ("公共标签裸字符串", lambda base, first: {
            **base, "tags": {"common": "不是数组"}}, "bad_field"),
        ("来源标签元素类型", lambda base, first: {
            **base, "tags": {"source": [1]}}, "bad_field"),
        ("presets非对象", lambda base, first: {
            **base, "tags": {"presets": []}}, "bad_field"),
        ("条目非对象", lambda base, first: {
            **base, "tags": {"presets": {first: []}}}, "bad_field"),
        ("条目未知键", lambda base, first: {
            **base, "tags": {"presets": {first: {"other": []}}}}, "unknown_field"),
        ("added裸字符串", lambda base, first: {
            **base, "tags": {"presets": {first: {"added": "单支"}}}}, "bad_field"),
        ("removed元素类型", lambda base, first: {
            **base, "tags": {"presets": {first: {"removed": [1]}}}}, "bad_field"),
        ("顶层未知键", lambda base, first: {**base, "extra": 1}, "unknown_field"),
        ("无任何一节", lambda base, first: {
            key: base[key] for key in ("format", "version", "source_digest")}, "bad_field"),
    ]


@pytest.mark.parametrize("name, mutator, code", _bad_cases(),
                         ids=[item[0] for item in _bad_cases()])
def test_bad_fields_fail_before_any_output(name, mutator, code,
                                           case_dir, monkeypatch, capsys):
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    base = _payload(state, metadata={"author": ""}, tags={"common": []})
    config = _config(case_dir, mutator(base, state.preset_ids[0]), name=name + ".json")
    out = case_dir / "坏字段绝不创建"
    result = _run_bundle(source, out, "--metadata-file", str(config))
    captured = capsys.readouterr()
    assert result == 2 and not out.exists(), name
    assert f"[{code}]" in captured.err, captured.err
    assert str(case_dir) not in captured.err
    _assert_safe_log(captured.err)


def test_unknown_and_unselected_stable_ids_are_explicit(case_dir, monkeypatch, capsys):
    """未知 ID 与已知但未选中的 ID 都必须整批失败，不静默丢逐支标签。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    first, second = state.preset_ids
    unknown = "bc1-000000000000-9999-00000000"
    config = _config(case_dir, _payload(
        state, tags={"presets": {unknown: {"added": ["甲"]}}}))
    out = case_dir / "未知ID输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 2
    captured = capsys.readouterr()
    assert not out.exists()
    assert "[unknown_id]" in captured.err and unknown in captured.err

    config = _config(case_dir, _payload(
        state, tags={"presets": {second: {"added": ["乙"]}}}),
        name="未选中ID178.json")
    out = case_dir / "未选中ID输出"
    assert _run_bundle(source, out, "--metadata-file", str(config),
                       "--preset-id", first) == 2
    captured = capsys.readouterr()
    assert not out.exists()
    assert "[unselected_id]" in captured.err and second in captured.err
    _assert_safe_log(captured.err)


@pytest.mark.parametrize("name, text, code", [
    ("语法错", '{"format": "brushconverter.export-metadata", ', "bad_json"),
    ("重复键", '{"format":"brushconverter.export-metadata","format":"brushconverter.export-metadata","version":1,"tags":{"common":[]}}', "bad_json"),
    ("NaN", '{"format":"brushconverter.export-metadata","version":1,"metadata":{"author":NaN}}', "bad_json"),
    ("顶层数组", '[]', "bad_field"),
])
def test_bad_json_is_rejected(name, text, code, case_dir, monkeypatch, capsys):
    source, _abr, _presets, _state = _source(case_dir, monkeypatch)
    config = case_dir / (name + ".json")
    config.write_text(text, encoding="utf-8")
    out = case_dir / ("坏JSON" + name)
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 2
    captured = capsys.readouterr()
    assert not out.exists() and f"[{code}]" in captured.err
    _assert_safe_log(captured.err)


def test_invalid_utf8_and_unreadable_config(case_dir, monkeypatch, capsys):
    """无效 UTF-8 与读不到的配置都按配置错误退出 2，不自动猜编码。"""
    source, _abr, _presets, _state = _source(case_dir, monkeypatch)
    broken = case_dir / "坏编码178.json"
    broken.write_bytes(b'{"format":"brushconverter.export-metadata","version":1,'
                       b'"metadata":{"author":"\xff"}}')
    out = case_dir / "坏编码输出"
    assert _run_bundle(source, out, "--metadata-file", str(broken)) == 2
    captured = capsys.readouterr()
    assert not out.exists() and "[bad_json]" in captured.err
    assert str(case_dir) not in captured.err

    missing = case_dir / "不存在178.json"
    assert _run_bundle(source, out, "--metadata-file", str(missing)) == 2
    captured = capsys.readouterr()
    assert not out.exists() and "[unreadable]" in captured.err
    _assert_safe_log(captured.err)


def test_format_version_and_source_digest_checks(case_dir, monkeypatch, capsys):
    """format/version/source_digest 必须精确匹配，不回退默认。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    cases = [
        ("format", {**_payload(state, tags={"common": []}),
                    "format": "brushconverter.export-options"}, "bad_field"),
        ("version", {**_payload(state, tags={"common": []}), "version": 2},
         "version_mismatch"),
        ("digest", {**_payload(state, tags={"common": []}),
                    "source_digest": "sha256:" + "0" * 64}, "source_mismatch"),
        ("bad_digest", {**_payload(state, tags={"common": []}),
                        "source_digest": "不是摘要"}, "bad_field"),
    ]
    for name, payload, code in cases:
        config = _config(case_dir, payload, name=name + ".json")
        out = case_dir / ("校验" + name)
        assert _run_bundle(source, out, "--metadata-file", str(config)) == 2
        captured = capsys.readouterr()
        assert not out.exists() and f"[{code}]" in captured.err, name
        assert str(case_dir) not in captured.err
    _assert_safe_log(captured.err)


def test_explicit_source_digest_match_is_accepted(case_dir, monkeypatch, capsys):
    """填写正确 source_digest 不改变结果；它是绑定校验，不是新字段。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    config = _config(case_dir, _payload(state, tags={"common": ["共享 178"]}))
    out = case_dir / "摘要匹配输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 0
    assert verify_bundle(_bundle(out)).ok
    _assert_safe_log(capsys.readouterr().out)


def test_multiple_metadata_files_and_list_conflict(case_dir, monkeypatch, capsys):
    """多份元信息配置、与 --list-presets 混用都必须失败且不创建输出。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    one = _config(case_dir, _payload(state, tags={"common": []}), name="一178.json")
    two = _config(case_dir, _payload(state, tags={"common": []}), name="二178.json")
    out = case_dir / "重复配置输出"
    assert _run_bundle(source, out, "--metadata-file", str(one),
                       "--metadata-file", str(two)) == 2
    captured = capsys.readouterr()
    assert not out.exists() and "[bad_field]" in captured.err

    out = case_dir / "列表冲突输出"
    code = cli.main(["convert", str(source), "--list-presets", "-o", str(out),
                     "--metadata-file", str(one)])
    captured = capsys.readouterr()
    assert code == 2 and not out.exists()
    assert "brushconverter.preset-list" not in captured.out
    assert "[bad_field]" in captured.err
    _assert_safe_log(captured.err)


def test_kpp_only_with_metadata_is_an_explicit_error(case_dir, monkeypatch, capsys):
    """只给独立 kpp 却要求元信息/标签：明确失败，不静默丢弃、不创建目录。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    config = _config(case_dir, _payload(state, metadata={"author": "合成作者乙"}))
    out = case_dir / "只kpp不得写出"
    code = cli.main(["convert", str(source), "-o", str(out), "--no-bundle",
                     "--metadata-file", str(config)])
    captured = capsys.readouterr()
    assert code == 2 and not out.exists()
    assert "[metadata_requires_bundle]" in captured.err
    assert "只写入 .bundle" in captured.err
    _assert_safe_log(captured.err)


def test_kpp_only_without_metadata_writes_no_package_fields(case_dir, monkeypatch, capsys):
    """纯独立 kpp 不承载包信息/原生标签/追溯；这是格式范围，不是回读失败。"""
    source, _abr, _presets, _state = _source(case_dir, monkeypatch)
    out = case_dir / "纯kpp输出"
    code = cli.main(["convert", str(source), "-o", str(out), "--no-bundle"])
    captured = capsys.readouterr()
    assert code == 0
    assert not _bundles(out)
    kpp_files = sorted((out / "kpp").glob("*.kpp"))
    assert len(kpp_files) == 2
    for path in kpp_files:
        data = path.read_bytes()
        for marker in (b"dc:author", b"manifest:tags", b"brushconverter-trace",
                       b"brushconverter-package-version"):
            assert marker not in data, (path.name, marker)
    assert "包信息与标签回读" not in captured.out
    _assert_safe_log(captured.out)


def test_control_characters_rejected_without_echoing_values(case_dir, monkeypatch, capsys):
    """非法 XML 字符必须报错；作者/说明/包名/标签原文不能出现在日志里。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    payload = _payload(
        state,
        metadata={"name": "绝不回显包名甲", "description": "绝不回显说明乙",
                  "author": "绝不回显作者丙\x01"},
        tags={"common": ["绝不回显标签丁\x02"]},
    )
    config = _config(case_dir, payload)
    out = case_dir / "非法字符输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 2
    captured = capsys.readouterr()
    assert not out.exists()
    assert "[bad_field]" in captured.err and "控制字符" in captured.err
    for marker in ("绝不回显包名甲", "绝不回显说明乙", "绝不回显作者丙", "绝不回显标签丁",
                   str(case_dir)):
        assert marker not in captured.err and marker not in captured.out, marker
    _assert_safe_log(captured.err)


def test_same_config_reuses_and_changed_text_gets_new_identity(
        case_dir, monkeypatch, capsys):
    """同配置复用原包且 mtime 不变；只改说明或标签得到新身份，旧包不动。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    first_payload = _payload(
        state, metadata={"description": "说明甲178"}, tags={"common": ["共享甲178"]})
    first = _config(case_dir, first_payload, name="复用甲178.json")
    out = case_dir / "复用输出"
    assert _run_bundle(source, out, "--metadata-file", str(first)) == 0
    bundle = _bundle(out)
    original = bundle.read_bytes()
    mtime = bundle.stat().st_mtime_ns
    kpp_before = _xml_texts(bundle)[2]
    assert len(kpp_before) == 2

    capsys.readouterr()
    assert _run_bundle(source, out, "--metadata-file", str(first)) == 0
    text = capsys.readouterr().out
    assert "复用" in text
    assert _bundle(out).read_bytes() == original
    assert _bundle(out).stat().st_mtime_ns == mtime

    second = _config(case_dir, _payload(
        state, metadata={"description": "说明乙178"}, tags={"common": ["共享甲178"]}),
        name="复用乙178.json")
    assert _run_bundle(source, out, "--metadata-file", str(second)) == 0
    after_description = _bundles(out)
    assert len(after_description) == 2
    new_bundle = next(path for path in after_description if path != bundle)
    assert new_bundle.name != bundle.name
    assert bundle.read_bytes() == original and bundle.stat().st_mtime_ns == mtime
    assert verify_bundle(new_bundle).meta["dc:description"] == "说明乙178"

    third = _config(case_dir, _payload(
        state, metadata={"description": "说明甲178"}, tags={"common": ["共享乙178"]}),
        name="复用丙178.json")
    assert _run_bundle(source, out, "--metadata-file", str(third)) == 0
    after_tags = _bundles(out)
    assert len(after_tags) == 3
    assert bundle.read_bytes() == original and bundle.stat().st_mtime_ns == mtime
    hash8s = {verify_bundle(path).identity_hash8 for path in after_tags}
    assert len(hash8s) == 3
    for path in after_tags:
        assert _xml_texts(path)[2] == kpp_before
    _assert_safe_log(capsys.readouterr().out)


def test_tag_order_is_deterministic_and_case_duplicates_collapse(
        case_dir, monkeypatch, capsys):
    """同集合不同输入顺序得到同样的标签与字节；大小写重复按 175 规则合并。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    one = _config(case_dir, _payload(
        state, tags={"common": [" Ink ", "ink", "共享 178"], "source": []}),
        name="顺序甲178.json")
    two = _config(case_dir, _payload(
        state, tags={"common": ["共享 178", "ink", "Ink"], "source": []}),
        name="顺序乙178.json")
    out_one = case_dir / "顺序甲输出"
    out_two = case_dir / "顺序乙输出"
    assert _run_bundle(source, out_one, "--metadata-file", str(one)) == 0
    assert _run_bundle(source, out_two, "--metadata-file", str(two)) == 0
    first = _bundle(out_one)
    second = _bundle(out_two)
    assert first.read_bytes() == second.read_bytes()
    verified = verify_bundle(first)
    expected = normalize_tags([" Ink ", "ink", "共享 178"])
    assert expected == ("Ink", "共享 178")
    for values in verified.preset_tags.values():
        assert values == expected
    _assert_safe_log(capsys.readouterr().out)


def test_bom_config_and_blank_tags(case_dir, monkeypatch, capsys):
    """PowerShell 5.1 的 UTF-8 BOM 可读；空串/空白不是标签，不写成空标签。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    config = _config(case_dir, _payload(
        state, tags={"common": ["", "   ", "有效 178"], "source": []}), bom=True)
    out = case_dir / "BOM输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 0
    verified = verify_bundle(_bundle(out))
    assert verified.ok
    for values in verified.preset_tags.values():
        assert values == ("有效 178",)
    assert "逐支标签 2/2 支（2 个逐笔关联）" in capsys.readouterr().out


def test_metadata_values_are_stripped_and_tags_normalized(case_dir, monkeypatch, capsys):
    """包名/作者去首尾空白；标签内部连续空白折叠，均为 175 既有规则。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    config = _config(case_dir, _payload(
        state,
        metadata={"name": "  合成包名  ", "author": "   ", "description": "  说明  "},
        tags={"common": ["  共享   标签  "], "source": []}))
    out = case_dir / "归一输出"
    assert _run_bundle(source, out, "--metadata-file", str(config)) == 0
    verified = verify_bundle(_bundle(out))
    assert verified.meta["dc:title"] == "合成包名"
    assert verified.meta["dc:description"] == "说明"
    assert "dc:author" not in verified.meta  # 纯空白作者=未知，保持空
    for values in verified.preset_tags.values():
        assert values == ("共享 标签",)
    _assert_safe_log(capsys.readouterr().out)


def test_subset_export_keeps_stable_ids_and_source_ordinal(case_dir, monkeypatch, capsys):
    """只选一支时标签仍按稳定 ID 落到该支，追溯序号仍是原始序号。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    first = state.preset_ids[0]
    config = _config(case_dir, _payload(
        state, tags={"common": ["公共 178"],
                     "presets": {first: {"added": ["单支 178"]}}}))
    out = case_dir / "子集输出"
    assert _run_bundle(source, out, "--metadata-file", str(config),
                       "--preset-id", first) == 0
    verified = verify_bundle(_bundle(out))
    assert verified.preset_count == 1
    trace = verified.trace["presets"]
    assert len(trace) == 1 and trace[0]["id"] == first
    assert trace[0]["ordinal"] == 1
    expected = _expected(common=("公共 178",), source=(source.stem,),
                         added=("单支 178",))
    assert tuple(trace[0]["tags"]) == expected
    assert verified.preset_tags[trace[0]["kpp"]] == expected
    _assert_safe_log(capsys.readouterr().out)


def test_options_file_and_metadata_file_work_together(case_dir, monkeypatch, capsys):
    """逐支开关配置与元信息/标签配置同时使用：两条校验互不覆盖，稳定 ID 仍对应。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    first, second = state.preset_ids
    options = _config(case_dir, {
        "format": "brushconverter.export-options",
        "version": 1,
        "source_digest": state.source_digest,
        "presets": [{"id": first, "switches": {"rasterize_computed": False}}],
    }, name="逐支开关178.json")
    metadata = _config(case_dir, _payload(
        state, metadata={"author": "合成作者丁"},
        tags={"common": ["共享丁"], "presets": {first: {"added": ["单支丁"]}}}),
        name="元信息丁178.json")
    out = case_dir / "两文件输出"
    assert _run_bundle(source, out, "--options-file", str(options),
                       "--metadata-file", str(metadata)) == 0
    bundle = _bundle(out)
    verified = verify_bundle(bundle)
    assert verified.ok
    assert verified.meta["dc:author"] == "合成作者丁"
    trace = _trace_by_id(verified)
    assert tuple(trace[first]["tags"]) == _expected(
        common=("共享丁",), source=(source.stem,), added=("单支丁",))
    assert tuple(trace[second]["tags"]) == _expected(
        common=("共享丁",), source=(source.stem,))
    first_switch = trace[first]["switches"]["rasterize_computed"]
    second_switch = trace[second]["switches"]["rasterize_computed"]
    assert first_switch["enabled"] is False and first_switch["actual"] == "alternative"
    # 第二支是采样笔刷：位图化不适用，不因另一支配了开关而误判适用。
    assert second_switch["enabled"] is False and second_switch["actual"] == "not_applicable"
    _assert_safe_log(capsys.readouterr().out)


def test_convert_function_returns_v2_identity_and_readback(case_dir, monkeypatch, capsys):
    """直接调用 CLI convert() 与命令行走同一条元信息/身份路径。"""
    source, _abr, _presets, state = _source(case_dir, monkeypatch)
    config = _config(case_dir, _payload(
        state, metadata={"author": "合成作者丙"}, tags={"common": ["共享丙"]}))
    out = case_dir / "函数输出"
    result = cli.convert(str(source), str(out), make_bundle=True,
                         make_standalone=False, metadata_file=str(config))
    assert result.status == "committed"
    assert result.bundle_identity is not None and result.bundle_identity.scheme == "v2"
    assert result.verify is not None and result.verify.ok
    assert result.verify.meta["dc:author"] == "合成作者丙"
    assert result.bundle_path and Path(result.bundle_path).exists()
    _assert_safe_log(capsys.readouterr().out)