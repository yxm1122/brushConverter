"""179 原生关联回归：目标源码回读 + 手列期望，而不是生产写入器自证。

读取口径锁定 174 合同的 Krita 5.3.3 / git 858d352：
- KoResourceBundleManifest.cpp:81–126、171–177：命名空间、首个 tags、同键后项覆盖；
- KoResourceBundle.cpp:68–112、313–364：缺资源剔除后继续、meta 字面根、包标签分离；
- KisBundleStorage.cpp:42–56：逐文件标签反转成 defaultResources 筛选集合。

下面的 minidom/Pillow 读者不调用生产 XML/标签/身份回读函数；生产 verify_bundle
只作为第二条交叉检查。源码模拟与项目严格合同分开，均不能替代 187 实机导入、
筛选、重启或用户资源库验收。所有运行期文件仅写 .tmp/179/，不读写真实用户库。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import struct
import sys
import tempfile
import zipfile
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from uuid import uuid4
from xml.dom import Node, minidom

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.convert import convert_presets  # noqa: E402
from brush_converter.export_metadata import BundleMetadata, PresetTagPlan  # noqa: E402
from brush_converter.export_options import build_identities  # noqa: E402
from brush_converter.kpp.bundle import verify_bundle  # noqa: E402
from brush_converter.mapping import BrushPreset  # noqa: E402

TASK = ROOT / ".tmp" / "179"
FIXTURE = ROOT / "tests" / "fixtures" / "bundle_interop" / "cases.json"
# 常量独立抄自目标合同，不导入 writer 的命名空间或内部校验函数。
MNS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
MIME = "application/x-krita-resourcebundle"
MANIFEST = "META-INF/manifest.xml"
PREFIX = "paintoppresets/"
pytestmark = pytest.mark.verify


def _config():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _children(node, local=None):
    return [child for child in node.childNodes
            if child.nodeType == Node.ELEMENT_NODE
            and (local is None or child.localName == local)]


def _text(node):
    """QDomElement::text() 拼接后代文本；包括 CDATA，不只取首段 text。"""
    return "".join(child.data if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE)
                   else _text(child) if child.nodeType == Node.ELEMENT_NODE else ""
                   for child in node.childNodes)


@dataclass
class NativeView:
    """load 仅模拟源码容器加载；不表示资源可绘制或已通过项目更严格的合同。"""

    would_load: bool | None = False
    reason: str = ""
    fields: dict = field(default_factory=dict)
    package_tags: set = field(default_factory=set)
    declared: list = field(default_factory=list)
    resources: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    root_entry: bool = False
    duplicates: list = field(default_factory=list)

    def filters(self):
        """按原生 BundleTagIterator 反转，值是资源文件名，不是笔刷显示名。"""
        result = {}
        for path, record in self.resources.items():
            if record["type"] == "paintoppresets":
                for tag in record["tags"]:
                    result.setdefault(tag, set()).add(PurePosixPath(path).name)
        return result


def _native_read(data):
    """独立源码读者。重复 ZIP 名的后端选择未证实，诚实返回未知而非猜最后项。"""
    view = NativeView()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            view.duplicates = [name for name, n in Counter(names).items() if n > 1]
            if view.duplicates:
                view.would_load = None
                view.reason = "重复 ZIP 的目标后端选择未验证"
                return view
            if MANIFEST not in names or "meta.xml" not in names:
                view.reason = "缺必需 manifest/meta"
                return view
            manifest = minidom.parseString(archive.read(MANIFEST)).documentElement
            if manifest.localName != "manifest" or manifest.namespaceURI != MNS:
                view.reason = "manifest 根命名空间不符"
                return view
            effective = {}
            for node in _children(manifest, "file-entry"):
                if node.namespaceURI != MNS:
                    view.skipped.append("file-entry 命名空间不符")
                    continue
                attrs = ("full-path", "media-type", "md5sum")
                path, kind, md5 = (node.getAttributeNS(MNS, attr) for attr in attrs)
                if path == "/" and kind == MIME:
                    view.root_entry = True
                    continue
                if not all(node.hasAttributeNS(MNS, attr) for attr in attrs):
                    view.skipped.append("file-entry 缺必需属性")
                    continue
                blocks = _children(node, "tags")
                tags = [_text(tag) for tag in _children(blocks[0], "tag")] if blocks else []
                record = dict(path=path, type=kind, md5=md5, tags=tags, blocks=len(blocks))
                view.declared.append(record)
                # 原生 m_resources[type].insert(path, ref)：后项覆盖，不做标签并集。
                effective[(kind, path)] = record
            for (_kind, path), record in effective.items():
                if path not in names:
                    view.removed.append(path)
                elif record["type"] == "paintoppresets":
                    # 只建立本任务笔刷类型的视图；其它类型不按同路径覆盖笔刷。
                    view.resources[path] = record
            meta = minidom.parseString(archive.read("meta.xml")).documentElement
            if meta.tagName != "meta:meta":
                view.reason = "meta 根必须是字面 meta:meta"
                return view
            for node in _children(meta):
                name, value = node.tagName, _text(node)
                if name == "meta:meta-userdefined":
                    name, value = node.getAttribute("meta:name"), node.getAttribute("meta:value")
                    if name == "tag":
                        view.package_tags.add(value)
                        continue
                    view.fields[name] = value
                    name = "meta:" + name
                elif name == "cd:creator":
                    name = "dc:creator"
                view.fields.setdefault(name, value)
            view.would_load = True
    except Exception as exc:
        # 全部是本任务合成字节；错误原文交给负例证据，不假装目标 UI 报了同样错误。
        view.reason = f"结构解析失败：{type(exc).__name__}: {exc}"
    return view


def _kpp_name(blob):
    """Pillow 独立读 PNG/文本及像素，不用生产 read_preset_xml。"""
    with Image.open(io.BytesIO(blob)) as image:
        image.load()
        assert image.format == "PNG" and image.size == (200, 200)
        assert image.text.get("version") == "5.0"
        preset = minidom.parseString(image.text["preset"]).documentElement
        assert preset.tagName == "Preset" and preset.getAttribute("paintopid") == "paintbrush"
        return preset.getAttribute("name")


def _tip_fingerprint(blob):
    """从内嵌 PNG 解码源笔尖内容；像素摘要只作测试锚点，绝不代替原始字节 MD5。"""
    with Image.open(io.BytesIO(blob)) as image:
        image.load()
        preset = minidom.parseString(image.text["preset"]).documentElement
    resources = [node for node in preset.getElementsByTagName("resource")
                 if node.getAttribute("type") == "brushes"]
    assert len(resources) == 1 and preset.getAttribute("embedded_resources") == "2"
    resource = resources[0]
    png = base64.b64decode(_text(resource), validate=True)
    assert hashlib.md5(png).hexdigest() == resource.getAttribute("md5sum")
    with Image.open(io.BytesIO(png)) as tip:
        tip.load()
        assert tip.mode == "L"
        return list(tip.size), hashlib.sha256(tip.tobytes()).hexdigest()


def _contract_errors(data, view):
    """项目严格不变量：比原生 load 更严；坏 MD5/重名不能冒充源码必拒。"""
    errors = []
    if view.duplicates:
        return ["ZIP 条目重复：" + "、".join(view.duplicates)]
    if view.would_load is not True:
        return [view.reason or "原生必需节点失败"]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if (not infos or infos[0].filename != "mimetype"
                or infos[0].compress_type != zipfile.ZIP_STORED
                or archive.read("mimetype") != MIME.encode()):
            errors.append("mimetype 首项/STORED/内容不符")
        if not view.root_entry:
            errors.append("manifest 缺根条目")
        errors.extend(view.skipped)
        if len({(r["type"], r["path"]) for r in view.declared}) != len(view.declared):
            errors.append("manifest 声明重复")
        if view.removed:
            errors.append("资源缺失：" + "、".join(view.removed))
        actual = {name for name in archive.namelist() if name.startswith(PREFIX)}
        declared = {r["path"] for r in view.declared if r["type"] == "paintoppresets"}
        if actual != declared:
            errors.append("manifest 与实际预设集合不一致")
        for record in view.declared:
            path = record["path"]
            if record["blocks"] > 1:
                errors.append("多个 tags 容器：" + path)
            tags = record["tags"]
            if any(not tag.strip() for tag in tags) or len({t.casefold() for t in tags}) != len(tags):
                errors.append("标签空值或重复：" + path)
            if path in actual:
                if (not re.fullmatch("[0-9a-f]{32}", record["md5"])
                        or hashlib.md5(archive.read(path)).hexdigest() != record["md5"]):
                    errors.append("MD5 原始字节不符：" + path)
                try:
                    _kpp_name(archive.read(path))
                except Exception as exc:
                    errors.append(f"KPP 独立读取失败：{path}: {exc}")
    return errors


def _key_paths(data, scenario):
    """追溯只负责 ID→文件定位；名称、序号、源摘要和实际字节另行独立核对。"""
    view = _native_read(data)
    trace = json.loads(view.fields["meta:brushconverter-trace"])
    expected = scenario["ids"]
    manual = {row["key"]: row for row in _config()["presets"]}
    assert trace["format"] == "brushconverter.bundle-trace"
    assert trace["identity_format"] == 2 and trace["version"] == 1
    assert trace["source"]["digest"] == "sha256:" + hashlib.sha256(scenario["source"].read_bytes()).hexdigest()
    assert trace["source"]["filename"] == scenario["source"].name
    assert trace["source"]["bound"] is True
    by_id = {row["id"]: row for row in trace["presets"]}
    assert len(by_id) == len(trace["presets"]) == len(expected)
    assert set(by_id) == {identity.preset_id for identity in expected.values()}
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for key, identity in expected.items():
            row = by_id[identity.preset_id]
            assert row["ordinal"] == manual[key]["ordinal"]
            assert row["name"] == manual[key]["name"]
            source_prefix = trace["source"]["digest"][7:19]
            assert row["id"].startswith(f"bc1-{source_prefix}-{manual[key]['ordinal']:04d}-")
            path = PREFIX + row["kpp"]
            assert hashlib.sha256(archive.read(path)).hexdigest() == row["kpp_sha256"]
            assert _kpp_name(archive.read(path)) == manual[key]["name"]
            tip_size, tip_digest = _tip_fingerprint(archive.read(path))
            assert (tip_size, tip_digest) == (manual[key]["expected_tip_size"],
                                             manual[key]["expected_tip_pixel_sha256"]), f"{key} 源笔尖像素锚点不符"
            result[key] = path
    assert len(set(result.values())) == len(result)
    return result


def _assert_expected(view, paths):
    """期望来自手写 cases.json，不调用任何生产归一/回读函数计算期待值。"""
    cfg = _config()
    assert view.would_load is True, view.reason
    assert set(view.resources) == set(paths.values()), "资源集合丢失或串入"
    for row in cfg["presets"]:
        actual = view.resources[paths[row["key"]]]["tags"]
        assert set(actual) == set(row["expected_tags"]), f'{row["key"]} 逐笔标签错误：{actual!r}'
        assert len(actual) == len(row["expected_tags"]), "重复标签不能靠集合掩盖"
    filters = view.filters()
    expected_nonempty = {tag for tag, keys in cfg["expected_filters"].items() if keys}
    assert set(filters) == expected_nonempty, "筛选标签集合错误"
    for tag, keys in cfg["expected_filters"].items():
        expected = {PurePosixPath(paths[key]).name for key in keys}
        assert filters.get(tag, set()) == expected, f"筛选集合错误：{tag!r}"
    untagged = {key for key, path in paths.items() if not view.resources[path]["tags"]}
    assert untagged == set(cfg["expected_untagged"])


@pytest.fixture(autouse=True)
def case_dir(monkeypatch):
    """不使用默认 tmp_path；同时隔离模拟用户库、标准临时目录与相对输出位置。"""
    path = TASK / "cases" / uuid4().hex
    path.mkdir(parents=True)
    for leaf, keys in (("temp", ("TEMP", "TMP", "TMPDIR")),
                       ("home", ("HOME", "USERPROFILE")),
                       ("appdata", ("APPDATA",)), ("local", ("LOCALAPPDATA",)),
                       ("cache", ("XDG_CACHE_HOME",)), ("config", ("XDG_CONFIG_HOME",)),
                       ("data", ("XDG_DATA_HOME",)), ("state", ("XDG_STATE_HOME",))):
        target = path / leaf
        target.mkdir()
        for key in keys:
            monkeypatch.setenv(key, str(target))
    monkeypatch.setattr(tempfile, "tempdir", str(path / "temp"))
    monkeypatch.chdir(path)
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(TASK.resolve())
    return path


def _scenario(case_dir, variant=0):
    """生成 12 支 v1 ABR，再取原序号 2/5/9/12；显示名由合成场景显式赋值。

    这是转换/打包回归，不验证 ABR→显示名映射；原始源字节与所选 tip 确实对应。
    """
    cfg = _config()
    source = case_dir / f"source-{variant}" / "179合成源.abr"
    source.parent.mkdir(parents=True)
    bodies = []
    for ordinal in range(1, 13):
        pixels = np.full((6, 8), 255, dtype=np.uint8)
        # 变体只改变未选择的第1支：四支待测 tip、名称、原序号完全相同。
        pixels[1:5, 2:6] = (ordinal * 13 + (variant if ordinal == 1 else 0)) % 180
        body = (struct.pack(">IH", 0, 25) + b"\x01"
                + struct.pack(">4h", 0, 0, 6, 8) + struct.pack(">4i", 0, 0, 6, 8)
                + struct.pack(">HB", 8, 0) + pixels.tobytes())
        bodies.append(struct.pack(">HI", 2, len(body)) + body)
    source.write_bytes(struct.pack(">HH", 1, 12) + b"".join(bodies))
    abr = AbrFile.parse(source)
    assert len(abr.tips) == 12
    presets = [BrushPreset(name=row["name"], uuid=f"synthetic-179-{row['ordinal']}",
                           tip_gray=abr.tips[row["ordinal"] - 1].gray.copy())
               for row in cfg["presets"]]
    digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    identities = build_identities(presets, digest, [row["ordinal"] for row in cfg["presets"]])
    return dict(source=source, abr=abr, presets=presets,
                ids={row["key"]: identity for row, identity in zip(cfg["presets"], identities)})


def _export(scenario, out, *, metadata=None, reverse=False, keys=None, tag_change=False):
    cfg = _config()
    tags = {}
    for row in cfg["presets"]:
        fields = {"common": cfg["common"], "source": cfg["source"],
                  "added": row["added"], "removed": row["removed"]}
        if reverse:
            fields = {key: list(reversed(value)) for key, value in fields.items()}
        final = PresetTagPlan(**fields).final()
        if tag_change and row["key"] == "A":
            final += ("新标签179",)
        tags[scenario["ids"][row["key"]].preset_id] = final
    if reverse:
        tags = dict(reversed(list(tags.items())))
    indices = list(range(4)) if keys is None else ["ABCD".index(key) for key in keys]
    chosen = [scenario["presets"][index] for index in indices]
    ordinals = [cfg["presets"][index]["ordinal"] for index in indices]
    selected_ids = {scenario["ids"]["ABCD"[index]].preset_id for index in indices}
    result = convert_presets(
        scenario["abr"], chosen, str(out), make_standalone=False,
        metadata=BundleMetadata(**(metadata if metadata is not None else cfg["metadata"])),
        preset_tags={key: value for key, value in tags.items() if key in selected_ids},
        package_tags=cfg["package_tags"], source_path=str(scenario["source"]),
        source_ordinals=ordinals, staging_dir=str(out.parent / (out.name + "-staging")),
    )
    assert result.status in ("committed", "reused_only")
    assert not result.skipped and not result.cancelled
    assert result.verify is not None and result.verify.ok, result.verify
    assert Path(result.bundle_path).resolve().is_relative_to(TASK.resolve())
    return result


@pytest.fixture
def exported(case_dir):
    scenario = _scenario(case_dir)
    result = _export(scenario, case_dir / "out")
    return scenario, result, Path(result.bundle_path).read_bytes()


def _rewrite(data, *, entry=None, edit=None, drop=None, duplicate=None):
    """仅对本任务内存副本改坏；源包与旧证据绝不原位重写。"""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            if info.filename == drop:
                continue
            blob = source.read(info)
            if info.filename == entry:
                doc = minidom.parseString(blob)
                edit(doc)
                blob = doc.toxml(encoding="utf-8")
            target.writestr(copy(info), blob)
        if duplicate is not None:
            with pytest.warns(UserWarning, match="Duplicate name"):
                target.writestr(duplicate, source.read(duplicate))
    return output.getvalue()


def _entries(doc):
    return [node for node in _children(doc.documentElement, "file-entry")
            if node.getAttributeNS(MNS, "media-type") == "paintoppresets"]


def _mutate(data, kind):
    if kind == "truncated_zip":
        return data[:80]
    first_path = next(path for path in _native_read(data).resources if path.startswith(PREFIX))
    if kind in {"missing_meta", "missing_manifest", "missing_kpp", "no_mimetype"}:
        drop = {"missing_meta": "meta.xml", "missing_manifest": MANIFEST,
                "missing_kpp": first_path, "no_mimetype": "mimetype"}[kind]
        return _rewrite(data, drop=drop)
    if kind.startswith("zip_duplicate_"):
        name = {"zip_duplicate_kpp": first_path, "zip_duplicate_meta": "meta.xml",
                "zip_duplicate_manifest": MANIFEST}[kind]
        return _rewrite(data, duplicate=name)
    if kind == "bad_meta_root":
        def wrap(doc):
            root = doc.documentElement
            wrapper = doc.createElement("office:document-meta")
            wrapper.setAttribute("xmlns:office", "urn:oasis:names:tc:opendocument:xmlns:office:1.0")
            doc.replaceChild(wrapper, root)
            wrapper.appendChild(root)
        return _rewrite(data, entry="meta.xml", edit=wrap)

    def edit(doc):
        first = _entries(doc)[0]
        if kind == "wrong_namespace":
            doc.documentElement.setAttribute("xmlns:manifest", "urn:invalid:179")
        elif kind == "bad_md5":
            first.setAttributeNS(MNS, "manifest:md5sum", "0" * 32)
        elif kind == "missing_md5":
            first.removeAttributeNS(MNS, "md5sum")
        elif kind == "duplicate_manifest":
            later = first.cloneNode(deep=True)
            tag = _children(_children(later, "tags")[0], "tag")[0]
            for child in list(tag.childNodes):
                tag.removeChild(child)
            tag.appendChild(doc.createTextNode("后项独有179"))
            doc.documentElement.appendChild(later)
        elif kind == "second_tags":
            block = doc.createElementNS(MNS, "manifest:tags")
            tag = doc.createElementNS(MNS, "manifest:tag")
            tag.appendChild(doc.createTextNode("不可合并的第二容器179"))
            block.appendChild(tag)
            first.appendChild(block)
        elif kind in {"duplicate_tag", "empty_tag"}:
            block = _children(first, "tags")[0]
            tag = (_children(block, "tag")[0].cloneNode(deep=True) if kind == "duplicate_tag"
                   else doc.createElementNS(MNS, "manifest:tag"))
            block.appendChild(tag)
        elif kind == "swapped_tags":
            second = _entries(doc)[1]
            old_a, old_b = _children(first, "tags")[0], _children(second, "tags")[0]
            first.replaceChild(old_b.cloneNode(deep=True), old_a)
            second.replaceChild(old_a.cloneNode(deep=True), old_b)
        elif kind in {"missing_one_tags", "package_tags_only"}:
            for node in _entries(doc)[:1] if kind == "missing_one_tags" else _entries(doc):
                for block in _children(node, "tags"):
                    node.removeChild(block)
        else:
            raise AssertionError("未实现的负例：" + kind)
    return _rewrite(data, entry=MANIFEST, edit=edit)


def _snapshot(root):
    """不比 atime（读取本身可改）；逐文件字节摘要+大小+mtime_ns+完整路径集合。"""
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                           p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_declarative_expected_sets_are_complete():
    cfg = _config()
    manual = {row["key"]: set(row["expected_tags"]) for row in cfg["presets"]}
    actual = {tag: sorted(key for key, tags in manual.items() if tag in tags)
              for tag in set().union(*manual.values())}
    assert actual == {tag: keys for tag, keys in cfg["expected_filters"].items() if keys}
    assert [key for key, tags in manual.items() if not tags] == cfg["expected_untagged"]
    assert cfg["manual_187"] and set(cfg["manual_187"].values()) == {"待人工"}


def test_independent_roundtrip_filters_and_same_names(exported):
    scenario, result, data = exported
    view = _native_read(data)
    assert not _contract_errors(data, view)
    paths = _key_paths(data, scenario)
    _assert_expected(view, paths)
    assert scenario["ids"]["A"].name == scenario["ids"]["B"].name
    assert scenario["ids"]["A"].preset_id != scenario["ids"]["B"].preset_id
    assert paths["A"] != paths["B"]
    assert view.package_tags == {"只在包级"}
    for key, value in _config()["metadata"].items():
        field_name = {"name": "dc:title", "description": "dc:description", "author": "dc:author",
                      "license": "meta:license", "package_version": "meta:brushconverter-package-version"}[key]
        assert view.fields[field_name] == value
    assert view.fields["meta:bundle-version"] == "1"
    assert result.bundle_identity.scheme == "v2"


def test_bytes_deterministic_across_input_order_and_output_path(exported, case_dir):
    scenario, first, data = exported
    repeat = _export(scenario, case_dir / "different-directory", reverse=True)
    assert Path(repeat.bundle_path).read_bytes() == data
    assert first.bundle_identity == repeat.bundle_identity
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) and info.create_system == 0
                   for info in archive.infolist())
    before = _snapshot(case_dir / "out")
    reused = _export(scenario, case_dir / "out")
    assert reused.bundle_reused and reused.status == "reused_only"
    assert _snapshot(case_dir / "out") == before


def test_same_source_bytes_in_different_directory_are_identical(exported, case_dir):
    scenario, first, data = exported
    copied = case_dir / "relocated-source" / scenario["source"].name
    copied.parent.mkdir()
    copied.write_bytes(scenario["source"].read_bytes())
    relocated = dict(scenario, source=copied, abr=AbrFile.parse(copied))
    result = _export(relocated, case_dir / "relocated-out")
    assert result.bundle_identity == first.bundle_identity
    assert Path(result.bundle_path).read_bytes() == data


def test_original_ordinals_survive_reordered_selection(exported, case_dir):
    scenario, _result, _data = exported
    ordered = _export(scenario, case_dir / "reordered", keys="DBAC")
    data = Path(ordered.bundle_path).read_bytes()
    paths = _key_paths(data, scenario)
    _assert_expected(_native_read(data), paths)
    subset = _export(scenario, case_dir / "subset", keys="BA")
    data = Path(subset.bundle_path).read_bytes()
    selected = dict(scenario, ids={key: scenario["ids"][key] for key in "BA"})
    assert set(_key_paths(data, selected)) == {"A", "B"}


def test_same_names_from_different_sources_do_not_share_ids(exported, case_dir):
    first, _result, _data = exported
    second = _scenario(case_dir, variant=1)
    assert first["ids"]["A"].name == second["ids"]["A"].name
    for key in "ABCD":
        index = "ABCD".index(key)
        assert np.array_equal(first["presets"][index].tip_gray, second["presets"][index].tip_gray)
        assert first["ids"][key].content_digest == second["ids"][key].content_digest
        assert first["ids"][key].ordinal == second["ids"][key].ordinal
    assert {i.preset_id for i in first["ids"].values()}.isdisjoint(
        i.preset_id for i in second["ids"].values())
    result = _export(second, case_dir / "other-source")
    data = Path(result.bundle_path).read_bytes()
    _assert_expected(_native_read(data), _key_paths(data, second))


@pytest.mark.parametrize("field_name,value", [
    ("description", "只改说明179"), ("license", "只改合成许可179"),
    ("author", "另一个合成作者179"), ("package_version", "fixture-2"),
])
def test_metadata_only_changes_identity_not_brushes(exported, case_dir, field_name, value):
    scenario, first, data = exported
    before = _snapshot(case_dir / "out")
    metadata = dict(_config()["metadata"], **{field_name: value})
    second = _export(scenario, case_dir / "out", metadata=metadata)
    changed = Path(second.bundle_path).read_bytes()
    assert second.bundle_identity != first.bundle_identity
    assert second.bundle_path != first.bundle_path and not second.bundle_reused
    with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(io.BytesIO(changed)) as new:
        assert old.read(MANIFEST) == new.read(MANIFEST)
        assert old.read("meta.xml") != new.read("meta.xml")
        assert {n: old.read(n) for n in old.namelist() if n.startswith(PREFIX)} == {
            n: new.read(n) for n in new.namelist() if n.startswith(PREFIX)}
    old_view, new_view = _native_read(data), _native_read(changed)
    expected_fields = dict(old_view.fields)
    targets = {"description": ("dc:description",), "license": ("meta:license",),
               "author": ("dc:author", "dc:creator", "meta:initial-creator"),
               "package_version": ("meta:brushconverter-package-version",)}
    for name in targets[field_name]:
        expected_fields[name] = value
    assert new_view.fields == expected_fields, "修改后的原生字段必须精确命中，其余字段不变"
    assert old_view.filters() == new_view.filters()
    assert all(_snapshot(case_dir / "out")[key] == value for key, value in before.items())


def test_tag_only_change_produces_new_bundle_without_relabeling_old(exported, case_dir):
    scenario, first, data = exported
    before = _snapshot(case_dir / "out")
    changed = _export(scenario, case_dir / "out", tag_change=True)
    assert first.bundle_identity != changed.bundle_identity
    assert first.bundle_path != changed.bundle_path
    assert all(_snapshot(case_dir / "out")[key] == value for key, value in before.items())
    paths = _key_paths(data, scenario)
    _assert_expected(_native_read(Path(first.bundle_path).read_bytes()), paths)
    changed_data = Path(changed.bundle_path).read_bytes()
    new_view = _native_read(changed_data)
    new_paths = _key_paths(changed_data, scenario)
    assert paths == new_paths
    for row in _config()["presets"]:
        expected = set(row["expected_tags"]) | ({"新标签179"} if row["key"] == "A" else set())
        assert set(new_view.resources[paths[row["key"]]]["tags"]) == expected
    expected_filters = _native_read(data).filters()
    expected_filters["新标签179"] = {PurePosixPath(paths["A"]).name}
    assert new_view.filters() == expected_filters
    with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(io.BytesIO(changed_data)) as new:
        assert {n: old.read(n) for n in old.namelist() if n.startswith(PREFIX)} == {
            n: new.read(n) for n in new.namelist() if n.startswith(PREFIX)}
    assert "新标签179" not in _native_read(data).filters()


def test_export_does_not_touch_sandbox_user_library(case_dir):
    scenario = _scenario(case_dir)
    library = Path(os.environ["APPDATA"]) / "krita"
    (library / "tags").mkdir(parents=True)
    (library / "resourcecache.sqlite").write_bytes(b"synthetic sqlite sentinel 179")
    (library / "tags" / "old.tag").write_text("合成旧标签179：旧笔刷", encoding="utf-8")
    (library / "old.bundle").write_bytes(b"synthetic existing user resource 179")
    before, source_before = _snapshot(library), _snapshot(scenario["source"].parent)
    _export(scenario, case_dir / "out")
    assert _snapshot(library) == before
    assert _snapshot(scenario["source"].parent) == source_before
    _export(scenario, case_dir / "out", tag_change=True)
    assert _snapshot(library) == before
    assert _snapshot(scenario["source"].parent) == source_before


def test_same_named_legacy_bundle_is_preserved(exported, case_dir):
    scenario, first, data = exported
    def remove_trace(doc):
        for node in list(_children(doc.documentElement)):
            if node.getAttribute("meta:name") == "brushconverter-trace":
                doc.documentElement.removeChild(node)
    legacy = _rewrite(data, entry="meta.xml", edit=remove_trace)
    out = case_dir / "legacy-collision"
    out.mkdir()
    path = out / Path(first.bundle_path).name
    path.write_bytes(legacy)
    os.utime(path, ns=(1_600_000_000_000_000_000,) * 2)
    before = _snapshot(out)
    new = _export(scenario, out)
    assert not new.bundle_reused
    assert Path(new.bundle_path).name == path.stem + "_2.bundle"
    assert all(_snapshot(out)[key] == value for key, value in before.items())
    assert _native_read(path.read_bytes()).filters() == _native_read(data).filters()
    checked = verify_bundle(path)
    assert checked.ok and checked.identity_scheme == "legacy"


BAD_CASES = [
    ("missing_meta", False, "缺必需"), ("missing_manifest", False, "缺必需"),
    ("bad_meta_root", False, "meta 根"), ("wrong_namespace", False, "命名空间"),
    ("missing_kpp", True, "资源缺失"), ("bad_md5", True, "MD5"),
    ("missing_md5", True, "缺必需属性"), ("missing_one_tags", True, "逐笔标签"),
    ("package_tags_only", True, "逐笔标签"), ("duplicate_manifest", True, "声明重复"),
    ("second_tags", True, "多个 tags"), ("no_mimetype", True, "mimetype"),
    ("duplicate_tag", True, "标签空值或重复"), ("empty_tag", True, "标签空值或重复"),
    ("truncated_zip", False, "结构解析失败"),
    ("zip_duplicate_kpp", None, "ZIP 条目重复"),
    ("zip_duplicate_meta", None, "ZIP 条目重复"),
    ("zip_duplicate_manifest", None, "ZIP 条目重复"),
]


def _negative_evidence(data, baseline_paths, directory, kind, expected_native, marker):
    view = _native_read(data)
    assert view.would_load is expected_native, view.reason
    errors = _contract_errors(data, view)
    if not errors:
        with pytest.raises(AssertionError) as caught:
            _assert_expected(view, baseline_paths)
        errors = [str(caught.value)]
    assert any(marker in error for error in errors), errors
    path = directory / (kind + ".bundle")
    path.write_bytes(data)
    verified = verify_bundle(path)
    assert not verified.ok and verified.errors, f"生产回读不应放行 {kind}"
    if kind == "second_tags":
        assert "不可合并的第二容器179" not in view.filters()
    if kind == "duplicate_manifest":
        assert "后项独有179" in view.filters()
    if kind == "missing_kpp":
        assert len(view.removed) == 1 and len(view.resources) == 3
    if kind == "package_tags_only":
        assert view.package_tags == {"只在包级"} and view.filters() == {}
    record = dict(case=kind, native_would_load=view.would_load, native_reason=view.reason,
                  independent_errors=errors, production_errors=verified.errors,
                  sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                  manual_187="待人工：不承诺坏包在实际界面的具体表现")
    (directory / (kind + ".json")).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


@pytest.mark.parametrize("kind,expected_native,marker", BAD_CASES)
def test_bad_packages_really_fail(exported, case_dir, kind, expected_native, marker):
    scenario, _result, data = exported
    paths = _key_paths(data, scenario)
    _negative_evidence(_mutate(data, kind), paths, case_dir, kind, expected_native, marker)


def _self_consistent_wrong_association(data, baseline_paths, directory):
    """反自证对照：同时换错 manifest 和 trace；生产自洽校验可过，手列语义门必须拒绝。"""
    swapped = _mutate(data, "swapped_tags")
    view = _native_read(swapped)

    def change_trace(doc):
        for node in _children(doc.documentElement):
            if node.getAttribute("meta:name") == "brushconverter-trace":
                trace = json.loads(node.getAttribute("meta:value"))
                for row in trace["presets"]:
                    row["tags"] = view.resources[PREFIX + row["kpp"]]["tags"]
                node.setAttribute("meta:value", json.dumps(
                    trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    wrong = _rewrite(swapped, entry="meta.xml", edit=change_trace)
    view = _native_read(wrong)
    assert view.would_load is True and not _contract_errors(wrong, view)
    assert set(view.filters()) == set(_native_read(data).filters()), "反例应保持全包标签并集"
    path = directory / "self_consistent_wrong_association.bundle"
    path.write_bytes(wrong)
    verified = verify_bundle(path)
    assert verified.ok, verified.errors
    with pytest.raises(AssertionError, match="逐笔标签错误") as caught:
        _assert_expected(view, baseline_paths)
    record = dict(case="self_consistent_wrong_association", native_would_load=True,
                  production_ok=True, independent_errors=[str(caught.value)],
                  sha256=hashlib.sha256(wrong).hexdigest(), bytes=len(wrong),
                  note="格式及追溯自洽不代表业务期望正确；A/B 同名标签互换必须由独立语义门拒绝",
                  manual_187="待人工；不是 Krita 应拒绝导入的格式坏包")
    (directory / "self_consistent_wrong_association.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def test_self_consistent_wrong_association_fails_independent_semantic_gate(exported, case_dir):
    scenario, _result, data = exported
    _self_consistent_wrong_association(data, _key_paths(data, scenario), case_dir)


def _wrong_source_binding(data, scenario, directory):
    """A/B 同名：连追溯文件定位和 manifest 标签一起互换，像素锚点仍须抓住串笔。"""
    swapped = _mutate(data, "swapped_tags")
    view = _native_read(swapped)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        blobs = {PurePosixPath(path).name: archive.read(path) for path in view.resources}

    def retarget(doc):
        for node in _children(doc.documentElement):
            if node.getAttribute("meta:name") == "brushconverter-trace":
                trace = json.loads(node.getAttribute("meta:value"))
                a, b = trace["presets"][:2]
                a["kpp"], b["kpp"] = b["kpp"], a["kpp"]
                for row in (a, b):
                    row["kpp_sha256"] = hashlib.sha256(blobs[row["kpp"]]).hexdigest()
                node.setAttribute("meta:value", json.dumps(
                    trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    wrong = _rewrite(swapped, entry="meta.xml", edit=retarget)
    view = _native_read(wrong)
    assert view.would_load is True and not _contract_errors(wrong, view)
    path = directory / "self_consistent_wrong_source_binding.bundle"
    path.write_bytes(wrong)
    verified = verify_bundle(path)
    assert verified.ok, verified.errors
    with pytest.raises(AssertionError, match="源笔尖像素锚点不符") as caught:
        _key_paths(wrong, scenario)
    record = dict(case="self_consistent_wrong_source_binding", native_would_load=True,
                  production_ok=True, independent_errors=[str(caught.value)],
                  sha256=hashlib.sha256(wrong).hexdigest(), bytes=len(wrong),
                  note="显示名、原序号与标签自洽不足；手工笔尖像素锚点识别错误源关联",
                  manual_187="待人工；不是要求 Krita 拒绝的格式坏包")
    (directory / "self_consistent_wrong_source_binding.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def test_self_consistent_wrong_source_binding_fails_pixel_anchor(exported, case_dir):
    scenario, _result, data = exported
    _wrong_source_binding(data, scenario, case_dir)


def test_independent_reader_first_tags_and_descendant_text(case_dir):
    """手写 XML 锚定目标读者：不通过生产 writer 生成输入或生产 verifier 求期待值。"""
    manifest = ('<m:manifest xmlns:m="' + MNS + '">'
                '<m:file-entry m:full-path="paintoppresets/manual.kpp" '
                'm:media-type="paintoppresets" m:md5sum="00">'
                '<m:tags><m:tag>中文<b>混合</b><![CDATA[ & <特殊>]]></m:tag></m:tags>'
                '<m:tags><m:tag>不应关联</m:tag></m:tags>'
                '</m:file-entry></m:manifest>')
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(MANIFEST, manifest)
        archive.writestr("meta.xml", '<meta:meta xmlns:meta="urn:test"/>')
        # 源码 load 不解析 KPP，也不比 MD5；占位不可当作187可绘制样例。
        archive.writestr("paintoppresets/manual.kpp", b"manual placeholder")
    view = _native_read(output.getvalue())
    assert view.would_load is True
    assert view.filters() == {"中文混合 & <特殊>": {"manual.kpp"}}


def test_187_handoff_catalog_contains_exact_expected_sets(exported, case_dir):
    """每次生成独立、不覆盖旧证据的187交接；二进制只在任务临时目录。"""
    scenario, first, data = exported
    paths = _key_paths(data, scenario)
    _assert_expected(_native_read(data), paths)
    directory = TASK / "handoff" / ("run-" + uuid4().hex)
    directory.mkdir(parents=True)
    repeated = _export(scenario, case_dir / "repeat-handoff", reverse=True)
    repeated_blob = Path(repeated.bundle_path).read_bytes()
    assert repeated_blob == data
    samples = []
    for filename, blob in [("01-native.bundle", data), ("02-repeat.bundle", repeated_blob)]:
        (directory / filename).write_bytes(blob)
        samples.append(dict(file=filename, sha256=hashlib.sha256(blob).hexdigest(), bytes=len(blob)))
    metadata = dict(_config()["metadata"], description="187 只改说明的对照179")
    changed = _export(scenario, case_dir / "metadata-handoff", metadata=metadata)
    metadata_blob = Path(changed.bundle_path).read_bytes()
    assert changed.bundle_identity != first.bundle_identity
    _assert_expected(_native_read(metadata_blob), _key_paths(metadata_blob, scenario))
    assert _native_read(metadata_blob).fields["dc:description"] == metadata["description"]
    (directory / "03-metadata-only.bundle").write_bytes(metadata_blob)
    samples.append(dict(file="03-metadata-only.bundle", sha256=hashlib.sha256(metadata_blob).hexdigest(),
                        bytes=len(metadata_blob)))
    negatives = [_negative_evidence(_mutate(data, kind), paths, directory, kind, native, marker)
                 for kind, native, marker in BAD_CASES]
    negatives.append(_self_consistent_wrong_association(data, paths, directory))
    negatives.append(_wrong_source_binding(data, scenario, directory))
    cfg = _config()
    catalog = dict(
        format="brushconverter.bundle-interop-handoff", version=1,
        target="Krita 5.3.3 / git 858d352（源码支持，非实机）",
        independent_reader="minidom + Pillow；不是 Krita 实机替代",
        samples=samples, negatives=negatives,
        brushes=[dict(key=row["key"], name=row["name"], ordinal=row["ordinal"],
                      id=scenario["ids"][row["key"]].preset_id, file=paths[row["key"]],
                      tags=row["expected_tags"], tip_size=row["expected_tip_size"],
                      tip_pixel_sha256=row["expected_tip_pixel_sha256"]) for row in cfg["presets"]],
        expected_filters={tag: [paths[key] for key in keys] for tag, keys in cfg["expected_filters"].items()},
        expected_untagged=[paths[key] for key in cfg["expected_untagged"]],
        manual_187=cfg["manual_187"],
    )
    target = directory / "catalog.json"
    target.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(target.read_text(encoding="utf-8"))["manual_187"] == cfg["manual_187"]
