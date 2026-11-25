"""打包 Krita 资源包（.bundle）。

.bundle 是 ZIP 归档，布局（严格匹配 Krita KoResourceBundle 的写入格式）：
    mimetype                        （首项、stored 不压缩，application/x-krita-resourcebundle）
    META-INF/manifest.xml           （OpenDocument manifest 命名空间，逐预设在那里挂标签）
    meta.xml                        （OpenDocument meta；根必须是字面 meta:meta）
    paintoppresets/*.kpp            （笔刷预设，自包含内嵌笔尖）

v1.3.1 产物加固（使用者批准的 4 条之二/之三/之五）：
1. **字节确定性**：所有条目时间戳固定为 ZIP_DATE_TIME（1980-01-01 00:00:00），
   create_system 固定为 FAT(0)。zip 头里原本带「当前时间 + 宿主平台」，
   同一输入两次转换产出不同字节，导致「产物是否变化」无法用哈希判断。
2. **内容短哈希**：bundle_content_hash8() 只对「逻辑内容」做确定性哈希
   （条目名排序 + 每个条目名/字节 sha256），不受 zip 头时间戳影响，
   用作产物文件名的 hash8 与「同内容复用」判据。
3. **回读校验**：verify_bundle() 写盘后重开产物自查（zip 完整性、mimetype、
   manifest 与条目一致、每个 .kpp 可读出），并记录文件 sha256 与大小。

任务 175「元信息与标签写入」追加（依据 174 契约与目标源码 858d352）：
4. **meta 根改成字面 meta:meta**：Krita 的 readMetaData 按 tagName 字面比较根元素，
   旧的 office:document-meta 根会被拒绝；本模块不再写 office 外层。
5. **逐支标签写进 manifest**：每个 file-entry 下挂 manifest:tags/manifest:tag，
   这是 Krita 创建器实际使用、KisBundleStorage 逐文件建立关联的位置。
   包级标签（meta:meta-userdefined name=tag）是另一套字段，不能替代逐笔关联。
6. **包级扩展字段**：资源包版本与来源文件名没有原生字段，写进
   meta:brushconverter-package-version / meta:brushconverter-source；
   界面不显示、Krita 重存会丢，属于「仅包内」记录。
7. **回读校验扩展**：除原有 zip/mimetype/manifest/kpp 检查外，还查 meta 根与字段、
   manifest 每预设标签、zip 路径安全与重复条目；任何失败都落进 errors，不抛异常。
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from ..export_metadata import (
    BUNDLE_FORMAT_VERSION,
    BundleMetadata,
    check_xml_text,
    normalize_tags,
)
from .kpp_writer import read_preset_xml

MIMETYPE = "application/x-krita-resourcebundle"
NS_MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
NS_META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
NS_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
# Dublin Core 的规范 URI 以斜杠结尾，缺斜杠会落到另一个命名空间。
# Krita 自己写 meta.xml 时用无斜杠的变体，但 readMetaData 只按字面 tagName
# 取字段、不校验 URI（174 契约 5.2），两种写法在目标源码等价；
# 这里保留既有标准 URI，避免为一个不校验的字段制造第二套口径。
NS_DC = "http://purl.org/dc/elements/1.1/"
RES_TYPE = "paintoppresets"
#: bundle 内预设条目的目录前缀
PREFIX = RES_TYPE + "/"

#: meta.xml 根元素的字面标签名。Krita 用 QDomDocument::tagName() 直接比较它，
#: 所以即使命名空间写对了、字面前缀不对也会被拒绝。
META_ROOT_LITERAL = "meta:meta"

#: 无原生字段的资源包版本/来源文件名所用的扩展元素名（仅保存在包内）。
ELEM_PACKAGE_VERSION = "brushconverter-package-version"
ELEM_SOURCE_FILE = "brushconverter-source"

#: 固定的 zip 条目时间戳：1980-01-01 00:00:00 是 ZIP 格式能表示的最早时间。
#: 写死它才能保证「同一输入两次转换产出字节完全相同」（确定性）。
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

_MANIFEST = "META-INF/manifest.xml"
_META = "meta.xml"
_MIMETYPE_ENTRY = "mimetype"

#: md5sum 必须是 32 位小写 hex（174 契约 4.3；源码 fromHex 不校验长度）。
_MD5_PATTERN = re.compile(r"[0-9a-f]{32}")


def _zip_info(name: str, compress_type: int = zipfile.ZIP_DEFLATED) -> zipfile.ZipInfo:
    """构造时间戳/宿主平台都固定的 ZipInfo（确定性写入的唯一入口）。"""
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
    info.compress_type = compress_type
    # create_system 默认值随宿主平台变化（Windows=0、POSIX=3），写死 0 后
    # 跨平台也能得到同样的 zip 头字节；external_attr 同理清零。
    info.create_system = 0
    info.external_attr = 0
    return info


def _manifest_xml(
    entries: list[tuple[str, str] | tuple[str, str, tuple[str, ...]]],
) -> str:
    """生成 manifest.xml；entries 为 [(文件名, md5hex[, 最终标签])]。

    标签必须挂在每个 file-entry 自己下面：Krita 解析后用每组标签给对应
    文件建资源关联（KoResourceBundleManifest::parseFileEntry），包级标签
    只在 getTagsList() 里出现，不会关联到某一支笔刷。

    兼容：既有测试 `tests/test_review_fixes.py` 仍用两元组调用本函数；
    两元组等价于「该预设没有标签」，与 175 之前的写包结构一致。
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<manifest:manifest xmlns:manifest="{NS_MANIFEST}" manifest:version="1.2">',
        '<manifest:file-entry manifest:full-path="/" '
        f'manifest:media-type="{MIMETYPE}"/>',
    ]
    for entry in entries:
        # 兼容两元组（无标签）与三元组（含标签）两种调用
        fname, md5 = entry[0], entry[1]
        tags = entry[2] if len(entry) > 2 else ()
        # 文件名来自 ABR/用户，属性值必须转义（" & < 会破坏 XML）
        full_path = f"{RES_TYPE}/{fname}"
        attrs = (
            f'manifest:media-type="{RES_TYPE}" '
            f"manifest:full-path={quoteattr(full_path)} "
            f"manifest:md5sum={quoteattr(md5)}"
        )
        if not tags:
            lines.append(f"<manifest:file-entry {attrs}/>")
            continue
        lines.append(f"<manifest:file-entry {attrs}>")
        lines.append("<manifest:tags>")
        for tag in tags:
            # 标签是元素文本：& < > 必须转义；属性值才需要 quoteattr
            lines.append(f"<manifest:tag>{escape(tag)}</manifest:tag>")
        lines.append("</manifest:tags>")
        lines.append("</manifest:file-entry>")
    lines.append("</manifest:manifest>")
    return "".join(lines)


def _meta_xml(
    metadata: BundleMetadata | str, package_tags: Sequence[str] = ()
) -> str:
    """生成 OpenDocument meta.xml（根是字面 meta:meta）。

    只写显式字段：空的作者/许可/邮箱/网站/日期/包版本/来源文件名一律不出现，
    避免用空元素冒充「已知」，也不写本机身份或当前时间。

    兼容：既有测试 `tests/test_review_fixes.py` 仍用包名字符串调用本函数，
    等价于「只有包名」的 BundleMetadata；新代码应传 BundleMetadata。
    """
    if isinstance(metadata, str):
        metadata = BundleMetadata(name=metadata)

    def element(tag: str, value: str) -> str:
        return f"<{tag}>{escape(value)}</{tag}>"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        # 根就是 meta:meta：Krita 的 readMetaData 按字面 tagName 比较
        # （research/KoResourceBundle.cpp:327），外面再包 office:document-meta
        # 会被拒绝。meta/dc 前缀必须在根上声明，否则命名空间感知解析器报
        # unbound prefix（当前实现不包 office 外层，保留这条提醒）。
        f'<meta:meta xmlns:meta="{NS_META}" xmlns:dc="{NS_DC}">',
        element("meta:generator", metadata.generator),
        # 格式版本固定 1；不是产品版本，也不是资源包版本
        element("meta:bundle-version", BUNDLE_FORMAT_VERSION),
        element("dc:title", metadata.name),
    ]
    description = metadata.description_or_default()
    if description:
        lines.append(element("dc:description", description))
    if metadata.author:
        # dc:author 是 Krita 详情面板显示的「作者」；另外两个只保存在包内，
        # 与 Krita 自己的创建器在缺省时写作者名的行为保持一致。
        lines.append(element("dc:author", metadata.author))
        lines.append(element("dc:creator", metadata.author))
        lines.append(element("meta:initial-creator", metadata.author))
    for tag, value in (
        ("meta:creation-date", metadata.created),
        ("meta:dc-date", metadata.updated),
        ("meta:email", metadata.email),
        ("meta:license", metadata.license),
        ("meta:website", metadata.website),
        (f"meta:{ELEM_PACKAGE_VERSION}", metadata.package_version),
        (f"meta:{ELEM_SOURCE_FILE}", metadata.source_filename),
    ):
        if value:
            lines.append(element(tag, value))
    for tag in package_tags:
        # 包级标签：兼容旧包读法用；不写时也没有任何逐笔关联损失
        lines.append(
            f'<meta:meta-userdefined meta:name="tag" meta:value={quoteattr(tag)}/>'
        )
    lines.append("</meta:meta>")
    return "\n".join(lines)


def bundle_content_hash8(kpp_files: list[tuple[str, bytes]]) -> str:
    """bundle 的确定性逻辑内容哈希（取 sha256 前 8 位 hex）。

    口径（使用者批准）：bundle 内的预设条目按名字排序，逐个喂入
    「条目名 + 0x00 + sha256(条目字节).hexdigest()」，再对整串取 sha256 前 8 位。

    刻意不用「zip 文件字节的哈希」：zip 头里有时间戳/压缩器差异，
    同一内容换个时间写就会变哈希，既不能做「同内容复用」判据，
    也无法用它判断产物是否随版本变化。

    注意：当前口径只覆盖 .kpp 字节。任务 176 会把元信息/标签/追溯也纳入
    逻辑身份；本任务不改这里的哈希语义，避免提前替 176 拍板。
    """
    digest = hashlib.sha256()
    entries = sorted(((PREFIX + fname, data) for fname, data in kpp_files),
                     key=lambda item: item[0])
    for entry_name, data in entries:
        digest.update(entry_name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii"))
    return digest.hexdigest()[:8]


def bundle_content_hash8_of_file(bundle_path: str | Path) -> str | None:
    """读出已存在 bundle 的逻辑内容哈希；文件不可读/不是合法 bundle 时返回 None。

    用于「目标已存在」时的防呆判断：返回相同 hash8 ⇒ 同内容，可复用不重写；
    返回 None 或不同值 ⇒ 视为不同内容，绝不覆盖（调用方加 _2/_3 后缀）。
    """
    try:
        with zipfile.ZipFile(bundle_path) as zf:
            entries = [
                (info.filename[len(PREFIX):], zf.read(info.filename))
                for info in zf.infolist()
                if info.filename.startswith(PREFIX)
            ]
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return None
    except Exception:  # noqa: BLE001 - 任何解压/IO 异常都按「不是同内容」处理
        return None
    if not entries:
        return None
    return bundle_content_hash8(entries)


def _checked_preset_name(name: str) -> str:
    """校验预设文件名：它只允许是文件名，不能带路径（防 zip 路径穿越）。"""
    if not isinstance(name, str):
        raise TypeError(f"预设文件名必须是字符串，实际 {type(name).__name__}")
    if not name:
        raise ValueError("预设文件名不能为空")
    if name != name.strip():
        raise ValueError(f"预设文件名首尾不能有空白：{name!r}")
    if name in {".", ".."}:
        raise ValueError(f"预设文件名不能是 {name!r}")
    for char, desc in (("/", "目录分隔符 /"), ("\\", "目录分隔符 \\"), ("\x00", "NUL")):
        if char in name:
            raise ValueError(f"预设文件名不能包含{desc}：{name!r}")
    if re.match(r"^[A-Za-z]:", name):
        raise ValueError(f"预设文件名不能带盘符：{name!r}")
    return check_xml_text(name, field="预设文件名")


def _as_bytes(data: bytes, fname: str) -> bytes:
    """只接受 bytes/bytearray；str 或其它类型立刻报错，避免悄悄写坏条目。"""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"预设 {fname!r} 的内容必须是 bytes，实际 {type(data).__name__}")
    return bytes(data)


def _normalized_preset_tags(
    preset_tags: Mapping[str, Sequence[str]] | None, names: list[str]
) -> dict[str, tuple[str, ...]]:
    """把 {文件名: 标签} 归一；出现包内不存在的文件名时报错（防拼写错误静默丢标签）。"""
    if preset_tags is None:
        return {}
    if not isinstance(preset_tags, Mapping):
        raise TypeError("preset_tags 必须是 {预设文件名: 标签序列} 的映射")
    unknown = sorted(set(preset_tags) - set(names))
    if unknown:
        raise ValueError(
            "preset_tags 指定了不在本包内的预设：" + "、".join(repr(name) for name in unknown)
        )
    resolved: dict[str, tuple[str, ...]] = {}
    for name in names:
        tags = normalize_tags(preset_tags.get(name, ()))
        if tags:
            resolved[name] = tags
    return resolved


def build_bundle_bytes(
    kpp_files: list[tuple[str, bytes]],
    bundle_name: str,
    *,
    metadata: BundleMetadata | None = None,
    preset_tags: Mapping[str, Sequence[str]] | None = None,
    package_tags: Sequence[str] | None = None,
) -> bytes:
    """按固定布局生成 .bundle 字节（不落盘，供确定性比对/测试用）。

    Parameters
    ----------
    kpp_files : [(文件名, 文件字节)]，将放入 paintoppresets/；文件名不能重复或带路径。
    bundle_name : 包名兜底；metadata.name 为空时用它（write_bundle 传文件 stem）。
    metadata : BundleMetadata；None 表示只写包名 + 格式版本 + 产品默认说明。
    preset_tags : {预设文件名: 标签序列}；写入该预设自己的 manifest entry。
        标签统一走 export_metadata 的归一（去空/去重/稳定排序）。
    package_tags : 包级标签（meta:meta-userdefined name=tag）。默认不写：
        包级标签只进 m_bundletags，不能代替逐笔关联；仅在需要兼容旧包读法时显式传。
    """
    if not isinstance(bundle_name, str):
        raise TypeError(f"bundle_name 必须是字符串，实际 {type(bundle_name).__name__}")
    if not kpp_files:
        raise ValueError("资源包至少需要一个预设；空包不生成 .bundle")

    prepared: list[tuple[str, bytes]] = []
    for fname, data in kpp_files:
        prepared.append((_checked_preset_name(fname), _as_bytes(data, fname)))
    names = [name for name, _blob in prepared]
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    if duplicates:
        raise ValueError("预设文件名重复：" + "、".join(sorted(duplicates)))

    if metadata is None:
        metadata = BundleMetadata()
    # 显式 name 优先；否则用调用方给的包名（文件名 stem）。空包名直接报错，
    # 避免生成一个详情面板看不出名字的资源包。
    if not metadata.name:
        metadata = replace(metadata, name=metadata.resolved_name(bundle_name))

    tags_by_name = _normalized_preset_tags(preset_tags, names)
    normalized_package_tags = normalize_tags(package_tags)

    entries: list[tuple[str, str, tuple[str, ...]]] = [
        (fname, hashlib.md5(blob).hexdigest(), tags_by_name.get(fname, ()))
        for fname, blob in prepared
    ]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype 必须是第一个条目且不压缩（Krita 据此识别资源包类型）
        zf.writestr(_zip_info(_MIMETYPE_ENTRY, zipfile.ZIP_STORED), MIMETYPE)
        zf.writestr(_zip_info(_MANIFEST), _manifest_xml(entries))
        zf.writestr(_zip_info(_META), _meta_xml(metadata, normalized_package_tags))
        for fname, blob in prepared:
            zf.writestr(_zip_info(PREFIX + fname), blob)
    return buf.getvalue()


def write_bundle(
    bundle_path: str,
    kpp_files: list[tuple[str, bytes]],
    *,
    metadata: BundleMetadata | None = None,
    preset_tags: Mapping[str, Sequence[str]] | None = None,
    package_tags: Sequence[str] | None = None,
) -> str:
    """把 .kpp 文件打包为 .bundle。

    Parameters
    ----------
    bundle_path : 输出 .bundle 路径（文件名 stem 作为包名兜底）
    kpp_files : [(文件名, 文件字节)]，将放入 paintoppresets/ 目录
    metadata / preset_tags / package_tags : 见 build_bundle_bytes

    同输入两次调用产出字节完全相同（条目时间戳固定 + 压缩级别固定）。
    """
    data = build_bundle_bytes(
        kpp_files,
        Path(bundle_path).stem,
        metadata=metadata,
        preset_tags=preset_tags,
        package_tags=package_tags,
    )
    with open(bundle_path, "wb") as f:
        f.write(data)
    return bundle_path


@dataclass
class BundleVerifyResult:
    """verify_bundle() 的结论：可直接用于 CLI 打印、GUI 提示与测试断言。"""

    ok: bool = False
    preset_count: int = 0
    size: int = 0
    sha256: str = ""
    errors: list[str] = field(default_factory=list)
    #: 读回的包级字段，键为 Krita 字面 tagName（如 "dc:title"、"meta:license"）
    meta: dict[str, str] = field(default_factory=dict)
    #: 读回的逐预设标签：{预设文件名（不含 paintoppresets/）: 标签元组}
    preset_tags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: 读回的包级标签（meta:meta-userdefined name=tag）
    package_tags: tuple[str, ...] = ()

    def summary(self) -> str:
        """单行摘要（CLI「输出校验：…」用）。"""
        return f"{self.preset_count} 个预设，sha256={self.sha256}，大小={self.size}B"


_PREFIX_OF_NS = {NS_META: "meta", NS_DC: "dc", NS_OFFICE: "office"}


def _literal_tag_name(tag: str) -> str:
    """把 ElementTree 的 {uri}local 还原成 Krita 存的字面前缀名。"""
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    prefix = _PREFIX_OF_NS.get(uri)
    return f"{prefix}:{local}" if prefix else f"{{{uri}}}{local}"


def _root_literal_name(xml_text: str) -> str:
    """取 XML 声明后第一个元素的名字（保留前缀），用于核对 Krita 的字面判断。"""
    stripped = re.sub(r"^\ufeff?\s*<\?xml[^>]*\?>", "", xml_text, count=1).lstrip()
    match = re.match(r"<([A-Za-z_][\w.\-]*(?::[\w.\-]+)?)", stripped)
    return match.group(1) if match else ""


def _read_meta(xml_text: str) -> tuple[dict[str, str], tuple[str, ...], list[str]]:
    """按 readMetaData 的字面 tagName 规则读回 meta.xml。

    返回 (字段, 包级标签, 错误)。未知普通元素/未知 meta-userdefined 允许存在
    （176 的追溯会用到），这里不报错也不隐藏；重复字段报错，因为源码
    「先出现的值优先」会让重复值静默失效。
    """
    errors: list[str] = []
    root = ET.fromstring(xml_text)
    if root.tag != f"{{{NS_META}}}meta":
        errors.append(f"meta.xml 根命名空间应为 {NS_META}，实际 {root.tag!r}")
    fields: dict[str, str] = {}
    package: list[str] = []
    for child in root:
        if child.tag == f"{{{NS_META}}}meta-userdefined":
            name = child.get(f"{{{NS_META}}}name")
            value = child.get(f"{{{NS_META}}}value")
            if name is None or value is None:
                errors.append("meta-userdefined 缺少 meta:name 或 meta:value 属性")
                continue
            if name == "tag":
                if value in package:
                    errors.append(f"包级标签重复：{value!r}")
                else:
                    package.append(value)
                continue
            key = f"meta:{name}"
            if key in fields:
                errors.append(f"meta 字段重复：{key}")
                continue
            fields[key] = value
            continue
        key = _literal_tag_name(child.tag)
        if key in fields:
            errors.append(f"meta 字段重复：{key}")
            continue
        fields[key] = "".join(child.itertext())
    return fields, tuple(package), errors


@dataclass(frozen=True)
class _ManifestRecord:
    """manifest 里一个非根条目（属性与逐预设标签）。"""

    full_path: str
    media_type: str
    md5sum: str | None
    tags: tuple[str, ...]


def _read_manifest(xml_text: str) -> tuple[list[_ManifestRecord], bool, list[str]]:
    """按 KoResourceBundleManifest::load 的规则读回 manifest。

    返回 (条目, 是否有 "/" 根条目, 结构问题)。根命名空间不对直接抛 ValueError，
    与目标源码 load() 返回 false 等价；条目级问题先收集，由调用方决定是否失败。
    """
    root = ET.fromstring(xml_text)
    if root.tag != f"{{{NS_MANIFEST}}}manifest":
        raise ValueError(f"manifest 根命名空间应为 {NS_MANIFEST}，实际 {root.tag!r}")
    records: list[_ManifestRecord] = []
    errors: list[str] = []
    root_entry = False
    for child in root:
        if child.tag != f"{{{NS_MANIFEST}}}file-entry":
            continue
        full_path = child.get(f"{{{NS_MANIFEST}}}full-path")
        media_type = child.get(f"{{{NS_MANIFEST}}}media-type")
        md5sum = child.get(f"{{{NS_MANIFEST}}}md5sum")
        tags_nodes = child.findall(f"{{{NS_MANIFEST}}}tags")
        if len(tags_nodes) > 1:
            errors.append(
                f"{full_path or '<缺 full-path>'} 有多个 manifest:tags 块；"
                "Krita 只读第一个，后面的标签不会关联"
            )
        tags: list[str] = []
        for node in tags_nodes:
            for tag_node in node.findall(f"{{{NS_MANIFEST}}}tag"):
                tags.append("".join(tag_node.itertext()))
        if full_path == "/" and media_type == MIMETYPE:
            root_entry = True
            continue
        if not full_path or not media_type or md5sum is None:
            errors.append(
                "file-entry 缺必需属性："
                f"full-path={full_path!r} media-type={media_type!r} md5sum={md5sum!r}"
            )
            continue
        records.append(_ManifestRecord(full_path, media_type, md5sum, tuple(tags)))
    return records, root_entry, errors


def _unsafe_zip_name(name: str) -> str | None:
    """检查 zip 条目名是否可能越出解压目录；安全时返回 None。"""
    if not name:
        return "条目名为空"
    if "\x00" in name:
        return "条目名含 NUL"
    if "\\" in name:
        return "条目名用反斜杠，zip 规范要求 /"
    trimmed = name[:-1] if name.endswith("/") else name
    if trimmed.startswith("/"):
        return "条目名是绝对路径"
    if re.match(r"^[A-Za-z]:", trimmed):
        return "条目名带盘符"
    if any(part == ".." for part in trimmed.split("/")):
        return "条目名含 ..，可能越出解压目录"
    return None


def _check_tag_list(label: str, tags: Sequence[str], errors: list[str]) -> None:
    """标签结构检查：空标签与同组重复都要报（写出侧已归一，出现即异常）。"""
    seen: set[str] = set()
    for tag in tags:
        if not tag.strip():
            errors.append(f"{label} 含空标签")
        elif tag in seen:
            errors.append(f"{label} 标签重复：{tag!r}")
        seen.add(tag)


def verify_bundle(path: str | Path) -> BundleVerifyResult:
    """写出后回读校验 .bundle（v1.3.1 加固之五，任务 175 扩展 meta/标签）。

    逐条检查：
      1. zip 能打开；testzip() 无损坏条目；条目名不含路径穿越/反斜杠，且不重复；
      2. mimetype 是第一个条目、stored 不压缩、内容等于 MIMETYPE；
      3. meta.xml 存在、可解析、根是字面 meta:meta 且命名空间正确；
         meta:bundle-version 是格式版本 1、dc:title 非空、无重复字段；
         读回包级字段、包级标签与扩展字段（包版本/来源文件名）；
      4. manifest 的预设条目数 == zip 里 paintoppresets/ 条目数，每条声明的
         md5sum 是 32 位小写 hex 与实际字节一致、条目确实存在且都被声明；
         逐预设标签读回到 result.preset_tags，空标签/重复标签报错；
      5. 每个 .kpp 能完整读出（PNG 容器 + iTXt 预设 XML，含块 CRC）。
    最后记录整文件 sha256 与字节数。

    不抛异常：任何失败都落进 errors，由调用方决定退出码/提示，
    避免「校验本身崩掉」被误当成通过。
    """
    target = Path(path)
    result = BundleVerifyResult()
    try:
        data = target.read_bytes()
    except OSError as exc:
        result.errors.append(f"无法读取产物：{exc}")
        return result
    result.size = len(data)
    result.sha256 = hashlib.sha256(data).hexdigest()

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        result.errors.append(f"不是有效的 zip 归档：{exc}")
        return result

    with zf:
        infos = zf.infolist()
        names = [info.filename for info in infos]
        kpp_names = [name for name in names if name.startswith(PREFIX)]
        result.preset_count = len(kpp_names)

        # 0) 条目名安全与重复（重复条目会让「读到哪个」不确定）
        for name in names:
            reason = _unsafe_zip_name(name)
            if reason:
                result.errors.append(f"zip 条目名不安全：{name!r}（{reason}）")
        for name in sorted(name for name, count in Counter(names).items() if count > 1):
            result.errors.append(f"zip 条目名重复：{name}")

        # 1) zip 完整性（截断文件可能在 testzip 内部就抛，这里一并兜住）
        try:
            broken = zf.testzip()
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"zip 完整性校验异常：{exc}")
            broken = None
        if broken is not None:
            result.errors.append(f"zip 条目 CRC 校验失败：{broken}")

        # 2) mimetype（Krita 靠「首项 + 不压缩 + 精确内容」识别资源包）
        if not infos:
            result.errors.append("zip 为空，没有任何条目")
        else:
            first = infos[0]
            if first.filename != _MIMETYPE_ENTRY:
                result.errors.append(
                    f"首条目不是 mimetype（实际 {first.filename}）")
            elif first.compress_type != zipfile.ZIP_STORED:
                result.errors.append("mimetype 条目被压缩（Krita 要求 stored）")
            try:
                mimetype = zf.read(_MIMETYPE_ENTRY).decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"读不出 mimetype：{exc}")
            else:
                if mimetype != MIMETYPE:
                    result.errors.append(f"mimetype 内容不正确：{mimetype!r}")

        # 3) meta.xml：根、字段、包级标签
        if _META not in names:
            result.errors.append(f"缺少 {_META}")
        else:
            try:
                meta_text = zf.read(_META).decode("utf-8")
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"读不出 meta.xml（不是 UTF-8？）：{exc}")
            else:
                literal = _root_literal_name(meta_text)
                if literal != META_ROOT_LITERAL:
                    result.errors.append(
                        f"meta.xml 根元素必须是字面 {META_ROOT_LITERAL}"
                        f"（Krita 按 tagName 字面判断），实际 {literal!r}")
                try:
                    fields, package_tags, meta_errors = _read_meta(meta_text)
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"meta.xml 解析失败：{exc}")
                else:
                    result.meta = fields
                    result.package_tags = package_tags
                    result.errors.extend(meta_errors)
                    version = fields.get("meta:bundle-version")
                    if version is None:
                        result.errors.append("meta.xml 缺少 meta:bundle-version")
                    elif version != BUNDLE_FORMAT_VERSION:
                        result.errors.append(
                            "meta:bundle-version 必须是格式版本 "
                            f"{BUNDLE_FORMAT_VERSION}（不是产品/资源包版本），"
                            f"实际 {version!r}")
                    if not fields.get("dc:title"):
                        result.errors.append("meta.xml 缺少 dc:title（包名）")

        # 4) manifest 与实际条目一一对应，并读回逐预设标签
        if _MANIFEST not in names:
            result.errors.append(f"缺少 {_MANIFEST}")
            declared: list[_ManifestRecord] = []
        else:
            try:
                manifest_text = zf.read(_MANIFEST).decode("utf-8")
                declared, root_entry, manifest_errors = _read_manifest(manifest_text)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"manifest 解析失败：{exc}")
                declared, root_entry, manifest_errors = [], False, []
            result.errors.extend(manifest_errors)
            if not root_entry:
                result.errors.append(
                    'manifest 缺少 full-path="/" 的根条目（原生 writer 会写）')

            seen_paths: set[str] = set()
            for record in declared:
                if record.full_path in seen_paths:
                    result.errors.append(
                        f"manifest 声明了重复条目：{record.full_path}")
                seen_paths.add(record.full_path)
                md5sum = record.md5sum or ""
                if not _MD5_PATTERN.fullmatch(md5sum):
                    result.errors.append(
                        f"manifest md5sum 不是 32 位小写 hex：{record.full_path}"
                        f"（实际 {md5sum!r}）")
                if record.full_path.startswith(PREFIX):
                    if record.media_type != RES_TYPE:
                        result.errors.append(
                            f"预设条目的 media-type 必须是 {RES_TYPE}，"
                            f"实际 {record.media_type!r}：{record.full_path}")
                    # 去重后的标签按文件关联；同一支的标签不能串到别的预设
                    bare = record.full_path[len(PREFIX):]
                    result.preset_tags[bare] = record.tags
                    _check_tag_list(f"预设 {bare} 的标签", record.tags, result.errors)
                if record.full_path not in names:
                    result.errors.append(
                        f"manifest 声明了不存在的条目：{record.full_path}")
                    continue
                try:
                    actual = hashlib.md5(zf.read(record.full_path)).hexdigest()
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"读不出条目 {record.full_path}：{exc}")
                    continue
                if md5sum and actual != md5sum:
                    result.errors.append(
                        f"条目 md5 与 manifest 不一致：{record.full_path}")

            declared_presets = [
                record for record in declared if record.full_path.startswith(PREFIX)]
            if len(declared_presets) != len(kpp_names):
                result.errors.append(
                    f"manifest 预设条目数（{len(declared_presets)}）与 zip 预设条目数"
                    f"（{len(kpp_names)}）不一致")
            for name in kpp_names:
                if name not in seen_paths:
                    result.errors.append(f"zip 预设未在 manifest 声明：{name}")

        # 5) 每个 .kpp 可读出
        for name in kpp_names:
            try:
                blob = zf.read(name)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"预设 {name} 读不出：{exc}")
                continue
            try:
                read_preset_xml(blob)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"预设 {name} 结构损坏：{exc}")

    result.ok = not result.errors
    return result
