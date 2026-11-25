"""任务 175 定向测试：资源包元信息与逐支标签写入/读回。

覆盖范围（全部为合成数据，不含个人信息、真实 ABR 或基准图）：

1. meta.xml 根是字面 meta:meta、命名空间可解析，字段只在显式输入时出现；
2. 包名/说明/作者/许可/邮箱/网站/日期/资源包版本/来源文件名的写出与读回一致；
3. 公共标签 + 来源标签 + 逐支追加 − 逐支移除 → 每支最终集合，互不串支；
4. 中文/空白/大小写重复标签归一、稳定排序、去空、XML 转义后原样读回；
5. 逐预设标签只写进 manifest 的 file-entry/tags/tag，包级标签不能替代；
6. ZIP 不变量：mimetype 首项且 stored、md5=条目原始字节、路径安全、无重复条目；
7. verify_bundle 能发现坏 meta 根、缺字段、坏命名空间、坏 md5、空/重复标签、
   未声明条目与路径穿越；读回字段/标签与写出前一致。

证据边界：这些测试只证明「本仓库写出的字节符合 174 契约的源码规则」。
真实 Krita 导入、界面显示、标签筛选与重启持久性不在这里，统一留任务 187。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from brush_converter.export_metadata import (  # noqa: E402
    BundleMetadata,
    PresetTagPlan,
    normalize_tags,
    resolve_preset_tags,
)
from brush_converter.kpp.bundle import (  # noqa: E402
    META_ROOT_LITERAL,
    MIMETYPE,
    NS_MANIFEST,
    NS_META,
    PREFIX,
    build_bundle_bytes,
    verify_bundle,
    write_bundle,
)
from brush_converter.kpp.kpp_writer import build_kpp  # noqa: E402

NS_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


# ---------------------------------------------------------------- 合成工具


def _kpp(seed: int = 0) -> bytes:
    """构造一个结构合法的 .kpp（PNG 容器 + iTXt 预设 XML），seed 只改字节内容。"""
    preview = np.zeros((4, 4, 4), dtype=np.uint8)
    preview[:, :, 0] = seed % 256
    return build_kpp(f"<preset seed='{seed}'/>", preview)


def _bundle(kpp_files=None, name="测试包", **kwargs) -> bytes:
    files = kpp_files or [("000_甲.kpp", _kpp(0)), ("001_乙.kpp", _kpp(1))]
    return build_bundle_bytes(files, name, **kwargs)


def _entry_bytes(bundle: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        return zf.read(name)


def _entry_text(bundle: bytes, name: str) -> str:
    return _entry_bytes(bundle, name).decode("utf-8")


def _manifest_records(bundle: bytes) -> list[dict]:
    """用命名空间感知解析读 manifest 条目（供断言属性/标签）。"""
    root = ET.fromstring(_entry_text(bundle, "META-INF/manifest.xml"))
    assert root.tag == f"{{{NS_MANIFEST}}}manifest"
    records = []
    for child in root:
        if child.tag != f"{{{NS_MANIFEST}}}file-entry":
            continue
        tags = [
            "".join(tag_node.itertext())
            for tags_node in child.findall(f"{{{NS_MANIFEST}}}tags")
            for tag_node in tags_node.findall(f"{{{NS_MANIFEST}}}tag")
        ]
        records.append(
            {
                "full_path": child.get(f"{{{NS_MANIFEST}}}full-path"),
                "media_type": child.get(f"{{{NS_MANIFEST}}}media-type"),
                "md5sum": child.get(f"{{{NS_MANIFEST}}}md5sum"),
                "tags": tags,
            }
        )
    return records


def _copy_info(info: zipfile.ZipInfo, name: str | None = None) -> zipfile.ZipInfo:
    """复制一个条目的时间戳/压缩方式/宿主标记，保持确定性写入风格。"""
    copied = zipfile.ZipInfo(name or info.filename, date_time=info.date_time)
    copied.compress_type = info.compress_type
    copied.create_system = info.create_system
    copied.external_attr = info.external_attr
    return copied


def _rewrite_zip(
    source: bytes,
    *,
    replace: dict[str, bytes] | None = None,
    drop: tuple[str, ...] = (),
    extra: tuple[tuple[str, bytes], ...] = (),
) -> bytes:
    """重打包 zip：替换/删除条目，再追加 extra（可用来刻意造重复条目）。"""
    replace = replace or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as src:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # 重复条目是测试刻意构造的
            with zipfile.ZipFile(buf, "w") as dst:
                for info in src.infolist():
                    if info.filename in drop:
                        continue
                    payload = replace.get(info.filename, src.read(info.filename))
                    dst.writestr(_copy_info(info), payload)
                for name, payload in extra:
                    dst.writestr(_copy_info(src.infolist()[0], name), payload)
    return buf.getvalue()


def _verify_bytes(tmp_path: Path, data: bytes):
    path = tmp_path / "case.bundle"
    path.write_bytes(data)
    return verify_bundle(path)


# ---------------------------------------------------------------- 1. meta 根


def test_meta_root_is_literal_meta_meta(tmp_path):
    """根必须是字面 meta:meta，不能再包 office:document-meta（174 契约 5.1）。"""
    bundle = _bundle(metadata=BundleMetadata(name="楷体笔刷包", description="合成测试包"))
    text = _entry_text(bundle, "meta.xml")
    assert "office:document-meta" not in text, "旧的 office 外层会让 Krita 拒绝 meta"
    assert f"<{META_ROOT_LITERAL} " in text
    assert 'xmlns:meta=' in text and 'xmlns:dc=' in text
    assert ET.fromstring(text).tag == f"{{{NS_META}}}meta"
    verified = _verify_bytes(tmp_path, bundle)
    assert verified.ok, verified.errors
    assert verified.meta["dc:title"] == "楷体笔刷包"
    assert verified.meta["meta:bundle-version"] == "1"


# ------------------------------------------------------- 2. 包级字段显式输入


def test_metadata_fields_are_written_and_read_back(tmp_path):
    """显式输入的作者/许可/邮箱/网站/日期/包版本/来源文件名逐项写读一致。"""
    metadata = BundleMetadata(
        name="我的笔刷包",
        description="合成说明 & <转义>",
        author="原作者甲",
        license="CC-BY-4.0",
        email="brush@example.invalid",
        website="https://example.invalid/brushes",
        created="25/11/2026",
        updated="26/11/2026",
        package_version="2026.11",
        source_filename="合成样本.abr",
    )
    verified = _verify_bytes(tmp_path, _bundle(metadata=metadata))
    assert verified.ok, verified.errors
    assert verified.meta["dc:title"] == "我的笔刷包"
    assert verified.meta["dc:description"] == "合成说明 & <转义>"
    assert verified.meta["dc:author"] == "原作者甲"
    assert verified.meta["dc:creator"] == "原作者甲"
    assert verified.meta["meta:initial-creator"] == "原作者甲"
    assert verified.meta["meta:license"] == "CC-BY-4.0"
    assert verified.meta["meta:email"] == "brush@example.invalid"
    assert verified.meta["meta:website"] == "https://example.invalid/brushes"
    assert verified.meta["meta:creation-date"] == "25/11/2026"
    assert verified.meta["meta:dc-date"] == "26/11/2026"
    assert verified.meta["meta:brushconverter-package-version"] == "2026.11"
    assert verified.meta["meta:brushconverter-source"] == "合成样本.abr"


def test_empty_optional_fields_stay_empty_no_mit_no_paths(tmp_path):
    """未知归属留空：不写作者/许可/邮箱/网站/日期，也不套项目 MIT 或本机路径。"""
    verified = _verify_bytes(tmp_path, _bundle(name="只有包名"))
    assert verified.ok, verified.errors
    text = _entry_text(tmp_path.joinpath("case.bundle").read_bytes(), "meta.xml")
    assert verified.meta["dc:title"] == "只有包名"
    assert verified.meta["dc:description"] == "Converted from Photoshop ABR"
    for key in ("dc:author", "dc:creator", "meta:initial-creator", "meta:license",
                "meta:email", "meta:website", "meta:creation-date", "meta:dc-date",
                "meta:brushconverter-package-version", "meta:brushconverter-source"):
        assert key not in verified.meta, f"{key} 在不该出现时出现了"
    assert "MIT" not in text
    assert "C:" not in text and "\\\\" not in text and "/Users/" not in text


@pytest.mark.parametrize("bad_source", ["C:/素材/样本.abr", "C:\\\\素材\\\\样本.abr", "dir/样本.abr", ".."])
def test_source_filename_rejects_paths(bad_source):
    """来源文件名只记文件名：带盘符/目录/.. 直接拒绝，避免把本机路径写进包。"""
    with pytest.raises(ValueError, match="来源文件名"):
        BundleMetadata(source_filename=bad_source)


@pytest.mark.parametrize("bad_date", ["2026-11-25", "25/11/26", "32/13/2026", "2026/11/25"])
def test_date_format_is_strict(bad_date):
    """日期只接受 dd/MM/yyyy（与目标源码一致），其它写法不静默猜测。"""
    with pytest.raises(ValueError, match="日期"):
        BundleMetadata(created=bad_date)


def test_metadata_rejects_illegal_xml_chars():
    """XML 1.0 禁止的控制字符直接报错，不悄悄删掉使用者输入。"""
    with pytest.raises(ValueError, match="U\\+0001"):
        BundleMetadata(name="坏\x01名字")


# ------------------------------------------------------------ 3. 标签集合


def test_resolve_common_source_added_removed():
    """最终集合 = 公共 ∪ 来源 ∪ 追加 − 移除，且稳定排序。"""
    result = resolve_preset_tags(
        common=("alpha", "shared"),
        source=("beta", "shared"),
        added=("勾线",),
        removed=("beta",),
    )
    assert result == ("alpha", "shared", "勾线")
    # 移除不存在的标签是无操作，方便界面反复编辑
    assert resolve_preset_tags(common=("x",), removed=("不存在",)) == ("x",)


def test_preset_tag_plan_matches_resolve():
    """GUI/CLI 预览与写出走同一个 final()，不允许两套算法。"""
    plan = PresetTagPlan(common=("alpha",), source=(" beta ",), added=("勾线",),
                         removed=("ALPHA",))
    assert plan.final() == resolve_preset_tags(
        common=("alpha",), source=(" beta ",), added=("勾线",), removed=("ALPHA",))
    assert plan.final() == ("beta", "勾线")


def test_tag_normalization_chinese_whitespace_and_case():
    """重复中文标签归一；空白/大小写变体合并；去空；排序稳定。"""
    assert normalize_tags([" 勾线 ", "勾线", "勾线　", "", "   "]) == ("勾线",)
    assert normalize_tags(["ink", "Ink", "iNk"]) == ("Ink",)
    assert normalize_tags(["beta", "alpha"]) == ("alpha", "beta")
    assert normalize_tags(["a\nb", "a  b"]) == ("a b",)
    assert normalize_tags(None) == ()
    with pytest.raises(TypeError):
        normalize_tags("alpha")  # 裸字符串不能被逐字符拆开


def test_removal_only_affects_its_own_preset(tmp_path):
    """公共标签在 A 支被移除，B 支仍保留；标签不能串到别的笔刷。"""
    common = ("alpha", "shared")
    tags_a = resolve_preset_tags(common=common, added=("A专用",), removed=("shared",))
    tags_b = resolve_preset_tags(common=common, added=("B专用",))
    bundle = _bundle(
        preset_tags={"000_甲.kpp": tags_a, "001_乙.kpp": tags_b},
    )
    verified = _verify_bytes(tmp_path, bundle)
    assert verified.ok, verified.errors
    assert verified.preset_tags["000_甲.kpp"] == tags_a
    assert verified.preset_tags["001_乙.kpp"] == tags_b
    assert "shared" not in verified.preset_tags["000_甲.kpp"]
    assert "shared" in verified.preset_tags["001_乙.kpp"]
    assert "B专用" not in verified.preset_tags["000_甲.kpp"]


def test_tags_with_special_characters_are_escaped_and_read_back(tmp_path):
    """标签/文件名里的 & < > 与中文在 XML 里转义，读回仍是原值。"""
    tag = 'a&b<c>"d'
    chinese = "描边🎨"
    fname = "000_甲&乙<丙>.kpp"
    bundle = _bundle(
        [ (fname, _kpp(7)) ],
        preset_tags={fname: (tag, chinese)},
    )
    manifest_text = _entry_text(bundle, "META-INF/manifest.xml")
    assert "&amp;" in manifest_text and "&lt;" in manifest_text and "&gt;" in manifest_text
    records = [r for r in _manifest_records(bundle) if r["media_type"] == "paintoppresets"]
    assert [r["full_path"] for r in records] == [PREFIX + fname]
    assert records[0]["tags"] == [tag, chinese]
    verified = _verify_bytes(tmp_path, bundle)
    assert verified.ok, verified.errors
    assert verified.preset_tags[fname] == (tag, chinese)


def test_tag_order_does_not_change_bytes():
    """同一组标签换输入顺序，产物字节必须完全相同（确定性）。"""
    kpps = [("000_甲.kpp", _kpp(0))]
    first = _bundle(kpps, preset_tags={"000_甲.kpp": ("beta", "alpha", "alpha")})
    second = _bundle(kpps, preset_tags={"000_甲.kpp": ("alpha", "beta")})
    assert first == second


def test_illegal_xml_char_in_tag_rejected():
    with pytest.raises(ValueError, match="U\\+0001"):
        normalize_tags(["坏\x01标签"])


# ------------------------------------------------- 4. 包级标签不能替代逐笔


def test_package_tags_are_compatibility_only(tmp_path):
    """包级标签显式传入才写；逐预设标签为空时它们不会变成逐笔关联。"""
    bundle = _bundle(package_tags=("共享", "共享", "b"))
    verified = _verify_bytes(tmp_path, bundle)
    assert verified.ok, verified.errors
    assert verified.package_tags == ("b", "共享")
    assert verified.preset_tags == {"000_甲.kpp": (), "001_乙.kpp": ()}
    meta_text = _entry_text(bundle, "meta.xml")
    assert 'meta:name="tag"' in meta_text
    # 对照：同样包级标签 + 逐笔标签时，逐笔标签独立存在
    per_preset = _bundle(preset_tags={"000_甲.kpp": ("共享",)})
    assert _manifest_records(per_preset)[1]["tags"] == ["共享"]


# ------------------------------------------------------ 5. ZIP 不变量


def test_zip_invariants_mimetype_first_stored_md5_raw_bytes(tmp_path):
    """mimetype 首项/stored/精确内容；md5 必须等于条目的原始字节。"""
    files = [("000_甲.kpp", _kpp(0)), ("001_乙.kpp", _kpp(1))]
    bundle = _bundle(files)
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype").decode("utf-8") == MIMETYPE
        names = [info.filename for info in infos]
        assert names.count("mimetype") == 1 and names.count("meta.xml") == 1
        assert all(name == "mimetype" or "/" in name or name == "meta.xml"
                   for name in names)
    records = [r for r in _manifest_records(bundle) if r["media_type"] == "paintoppresets"]
    assert len(records) == 2
    for record, (fname, payload) in zip(records, files):
        assert record["full_path"] == PREFIX + fname
        assert record["md5sum"] == hashlib.md5(payload).hexdigest()


@pytest.mark.parametrize(
    "bad_name",
    ["../evil.kpp", "a/b.kpp", "a\\\\b.kpp", "C:evil.kpp", "", " x.kpp", ".", ".."],
)
def test_build_rejects_unsafe_preset_names(bad_name):
    """预设文件名不能带路径/盘符/首尾空白，防止 zip 路径穿越。"""
    with pytest.raises((ValueError, TypeError)):
        build_bundle_bytes([(bad_name, _kpp(0))], "包名")


def test_build_rejects_duplicate_and_unknown_names():
    """重复预设名、未知 preset_tags 键都要明确报错，不静默丢。"""
    with pytest.raises(ValueError, match="重复"):
        build_bundle_bytes([("a.kpp", _kpp(0)), ("a.kpp", _kpp(1))], "包名")
    with pytest.raises(ValueError, match="不在本包内"):
        build_bundle_bytes([("a.kpp", _kpp(0))], "包名",
                           preset_tags={"b.kpp": ("x",)})
    with pytest.raises(ValueError, match="至少需要一个预设"):
        build_bundle_bytes([], "包名")
    with pytest.raises(TypeError, match="bytes"):
        build_bundle_bytes([("a.kpp", "不是字节")], "包名")


# ------------------------------------------------------ 6. verify_bundle


def test_write_then_verify_roundtrip(tmp_path):
    """落盘后回读：包名/字段/逐支标签/包级标签与写出前一致。"""
    metadata = BundleMetadata(name="回读包", author="原作者乙", license="自定义许可",
                              package_version="1.0", source_filename="合成.abr")
    tags = {"000_甲.kpp": ("alpha", "勾线"), "001_乙.kpp": ()}
    path = tmp_path / "回读.bundle"
    write_bundle(str(path), [("000_甲.kpp", _kpp(0)), ("001_乙.kpp", _kpp(1))],
                 metadata=metadata, preset_tags=tags, package_tags=("包级",))
    verified = verify_bundle(path)
    assert verified.ok, verified.errors
    assert verified.preset_count == 2
    assert verified.meta["dc:title"] == "回读包"
    assert verified.meta["dc:author"] == "原作者乙"
    assert verified.meta["meta:license"] == "自定义许可"
    assert verified.meta["meta:brushconverter-package-version"] == "1.0"
    assert verified.meta["meta:brushconverter-source"] == "合成.abr"
    assert verified.preset_tags == {"000_甲.kpp": ("alpha", "勾线"), "001_乙.kpp": ()}
    assert verified.package_tags == ("包级",)
    assert path.stat().st_size == verified.size
    assert verified.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_backward_compatible_signature_without_new_options(tmp_path):
    """旧调用方式（只有 kpp_files + 包名）仍能生成并通过回读校验。"""
    bundle = build_bundle_bytes([("000_甲.kpp", _kpp(0))], "旧调用包")
    verified = _verify_bytes(tmp_path, bundle)
    assert verified.ok, verified.errors
    assert verified.meta["dc:title"] == "旧调用包"
    assert verified.preset_tags == {"000_甲.kpp": ()}


def _expect_failure(tmp_path, data: bytes, fragment: str) -> None:
    """坏包必须失败，且失败原因里点名预期问题（不能只看 ok=False）。"""
    verified = _verify_bytes(tmp_path, data)
    assert verified.ok is False
    joined = "；".join(verified.errors)
    assert fragment in joined, joined


def test_verify_rejects_office_root_meta(tmp_path):
    """旧 writer 的 office 外层根会被拒绝（175 修的就是这个问题）。"""
    bundle = _bundle(metadata=BundleMetadata(name="包里名"))
    meta_text = _entry_text(bundle, "meta.xml")
    body = meta_text.split("\n", 1)[1]
    bad = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<office:document-meta xmlns:office="{NS_OFFICE}">\n'
           + body + "\n</office:document-meta>")
    broken = _rewrite_zip(bundle, replace={"meta.xml": bad.encode("utf-8")})
    _expect_failure(tmp_path, broken, "meta.xml 根元素必须是字面 meta:meta")


def test_verify_rejects_missing_meta(tmp_path):
    _expect_failure(tmp_path, _rewrite_zip(_bundle(), drop=("meta.xml",)),
                    "缺少 meta.xml")


def test_verify_rejects_missing_bundle_version(tmp_path):
    bundle = _bundle()
    meta_text = _entry_text(bundle, "meta.xml")
    bad = re.sub(r"<meta:bundle-version>1</meta:bundle-version>", "", meta_text, count=1)
    broken = _rewrite_zip(bundle, replace={"meta.xml": bad.encode("utf-8")})
    _expect_failure(tmp_path, broken, "缺少 meta:bundle-version")


def test_verify_rejects_duplicate_meta_field(tmp_path):
    bundle = _bundle(metadata=BundleMetadata(name="包", license="许可甲"))
    meta_text = _entry_text(bundle, "meta.xml")
    bad = meta_text.replace("<meta:license>许可甲</meta:license>",
                            "<meta:license>许可甲</meta:license>"
                            "<meta:license>许可乙</meta:license>", 1)
    broken = _rewrite_zip(bundle, replace={"meta.xml": bad.encode("utf-8")})
    _expect_failure(tmp_path, broken, "meta 字段重复：meta:license")


def test_verify_rejects_wrong_manifest_namespace(tmp_path):
    bundle = _bundle()
    manifest_text = _entry_text(bundle, "META-INF/manifest.xml")
    bad = manifest_text.replace(NS_MANIFEST, NS_MANIFEST + "-9.9", 1)
    broken = _rewrite_zip(bundle, replace={"META-INF/manifest.xml": bad.encode("utf-8")})
    _expect_failure(tmp_path, broken, "manifest 解析失败")


def test_verify_rejects_bad_md5(tmp_path):
    bundle = _bundle()
    manifest_text = _entry_text(bundle, "META-INF/manifest.xml")
    bad = re.sub(r'manifest:md5sum="[0-9a-f]{32}"',
                 'manifest:md5sum="' + "0" * 32 + '"', manifest_text, count=1)
    broken = _rewrite_zip(bundle, replace={"META-INF/manifest.xml": bad.encode("utf-8")})
    _expect_failure(tmp_path, broken, "条目 md5 与 manifest 不一致")


def test_verify_rejects_missing_declared_entry(tmp_path):
    bundle = _bundle()
    broken = _rewrite_zip(bundle, drop=(PREFIX + "000_甲.kpp",))
    _expect_failure(tmp_path, broken, "manifest 声明了不存在的条目")


def test_verify_rejects_undeclared_preset_entry(tmp_path):
    bundle = _bundle()
    extra = (PREFIX + "999_私下.kpp", _kpp(9))
    broken = _rewrite_zip(bundle, extra=(extra,))
    _expect_failure(tmp_path, broken, "zip 预设未在 manifest 声明")


def test_verify_rejects_blank_and_duplicate_tags(tmp_path):
    bundle = _bundle(preset_tags={"000_甲.kpp": ("alpha",)})
    manifest_text = _entry_text(bundle, "META-INF/manifest.xml")
    blank = manifest_text.replace("<manifest:tags>",
                                  "<manifest:tags><manifest:tag>   </manifest:tag>", 1)
    _expect_failure(tmp_path,
                    _rewrite_zip(bundle, replace={"META-INF/manifest.xml": blank.encode()}),
                    "含空标签")
    duplicated = manifest_text.replace("<manifest:tags>",
                                       "<manifest:tags><manifest:tag>alpha</manifest:tag>", 1)
    _expect_failure(tmp_path,
                    _rewrite_zip(bundle, replace={
                        "META-INF/manifest.xml": duplicated.encode()}),
                    "标签重复")


def test_verify_rejects_duplicate_zip_entry(tmp_path):
    bundle = _bundle()
    first_info = None
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        first_info = _copy_info(zf.infolist()[0], PREFIX + "000_甲.kpp")
    broken = _rewrite_zip(bundle, extra=((PREFIX + "000_甲.kpp", _kpp(0)),))
    assert first_info is not None
    _expect_failure(tmp_path, broken, "zip 条目名重复")


def test_verify_rejects_unsafe_zip_path(tmp_path):
    broken = _rewrite_zip(_bundle(), extra=(("../evil.kpp", b"x"),))
    _expect_failure(tmp_path, broken, "zip 条目名不安全")


def test_verify_rejects_missing_mimetype(tmp_path):
    broken = _rewrite_zip(_bundle(), drop=("mimetype",))
    _expect_failure(tmp_path, broken, "首条目不是 mimetype")


# ----------------------------------------------------------- 7. 确定性与探针


def test_bundle_bytes_are_deterministic_but_metadata_sensitive():
    """同输入字节相同；只改显式作者 → 字节不同（说明元信息确实写进包）。"""
    kpps = [("000_甲.kpp", _kpp(0))]
    first = _bundle(kpps, metadata=BundleMetadata(name="包", author="甲"))
    second = _bundle(kpps, metadata=BundleMetadata(name="包", author="甲"))
    changed = _bundle(kpps, metadata=BundleMetadata(name="包", author="乙"))
    assert first == second
    assert first != changed


def test_independent_contract_probe_accepts_our_bundle(tmp_path):
    """用 174 的源码规则模拟探针独立复核（不是 Krita 实机，留 187）。"""
    tool = REPO_ROOT / "tools" / "probe_bundle_contract.py"
    if not tool.exists():
        pytest.skip("缺少 174 探针 tools/probe_bundle_contract.py（skip 不等于通过）")
    path = tmp_path / "探针样本.bundle"
    write_bundle(str(path), [("000_甲.kpp", _kpp(0)), ("001_乙.kpp", _kpp(1))],
                 metadata=BundleMetadata(name="探针包", author="原作者丙",
                                         license="自定义许可"),
                 preset_tags={"000_甲.kpp": ("alpha", "勾线")})
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", str(tool), "--probe", str(path), "--json"],
        cwd=str(REPO_ROOT), capture_output=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    report = json.loads(proc.stdout.decode("utf-8"))
    assert report["would_load"] is True, report["would_load_reasons"]
    assert report["meta"]["accepted"] is True
    assert report["meta"]["root"] == META_ROOT_LITERAL
    assert report["brushconverter_invariants_ok"] is True, report["invariant_failures"]
    tags = {Path(preset["full_path"]).name: preset["tags"] for preset in report["presets"]}
    assert tags == {"000_甲.kpp": ["alpha", "勾线"], "001_乙.kpp": []}
