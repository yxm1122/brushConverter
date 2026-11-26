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
   用作产物文件名的 hash8；任务182补充起复用判定改用完整身份，8 位
   hash8 只用于命名/人工核对，不得单独作为同包依据。
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

任务 176「追溯与资源包身份」追加（依据 174 契约 8.2 方案 A 与任务书）：
8. **包内追溯**：meta-userdefined name=brushconverter-trace 存紧凑 JSON，记录源文件名/
   可复算摘要、真实程序版本（__version__，候选阶段不谎写 1.6.0）、逐支稳定原始 ID、
   实际开关处理与编辑代价。界面不显示、Krita 重存会丢（174 契约 8.3）。
9. **新版逻辑身份（identity_format=2）**：覆盖 meta.xml（元信息/包级标签/追溯）、
   manifest.xml（逐支标签/md5）与每个 .kpp 原始字节；不写当前时间、不读本机身份；
   包内不存自身哈希，因此没有哈希自引用；文件名里的 hash8 只用于命名，不参与计算。
10. **旧包兼容**：无 trace 的旧包按 kpp-only 口径标记 scheme=legacy 读回，不参与新版
   同包复用判定；verify_bundle 对旧包给出 note，对新包校验追溯关联、标签与声明摘要。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .. import __version__
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


# ---------------------------------------------------------------------------
# 任务 176：包内追溯（brushconverter-trace）与新版逻辑身份（identity_format=2）
#
# 对应任务书四条：
# 1. 追溯按稳定原始 ID 记录源文件名与可复算摘要、真实程序版本（__version__，
#    候选阶段不谎写未发布的 1.6.0）、逐支实际开关处理与编辑代价（reason_texts）。
# 2. 逻辑身份覆盖 meta.xml（全部元信息/包级标签/追溯）、manifest.xml（逐支标签/
#    md5）与每个 .kpp 原始字节；不写当前时间、不读本机身份；包内不写自身哈希，
#    因此不存在哈希自引用；最终文件名里的 hash8 只用于命名，不参与身份计算。
# 3. 只改说明/许可/来源标签/单支标签/开关 → 上述内容变化 ⇒ 身份变化；
#    同源同配置重复导出字节确定；旧包按 scheme=legacy 明确读回，绝不用 kpp-only
#    哈希判为「同包」。
# 4. verify_bundle 对 trace 做关联校验（文件名、声明 sha256、标签），并由
#    convert.py 的双路径写出（169 事务）继续保证「不静默覆盖、同名原文件
#    mtime 不变」。
#
# 为什么不把身份哈希写进包：写进去就要么把哈希本身纳入计算（自引用、永不收敛），
# 要么在计算时特殊排除该字段（两套口径容易漂移）。本实现选择包内只存可复算的
# 分项摘要（源摘要、逐支 kpp sha256、reason 文本哈希），身份由这些内容整体导出。
# ---------------------------------------------------------------------------

#: 追溯 JSON 的 meta-userdefined 名称（174 契约 8.2 方案 A）。
TRACE_META_NAME = "brushconverter-trace"
#: 追溯自身格式与版本（与产品版本、.bundle 格式版本无关）。
TRACE_FORMAT = "brushconverter.bundle-trace"
TRACE_VERSION = 1
#: 新版逻辑身份格式版本；旧包没有 trace，按 legacy 读回。
IDENTITY_FORMAT = 2
#: 生成器名与发布状态：产品版本 bump/发布由任务 181/188 决定，
#: 候选阶段固定写 unreleased，绝不冒充「已发布 1.6.0」。
PRODUCER_NAME = "brushConverter"
RELEASE_STATE_UNRELEASED = "unreleased"

#: 任务 167 冻结的三项开关 ID 与 actual 取值。这里不 import export_options：
#: kpp/__init__ 会先导入本模块，反向 import 会形成循环；tests/test_bundle_identity.py
#: 断言本文件的两个元组与 export_options 的冻结值完全一致（单一来源由测试守住）。
_TRACE_SWITCH_IDS = ("rasterize_computed", "prescale_texture", "bake_texture_bc")
_TRACE_ACTUAL_VALUES = frozenset({
    "applied",
    "alternative",
    "not_applicable",
    "not_processed",
    "pending_alternative",
})
#: 稳定原始 ID 的格式（bc1-源摘要12-原始序号-内容摘要8），与 export_options 一致。
_STABLE_ID_PATTERN = re.compile(r"^bc1-[0-9a-f]{12}-[0-9]{4,}-[0-9a-f]{8}$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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
    metadata: BundleMetadata | str,
    package_tags: Sequence[str] = (),
    trace_value: str | None = None,
) -> str:
    """生成 OpenDocument meta.xml（根是字面 meta:meta）。

    只写显式字段：空的作者/许可/邮箱/网站/日期/包版本/来源文件名一律不出现，
    避免用空元素冒充「已知」，也不写本机身份或当前时间。

    trace_value 非空时额外写一条 meta-userdefined name=brushconverter-trace
    （任务176 的包内追溯；174 契约 8.2 方案 A）。它是机器记录，界面不显示，
    Krita 重新保存时会丢；不得塞进 dc:description 冒充人类说明。

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
    if trace_value:
        # 追溯 JSON 是属性值：quoteattr 会转义引号/&/<；紧凑 JSON 不含换行。
        # 放在固定字段之后、包级标签之后，保证同输入字节顺序确定。
        lines.append(
            f'<meta:meta-userdefined meta:name="{TRACE_META_NAME}" '
            f"meta:value={quoteattr(trace_value)}/>"
        )
    lines.append("</meta:meta>")
    return "\n".join(lines)


@dataclass(frozen=True)
class SwitchTrace:
    """一支笔刷一项开关的**本次实际处理**记录（actual 不是勾选快照）。

    actual 取值沿用任务167 冻结的五个值；reason 是该次处理的真实说明
    （现行处理/替代路径的编辑代价或不适用原因），由 build_bundle 转成
    可复算的 sha256 引用存进包内。
    """

    switch_id: str
    applicable: bool
    enabled: bool
    actual: str
    limited: bool = False
    reason: str = ""


@dataclass(frozen=True)
class PresetTrace:
    """包内逐支追溯：稳定原始 ID + 文件关联 + 三项开关实际处理。

    - preset_id：任务167 的 bc1 稳定 ID；直接 API 没有源摘要时为 None（显式留空，
      不伪造）。
    - ordinal：源顺序序号（不是筛选后的行号）；与 preset_id 内的序号一致。
    - kpp：本包内的预设文件名（与 kpp_files 顺序逐一对应）。
    """

    kpp: str
    ordinal: int
    name: str
    preset_id: str | None = None
    switches: tuple[SwitchTrace, ...] = ()


@dataclass(frozen=True)
class BundleTrace:
    """整包追溯输入。摘要、逐支标签与 reason 哈希由 build_bundle 从实际字节复算，
    不接受调用方自报，避免「声明摘要」与产物不一致。

    - source_filename：源文件名本身（不含目录/盘符）；调用方没给路径时留空。
    - source_stem：ABR 解析出的文件主干名，便于人回溯；同样不含路径。
    - source_digest：源文件原始字节 sha256（sha256:<64hex>）；无法取得时为
      空串，trace 里 bound=false，绝不拿别的摘要顶替。
    - producer_version：留空则使用程序真实 __version__（候选阶段不写 1.6.0）。
    """

    presets: tuple[PresetTrace, ...]
    source_filename: str = ""
    source_stem: str = ""
    source_digest: str = ""
    producer_version: str = ""


@dataclass(frozen=True)
class BundleIdentity:
    """资源包逻辑身份，**显式分两套口径**：

    - scheme="v2"：任务176 新版身份，覆盖 meta/标签/追溯/kpp；sha256 是完整摘要，
      hash8 取前 8 位；
    - scheme="legacy"：没有 trace 的旧口径（只覆盖 .kpp 字节），sha256 留空，
      hash8 是旧 bundle_content_hash8 的结果。

    两者绝不互相比较；调用方先看 scheme。旧包继续可读，但不会被误判成新版同包。
    """

    scheme: str = "v2"
    sha256: str = ""
    legacy_hash8: str = ""
    identity_format: int = IDENTITY_FORMAT

    @property
    def hash8(self) -> str:
        if self.scheme == "v2":
            return self.sha256[:8]
        return self.legacy_hash8

    def summary(self) -> str:
        """单行机器摘要（报告/日志用）。"""
        if self.scheme == "v2":
            return f"identity v{self.identity_format} sha256={self.sha256}"
        return f"legacy identity hash8={self.legacy_hash8}"


@dataclass(frozen=True)
class BundleBuild:
    """一次构建的产物：字节 + 可比较的身份 + 已写入包内的追溯字典。"""

    data: bytes
    identity: BundleIdentity
    trace: dict | None
    trace_json: str = ""


@dataclass(frozen=True)
class BundleIdentityRead:
    """回读既有 .bundle 的身份结论（不抛异常）。"""

    scheme: str = "invalid"  # "v2" | "legacy" | "invalid"
    hash8: str = ""
    sha256: str = ""
    trace: dict | None = None
    error: str = ""

    @property
    def reusable(self) -> bool:
        """只有 v2 身份可以参与新版「同内容复用」判定。"""
        return self.scheme == "v2"


def _checked_trace_text(value: object, field: str, *, allow_empty: bool = True) -> str:
    """追溯文本：去首尾空白、拒绝 XML 1.0 非法字符；空值是否允许由调用方决定。"""
    if not isinstance(value, str):
        raise TypeError(f"{field} 必须是字符串，实际 {type(value).__name__}")
    text = value.strip()
    if not text and not allow_empty:
        raise ValueError(f"{field} 不能为空")
    return check_xml_text(text, field=field)


def _checked_trace_filename(value: object, field: str) -> str:
    """来源文件名/主干：只允许文件名本身，不允许目录、盘符、NUL 或 ..。"""
    text = _checked_trace_text(value, field)
    if not text:
        return ""
    if text in {".", ".."} or any(ch in text for ch in ("/", "\\", ":", "\x00")):
        raise ValueError(
            f"{field} 只能记文件名本身，不能包含目录、盘符或路径分隔符：{text!r}")
    if re.match(r"^[A-Za-z]:", text):
        raise ValueError(f"{field} 不能带盘符：{text!r}")
    return text


def _checked_trace_digest(value: object, field: str) -> str:
    """源内容摘要：空串（未绑定）或 sha256:<64位小写hex>。"""
    text = _checked_trace_text(value, field)
    if not text:
        return ""
    if not _DIGEST_PATTERN.fullmatch(text):
        raise ValueError(f"{field} 必须是 sha256:<64位小写hex> 或留空，实际 {text!r}")
    return text


def _reason_key(text: str) -> str:
    """处理说明文本 → 可复算的 sha256 引用（包内 reason_texts 的键）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_trace_document(
    trace: BundleTrace,
    prepared: list[tuple[str, bytes]],
    tags_by_name: Mapping[str, tuple[str, ...]],
) -> tuple[str, dict]:
    """把追溯输入变成确定性 JSON（紧凑、键排序、无时间/无本机身份）。

    摘要与标签从 prepared / 归一后的标签复算，不读取调用方自报值；
    返回 (JSON 文本, 解析后的 dict)，二者内容一致，便于测试与调用方复用。
    """
    if not isinstance(trace, BundleTrace):
        raise TypeError(f"trace 必须是 BundleTrace，实际 {type(trace).__name__}")
    if len(trace.presets) != len(prepared):
        raise ValueError(
            f"追溯条目数（{len(trace.presets)}）必须与预设数（{len(prepared)}）一致")
    source_filename = _checked_trace_filename(trace.source_filename, "追溯源文件名")
    source_stem = _checked_trace_filename(trace.source_stem, "追溯源文件名主干")
    source_digest = _checked_trace_digest(trace.source_digest, "追溯源内容摘要")
    producer_version = _checked_trace_text(
        trace.producer_version or __version__, "追溯程序版本", allow_empty=False)

    reason_texts: dict[str, str] = {}
    presets_json: list[dict] = []
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    for item, (fname, blob) in zip(trace.presets, prepared):
        if not isinstance(item, PresetTrace):
            raise TypeError("trace.presets 只能包含 PresetTrace")
        if item.kpp != fname:
            raise ValueError(
                "追溯顺序必须与预设顺序一致：追溯第 "
                f"{len(presets_json) + 1} 条是 {item.kpp!r}，实际是 {fname!r}")
        if type(item.ordinal) is not int or item.ordinal < 1:
            raise ValueError(f"追溯原始序号必须是从 1 开始的整数，实际 {item.ordinal!r}")
        if item.ordinal in seen_ordinals:
            raise ValueError(f"追溯原始序号重复：{item.ordinal}")
        seen_ordinals.add(item.ordinal)
        name = _checked_trace_text(item.name, "追溯预设名")
        preset_id = None
        if item.preset_id:
            preset_id = _checked_trace_text(item.preset_id, "追溯稳定 ID")
            if not _STABLE_ID_PATTERN.fullmatch(preset_id):
                raise ValueError(f"追溯稳定 ID 格式不合法：{preset_id!r}")
            if preset_id in seen_ids:
                raise ValueError(f"追溯稳定 ID 重复：{preset_id}")
            seen_ids.add(preset_id)

        by_id: dict[str, SwitchTrace] = {}
        for switch in item.switches:
            if not isinstance(switch, SwitchTrace):
                raise TypeError("PresetTrace.switches 只能包含 SwitchTrace")
            if switch.switch_id not in _TRACE_SWITCH_IDS:
                raise ValueError(f"追溯里出现未知开关 ID：{switch.switch_id!r}")
            if switch.switch_id in by_id:
                raise ValueError(f"追溯里开关重复：{switch.switch_id}")
            by_id[switch.switch_id] = switch
        if set(by_id) != set(_TRACE_SWITCH_IDS):
            raise ValueError(
                "每支追溯必须完整记录三项开关：" + "、".join(_TRACE_SWITCH_IDS))

        switches_json: dict[str, dict] = {}
        for switch_id in _TRACE_SWITCH_IDS:
            switch = by_id[switch_id]
            if type(switch.applicable) is not bool or type(switch.enabled) is not bool:
                raise TypeError(f"追溯开关 {switch_id} 的 applicable/enabled 必须是布尔")
            if type(switch.limited) is not bool:
                raise TypeError(f"追溯开关 {switch_id} 的 limited 必须是布尔")
            if not isinstance(switch.actual, str) or switch.actual not in _TRACE_ACTUAL_VALUES:
                raise ValueError(
                    f"追溯开关 {switch_id} 的 actual 不受支持：{switch.actual!r}")
            if not switch.applicable and switch.enabled:
                raise ValueError(f"追溯开关 {switch_id} 不适用却被记为勾选")
            # 「编辑代价」必须实际记录：每项开关都要求非空说明（现行处理/替代
            # 路径的代价或不适用原因），转成可复算哈希后存进 reason_texts。
            reason = _checked_trace_text(
                switch.reason, f"追溯开关 {switch_id} 的处理说明", allow_empty=False)
            reason_ref = _reason_key(reason)
            previous = reason_texts.get(reason_ref)
            if previous is not None and previous != reason:
                raise ValueError("追溯 reason 文本哈希冲突（极罕见，请报告维护者）")
            reason_texts[reason_ref] = reason
            switches_json[switch_id] = {
                "applicable": switch.applicable,
                "enabled": switch.enabled,
                "actual": switch.actual,
                "limited": switch.limited,
                "reason": reason_ref,
            }
        presets_json.append({
            "id": preset_id,
            "ordinal": item.ordinal,
            "name": name,
            "kpp": fname,
            "kpp_sha256": hashlib.sha256(blob).hexdigest(),
            "tags": list(tags_by_name.get(fname, ())),
            "switches": switches_json,
        })

    document = {
        "format": TRACE_FORMAT,
        "version": TRACE_VERSION,
        "identity_format": IDENTITY_FORMAT,
        "producer": {
            "name": PRODUCER_NAME,
            "version": producer_version,
            # 候选阶段固定 unreleased；产品版本/发布批准由 181/188 决定。
            "release_state": RELEASE_STATE_UNRELEASED,
        },
        "source": {
            "filename": source_filename,
            "stem": source_stem,
            "digest": source_digest,
            "bound": bool(source_digest),
            "preset_count": len(presets_json),
        },
        "presets": presets_json,
        "reason_texts": reason_texts,
    }
    text = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False)
    return text, document


#: 身份计算的固定域分隔盐；改它等于换身份格式，必须同步升 IDENTITY_FORMAT。
_IDENTITY_SALT = b"brushconverter.bundle-identity"


def _identity_of_parts(
    meta_text: str, manifest_text: str, prepared: list[tuple[str, bytes]]
) -> BundleIdentity:
    """按「meta.xml 字节 + manifest.xml 字节 + 每个 .kpp 原始字节」计算确定性身份。

    刻意不包含：zip 头时间戳/create_system、输出文件名/路径、包内任何自身哈希。
    meta.xml 覆盖全部元信息、包级标签与追溯；manifest.xml 覆盖逐支标签与 md5；
    prepared 覆盖每个预设的原始字节。三者任一变化 ⇒ 身份变化。
    """
    digest = hashlib.sha256()
    digest.update(_IDENTITY_SALT)
    digest.update(str(IDENTITY_FORMAT).encode("ascii"))
    for label, payload in (
        ("meta.xml", meta_text.encode("utf-8")),
        ("META-INF/manifest.xml", manifest_text.encode("utf-8")),
    ):
        digest.update(b"\x00")
        digest.update(label.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(payload).digest())
    for fname, blob in sorted(prepared, key=lambda item: PREFIX + item[0]):
        digest.update(b"\x00")
        digest.update((PREFIX + fname).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(blob).digest())
    return BundleIdentity(scheme="v2", sha256=digest.hexdigest())


def bundle_content_hash8(kpp_files: list[tuple[str, bytes]]) -> str:
    """**旧口径**：只覆盖 .kpp 字节的兼容哈希（sha256 前 8 位 hex）。

    任务176 起，生产路径改用 _identity_of_parts()/build_bundle().identity
    （覆盖元信息/标签/追溯）。本函数保留给：
    - 旧包（无 brushconverter-trace）的兼容读回与历史调用方；
    - 既有 v1.3.1 加固用例的字节口径对照。

    绝不能用它判断「新版包是否同内容」：只改说明/许可/标签时它不会变，
    会把不同包误判为同包（任务176 要消灭的正是这个误判）。新代码请用
    bundle_identity_of_file() 并显式区分 scheme（v2 / legacy）。
    """
    digest = hashlib.sha256()
    entries = sorted(((PREFIX + fname, data) for fname, data in kpp_files),
                     key=lambda item: item[0])
    for entry_name, data in entries:
        digest.update(entry_name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii"))
    return digest.hexdigest()[:8]


def bundle_identity_of_file(bundle_path: str | Path) -> BundleIdentityRead:
    """回读既有 .bundle 的身份结论（不抛异常）。

    明确的两条兼容规则（任务176 第 3 条）：

    - **v2**：meta 里有 brushconverter-trace 且 identity_format==2 ⇒ 按
      「meta + manifest + 全部 kpp 原始字节」重算身份，可参与新版同包复用；
    - **legacy**：没有 trace（175 及更早产物）⇒ 只按旧 kpp-only 口径给出
      hash8 供人核对，scheme=legacy，**不得**与 v2 身份比较后复用；
    - **invalid**：zip 坏、trace 坏、identity_format 不受支持或没有预设 ⇒
      hash8 为空，同样不得复用。

    调用方必须先看 scheme，再决定是否复用（convert.py 对 legacy 会主动顺延）。
    """
    try:
        data = Path(bundle_path).read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            meta_text = zf.read(_META).decode("utf-8") if _META in names else None
            trace_value = None
            if meta_text is not None:
                try:
                    fields, _package, _meta_errors = _read_meta(meta_text)
                except Exception:  # noqa: BLE001 - 读不出 meta 就按旧包/坏包处理
                    fields = {}
                trace_value = fields.get(f"meta:{TRACE_META_NAME}")
            if trace_value is not None:
                try:
                    trace = _parse_trace_json(trace_value)
                except Exception as exc:  # noqa: BLE001
                    return BundleIdentityRead(
                        scheme="invalid", error=f"追溯 JSON 解析失败：{exc}")
                if (trace.get("format") != TRACE_FORMAT
                        or trace.get("version") != TRACE_VERSION
                        or trace.get("identity_format") != IDENTITY_FORMAT):
                    return BundleIdentityRead(
                        scheme="invalid", error="追溯身份格式不受支持")
                if _MANIFEST not in names:
                    return BundleIdentityRead(
                        scheme="invalid", error="缺少 manifest，无法计算新版身份")
                manifest_text = zf.read(_MANIFEST).decode("utf-8")
                prepared = [
                    (name[len(PREFIX):], zf.read(name))
                    for name in names
                    if name.startswith(PREFIX)
                ]
                if not prepared:
                    return BundleIdentityRead(scheme="invalid", error="没有预设条目")
                identity = _identity_of_parts(meta_text, manifest_text, prepared)
                return BundleIdentityRead(
                    scheme="v2", hash8=identity.hash8, sha256=identity.sha256,
                    trace=trace)
            prepared = [
                (name[len(PREFIX):], zf.read(name))
                for name in names
                if name.startswith(PREFIX)
            ]
            if not prepared:
                return BundleIdentityRead(scheme="invalid", error="没有预设条目")
            return BundleIdentityRead(
                scheme="legacy", hash8=bundle_content_hash8(prepared))
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError) as exc:
        return BundleIdentityRead(scheme="invalid", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - 任何解压/IO 异常都按「不可复用」处理
        return BundleIdentityRead(scheme="invalid", error=str(exc))


def bundle_content_hash8_of_file(bundle_path: str | Path) -> str | None:
    """既有（169 事务使用）的「文件 → hash8」入口，语义按版本显式分派。

    - v2 文件：返回新版逻辑身份 hash8（内容全同一定相同；但相同不保证
      内容全同，真实 32 位前缀碰撞已复现，不能单独作为同包依据）；
    - legacy 文件：返回旧 kpp-only hash8（保持 v1.3.1 既有用例与旧工具口径）；
    - 坏文件/不可判：None（调用方按「不同内容」处理，绝不覆盖）。

    注意：返回值本身不带版本信息，所以新的比对代码应改用
    bundle_identity_of_file() + bundle_identity_matches()；本包装只为兼容
    169 任务遗留的调用签名，生产复用判定不再使用它。
    """
    identity = bundle_identity_of_file(bundle_path)
    if identity.scheme in ("v2", "legacy"):
        return identity.hash8
    return None


def bundle_identity_matches(
    actual: BundleIdentityRead, expected: BundleIdentity
) -> bool:
    """完整身份比对：只有「口径 + 完整摘要」一致才算同一个包。

    任务182补充纠偏的核心判据（不同内容不得误复用）：

    - v2 ↔ v2：必须比较完整的 64 位 sha256；8 位 hash8 只用于文件名和
      人工核对，真实生日碰撞（182 已用 125,791 次合成说明复现）绝不能
      作为复用依据；
    - legacy ↔ legacy：旧包没有 v2 摘要，只能沿用旧 kpp-only hash8 口径；
    - invalid、空摘要或跨 scheme：一律 False，绝不因短哈希相同而复用。

    调用方必须先读到具体文件的实际身份，再让本函数与本次构建的完整
    身份比较；不要先把两边都降成 hash8 再比较。
    """
    if expected.scheme == "v2":
        return (
            actual.scheme == "v2"
            and bool(expected.sha256)
            and actual.sha256 == expected.sha256
        )
    if expected.scheme == "legacy":
        return (
            actual.scheme == "legacy"
            and bool(expected.legacy_hash8)
            and actual.hash8 == expected.legacy_hash8
        )
    return False


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


def build_bundle(
    kpp_files: list[tuple[str, bytes]],
    bundle_name: str,
    *,
    metadata: BundleMetadata | None = None,
    preset_tags: Mapping[str, Sequence[str]] | None = None,
    package_tags: Sequence[str] | None = None,
    trace: BundleTrace | None = None,
) -> BundleBuild:
    """生成 .bundle 字节与新版逻辑身份（任务176 的唯一构建入口）。

    Parameters
    ----------
    kpp_files : [(文件名, 文件字节)]，按转换顺序；将放入 paintoppresets/。
    bundle_name : **不含 hash 的展示名**（例如 ABR 名）。不要传最终文件名，
        否则 meta.xml 的 dc:title 会带上身份 hash，形成「文件名 → 标题 → 身份」
        的递归；最终文件名由调用方用 identity.hash8 另行拼装。
    metadata / preset_tags / package_tags : 含义见 build_bundle_bytes。
    trace : 包内追溯输入；None 表示旧式构建（verify 会按 legacy 读回）。

    身份口径警告：传了 metadata/preset_tags/package_tags 却**不传 trace** 时，
    构建结果仍按 legacy 口径（kpp-only hash8）标记——这是为了兼容 175 及更早的
    直接调用与旧包读回；此时只改元信息/标签不会改变 legacy hash8。生产导出
    必须同时给 trace（convert_presets 在出现元信息/标签/源路径时已自动补齐）。

    同输入两次调用产出字节完全相同（条目时间戳固定 + 压缩级别固定），
    身份与字节一一对应；包内不写身份哈希（无自引用）。
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
    # 显式 name 优先；否则用调用方给的展示名（不含 hash）。空包名直接报错，
    # 避免生成一个详情面板看不出名字的资源包。
    if not metadata.name:
        metadata = replace(metadata, name=metadata.resolved_name(bundle_name))

    tags_by_name = _normalized_preset_tags(preset_tags, names)
    normalized_package_tags = normalize_tags(package_tags)

    trace_json = ""
    trace_doc: dict | None = None
    if trace is not None:
        trace_json, trace_doc = _build_trace_document(trace, prepared, tags_by_name)

    entries: list[tuple[str, str, tuple[str, ...]]] = [
        (fname, hashlib.md5(blob).hexdigest(), tags_by_name.get(fname, ()))
        for fname, blob in prepared
    ]
    manifest_text = _manifest_xml(entries)
    meta_text = _meta_xml(metadata, normalized_package_tags, trace_json or None)
    if trace is None:
        # 无追溯 = 旧口径：保持 175 及更早的字节与 kpp-only hash8，便于旧包读回；
        # 此时元信息/标签仍写进 meta/manifest，但不声称有新版身份。
        identity = BundleIdentity(
            scheme="legacy", legacy_hash8=bundle_content_hash8(prepared))
    else:
        identity = _identity_of_parts(meta_text, manifest_text, prepared)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype 必须是第一个条目且不压缩（Krita 据此识别资源包类型）
        zf.writestr(_zip_info(_MIMETYPE_ENTRY, zipfile.ZIP_STORED), MIMETYPE)
        zf.writestr(_zip_info(_MANIFEST), manifest_text)
        zf.writestr(_zip_info(_META), meta_text)
        for fname, blob in prepared:
            zf.writestr(_zip_info(PREFIX + fname), blob)
    return BundleBuild(
        data=buf.getvalue(), identity=identity, trace=trace_doc, trace_json=trace_json)


def build_bundle_bytes(
    kpp_files: list[tuple[str, bytes]],
    bundle_name: str,
    *,
    metadata: BundleMetadata | None = None,
    preset_tags: Mapping[str, Sequence[str]] | None = None,
    package_tags: Sequence[str] | None = None,
    trace: BundleTrace | None = None,
) -> bytes:
    """按固定布局生成 .bundle 字节（不落盘，供确定性比对/测试用）。

    旧签名兼容入口：只返回字节。需要同时拿到新版逻辑身份时请用 build_bundle()。
    参数含义（含任务176 的 trace）见 build_bundle()；**不传 trace 时身份按
    legacy（kpp-only）口径**，只适合旧调用兼容，不能拿来判断「新版同内容」。
    """
    return build_bundle(
        kpp_files,
        bundle_name,
        metadata=metadata,
        preset_tags=preset_tags,
        package_tags=package_tags,
        trace=trace,
    ).data


def write_bundle(
    bundle_path: str,
    kpp_files: list[tuple[str, bytes]],
    *,
    metadata: BundleMetadata | None = None,
    preset_tags: Mapping[str, Sequence[str]] | None = None,
    package_tags: Sequence[str] | None = None,
    trace: BundleTrace | None = None,
) -> str:
    """把 .kpp 文件打包为 .bundle。

    Parameters
    ----------
    bundle_path : 输出 .bundle 路径。**只有** metadata.name 为空时，才用它的
        stem 作 dc:title 兜底；因此调用方必须传展示名（不含身份 hash），
        或者显式给 metadata.name，避免「文件名 → 标题 → 身份」递归。
    kpp_files : [(文件名, 文件字节)]，将放入 paintoppresets/ 目录
    metadata / preset_tags / package_tags / trace : 见 build_bundle

    同输入两次调用产出字节完全相同（条目时间戳固定 + 压缩级别固定）。
    """
    data = build_bundle_bytes(
        kpp_files,
        Path(bundle_path).stem,
        metadata=metadata,
        preset_tags=preset_tags,
        package_tags=package_tags,
        trace=trace,
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
    #: 任务176：读回的追溯 JSON（无追溯的旧包为 None）
    trace: dict | None = None
    #: 身份口径："v2"（新版）/ "legacy"（旧包 kpp-only）/ ""（无法判定）
    identity_scheme: str = ""
    #: 回读重算的身份（v2 时是可比较的完整 sha256 与前 8 位）
    identity_hash8: str = ""
    identity_sha256: str = ""
    #: 非阻塞说明（例如旧包按兼容口径读回），与 errors 分开
    notes: list[str] = field(default_factory=list)

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


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict:
    """JSON 对象重复键直接失败：静默取最后一个会让读回结论不确定。"""
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 对象含重复键 {key!r}")
        result[key] = value
    return result


def _parse_trace_json(text: str) -> dict:
    """解析包内追溯 JSON；非法/非对象/重复键都抛 ValueError。"""
    if not isinstance(text, str):
        raise TypeError("追溯必须是字符串")
    document = json.loads(text, object_pairs_hook=_no_duplicate_pairs)
    if not isinstance(document, dict):
        raise ValueError("追溯 JSON 顶层必须是对象")
    return document


def _trace_errors(
    trace: dict,
    kpp_blobs: Mapping[str, bytes],
    preset_tags: Mapping[str, tuple[str, ...]],
) -> list[str]:
    """校验包内追溯与 manifest/实际字节的关联；返回中文错误列表。

    这是回读校验的「追溯关联」部分：不信任 trace 自报，逐条复算：
    - 每支 kpp 必须真实存在，声明的 sha256 必须等于实际字节；
    - trace 的逐支标签必须与 manifest 读回的完全一致（防止只改标签但追溯未更新）；
    - trace 与 manifest 的预设集合必须一一对应（不少、不多）；
    - reason 引用必须能还原成文本，且文本哈希等于引用键（可复算的编辑代价）。
    """
    errors: list[str] = []
    # 追溯里的 kpp 是裸文件名（与 manifest full-path 去掉前缀后一致）；
    # verify_bundle 传进来的是 zip 全路径，这里先归一，避免两套键名。
    blobs: dict[str, bytes] = {
        name[len(PREFIX):]: blob
        for name, blob in kpp_blobs.items()
        if name.startswith(PREFIX)
    }

    def fail(message: str) -> None:
        errors.append(f"追溯：{message}")

    if trace.get("format") != TRACE_FORMAT:
        fail(f"format 必须是 {TRACE_FORMAT!r}，实际 {trace.get('format')!r}")
    if trace.get("version") != TRACE_VERSION:
        fail(f"version 必须是 {TRACE_VERSION}，实际 {trace.get('version')!r}")
    if trace.get("identity_format") != IDENTITY_FORMAT:
        fail(
            f"identity_format 必须是 {IDENTITY_FORMAT}，"
            f"实际 {trace.get('identity_format')!r}")

    producer = trace.get("producer")
    if not isinstance(producer, dict):
        fail("缺少 producer 段")
    else:
        if producer.get("name") != PRODUCER_NAME:
            fail(f"producer.name 必须是 {PRODUCER_NAME!r}")
        for key, label in (("version", "程序版本"), ("release_state", "发布状态")):
            value = producer.get(key)
            if not isinstance(value, str) or not value.strip():
                fail(f"producer.{key}（{label}）不能为空")

    source = trace.get("source")
    if not isinstance(source, dict):
        fail("缺少 source 段")
    else:
        for key, label in (("filename", "源文件名"), ("stem", "源文件名主干")):
            value = source.get(key)
            if value is None:
                continue
            try:
                _checked_trace_filename(value, f"追溯 {label}")
            except (TypeError, ValueError) as exc:
                fail(str(exc))
        digest = source.get("digest")
        if digest is not None:
            try:
                _checked_trace_digest(digest, "追溯源内容摘要")
            except (TypeError, ValueError) as exc:
                fail(str(exc))
        bound = source.get("bound")
        if type(bound) is not bool:
            fail("source.bound 必须是布尔")
        elif bound != bool(digest):
            fail("source.bound 与 source.digest 是否存在不一致")
        count = source.get("preset_count")
        if type(count) is not int or count < 0:
            fail("source.preset_count 必须是非负整数")
        elif count != len(blobs):
            fail(f"source.preset_count（{count}）与包内预设数（{len(blobs)}）不一致")

    reason_texts = trace.get("reason_texts")
    if not isinstance(reason_texts, dict):
        fail("缺少 reason_texts 段")
        reason_texts = {}
    for key, value in reason_texts.items():
        if not isinstance(key, str) or not _HEX64_PATTERN.fullmatch(key):
            fail(f"reason_texts 的键必须是 64 位小写 hex：{key!r}")
            continue
        if not isinstance(value, str) or not value.strip():
            fail(f"reason_texts[{key}] 必须是非空文本")
            continue
        if _reason_key(value) != key:
            fail(f"reason_texts[{key}] 的文本哈希不匹配，无法复算")

    presets = trace.get("presets")
    if not isinstance(presets, list):
        fail("presets 必须是数组")
        return errors
    if len(presets) != len(blobs):
        fail(f"presets 条目数（{len(presets)}）与包内预设数（{len(blobs)}）不一致")
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    for index, item in enumerate(presets):
        label = f"presets[{index}]"
        if not isinstance(item, dict):
            fail(f"{label} 必须是对象")
            continue
        kpp = item.get("kpp")
        if not isinstance(kpp, str) or kpp not in blobs:
            fail(f"{label}.kpp 关联的预设不存在：{kpp!r}")
            continue
        if kpp in seen_names:
            fail(f"{label}.kpp 重复：{kpp!r}")
        seen_names.add(kpp)
        declared = item.get("kpp_sha256")
        actual = hashlib.sha256(blobs[kpp]).hexdigest()
        if not isinstance(declared, str) or not _HEX64_PATTERN.fullmatch(declared):
            fail(f"{label}.kpp_sha256 必须是 64 位小写 hex")
        elif declared != actual:
            fail(f"{label}（{kpp}）的声明摘要与实际字节不一致")
        tags = item.get("tags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            fail(f"{label}.tags 必须是字符串数组")
        else:
            expected = list(preset_tags.get(kpp, ()))
            if tags != expected:
                fail(f"{label}（{kpp}）的追溯标签与 manifest 不一致")
        preset_id = item.get("id")
        if preset_id is not None:
            if not isinstance(preset_id, str) or not _STABLE_ID_PATTERN.fullmatch(preset_id):
                fail(f"{label}.id 不是合法的稳定原始 ID：{preset_id!r}")
            elif preset_id in seen_ids:
                fail(f"{label}.id 重复：{preset_id}")
            else:
                seen_ids.add(preset_id)
        ordinal = item.get("ordinal")
        if type(ordinal) is not int or ordinal < 1:
            fail(f"{label}.ordinal 必须是从 1 开始的整数")
        elif ordinal in seen_ordinals:
            fail(f"{label}.ordinal 重复：{ordinal}")
        else:
            seen_ordinals.add(ordinal)
        name = item.get("name")
        if not isinstance(name, str):
            fail(f"{label}.name 必须是字符串")
        else:
            try:
                check_xml_text(name, field="追溯预设名")
            except ValueError as exc:
                fail(str(exc))
        switches = item.get("switches")
        if not isinstance(switches, dict):
            fail(f"{label}.switches 必须是对象")
            continue
        if set(switches) != set(_TRACE_SWITCH_IDS):
            fail(
                f"{label}.switches 必须完整包含三项开关："
                + "、".join(_TRACE_SWITCH_IDS))
        for switch_id in _TRACE_SWITCH_IDS:
            switch = switches.get(switch_id)
            if not isinstance(switch, dict):
                fail(f"{label}.switches.{switch_id} 必须是对象")
                continue
            if type(switch.get("applicable")) is not bool:
                fail(f"{label}.switches.{switch_id}.applicable 必须是布尔")
            if type(switch.get("enabled")) is not bool:
                fail(f"{label}.switches.{switch_id}.enabled 必须是布尔")
            if switch.get("actual") not in _TRACE_ACTUAL_VALUES:
                fail(
                    f"{label}.switches.{switch_id}.actual 不受支持："
                    f"{switch.get('actual')!r}")
            if type(switch.get("limited")) is not bool:
                fail(f"{label}.switches.{switch_id}.limited 必须是布尔")
            reason_ref = switch.get("reason")
            if not isinstance(reason_ref, str):
                fail(f"{label}.switches.{switch_id}.reason 必须是非空说明引用")
            elif reason_ref not in reason_texts:
                fail(
                    f"{label}.switches.{switch_id}.reason 引用了不存在的说明："
                    f"{reason_ref!r}")
    missing = sorted(set(blobs) - seen_names)
    if missing:
        fail("缺少逐支追溯：" + "、".join(missing))
    return errors


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
      5. 每个 .kpp 能完整读出（PNG 容器 + iTXt 预设 XML，含块 CRC）；
      6. 任务176：有 trace 时校验追溯关联（每支 kpp/声明 sha256/标签/reason 引用）、
         追溯与 manifest 的预设集合一一对应，并重算新版逻辑身份；
         无 trace 的旧包只给 note 并按 legacy 口径记录 kpp-only hash8；
         坏 trace 一律报错（不能把「声称有追溯」的包静默当旧包）。
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

    meta_text: str | None = None
    manifest_text: str | None = None
    kpp_blobs: dict[str, bytes] = {}
    with zf:
        infos = zf.infolist()
        names = [info.filename for info in infos]
        kpp_names = [name for name in names if name.startswith(PREFIX)]
        result.preset_count = len(kpp_names)
        # 一次读出所有预设字节：md5、结构、追溯声明摘要与身份计算共用同一份，
        # 避免重复解压，也避免「校验一处读一次」产生不一致。
        for name in kpp_names:
            try:
                kpp_blobs[name] = zf.read(name)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"预设 {name} 读不出：{exc}")

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
                    payload = kpp_blobs.get(record.full_path)
                    if payload is None:
                        payload = zf.read(record.full_path)
                    actual = hashlib.md5(payload).hexdigest()
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
            blob = kpp_blobs.get(name)
            if blob is None:
                continue  # 读取失败已在上面登记，不重复报错
            try:
                read_preset_xml(blob)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"预设 {name} 结构损坏：{exc}")

        # 6) 任务176：追溯关联、逐支标签与声明摘要；并记录新版逻辑身份
        trace_value = result.meta.get(f"meta:{TRACE_META_NAME}")
        if trace_value is None:
            result.identity_scheme = "legacy"
            result.notes.append(
                "旧格式包：没有 brushconverter-trace；按 kpp-only 兼容口径读回，"
                "不得用它替代新版同包判定（只改元信息/标签时旧哈希不变）")
            if kpp_blobs:
                legacy_entries = [
                    (name[len(PREFIX):], blob) for name, blob in kpp_blobs.items()]
                result.identity_hash8 = bundle_content_hash8(legacy_entries)
        else:
            result.identity_scheme = "v2"
            try:
                trace_doc = _parse_trace_json(trace_value)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"追溯 JSON 解析失败：{exc}")
            else:
                result.trace = trace_doc
                result.errors.extend(
                    _trace_errors(trace_doc, kpp_blobs, result.preset_tags))
            if meta_text is not None and manifest_text is not None and kpp_blobs:
                identity = _identity_of_parts(
                    meta_text,
                    manifest_text,
                    [(name[len(PREFIX):], blob) for name, blob in kpp_blobs.items()],
                )
                result.identity_sha256 = identity.sha256
                result.identity_hash8 = identity.hash8

    result.ok = not result.errors
    return result
