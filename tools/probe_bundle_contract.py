# -*- coding: utf-8 -*-
"""Krita 资源包（.bundle）原生契约探针（任务 174）。

这个脚本不启动 Krita，也不把“文件能解析”当成“Krita 已实机生效”。
它把目标版本源码里能读到的读取规则，逐条实现成一个可重复的“读回探针”：

  1. 读 meta.xml：根元素是否字面为 meta:meta、字段名/前缀、包级标签、未知字段；
  2. 读 META-INF/manifest.xml：命名空间、file-entry、md5、每预设 tags/tag；
  3. 读 zip：mimetype 是否首项且不压缩、条目是否齐全、声明 md5 与字节是否一致；
  4. 给出“按目标源码，Krita 的 KoResourceBundle::load() 会不会继续”的模拟结论。

证据边界（必须一起读）：
  - “模拟 reader 通过”只代表与源码规则一致，不是 Krita 实机导入通过；
  - 真实导入、界面显示、标签筛选与重启持久性统一留给任务 187 人工；
  - kpp 只是结构占位（合成样例不是可绘制预设），本探针不解析预设 XML。

用法（在仓库根目录；$PY 先按通用规范核验）：
    & $PY -B tools/probe_bundle_contract.py --help
    & $PY -B tools/probe_bundle_contract.py --selftest
    & $PY -B tools/probe_bundle_contract.py --emit-fixtures tests/fixtures/bundle_contract
    & $PY -B tools/probe_bundle_contract.py --probe tests/fixtures/bundle_contract/native_meta_meta.bundle
    & $PY -B tools/probe_bundle_contract.py --probe <某个.bundle> --json

退出码：
    0 = 检查/生成成功（--probe 时即使模拟 reader 拒绝也返回 0，报告里会写明原因）
    1 = 自检断言失败，或夹具与生成规则不一致
    2 = 输入文件读不了 / zip 打不开 / 参数用法错误
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.dom import Node, minidom
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_FIXTURES = REPO / "tests" / "fixtures" / "bundle_contract"

# ── 目标版本与命名空间（与 research/ 参考源码、libs/resources 源码一致）──────
TARGET_KRITA = "Krita 5.3.3 (git 858d352, Qt5 Windows x64)"
TARGET_SOURCE_COMMIT = "858d352e52e68831693067763b9cdaf8bb9a05ce"
MIMETYPE = "application/x-krita-resourcebundle"
NS_MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
NS_META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
# Krita 自己写 meta.xml 时用的 DC URI 就是“没有尾斜杠”这一串；readMetaData
# 只按字面前缀取字段、不校验 URI，所以这里照抄原生 writer 的值，避免制造第二个口径。
NS_DC = "http://purl.org/dc/elements/1.1"
NS_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
NS_MANIFEST_WRONG = "urn:oasis:names:tc:opendocument:xmlns:manifest:9.9"

MANIFEST_PATH = "META-INF/manifest.xml"
META_PATH = "meta.xml"
MIMETYPE_PATH = "mimetype"
PREVIEW_PATH = "preview.png"
PRESET_PREFIX = "paintoppresets/"

# 固定 zip 时间戳与宿主标记：同一份样例两次生成字节相同（证据可复核）。
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# 1×1 透明 PNG（preview.png 可选字段的最小示意；不是美术素材）。
PREVIEW_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# meta.xml 字段在 Krita 包管理器详情面板里的实际去向（源码级分类，不是实机截图）。
# 依据：dlg_bundle_manager.cpp::updateBundleInformation() 只读这几个键；
#       标题用的是文件名（storage->name()），不是 dc:title。
DISPLAYED_IN_BUNDLE_MANAGER = {
    "dc:author": "包管理器详情面板显示",
    "meta:creation-date": "包管理器详情面板显示（Created）",
    "meta:dc-date": "包管理器详情面板显示（Updated）",
    "dc:description": "包管理器详情面板显示",
    "meta:email": "包管理器详情面板显示",
    "meta:license": "包管理器详情面板显示",
    "meta:website": "包管理器详情面板显示",
}
STORED_ONLY = {
    "dc:title": "仅保存在包内；包管理器标题用文件名，不用 dc:title",
    "meta:generator": "仅保存在包内；本任务检索到的详情面板不显示",
    "meta:bundle-version": "bundle 格式版本（源码固定为 1），不是产品/资源包版本",
    "meta:initial-creator": "仅保存在包内；创建器在缺省时写入作者名",
    "dc:creator": "仅保存在包内；创建器写入作者名",
}
UNKNOWN_META = "未知字段（Krita 读取时保留，界面不显示，Krita 重存会丢）"


def _zip_info(name: str, stored: bool = True) -> zipfile.ZipInfo:
    """构造固定时间戳/宿主标记的 ZipInfo（让样例字节可复现）。"""
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0
    return info


def _manifest_xml(entries: list[dict], *, namespace: str = NS_MANIFEST,
                  include_root_entry: bool = True,
                  extra_root_attrs: str = "",
                  extra_entry_attrs: str = "") -> str:
    """生成最小 manifest.xml；entries 里每项含 name/payload/tags。"""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<manifest:manifest xmlns:manifest="{namespace}" '
        f'manifest:version="1.2"{extra_root_attrs}>',
    ]
    if include_root_entry:
        lines.append(
            '<manifest:file-entry manifest:full-path="/" '
            f'manifest:media-type="{MIMETYPE}"/>'
        )
    for entry in entries:
        payload = entry["payload"]
        md5 = hashlib.md5(payload).hexdigest()
        full_path = PRESET_PREFIX + entry["name"]
        attrs = (
            f'manifest:media-type="paintoppresets" '
            f'manifest:full-path={quoteattr(full_path)} '
            f'manifest:md5sum="{md5}"{extra_entry_attrs}'
        )
        tags = entry.get("tags") or []
        if tags:
            lines.append(f"<manifest:file-entry {attrs}>")
            lines.append("<manifest:tags>")
            for tag in tags:
                lines.append(f"<manifest:tag>{escape(tag)}</manifest:tag>")
            lines.append("</manifest:tags>")
            lines.append("</manifest:file-entry>")
        else:
            lines.append(f"<manifest:file-entry {attrs}/>")
    lines.append("</manifest:manifest>")
    return "\n".join(lines)


def _meta_xml(*, root_kind: str, fields: list[tuple[str, str]],
              bundle_tags: list[str] | None = None,
              unknown_elements: list[tuple[str, str]] | None = None,
              userdefined: list[tuple[str, str]] | None = None) -> str:
    """生成最小 meta.xml。

    root_kind="meta"   → 原生写法：<meta:meta>（目标源码要求）
    root_kind="office" → 当前 bundle.py 的写法：<office:document-meta> 包住 <meta:meta>
    """
    bundle_tags = bundle_tags or []
    unknown_elements = unknown_elements or []
    userdefined = userdefined or []
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    if root_kind == "office":
        lines.append(f'<office:document-meta xmlns:office="{NS_OFFICE}">')
    lines.append(f'<meta:meta xmlns:meta="{NS_META}" xmlns:dc="{NS_DC}">')
    for tag, value in fields:
        lines.append(f"<{tag}>{escape(value)}</{tag}>")
    for tag, value in unknown_elements:
        lines.append(f"<{tag}>{escape(value)}</{tag}>")
    for tag, value in userdefined:
        lines.append(
            f'<meta:meta-userdefined meta:name={quoteattr(tag)} '
            f"meta:value={quoteattr(value)}/>"
        )
    for tag in bundle_tags:
        lines.append(
            f'<meta:meta-userdefined meta:name="tag" meta:value={quoteattr(tag)}/>'
        )
    lines.append("</meta:meta>")
    if root_kind == "office":
        lines.append("</office:document-meta>")
    return "\n".join(lines)


def _fixture_a() -> bytes:
    return b"fixture-kpp-a\n"


def _fixture_b() -> bytes:
    return b"fixture-kpp-b\n"


STANDARD_FIELDS = [
    ("meta:generator", "brushConverter-fixture"),
    ("meta:bundle-version", "1"),
    ("dc:title", "Fixture Bundle"),
    ("dc:description", "合成契约样例，不含个人信息"),
    ("dc:author", "Fixture Author"),
    ("meta:initial-creator", "Fixture Author"),
    ("dc:creator", "Fixture Author"),
    ("meta:creation-date", "01/01/2024"),
    ("meta:dc-date", "01/01/2024"),
    ("meta:email", "placeholder-email"),
    ("meta:license", "test-license"),
    ("meta:website", "https://example.invalid/"),
]

FIXTURES: dict[str, dict] = {
    # 正例：目标源码要求的根、字段、逐预设标签都在。
    "native_meta_meta.bundle": {
        "kind": "native",
        "root_kind": "meta",
        "fields": STANDARD_FIELDS,
        "bundle_tags": [],
        "include_meta": True,
        "include_mimetype": True,
        "include_preview": True,
        "entries": [
            {"name": "fixture-a.kpp", "payload": _fixture_a(), "tags": ["fixture-tag-a", "shared-tag"]},
            {"name": "fixture-b.kpp", "payload": _fixture_b(), "tags": ["fixture-tag-b", "shared-tag"]},
        ],
    },
    # 反例：当前 bundle.py 的 office:document-meta 根（源码 reader 会拒绝）。
    "office_document_meta_root.bundle": {
        "kind": "office_root",
        "root_kind": "office",
        "fields": STANDARD_FIELDS,
        "bundle_tags": [],
        "include_meta": True,
        "include_mimetype": True,
        "include_preview": False,
        "entries": [
            {"name": "fixture-a.kpp", "payload": _fixture_a(), "tags": []},
        ],
    },
    # 反例：只写包级 tag，不写每个预设的 manifest tags。
    "package_tags_only.bundle": {
        "kind": "package_tags_only",
        "root_kind": "meta",
        "fields": [STANDARD_FIELDS[0], STANDARD_FIELDS[1], STANDARD_FIELDS[3], STANDARD_FIELDS[4]],
        "bundle_tags": ["package-tag", "shared-tag"],
        "include_meta": True,
        "include_mimetype": True,
        "include_preview": False,
        "entries": [
            {"name": "fixture-a.kpp", "payload": _fixture_a(), "tags": []},
            {"name": "fixture-b.kpp", "payload": _fixture_b(), "tags": []},
        ],
    },
    # 未知字段：普通未知元素（无警告）+ meta-userdefined 未知名（有警告）+ JSON 值。
    "unknown_fields.bundle": {
        "kind": "unknown_fields",
        "root_kind": "meta",
        "fields": STANDARD_FIELDS,
        "unknown_elements": [("meta:custom-note", "opaque-value")],
        "userdefined": [
            ("brushconverter-trace",
             '{"format":1,"source":"fixture","items":[{"id":"bc1-fixture-1"}]}'),
            ("license", "compat-license"),
        ],
        "bundle_tags": [],
        "include_meta": True,
        "include_mimetype": True,
        "include_preview": False,
        "entries": [
            {"name": "fixture-a.kpp", "payload": _fixture_a(), "tags": ["fixture-tag-a"]},
        ],
        "extra_entry_attrs": ' manifest:future-attr="ignored"',
        "extra_root_attrs": ' manifest:future-version="9"',
    },
    # 反例：manifest 命名空间 URI 不是目标源码要求的那个。
    "manifest_wrong_namespace.bundle": {
        "kind": "manifest_wrong_namespace",
        "root_kind": "meta",
        "manifest_namespace": NS_MANIFEST_WRONG,
        "fields": [STANDARD_FIELDS[0], STANDARD_FIELDS[1]],
        "bundle_tags": [],
        "include_meta": True,
        "include_mimetype": True,
        "include_preview": False,
        "entries": [
            {"name": "fixture-a.kpp", "payload": _fixture_a(), "tags": ["fixture-tag-a"]},
        ],
    },
    # 反例：缺 meta.xml（源码 load() 直接返回 false）。
    "missing_meta.bundle": {
        "kind": "missing_meta",
        "root_kind": "meta",
        "fields": [],
        "bundle_tags": [],
        "include_meta": False,
        "include_mimetype": True,
        "include_preview": False,
        "entries": [
            {"name": "fixture-a.kpp", "payload": _fixture_a(), "tags": []},
        ],
    },
    # 对照：缺 mimetype。目标源码 reader 不校验它，但本项目写包不变量要求它在。
    "no_mimetype.bundle": {
        "kind": "no_mimetype",
        "root_kind": "meta",
        "fields": [STANDARD_FIELDS[0], STANDARD_FIELDS[1]],
        "bundle_tags": [],
        "include_meta": True,
        "include_mimetype": False,
        "include_preview": False,
        "entries": [
            {"name": "fixture-a.kpp", "payload": _fixture_a(), "tags": []},
        ],
    },
}


def build_fixture(name: str) -> bytes:
    """按 FIXTURES 定义生成一个样例的完整字节（确定性）。"""
    spec = FIXTURES[name]
    manifest = _manifest_xml(
        spec["entries"],
        namespace=spec.get("manifest_namespace", NS_MANIFEST),
        extra_root_attrs=spec.get("extra_root_attrs", ""),
        extra_entry_attrs=spec.get("extra_entry_attrs", ""),
    )
    meta = _meta_xml(
        root_kind=spec["root_kind"],
        fields=spec.get("fields", []),
        bundle_tags=spec.get("bundle_tags", []),
        unknown_elements=spec.get("unknown_elements", []),
        userdefined=spec.get("userdefined", []),
    )

    buf = io.BytesIO()
    # 全部用 STORED：避免 zlib 版本差异让“生成字节”在同一份样例上漂移。
    # 原生 writer 对资源用 DEFLATE，但目标 reader 不检查压缩方式。
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        if spec.get("include_mimetype", True):
            zf.writestr(_zip_info(MIMETYPE_PATH, stored=True), MIMETYPE)
        for entry in spec["entries"]:
            zf.writestr(_zip_info(PRESET_PREFIX + entry["name"]), entry["payload"])
        if spec.get("include_preview", False):
            zf.writestr(_zip_info(PREVIEW_PATH), PREVIEW_PNG)
        zf.writestr(_zip_info(MANIFEST_PATH), manifest.encode("utf-8"))
        if spec.get("include_meta", True):
            zf.writestr(_zip_info(META_PATH), meta.encode("utf-8"))
    return buf.getvalue()


def _element_text(node) -> str:
    """等价于 QDomElement::text()：拼接所有后代文本节点。"""
    parts: list[str] = []
    for child in node.childNodes:
        if child.nodeType == Node.TEXT_NODE:
            parts.append(child.data)
        elif child.nodeType == Node.ELEMENT_NODE:
            parts.append(_element_text(child))
    return "".join(parts)


def read_meta_like_krita(data: bytes) -> dict:
    """模拟 KoResourceBundle::readMetaData（不校验 URI，只认字面前缀）。"""
    result = {
        "accepted": False,
        "root": None,
        "reason": "",
        "fields": {},
        "bundle_tags": [],
        "warnings": [],
    }
    try:
        doc = minidom.parseString(data)
    except Exception as exc:  # noqa: BLE001 - 解析失败就是要落进报告，不吞掉原因
        result["reason"] = f"meta.xml 解析失败：{exc}"
        return result
    root = doc.documentElement
    result["root"] = root.tagName
    if root.tagName != "meta:meta":
        result["reason"] = (
            f"根元素必须是字面 meta:meta（目标源码 KoResourceBundle.cpp:327），"
            f"实际 {root.tagName!r}"
        )
        return result

    fields: dict[str, str] = {}
    bundle_tags: list[str] = []
    warnings: list[str] = []
    for node in root.childNodes:
        if node.nodeType != Node.ELEMENT_NODE:
            continue
        name = node.tagName
        value = _element_text(node)
        if name == "meta:meta-userdefined":
            name = node.getAttribute("meta:name")
            value = node.getAttribute("meta:value")
            if name == "tag":
                if value not in bundle_tags:
                    bundle_tags.append(value)
                continue
            if name not in {"email", "license", "website"}:
                warnings.append(f"未识别 meta-userdefined：name={name!r}")
            # 源码：m_metadata.insert(name, value); name = "meta:" + name
            fields.setdefault("meta:" + name, value)
            continue
        if name == "cd:creator":
            # 源码兼容旧包的 cd 前缀拼写
            name = "dc:creator"
        fields.setdefault(name, value)
    result.update(
        accepted=True,
        fields=fields,
        bundle_tags=bundle_tags,
        warnings=warnings,
    )
    return result


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_manifest_like_krita(data: bytes) -> dict:
    """模拟 KoResourceBundleManifest::load（命名空间感知 + 每预设 tags）。"""
    result = {
        "accepted": False,
        "root": None,
        "reason": "",
        "version_attr": None,
        "root_entry_present": False,
        "entries": [],
        "errors": [],
    }
    try:
        root = ET.fromstring(data)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"manifest.xml 解析失败：{exc}"
        return result
    result["root"] = root.tag
    if root.tag != f"{{{NS_MANIFEST}}}manifest":
        result["reason"] = (
            f"manifest 根命名空间必须为 {NS_MANIFEST}（KoResourceBundleManifest.cpp:82），"
            f"实际 {root.tag!r}"
        )
        return result
    result["accepted"] = True
    result["version_attr"] = root.get(f"{{{NS_MANIFEST}}}version")

    for child in root:
        if _local(child.tag) != "file-entry":
            continue
        full_path = child.get(f"{{{NS_MANIFEST}}}full-path")
        media_type = child.get(f"{{{NS_MANIFEST}}}media-type")
        md5sum = child.get(f"{{{NS_MANIFEST}}}md5sum")
        if full_path in (None, "/") and media_type == MIMETYPE:
            result["root_entry_present"] = True
            continue
        if full_path is None or media_type is None or md5sum is None:
            result["errors"].append(
                f"file-entry 缺必需属性：full-path={full_path!r} "
                f"media-type={media_type!r} md5sum={md5sum!r}"
            )
            continue
        tags: list[str] = []
        for tags_node in child:
            if _local(tags_node.tag) != "tags":
                continue
            for tag_node in tags_node:
                if _local(tag_node.tag) == "tag":
                    tags.append(tag_node.text or "")
        result["entries"].append(
            {
                "full_path": full_path,
                "media_type": media_type,
                "md5sum": md5sum,
                "tags": tags,
            }
        )
    return result


def _classify_field(name: str) -> str:
    if name.startswith("meta:custom-"):
        return UNKNOWN_META
    if name.startswith("meta:brushconverter") or name.startswith("meta:meta-userdefined"):
        return UNKNOWN_META
    return DISPLAYED_IN_BUNDLE_MANAGER.get(name) or STORED_ONLY.get(name) or UNKNOWN_META


def inspect_bundle_bytes(data: bytes, label: str) -> dict:
    """检查一份 bundle 字节；返回可直接打印/断言的报告。"""
    report = {
        "label": label,
        "target": TARGET_KRITA,
        "target_source_commit": TARGET_SOURCE_COMMIT,
        "zip_ok": False,
        "zip_error": "",
        "entries": [],
        "mimetype": {},
        "manifest": {},
        "meta": {},
        "presets": [],
        "would_load": False,
        "would_load_reasons": [],
        "brushconverter_invariants_ok": False,
        "invariant_failures": [],
        "package_tags_cannot_substitute": False,
        "kpp_parse_checked": False,
        "note": "源码模拟结果，不是 Krita 实机导入；实机统一留 187。",
    }
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        report["zip_error"] = f"不是有效 zip：{exc}"
        return report
    with zf:
        infos = zf.infolist()
        names = [info.filename for info in infos]
        report["zip_ok"] = True
        report["entries"] = [
            {
                "name": info.filename,
                "compress_type": info.compress_type,
                "size": info.file_size,
                "is_first": i == 0,
            }
            for i, info in enumerate(infos)
        ]
        first = infos[0] if infos else None
        mimetype_value = None
        if MIMETYPE_PATH in names:
            mimetype_value = zf.read(MIMETYPE_PATH).decode("utf-8", "replace")
        report["mimetype"] = {
            "present": MIMETYPE_PATH in names,
            "first": bool(first and first.filename == MIMETYPE_PATH),
            "stored": bool(first and first.filename == MIMETYPE_PATH
                           and first.compress_type == zipfile.ZIP_STORED),
            "value": mimetype_value,
            "value_ok": mimetype_value == MIMETYPE,
        }

        manifest_bytes = zf.read(MANIFEST_PATH) if MANIFEST_PATH in names else None
        meta_bytes = zf.read(META_PATH) if META_PATH in names else None
        preview_present = PREVIEW_PATH in names

    report["manifest"] = (
        read_manifest_like_krita(manifest_bytes)
        if manifest_bytes is not None
        else {"accepted": False, "reason": "缺 META-INF/manifest.xml", "entries": []}
    )
    report["meta"] = (
        read_meta_like_krita(meta_bytes)
        if meta_bytes is not None
        else {"accepted": False, "reason": "缺 meta.xml", "fields": {}, "bundle_tags": [], "warnings": []}
    )

    reasons: list[str] = []
    if manifest_bytes is None:
        reasons.append("缺 META-INF/manifest.xml：KoResourceBundle::load() 返回 false")
    elif not report["manifest"].get("accepted"):
        reasons.append("manifest 解析/根命名空间不符合：" + report["manifest"].get("reason", ""))
    if meta_bytes is None:
        reasons.append("缺 meta.xml：KoResourceBundle::load() 返回 false")
    elif not report["meta"].get("accepted"):
        reasons.append("meta.xml 不符合：" + report["meta"].get("reason", ""))
    report["would_load"] = not reasons
    report["would_load_reasons"] = reasons

    invariant_failures: list[str] = []
    mt = report["mimetype"]
    if not mt["present"]:
        invariant_failures.append("缺 mimetype（本项目写包不变量要求它在）")
    else:
        if not mt["first"]:
            invariant_failures.append("mimetype 不是首条目（本项目写包不变量）")
        if not mt["stored"]:
            invariant_failures.append("mimetype 被压缩（本项目写包不变量）")
        if not mt["value_ok"]:
            invariant_failures.append(f"mimetype 内容不是 {MIMETYPE}")
    if not report["manifest"].get("accepted"):
        invariant_failures.append("manifest 未通过读回检查")
    if not report["manifest"].get("root_entry_present"):
        invariant_failures.append("manifest 缺 / 根条目（原生 writer 会写；reader 不强制）")
    if not report["meta"].get("accepted"):
        invariant_failures.append("meta 根/字段未通过读回检查")

    # 逐预设检查：重新打开 zip 读字节（避免在 with 结束后使用已关闭的 zf）。
    presets = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf_read:
        for entry in report["manifest"].get("entries", []):
            if entry["media_type"] != "paintoppresets":
                continue
            exists = entry["full_path"] in names
            actual_md5 = ""
            md5_ok = None
            if exists:
                actual_md5 = hashlib.md5(zf_read.read(entry["full_path"])).hexdigest()
                md5_ok = actual_md5 == entry["md5sum"]
                if not md5_ok:
                    invariant_failures.append(f"{entry['full_path']} 的 md5 与声明不一致")
            else:
                invariant_failures.append(f"manifest 声明的条目不存在：{entry['full_path']}")
            presets.append(
                {
                    "full_path": entry["full_path"],
                    "exists": exists,
                    "md5sum_declared": entry["md5sum"],
                    "md5sum_actual": actual_md5,
                    "md5_ok": md5_ok,
                    "tags": list(entry["tags"]),
                }
            )
    report["presets"] = presets
    report["preview_present"] = preview_present

    bundle_tags = report["meta"].get("bundle_tags", [])
    report["bundle_tags"] = bundle_tags
    report["package_tags_cannot_substitute"] = bool(bundle_tags) and all(
        not p["tags"] for p in presets
    )

    fields = report["meta"].get("fields", {})
    report["meta_field_display"] = {
        name: _classify_field(name) for name in fields
    }
    report["unknown_meta_fields"] = [
        name for name in fields if _classify_field(name) == UNKNOWN_META
    ]

    report["brushconverter_invariants_ok"] = not invariant_failures
    report["invariant_failures"] = invariant_failures
    return report


def inspect_bundle_path(path: Path) -> dict:
    data = path.read_bytes()
    report = inspect_bundle_bytes(data, path.name)
    report["path"] = str(path)
    report["bytes"] = len(data)
    report["sha256"] = hashlib.sha256(data).hexdigest()
    return report


def _print_probe_text(report: dict) -> None:
    print(f"bundle: {report.get('path') or report['label']}")
    print(f"目标: {report['target']} / 源码 {report['target_source_commit']}")
    if not report["zip_ok"]:
        print(f"zip: 打不开：{report['zip_error']}")
        return
    mi = report["mimetype"]
    print(
        "mimetype: "
        f"present={mi['present']} first={mi['first']} stored={mi['stored']} "
        f"value_ok={mi['value_ok']}"
    )
    print(
        "manifest: "
        f"accepted={report['manifest'].get('accepted')} "
        f"entries={len(report['manifest'].get('entries', []))} "
        f"root_entry={report['manifest'].get('root_entry_present')}"
    )
    if report["manifest"].get("reason"):
        print(f"  reason: {report['manifest']['reason']}")
    print(
        "meta: "
        f"accepted={report['meta'].get('accepted')} "
        f"root={report['meta'].get('root')!r}"
    )
    if report["meta"].get("reason"):
        print(f"  reason: {report['meta']['reason']}")
    print(f"模拟 reader: would_load={report['would_load']}")
    for reason in report["would_load_reasons"]:
        print(f"  - {reason}")
    print(f"逐预设标签: {len(report['presets'])} 个预设")
    for preset in report["presets"]:
        print(
            f"  {preset['full_path']}: exists={preset['exists']} "
            f"md5_ok={preset['md5_ok']} tags={preset['tags']}"
        )
    print(f"包级标签（meta-userdefined name=tag）: {report.get('bundle_tags', [])}")
    if report["package_tags_cannot_substitute"]:
        print("  注意：只有包级标签、逐预设 tags 为空 → 不能代替逐笔关联。")
    print("字段去向（源码分类）:")
    for name, where in report.get("meta_field_display", {}).items():
        print(f"  {name}: {where}")
    if report["meta"].get("warnings"):
        print("meta 读取警告: " + "；".join(report["meta"]["warnings"]))
    if report["invariant_failures"]:
        print("本项目写包不变量失败:")
        for failure in report["invariant_failures"]:
            print(f"  - {failure}")
    else:
        print("本项目写包不变量: OK")
    print("NOTE: 以上是源码模拟，不是 Krita 实机导入；真实界面/筛选/重启留给 187。")


def _collect_xml_text(data: bytes) -> str:
    """只收集 XML 条目文本，用于“夹具无个人信息”自检（不扫 PNG 二进制）。"""
    chunks: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in (MANIFEST_PATH, META_PATH):
            if name in zf.namelist():
                chunks.append(zf.read(name).decode("utf-8", "replace"))
        chunks.extend(zf.namelist())
    return "\n".join(chunks)


PERSONAL_PATTERNS = [
    (r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", "盘符路径"),
    (r"/Users/", "用户目录"),
    (r"%USERPROFILE%", "用户目录变量"),
    (r"[\w.+-]+@[\w.-]+\.\w+", "邮箱"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "IP"),
]


def _personal_hits(data: bytes) -> list[str]:
    text = _collect_xml_text(data)
    hits = []
    for pattern, label in PERSONAL_PATTERNS:
        if re.search(pattern, text):
            hits.append(label)
    return hits


def run_selftest(fixtures_dir: Path) -> int:
    """逐条断言契约样例；返回退出码（0 通过 / 1 失败）。"""
    checks: list[tuple[bool, str]] = []

    def check(condition: bool, message: str) -> None:
        checks.append((bool(condition), message))

    # 1) 生成确定性 + 落盘一致性
    generated = {name: build_fixture(name) for name in FIXTURES}
    for name, first in generated.items():
        check(build_fixture(name) == first, f"{name} 两次生成字节相同")
        disk = fixtures_dir / name
        check(disk.exists(), f"{name} 存在磁盘夹具")
        if disk.exists():
            check(disk.read_bytes() == first, f"{name} 磁盘字节与生成规则一致")

    # 2) 正例
    native = inspect_bundle_bytes(generated["native_meta_meta.bundle"], "native")
    check(native["zip_ok"], "正例 zip 可打开")
    check(native["would_load"], "正例按源码规则会继续 load()")
    check(native["manifest"]["accepted"], "正例 manifest 命名空间/结构通过")
    check(native["meta"]["accepted"], "正例 meta 根 meta:meta 通过")
    check(native["mimetype"]["first"] and native["mimetype"]["stored"], "正例 mimetype 首项且 stored")
    check(len(native["presets"]) == 2, "正例有 2 个预设")
    tags_a = [p["tags"] for p in native["presets"] if p["full_path"].endswith("fixture-a.kpp")]
    check(tags_a == [["fixture-tag-a", "shared-tag"]], "正例 fixture-a 得到逐预设标签")
    check(all(p["md5_ok"] for p in native["presets"]), "正例 manifest md5 与字节一致")
    check(not native["bundle_tags"], "正例没有包级标签")

    # 3) 当前 bundle.py 的 office 根 → 源码 reader 拒绝
    office = inspect_bundle_bytes(generated["office_document_meta_root.bundle"], "office")
    check(not office["meta"]["accepted"], "office:document-meta 根被 reader 拒绝")
    check(not office["would_load"], "office 根导致 would_load=False")
    check("meta:meta" in office["meta"].get("reason", ""), "拒绝原因点名 meta:meta")

    # 4) 包级标签不能代替逐笔
    only = inspect_bundle_bytes(generated["package_tags_only.bundle"], "package")
    check(only["would_load"], "只写包级标签时结构仍可读")
    check(only["bundle_tags"] == ["package-tag", "shared-tag"], "包级标签被读入 m_bundletags")
    check(all(not p["tags"] for p in only["presets"]), "包级标签不会变成逐预设 tags")
    check(only["package_tags_cannot_substitute"], "报告判定“不能代替逐笔关联”")

    # 5) 未知字段兼容
    unknown = inspect_bundle_bytes(generated["unknown_fields.bundle"], "unknown")
    check(unknown["would_load"], "未知字段不影响 load()")
    check("meta:custom-note" in unknown["meta"]["fields"], "普通未知元素被保留")
    check("meta:brushconverter-trace" in unknown["meta"]["fields"], "meta-userdefined 未知项被保留")
    check(
        any("brushconverter-trace" in w for w in unknown["meta"]["warnings"]),
        "未知 meta-userdefined 产生读取警告",
    )
    trace_value = unknown["meta"]["fields"].get("meta:brushconverter-trace", "")
    try:
        json.loads(trace_value)
        json_ok = True
    except Exception:  # noqa: BLE001
        json_ok = False
    check(json_ok, "meta-userdefined 里的 JSON 值可原样取回")
    check(
        unknown["meta"]["fields"].get("meta:license") == "test-license",
        "原生 meta:license 先出现时保留原生值（首个优先）",
    )
    check(
        not any("license" in w for w in unknown["meta"]["warnings"]),
        "meta-userdefined name=license 走兼容路径，不报警",
    )
    check(unknown["presets"][0]["tags"] == ["fixture-tag-a"], "未知字段样例保留逐预设 tags")

    # 6) manifest 命名空间反例
    wrong = inspect_bundle_bytes(generated["manifest_wrong_namespace.bundle"], "wrong_ns")
    check(not wrong["manifest"]["accepted"], "错误 manifest 命名空间被拒绝")
    check(not wrong["would_load"], "错误 manifest 命名空间导致 would_load=False")

    # 7) 缺 meta
    missing = inspect_bundle_bytes(generated["missing_meta.bundle"], "missing_meta")
    check(not missing["would_load"], "缺 meta.xml 时 load() 失败")

    # 8) mimetype 不是 reader 的必需项（但仍是本项目不变量）
    no_mt = inspect_bundle_bytes(generated["no_mimetype.bundle"], "no_mimetype")
    check(no_mt["would_load"], "按目标源码，缺 mimetype 仍能 load()（reader 不校验）")
    check(not no_mt["brushconverter_invariants_ok"], "缺 mimetype 违反本项目写包不变量")
    check(any("mimetype" in x for x in no_mt["invariant_failures"]), "不变量失败点名 mimetype")

    # 9) md5 不一致：源码 load() 不校验，但本项目不变量必须失败
    entries = [{"name": "fixture-a.kpp", "payload": _fixture_a(), "tags": ["fixture-tag-a"]}]
    manifest = _manifest_xml(entries).replace(
        hashlib.md5(_fixture_a()).hexdigest(), "0" * 32
    )
    meta = _meta_xml(root_kind="meta", fields=[STANDARD_FIELDS[0], STANDARD_FIELDS[1]])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(_zip_info(MIMETYPE_PATH), MIMETYPE)
        zf.writestr(_zip_info(PRESET_PREFIX + "fixture-a.kpp"), _fixture_a())
        zf.writestr(_zip_info(MANIFEST_PATH), manifest.encode("utf-8"))
        zf.writestr(_zip_info(META_PATH), meta.encode("utf-8"))
    md5_bad = inspect_bundle_bytes(buf.getvalue(), "bad_md5")
    check(md5_bad["would_load"], "md5 错但结构可读时，源码 load() 仍继续（不校验 md5）")
    check(not md5_bad["brushconverter_invariants_ok"], "md5 错触发本项目不变量失败")
    check(any("md5" in x for x in md5_bad["invariant_failures"]), "不变量失败点名 md5")

    # 10) 夹具无个人信息
    for name, data in generated.items():
        hits = _personal_hits(data)
        check(not hits, f"{name} 无个人信息命中（{hits}）")

    # 11) 字段去向分类与源码面板一致
    check(native["meta_field_display"].get("meta:email") == "包管理器详情面板显示", "email 属可见字段")
    check(native["meta_field_display"].get("meta:license") == "包管理器详情面板显示", "license 属可见字段")
    check("仅保存在包内" in native["meta_field_display"].get("dc:title", ""), "title 属仅保存字段")

    passed = sum(1 for ok, _ in checks if ok)
    failed = len(checks) - passed
    for ok, message in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {message}")
    print(f"SELFTEST: {passed} passed, {failed} failed, {len(checks)} total")
    return 0 if failed == 0 else 1


def emit_fixtures(fixtures_dir: Path) -> int:
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for name in FIXTURES:
        data = build_fixture(name)
        (fixtures_dir / name).write_bytes(data)
        print(f"{name}\t{len(data)} B\tsha256={hashlib.sha256(data).hexdigest()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Krita .bundle 原生契约探针（任务 174；源码模拟，不启动 Krita）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python -B tools/probe_bundle_contract.py --selftest\n"
            "  python -B tools/probe_bundle_contract.py --probe <bundle> --json\n"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--selftest", action="store_true", help="运行内置断言（含夹具落盘一致性）")
    group.add_argument("--emit-fixtures", metavar="DIR", help="生成最小合成样例到指定目录")
    group.add_argument("--probe", metavar="BUNDLE", help="检查一份 .bundle 并打印读回结论")
    parser.add_argument("--json", action="store_true", help="--probe 时输出 JSON")
    parser.add_argument(
        "--fixtures",
        metavar="DIR",
        default=str(DEFAULT_FIXTURES),
        help="夹具目录（默认 tests/fixtures/bundle_contract；相对仓库根）",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest(Path(args.fixtures))
    if args.emit_fixtures:
        return emit_fixtures(Path(args.emit_fixtures))
    if args.probe:
        path = Path(args.probe)
        try:
            report = inspect_bundle_path(path)
        except OSError as exc:
            print(f"读不了 {path}：{exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_probe_text(report)
        return 0 if report["zip_ok"] else 2
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
