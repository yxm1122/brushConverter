"""任务 176 定向测试：包内追溯与新版资源包逻辑身份。

覆盖（全部为合成数据，不含个人信息、真实 ABR 或基准图）：

1. 追溯 JSON 的格式/版本/真实程序版本与「未发布」状态，不含时间/本机身份/绝对路径；
2. 新版逻辑身份覆盖元信息、包级标签、逐支标签、追溯（开关 actual/编辑代价）与
   .kpp 原始字节；只改任一项都必须得到不同身份与不同字节；
3. 同输入重复构建字节与身份确定，zip 头时间戳/输出路径不扰动身份；
4. 包内不存自身哈希（无自引用），convert 用展示名写 dc:title，产物文件名里的
   hash8 不会进入 meta/trace；
5. 旧包（无 trace）按 scheme=legacy 读回，kpp-only 哈希只用于旧口径；
   convert 对同名旧包绝不复用/覆盖，原文件内容与 mtime 都不变；
6. verify_bundle 检查追溯关联、声明 sha256、逐支标签与 reason 可复算；
   坏 trace/篡改一律失败；169 事务复用路径的 mtime 不变；
7. build/write 旧签名兼容：不传 trace 时仍生成可读包，并标记 legacy。

证据边界：不启动 Krita、不验证界面显示/标签筛选/重存丢字段；这些留任务187。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path
from xml.sax.saxutils import quoteattr, unescape

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from brush_converter import __version__  # noqa: E402
from brush_converter.convert import convert_presets  # noqa: E402
from brush_converter.export_metadata import (  # noqa: E402
    BundleMetadata,
    normalize_tags,
)
from brush_converter.export_options import (  # noqa: E402
    ACTUAL_ALTERNATIVE,
    ACTUAL_APPLIED,
    SWITCH_IDS,
    ExportOptionState,
    build_identities,
    source_digest_of_bytes,
    source_digest_of_file,
)
from brush_converter.kpp.bundle import (  # noqa: E402
    _STABLE_ID_PATTERN,
    _TRACE_ACTUAL_VALUES,
    _TRACE_SWITCH_IDS,
    IDENTITY_FORMAT,
    RELEASE_STATE_UNRELEASED,
    TRACE_FORMAT,
    TRACE_META_NAME,
    TRACE_VERSION,
    BundleTrace,
    PresetTrace,
    SwitchTrace,
    build_bundle,
    build_bundle_bytes,
    bundle_content_hash8,
    bundle_content_hash8_of_file,
    bundle_identity_of_file,
    verify_bundle,
    write_bundle,
)
from brush_converter.kpp.kpp_writer import build_kpp  # noqa: E402
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402

# ---------------------------------------------------------------- 合成工具


def _kpp(seed: int = 0) -> bytes:
    """结构合法的 .kpp（PNG 容器 + iTXt 预设 XML）；seed 只改字节内容。"""
    preview = np.zeros((4, 4, 4), dtype=np.uint8)
    preview[:, :, 0] = seed % 256
    return build_kpp(f"<preset seed='{seed}'/>", preview)


def _kpps(count: int = 2) -> list[tuple[str, bytes]]:
    return [(f"{index:03d}_预设{index}.kpp", _kpp(index)) for index in range(count)]


def _applied_switches(
    reason: str = "现行处理：合成测试用代价说明",
) -> tuple[SwitchTrace, ...]:
    """三项开关全部「已执行现行处理」的合法追溯记录。"""
    return tuple(
        SwitchTrace(
            switch_id=switch_id,
            applicable=True,
            enabled=True,
            actual=ACTUAL_APPLIED,
            limited=False,
            reason=f"{reason}（{switch_id}）",
        )
        for switch_id in SWITCH_IDS
    )


def _trace(
    kpps: list[tuple[str, bytes]],
    *,
    filename: str = "合成源.abr",
    stem: str = "合成源",
    digest: str | None = None,
    producer_version: str = "",
    switches: tuple[SwitchTrace, ...] | None = None,
    preset_id: str | None = None,
) -> BundleTrace:
    if digest is None:
        digest = "sha256:" + hashlib.sha256(b"source-bytes").hexdigest()
    presets = tuple(
        PresetTrace(
            kpp=fname,
            ordinal=index + 1,
            name=f"预设{index}",
            preset_id=preset_id if index == 0 else None,
            switches=switches or _applied_switches(),
        )
        for index, (fname, _blob) in enumerate(kpps)
    )
    return BundleTrace(
        presets=presets,
        source_filename=filename,
        source_stem=stem,
        source_digest=digest,
        producer_version=producer_version,
    )


def _build(
    kpps: list[tuple[str, bytes]] | None = None,
    *,
    metadata: BundleMetadata | None = None,
    tags: dict[str, tuple[str, ...]] | None = None,
    package_tags: tuple[str, ...] | None = None,
    trace: BundleTrace | None | str = "default",
    name: str = "示例展示名",
):
    """默认带一份合法追溯；显式 trace=None 时生成旧式（legacy）包。"""
    files = kpps or _kpps()
    if trace == "default":
        trace = _trace(files)
    return build_bundle(
        files,
        name,
        metadata=metadata,
        preset_tags=tags,
        package_tags=package_tags,
        trace=trace,
    )


def _entry_bytes(bundle: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        return zf.read(name)


def _entry_text(bundle: bytes, name: str) -> str:
    return _entry_bytes(bundle, name).decode("utf-8")


def _copy_info(info: zipfile.ZipInfo, date_time=None) -> zipfile.ZipInfo:
    copied = zipfile.ZipInfo(info.filename, date_time=date_time or info.date_time)
    copied.compress_type = info.compress_type
    copied.create_system = info.create_system
    copied.external_attr = info.external_attr
    return copied


def _rewrite_zip(
    source: bytes,
    *,
    replace_entries: dict[str, bytes] | None = None,
    date_time=None,
) -> bytes:
    """按原条目顺序重打包；可替换条目字节或统一改 zip 头时间戳。"""
    replace_entries = replace_entries or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as src:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with zipfile.ZipFile(buf, "w") as dst:
                for info in src.infolist():
                    payload = replace_entries.get(info.filename, src.read(info.filename))
                    dst.writestr(_copy_info(info, date_time=date_time), payload)
    return buf.getvalue()


#: quoteattr 在值含双引号且不含单引号时会改用单引号包裹（JSON 正是这种值）；
#: 所以追溯属性必须同时接受双引号/单引号两种写法。
_TRACE_ATTRIBUTE = re.compile(
    r'meta:name="brushconverter-trace" meta:value=(?:"([^"]*)"|\'([^\']*)\')')


def _trace_attribute_value(match: re.Match) -> str:
    raw = match.group(1) if match.group(1) is not None else match.group(2)
    return unescape(raw, {"&quot;": '"', "&apos;": "'"})


def _replace_trace_text(bundle: bytes, text: str) -> bytes:
    meta = _entry_text(bundle, "meta.xml")
    match = _TRACE_ATTRIBUTE.search(meta)
    assert match, "合成包内应有 brushconverter-trace"
    new_meta = (
        meta[: match.start()]
        + 'meta:name="brushconverter-trace" meta:value=' + quoteattr(text)
        + meta[match.end():]
    )
    return _rewrite_zip(bundle, replace_entries={"meta.xml": new_meta.encode("utf-8")})


def _mutate_trace(bundle: bytes, mutate) -> bytes:
    meta = _entry_text(bundle, "meta.xml")
    match = _TRACE_ATTRIBUTE.search(meta)
    assert match, "合成包内应有 brushconverter-trace"
    value = _trace_attribute_value(match)
    document = json.loads(value)
    mutate(document)
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _replace_trace_text(bundle, text)


def _add_manifest_tag(bundle: bytes, tag: str) -> bytes:
    """构造「只改 manifest 标签、不改 trace」的篡改包。"""
    manifest = _entry_text(bundle, "META-INF/manifest.xml")
    assert "<manifest:tags>" in manifest
    broken = manifest.replace(
        "<manifest:tags>",
        f"<manifest:tags><manifest:tag>{tag}</manifest:tag>",
        1,
    )
    return _rewrite_zip(
        bundle, replace_entries={"META-INF/manifest.xml": broken.encode("utf-8")})


def _verify_bytes(tmp_path: Path, data: bytes, name: str = "案例.bundle"):
    path = tmp_path / name
    path.write_bytes(data)
    return verify_bundle(path)


def _expect_failure(tmp_path: Path, data: bytes, fragment: str) -> None:
    verified = _verify_bytes(tmp_path, data, name="坏包.bundle")
    assert verified.ok is False
    joined = "；".join(verified.errors)
    assert fragment in joined, joined


class _FakeAbr:
    """convert_presets 只用 abr.name 作产物展示名；这里给最小替身。"""

    name = "样本笔刷"


def _presets(count: int = 2) -> list[BrushPreset]:
    return [
        BrushPreset(name=f"笔刷{index}",
                    tip_gray=np.full((16, 16), 200 + index, dtype=np.uint8))
        for index in range(count)
    ]


def _source_file(tmp_path: Path) -> Path:
    """合成「源文件」：只提供字节摘要，不参与解析（测试不依赖真实 ABR）。"""
    path = tmp_path / "合成源.abr"
    path.write_bytes(b"synthetic-abr-source-bytes-176")
    return path


def _convert_common(tmp_path: Path, presets, **kwargs):
    source = kwargs.pop("source_path", None) or _source_file(tmp_path)
    return convert_presets(
        _FakeAbr(), presets, str(tmp_path / "out"), make_standalone=False,
        source_path=str(source), **kwargs)


# ------------------------------------------------- 1. 契约常量与追溯读回


def test_trace_constants_match_task167_frozen_contract():
    """追溯里的开关/actual 与任务167 冻结值一致；稳定 ID 格式可被复用。"""
    assert _TRACE_SWITCH_IDS == SWITCH_IDS
    assert _TRACE_ACTUAL_VALUES == {
        "applied", "alternative", "not_applicable", "not_processed", "pending_alternative"}
    assert TRACE_FORMAT == "brushconverter.bundle-trace"
    assert TRACE_META_NAME == "brushconverter-trace"
    assert TRACE_VERSION == 1 and IDENTITY_FORMAT == 2
    identity = build_identities(
        [BrushPreset(name="甲", tip_gray=np.zeros((2, 2), dtype=np.uint8))],
        source_digest_of_bytes(b"x"),
    )[0]
    assert _STABLE_ID_PATTERN.fullmatch(identity.preset_id)


def test_build_writes_trace_and_identity_roundtrip(tmp_path):
    """追溯含真实版本/未发布状态/源摘要/逐支摘要与标签；回读身份与构建一致。"""
    files = _kpps()
    metadata = BundleMetadata(
        name="追溯包", description="合成说明", author="原作者甲",
        license="自定义许可", package_version="2026.11",
        source_filename="用户填写.abr",
    )
    build = _build(
        files, metadata=metadata,
        tags={files[0][0]: ("alpha", "勾线")}, package_tags=("包级",))
    path = tmp_path / "追溯.bundle"
    path.write_bytes(build.data)
    verified = verify_bundle(path)
    assert verified.ok, verified.errors
    assert verified.identity_scheme == "v2"
    assert verified.identity_sha256 == build.identity.sha256
    assert verified.identity_hash8 == build.identity.hash8

    trace = verified.trace
    assert trace is not None
    assert trace["format"] == TRACE_FORMAT
    assert trace["version"] == TRACE_VERSION
    assert trace["identity_format"] == IDENTITY_FORMAT
    assert trace["producer"]["name"] == "brushConverter"
    assert trace["producer"]["version"] == __version__
    assert trace["producer"]["release_state"] == RELEASE_STATE_UNRELEASED
    if __version__ != "1.6.0":
        # 产品版本未 bump 时不得谎写已发布 1.6.0。
        assert "1.6.0" not in build.trace_json

    assert trace["source"]["filename"] == "合成源.abr"
    assert trace["source"]["bound"] is True
    assert trace["source"]["digest"] == _trace(files).source_digest
    assert trace["source"]["preset_count"] == len(files)
    assert [item["kpp"] for item in trace["presets"]] == [name for name, _ in files]
    assert trace["presets"][0]["tags"] == ["alpha", "勾线"]
    assert trace["presets"][0]["kpp_sha256"] == hashlib.sha256(files[0][1]).hexdigest()
    assert verified.preset_tags[files[0][0]] == ("alpha", "勾线")


def test_trace_kpp_digests_tags_and_reasons_are_recomputable(tmp_path):
    """声明摘要、标签与 reason 文本都能从包内内容独立复算。"""
    files = _kpps()
    build = _build(files, tags={files[0][0]: ("勾线", "alpha")})
    verified = _verify_bytes(tmp_path, build.data)
    assert verified.ok, verified.errors
    trace = verified.trace
    for item, (fname, blob) in zip(trace["presets"], files):
        assert item["kpp_sha256"] == hashlib.sha256(blob).hexdigest()
        expected_tags = list(normalize_tags({files[0][0]: ("勾线", "alpha")}.get(fname, ())))
        assert item["tags"] == expected_tags
        for switch in item["switches"].values():
            key = switch["reason"]
            assert hashlib.sha256(trace["reason_texts"][key].encode("utf-8")).hexdigest() == key


def test_trace_has_no_time_host_identity_or_absolute_paths(tmp_path):
    """追溯不含当前时间/主机/用户名/绝对路径/包自身哈希。"""
    files = _kpps()
    build = _build(files)
    trace = json.loads(build.trace_json)
    forbidden_keys = {
        "time", "timestamp", "created_at", "exported_at", "host", "hostname",
        "user", "username", "machine", "computer", "path", "absolute_path",
        "self_hash", "bundle_hash", "package_hash", "identity_hash",
    }

    def walk(node, location="$"):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key.casefold() not in forbidden_keys, f"禁止字段：{location}.{key}"
                walk(value, f"{location}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{location}[{index}]")
        elif isinstance(node, str):
            assert not re.match(r"^[A-Za-z]:[\\/]", node), f"绝对路径：{location}"
            assert "\\\\" not in node, f"路径分隔符：{location}"
            assert "/Users/" not in node and "/home/" not in node, f"家目录：{location}"

    walk(trace)
    # 包内不写自身身份（无哈希自引用）。
    meta = _entry_text(build.data, "meta.xml")
    manifest = _entry_text(build.data, "META-INF/manifest.xml")
    assert build.identity.sha256 not in build.trace_json
    assert build.identity.sha256 not in meta
    assert build.identity.hash8 not in build.trace_json
    assert build.identity.hash8 not in manifest


# --------------------------------------------- 2. 逻辑身份对语义的敏感性


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "另一个包名"),
        ("description", "另一段说明"),
        ("author", "原作者乙"),
        ("license", "另一许可"),
        ("email", "another@example.invalid"),
        ("website", "https://example.invalid/other"),
        ("created", "25/11/2026"),
        ("updated", "26/11/2026"),
        ("package_version", "2026.12"),
        ("source_filename", "另一来源.abr"),
    ],
)
def test_metadata_field_change_changes_identity(field, value):
    """只改任意一个显式元信息字段（说明/许可/来源等）都必须得到新身份。"""
    base = BundleMetadata(name="基础包")
    changed_kwargs = {"name": "基础包", field: value}
    first = _build(metadata=base)
    second = _build(metadata=BundleMetadata(**changed_kwargs))
    assert second.identity.sha256 != first.identity.sha256
    assert second.data != first.data


def test_package_tags_and_preset_tags_change_identity_but_order_does_not():
    """包级标签/逐支标签进入身份；同集合不同顺序/重复不改变身份。"""
    files = _kpps()
    base = _build(files)
    package = _build(files, package_tags=("共享",))
    package_dup = _build(files, package_tags=("共享", "共享"))
    assert package.identity.sha256 != base.identity.sha256
    assert package.identity == package_dup.identity

    tagged = _build(files, tags={files[0][0]: ("alpha", "辅助")})
    tagged_reordered = _build(files, tags={files[0][0]: ("辅助", "alpha", "alpha")})
    assert tagged.identity.sha256 != base.identity.sha256
    assert tagged.identity == tagged_reordered.identity
    # 只给第二支标签也必须与第一支不同（标签不串支）
    other = _build(files, tags={files[1][0]: ("alpha", "辅助")})
    assert other.identity.sha256 != tagged.identity.sha256


def test_trace_switch_actual_cost_and_source_changes_identity():
    """追溯里的 actual/编辑代价/源文件名/源摘要/稳定 ID/序号都参与身份。"""
    files = _kpps()
    base = _build(files)
    switches = list(_applied_switches())
    alternative = list(switches)
    alternative[0] = replace(
        switches[0], actual=ACTUAL_ALTERNATIVE, limited=True,
        reason="替代路径代价：保留源纹理字节，Qt 重采样与 PS 不同")
    changed = _build(files, trace=_trace(files, switches=tuple(alternative)))
    assert changed.identity.sha256 != base.identity.sha256

    new_cost = list(switches)
    new_cost[0] = replace(switches[0], reason="另一个代价说明：字段可编辑但范围变化")
    cost_changed = _build(files, trace=_trace(files, switches=tuple(new_cost)))
    assert cost_changed.identity.sha256 != base.identity.sha256

    source_changed = _build(files, trace=_trace(files, filename="另一合成源.abr"))
    assert source_changed.identity.sha256 != base.identity.sha256
    digest_changed = _build(
        files, trace=_trace(files, digest="sha256:" + "b" * 64))
    assert digest_changed.identity.sha256 != base.identity.sha256


def test_trace_stable_id_ordinal_and_name_changes_identity():
    """稳定原始 ID/序号/显示名属于追溯语义；改动必须改变身份。"""
    files = _kpps()
    base_trace = _trace(files)
    base = _build(files, trace=base_trace)

    valid_id = "bc1-0123456789ab-0001-01234567"
    assert _STABLE_ID_PATTERN.fullmatch(valid_id)
    id_trace = replace(
        base_trace,
        presets=(replace(base_trace.presets[0], preset_id=valid_id),
                 base_trace.presets[1]),
    )
    id_changed = _build(files, trace=id_trace)
    assert id_changed.identity.sha256 != base.identity.sha256

    ordinal_trace = replace(
        base_trace,
        presets=(replace(base_trace.presets[0], ordinal=5),
                 base_trace.presets[1]),
    )
    assert _build(files, trace=ordinal_trace).identity.sha256 != base.identity.sha256

    name_trace = replace(
        base_trace,
        presets=(replace(base_trace.presets[0], name="改名后"),
                 base_trace.presets[1]),
    )
    assert _build(files, trace=name_trace).identity.sha256 != base.identity.sha256


def test_kpp_bytes_change_identity():
    files = _kpps()
    changed_files = [(files[0][0], _kpp(9)), files[1]]
    first = _build(files)
    second = _build(changed_files)
    assert second.identity.sha256 != first.identity.sha256
    assert second.data != first.data


def test_identity_is_deterministic_and_zip_header_independent(tmp_path):
    """同输入字节/身份确定；zip 头时间戳不同不影响身份，但整文件字节不同。"""
    first = _build()
    second = _build()
    assert first.identity == second.identity
    assert first.data == second.data

    rewritten = _rewrite_zip(first.data, date_time=(2026, 11, 25, 12, 34, 56))
    assert rewritten != first.data
    path_a = tmp_path / "a.bundle"
    path_b = tmp_path / "b.bundle"
    path_a.write_bytes(first.data)
    path_b.write_bytes(rewritten)
    read_a = bundle_identity_of_file(path_a)
    read_b = bundle_identity_of_file(path_b)
    assert read_a.scheme == read_b.scheme == "v2"
    assert read_a.sha256 == read_b.sha256
    assert verify_bundle(path_b).ok


def test_identity_is_independent_of_output_path(tmp_path):
    build = _build()
    one = tmp_path / "一.bundle"
    two = tmp_path / "二.bundle"
    one.write_bytes(build.data)
    two.write_bytes(build.data)
    assert bundle_identity_of_file(one).sha256 == bundle_identity_of_file(two).sha256


def test_title_comes_from_display_name_not_final_hashed_filename(tmp_path):
    """build_bundle 的展示名不含 hash：dc:title 稳定，文件名 hash 不进入内容。"""
    files = _kpps()
    first = _build(files, name="展示名甲")
    same = _build(files, name="展示名甲")
    other = _build(files, name="展示名乙")
    assert first.identity == same.identity
    assert first.identity.sha256 != other.identity.sha256
    assert _entry_text(first.data, "meta.xml").count("展示名甲") == 1


# ------------------------------------------------------ 3. 旧包兼容规则


def test_legacy_bundle_reads_with_explicit_scheme(tmp_path):
    """无 trace 的旧包：可读、可验证，但标记 legacy，且只给 kpp-only hash8。"""
    files = _kpps()
    legacy = build_bundle_bytes(files, "旧式包")
    path = tmp_path / "旧式.bundle"
    path.write_bytes(legacy)
    read = bundle_identity_of_file(path)
    assert read.scheme == "legacy"
    assert read.hash8 == bundle_content_hash8(files)
    assert read.trace is None
    assert bundle_content_hash8_of_file(path) == bundle_content_hash8(files)

    verified = verify_bundle(path)
    assert verified.ok, verified.errors
    assert verified.identity_scheme == "legacy"
    assert verified.trace is None
    assert verified.identity_hash8 == bundle_content_hash8(files)
    assert any("旧格式包" in note for note in verified.notes)


def test_v2_identity_differs_from_legacy_kpp_only_hash(tmp_path):
    """同一批 .kpp：旧口径 hash8 不得等于新版身份 hash8（两套 namespace）。"""
    files = _kpps()
    legacy_path = tmp_path / "旧式.bundle"
    legacy_path.write_bytes(build_bundle_bytes(files, "旧式"))
    build = _build(files)
    assert build.identity.hash8 != bundle_content_hash8(files)
    assert build.identity.hash8 != bundle_content_hash8_of_file(legacy_path)


def test_identity_reader_distinguishes_v2_legacy_and_broken(tmp_path):
    files = _kpps()
    v2_path = tmp_path / "新版.bundle"
    v2_path.write_bytes(_build(files).data)
    legacy_path = tmp_path / "旧版.bundle"
    legacy_path.write_bytes(build_bundle_bytes(files, "旧版"))
    broken_path = tmp_path / "坏.bundle"
    broken_path.write_bytes(b"not a zip")

    v2 = bundle_identity_of_file(v2_path)
    assert v2.scheme == "v2" and v2.reusable and v2.trace is not None
    assert bundle_content_hash8_of_file(v2_path) == v2.hash8

    legacy = bundle_identity_of_file(legacy_path)
    assert legacy.scheme == "legacy" and not legacy.reusable
    assert bundle_content_hash8_of_file(legacy_path) == legacy.hash8

    broken = bundle_identity_of_file(broken_path)
    assert broken.scheme == "invalid"
    assert bundle_content_hash8_of_file(broken_path) is None


def test_write_bundle_without_trace_remains_legacy(tmp_path):
    """旧签名 write_bundle 不传 trace 时仍可读，且显式标记 legacy。"""
    path = tmp_path / "无追溯.bundle"
    write_bundle(str(path), _kpps(), metadata=BundleMetadata(name="无追溯"))
    verified = verify_bundle(path)
    assert verified.ok, verified.errors
    assert verified.identity_scheme == "legacy" and verified.trace is None
    assert any("旧格式包" in note for note in verified.notes)


# ------------------------------------------ 4. 转换路径：只改语义就换新包


def test_convert_metadata_and_tags_create_new_packages_and_reuse_same(tmp_path):
    """只改说明/只加标签都得到新包；再次同输入复用且 mtime 不变。"""
    source = _source_file(tmp_path)
    presets = _presets()
    digest = source_digest_of_file(source)
    ids = build_identities(presets, digest)
    tags = {ids[0].preset_id: ("alpha", "勾线")}

    first = convert_presets(
        _FakeAbr(), presets, str(tmp_path / "out"), make_standalone=False,
        metadata=BundleMetadata(description="第一版说明"), source_path=str(source))
    assert first.status == "committed"
    assert first.bundle_identity is not None
    target1 = Path(first.bundle_path)
    bytes1 = target1.read_bytes()
    mtime1 = target1.stat().st_mtime_ns
    assert first.content_hash8 in target1.name
    verified1 = verify_bundle(target1)
    assert verified1.ok, verified1.errors
    assert verified1.meta["dc:description"] == "第一版说明"
    assert verified1.trace["source"]["filename"] == source.name
    assert verified1.trace["source"]["digest"] == digest
    assert [item["id"] for item in verified1.trace["presets"]] == [
        identity.preset_id for identity in ids]
    assert first.content_hash8 not in verified1.meta["dc:title"]
    assert first.content_hash8 not in json.dumps(verified1.trace, ensure_ascii=False)

    second = convert_presets(
        _FakeAbr(), presets, str(tmp_path / "out"), make_standalone=False,
        metadata=BundleMetadata(description="第二版说明"), source_path=str(source))
    target2 = Path(second.bundle_path)
    assert target2 != target1
    assert second.content_hash8 != first.content_hash8
    assert target1.read_bytes() == bytes1
    assert target1.stat().st_mtime_ns == mtime1
    assert verify_bundle(target2).meta["dc:description"] == "第二版说明"

    third = convert_presets(
        _FakeAbr(), presets, str(tmp_path / "out"), make_standalone=False,
        metadata=BundleMetadata(description="第一版说明"), source_path=str(source),
        preset_tags=tags)
    target3 = Path(third.bundle_path)
    assert target3 not in (target1, target2)
    verified3 = verify_bundle(target3)
    assert verified3.ok, verified3.errors
    kpp0 = verified3.trace["presets"][0]["kpp"]
    assert kpp0 == Path(first.kpp_files[0][0]).name
    assert verified3.preset_tags[kpp0] == ("alpha", "勾线")
    assert verified3.trace["presets"][0]["tags"] == ["alpha", "勾线"]
    assert target1.read_bytes() == bytes1

    again = convert_presets(
        _FakeAbr(), presets, str(tmp_path / "out"), make_standalone=False,
        metadata=BundleMetadata(description="第一版说明"), source_path=str(source))
    assert again.bundle_reused is True
    assert Path(again.bundle_path) == target1
    assert target1.read_bytes() == bytes1
    assert target1.stat().st_mtime_ns == mtime1


def test_convert_switch_only_change_changes_identity(tmp_path):
    """只关闭一个适用开关（预缩放）也必须得到不同身份与不同实际处理。"""
    source = _source_file(tmp_path)
    # scale=1200%（原生 Scale=12）既触发预缩放，又超出旧转换器曾截断的 10 上界，
    # 因此关闭预缩放后 limited=True（代价说明里会点名「待 187 实机核验」）。
    texture = TextureSettings(
        name="合成纹理", uuid="tex-176", scale=1200.0,
        image=np.arange(64, dtype=np.uint8).reshape(8, 8))
    preset = BrushPreset(
        name="纹理笔", tip_gray=np.full((16, 16), 200, dtype=np.uint8),
        texture=texture)
    state = ExportOptionState.create([preset], source_digest_of_bytes(source.read_bytes()))
    preset_id = state.preset_ids[0]
    assert state.applicability(preset_id, "prescale_texture").applicable

    first = _convert_common(tmp_path, [preset], option_state=state)
    state.set_override(preset_id, "prescale_texture", False)
    second = _convert_common(tmp_path, [preset], option_state=state)

    assert first.bundle_identity is not None and second.bundle_identity is not None
    assert first.bundle_identity.sha256 != second.bundle_identity.sha256
    assert Path(first.bundle_path) != Path(second.bundle_path)
    trace_first = verify_bundle(first.bundle_path).trace
    trace_second = verify_bundle(second.bundle_path).trace
    assert trace_first["presets"][0]["switches"]["prescale_texture"]["actual"] == ACTUAL_APPLIED
    switch = trace_second["presets"][0]["switches"]["prescale_texture"]
    assert switch["actual"] == ACTUAL_ALTERNATIVE
    assert switch["limited"] is True
    assert switch["reason"] in trace_second["reason_texts"]


def test_convert_never_reuses_same_named_legacy_bundle(tmp_path):
    """同名旧包绝不按 kpp-only 哈希当同包复用；原内容与 mtime 不变。"""
    _source_file(tmp_path)
    presets = _presets()
    first = _convert_common(tmp_path, presets)
    target = Path(first.bundle_path)
    legacy = build_bundle_bytes(first.kpp_files, _FakeAbr.name)
    target.write_bytes(legacy)
    os.utime(target, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    legacy_bytes = target.read_bytes()
    legacy_mtime = target.stat().st_mtime_ns
    assert bundle_identity_of_file(target).scheme == "legacy"

    second = _convert_common(tmp_path, presets)
    assert Path(second.bundle_path).name == f"{target.stem}_2{target.suffix}"
    assert target.read_bytes() == legacy_bytes
    assert target.stat().st_mtime_ns == legacy_mtime
    assert any("旧格式资源包" in warning for warning in second.warnings)
    assert verify_bundle(second.bundle_path).ok


def test_convert_source_digest_mismatch_stops_before_write(tmp_path):
    source_a = tmp_path / "a.abr"
    source_a.write_bytes(b"aaaa")
    source_b = tmp_path / "b.abr"
    source_b.write_bytes(b"bbbb")
    presets = _presets()
    state = ExportOptionState.create(presets, source_digest_of_bytes(b"aaaa"))
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="不一致"):
        convert_presets(
            _FakeAbr(), presets, str(out), make_standalone=False,
            option_state=state, source_path=str(source_b))
    assert not out.exists()


def test_convert_preset_tags_require_known_stable_ids(tmp_path):
    source = _source_file(tmp_path)
    presets = _presets()
    out = tmp_path / "out"
    unknown_id = "bc1-000000000000-0001-00000000"
    assert _STABLE_ID_PATTERN.fullmatch(unknown_id)
    with pytest.raises(ValueError, match="稳定 ID"):
        convert_presets(
            _FakeAbr(), presets, str(out), make_standalone=False,
            source_path=str(source), preset_tags={unknown_id: ("x",)})
    assert not out.exists()


def test_convert_metadata_without_bundle_is_explained(tmp_path):
    """没有 .bundle 时元信息/标签无处可写，必须明确提示而不是静默丢弃。"""
    presets = _presets()
    result = convert_presets(
        _FakeAbr(), presets, str(tmp_path / "out"), make_bundle=False,
        make_standalone=True, metadata=BundleMetadata(description="说明"))
    assert result.status == "committed"
    assert any("没有写出" in warning for warning in result.warnings)


# --------------------------------------------- 5. 回读校验的追溯负例


def test_verify_rejects_trace_tags_mismatch(tmp_path):
    files = _kpps()
    build = _build(files, tags={files[0][0]: ("alpha",)})
    _expect_failure(tmp_path, _add_manifest_tag(build.data, "偷偷加的标签"),
                    "追溯标签与 manifest 不一致")


def test_verify_rejects_declared_digest_mismatch(tmp_path):
    build = _build()

    def zero_digest(trace):
        trace["presets"][0]["kpp_sha256"] = "0" * 64

    _expect_failure(tmp_path, _mutate_trace(build.data, zero_digest),
                    "声明摘要与实际字节不一致")


def test_verify_rejects_missing_trace_entry(tmp_path):
    build = _build()

    def drop_preset(trace):
        trace["presets"].pop()

    _expect_failure(tmp_path, _mutate_trace(build.data, drop_preset),
                    "与包内预设数")
    _expect_failure(tmp_path, _mutate_trace(build.data, drop_preset),
                    "缺少逐支追溯")


def test_verify_rejects_unknown_reason_reference(tmp_path):
    build = _build()

    def drop_reason(trace):
        key = next(iter(trace["reason_texts"]))
        trace["reason_texts"].pop(key)

    _expect_failure(tmp_path, _mutate_trace(build.data, drop_reason),
                    "引用了不存在的说明")


def test_verify_rejects_bad_trace_json(tmp_path):
    build = _build()
    _expect_failure(tmp_path, _replace_trace_text(build.data, "不是 JSON"),
                    "追溯 JSON 解析失败")


def test_verify_rejects_unsupported_trace_identity_format(tmp_path):
    build = _build()

    def old_format(trace):
        trace["identity_format"] = 1

    broken = _mutate_trace(build.data, old_format)
    _expect_failure(tmp_path, broken, "identity_format 必须是 2")
    path = tmp_path / "旧格式声明.bundle"
    path.write_bytes(broken)
    assert bundle_identity_of_file(path).scheme == "invalid"
    assert bundle_content_hash8_of_file(path) is None


def test_verify_rejects_trace_source_with_absolute_path(tmp_path):
    build = _build()

    def absolute_source(trace):
        trace["source"]["filename"] = "C:\\秘密\\源.abr"

    _expect_failure(tmp_path, _mutate_trace(build.data, absolute_source),
                    "只能记文件名本身")


def test_verify_rejects_missing_edit_cost_reference(tmp_path):
    build = _build()

    def clear_reason(trace):
        switch = trace["presets"][0]["switches"][SWITCH_IDS[0]]
        switch["reason"] = None

    _expect_failure(tmp_path, _mutate_trace(build.data, clear_reason),
                    "必须是非空说明引用")


# ---------------------------------------------------------- 6. 兼容入口


def test_build_bundle_bytes_matches_build_bundle_and_legacy_switch(tmp_path):
    """build_bundle_bytes 与 build_bundle().data 一致；不传 trace 时生成 legacy 包。"""
    files = _kpps()
    build = _build(files)
    assert build_bundle_bytes(files, "示例展示名", trace=_trace(files)) == build.data

    legacy_path = tmp_path / "旧调用包.bundle"
    legacy_path.write_bytes(build_bundle_bytes(files, "旧调用包"))
    verified = verify_bundle(legacy_path)
    assert verified.ok, verified.errors
    assert verified.identity_scheme == "legacy"
    assert verified.trace is None
