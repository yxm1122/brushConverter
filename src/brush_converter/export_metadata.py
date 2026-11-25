"""资源包元信息与逐支标签的数据模型（任务 175）。

这个模块只负责「把使用者显式输入的信息变成可直接写进 .bundle 的数据」：
包级元信息字段的归一与校验，以及标签的归一/排序/去重/可增删集合。
拼 XML 与写 ZIP 仍在 kpp/bundle.py 里，读回校验也在那里 —— 格式规则只写一套，
避免「写出按一个口径、校验按另一套口径」。

三条硬边界（与任务 174 的《资源格式契约》一致）：

1. **只有显式输入才出现**：作者、许可、邮箱、网站、创建/更新日期、资源包版本、
   来源文件名全部默认留空；不读取系统身份（用户名/主机名/路径），不把项目自身的
   MIT 许可套到笔刷素材上，也不自动写当前时间。
2. **标签不推断**：标签只来自公共标签、来源标签、逐支追加、逐支移除四种显式输入；
   不按笔刷名、ABR 用途或文件扩展名自动生成标签。来源标签（例如从 ABR 名称整理
   出来的候选）可以改写或删除 —— 改写 = 先删再加。
3. **归一规则确定**：去首尾空白、内部连续空白折叠成一个空格、Unicode NFC、
   去空、按大小写不敏感判重后稳定排序。同一组标签无论输入顺序如何，写出的
   集合与顺序都一样，保证 .bundle 字节确定。

术语：
- 「公共标签」= 每个预设都会带上的标签；逐支可以移除。
- 「来源标签」= 来自原始素材（例如 ABR 文件名）的候选标签；同样可改写/删除。
- 「逐支追加/移除」= 只影响该预设的增删；移除靠「判重键」（大小写不敏感）匹配，
  所以删除「勾线」也能删掉输入里的「 勾线 」。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

#: 写进 meta:generator 的生成器名。只记工具名，不记版本号（候选阶段不谎写已发布版本）。
DEFAULT_GENERATOR = "brushConverter"

#: .bundle 的格式版本。目标源码 KoResourceBundle 固定写 1，它不是产品版本，
#: 也不是资源包版本（174 契约第 7 节）。
BUNDLE_FORMAT_VERSION = "1"

#: 未显式填写说明时的产品默认说明。只描述「由 Photoshop ABR 转换而来」，
#: 不含用户名、机器名或路径；显式输入会覆盖它。
DEFAULT_DESCRIPTION = "Converted from Photoshop ABR"

#: 元信息日期字段的格式。目标源码原生 writer 用 dd/MM/yyyy（174 契约 5.3）。
DATE_FORMAT = "dd/MM/yyyy"

_DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")

#: XML 1.0 不允许出现的字符（含孤立代理项与 U+FFFE/U+FFFF）。
#: 这些字符写进 XML 会让解析器报错或产生不可复现字节，宁可在这里显式拒绝，
#: 也不能悄悄删掉使用者输入。
_XML_ILLEGAL = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)

#: 资源包元信息的字段名（中文标签仅用于报错文案）。
_METADATA_LABELS = {
    "name": "包名",
    "description": "说明",
    "author": "作者",
    "license": "许可",
    "email": "邮箱",
    "website": "网站",
    "created": "创建日期",
    "updated": "更新日期",
    "package_version": "资源包版本",
    "source_filename": "来源文件名",
    "generator": "生成器名",
}


def check_xml_text(value: str, *, field: str = "文本") -> str:
    """确认字符串能安全写进 XML 1.0；不能就抛 ValueError（不静默清洗）。"""
    if not isinstance(value, str):
        raise TypeError(f"{field} 必须是字符串，实际 {type(value).__name__}")
    match = _XML_ILLEGAL.search(value)
    if match:
        code = ord(match.group(0))
        raise ValueError(
            f"{field} 含有 XML 1.0 不允许的控制字符 U+{code:04X}；"
            "请删除该字符或改用普通文字"
        )
    return value


def normalize_tag(tag: str) -> str | None:
    """把单个标签归一；空标签返回 None。

    规则：Unicode NFC → 去首尾空白 → 内部连续空白折叠成单个空格 → 校验 XML 字符。
    刻意不做 NFKC 兼容折叠：那会把全角字母/半角假名等用户文字改掉，
    属于「自作主张改写输入」。
    """
    if not isinstance(tag, str):
        raise TypeError(f"标签必须是字符串，实际 {type(tag).__name__}")
    text = unicodedata.normalize("NFC", tag).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    return check_xml_text(text, field="标签")


def _tag_items(label: str, tags: Iterable[str] | None) -> tuple[str, ...]:
    """把标签输入统一成元组；单独一个字符串要报错，避免被逐字符拆开。"""
    if tags is None:
        return ()
    if isinstance(tags, str):
        raise TypeError(
            f"{label} 必须是一组标签；只给一个标签时请写成 (标签,) 而不是裸字符串"
        )
    try:
        return tuple(tags)
    except TypeError as exc:
        raise TypeError(f"{label} 必须是可迭代的一组字符串") from exc


def normalize_tags(tags: Iterable[str] | None) -> tuple[str, ...]:
    """把一组标签归一：去空、按大小写不敏感判重、稳定排序。

    同一判重键出现多个拼写（如 "Ink"/"ink"）时保留字典序最小的那个 ——
    这样输出不随输入顺序变化，重复转换的字节也一样。
    """
    best: dict[str, str] = {}
    for tag in _tag_items("标签集合", tags):
        text = normalize_tag(tag)
        if text is None:
            continue
        key = text.casefold()
        current = best.get(key)
        if current is None or text < current:
            best[key] = text
    return tuple(sorted(best.values(), key=lambda item: (item.casefold(), item)))


def resolve_preset_tags(
    common: Iterable[str] | None = (),
    source: Iterable[str] | None = (),
    added: Iterable[str] | None = (),
    removed: Iterable[str] | None = (),
) -> tuple[str, ...]:
    """求一支笔刷的最终标签集合 = (公共 ∪ 来源 ∪ 逐支追加) − 逐支移除。

    移除先按 normalize_tag 归一，再按大小写不敏感判重键匹配；
    移除集合里有、但基础集合里没有的标签视为无操作（不报错，方便界面反复编辑）。
    结果已去空、去重、稳定排序。
    """
    removed_keys: set[str] = set()
    for tag in _tag_items("移除标签", removed):
        text = normalize_tag(tag)
        if text is not None:
            removed_keys.add(text.casefold())
    base = normalize_tags(
        list(_tag_items("公共标签", common))
        + list(_tag_items("来源标签", source))
        + list(_tag_items("逐支追加标签", added))
    )
    return tuple(tag for tag in base if tag.casefold() not in removed_keys)


@dataclass(frozen=True)
class PresetTagPlan:
    """一支预设的标签输入（界面/命令行传入的原始四项）。

    final() 给出写包用的最终集合；GUI 预览与 CLI 写出都调用它，
    保证「导出前看到的标签」与「写进 manifest 的标签」是同一个函数算出来的。
    """

    common: tuple[str, ...] = ()
    source: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("common", "source", "added", "removed"):
            object.__setattr__(self, name, _tag_items(name, getattr(self, name)))

    def final(self) -> tuple[str, ...]:
        """最终标签（已归一、排序、去重）。"""
        return resolve_preset_tags(self.common, self.source, self.added, self.removed)


@dataclass(frozen=True)
class BundleMetadata:
    """资源包的包级元信息（全部可选，空串表示「未提供」）。

    字段去向（按目标源码 KoResourceBundle::readMetaData / saveMetadata）：

    - name → dc:title（包名；详情面板标题始终用文件名，不显示 dc:title）
    - description → dc:description（详情面板显示；空则用产品默认说明）
    - author → dc:author（详情面板显示的「作者」）；同时写 dc:creator 与
      meta:initial-creator（只保存在包内，与 Krita 自己的创建器行为一致）
    - license → meta:license（详情面板显示）。**不默认写 MIT**：
      项目自身的 MIT 许可证不是笔刷素材的版权许可。
    - email/website → meta:email / meta:website（详情面板显示）
    - created/updated → meta:creation-date / meta:dc-date（详情面板显示 Created/Updated）
    - package_version / source_filename → 非原生字段
      meta:brushconverter-package-version / meta:brushconverter-source；
      仅保存在包内，界面不显示，Krita 重新保存时会丢（174 契约第 7 节）。

    构造时会立即归一（去首尾空白）并校验：非法 XML 字符、日期格式、来源文件名含路径
    都会直接抛 ValueError，避免带着坏值一路写到磁盘才失败。
    """

    name: str = ""
    description: str = ""
    author: str = ""
    license: str = ""
    email: str = ""
    website: str = ""
    created: str = ""
    updated: str = ""
    package_version: str = ""
    source_filename: str = ""
    generator: str = DEFAULT_GENERATOR

    def __post_init__(self) -> None:
        for name, label in _METADATA_LABELS.items():
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{label}（{name}）必须是字符串，实际 {type(value).__name__}")
            cleaned = check_xml_text(value.strip(), field=label)
            object.__setattr__(self, name, cleaned)

        if not self.generator:
            object.__setattr__(self, "generator", DEFAULT_GENERATOR)

        source = self.source_filename
        if source:
            if source in {".", ".."} or any(ch in source for ch in ("/", "\\", ":", "\x00")):
                raise ValueError(
                    "来源文件名只能记文件名本身，不能包含目录、盘符或路径分隔符；"
                    f"实际 {source!r}"
                )

        for label, value in (("创建日期", self.created), ("更新日期", self.updated)):
            if value:
                _validate_date(value, label)

    def description_or_default(self) -> str:
        """写包用的说明文本：显式说明优先，否则用产品默认说明。"""
        return self.description or DEFAULT_DESCRIPTION

    def resolved_name(self, fallback: str) -> str:
        """包名：显式 name 优先，否则用调用方给的文件名（bundle_name/stem）。"""
        name = self.name or fallback.strip()
        if not name:
            raise ValueError(
                "包名不能为空：请给 BundleMetadata.name 或 build/write 的 bundle_name"
            )
        return name


def _validate_date(value: str, label: str) -> None:
    """严格按 dd/MM/yyyy 校验；不接受 2026-11-25 这类别的写法（避免歧义）。"""
    if not _DATE_PATTERN.fullmatch(value):
        raise ValueError(
            f"{label} 必须是 {DATE_FORMAT} 格式（例如 25/11/2026），实际 {value!r}"
        )
    try:
        datetime.strptime(value, "%d/%m/%Y")
    except ValueError as exc:
        raise ValueError(f"{label} 不是合法日期（{DATE_FORMAT}）：{value!r}") from exc
