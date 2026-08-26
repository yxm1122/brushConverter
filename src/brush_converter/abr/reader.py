"""ABR 文件级读取：头部 + 8BIM 区段遍历。"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from .patterns import PatternTexture, parse_patterns
from .samples import BrushTip, parse_samples


@dataclass
class AbrSection:
    """一个 8BIM 区段的原始数据。"""

    key: str
    offset: int  # 数据区起始位置（绝对）
    length: int
    data: bytes


@dataclass
class AbrFile:
    """解析后的 ABR 文件。

    Attributes
    ----------
    version : 主版本（1/2 旧格式，6/10 新格式）
    subversion : 旧格式中为笔刷数量；新格式中为子版本（1 或 2）
    sections : {key: AbrSection}
    tips : samp 区解析出的采样笔尖列表
    """

    version: int
    subversion: int
    sections: dict[str, AbrSection] = field(default_factory=dict)
    tips: list[BrushTip] = field(default_factory=list)
    patterns: dict[str, PatternTexture] = field(default_factory=dict)  # patt 纹理 {uuid: PatternTexture}
    name: str = ""  # 文件名（不含扩展名）

    @classmethod
    def parse(cls, path: str | os.PathLike) -> "AbrFile":
        path = os.fspath(path)
        with open(path, "rb") as f:
            data = f.read()
        base_name = os.path.splitext(os.path.basename(path))[0]
        return cls._from_bytes(data, base_name)

    @classmethod
    def _from_bytes(cls, data: bytes, base_name: str) -> "AbrFile":
        if len(data) < 4:
            raise ValueError("文件过小，不是 ABR")
        version, subversion = struct.unpack_from(">HH", data, 0)

        sections: dict[str, AbrSection] = {}
        if version in (6, 10):
            pos = 4
            while pos + 12 <= len(data):
                sig, key = data[pos : pos + 4], data[pos + 4 : pos + 8]
                if sig != b"8BIM":
                    break
                (length,) = struct.unpack_from(">I", data, pos + 8)
                start = pos + 12
                if start + length > len(data):
                    length = len(data) - start  # 最后一段可能无填充
                sections[key.decode("latin1")] = AbrSection(
                    key=key.decode("latin1"), offset=start, length=length,
                    data=data[start : start + length],
                )
                pos = start + length + (length % 4)

        abr = cls(version=version, subversion=subversion, sections=sections, name=base_name)
        samp = sections.get("samp")
        if samp is not None:
            abr.tips = parse_samples(samp.data, version, subversion, base_name)
        elif version in (1, 2):
            abr.tips = parse_samples(data, version, subversion, base_name)
        patt = sections.get("patt")
        if patt is not None:
            abr.patterns = parse_patterns(patt.data)
        return abr

    @property
    def has_descriptors(self) -> bool:
        return "desc" in self.sections

    @property
    def has_patterns(self) -> bool:
        return "patt" in self.sections

    def summary(self) -> str:
        lines = [
            f"版本: {self.version}.{self.subversion}",
            f"区段: {', '.join(f'{k}({v.length}B)' for k, v in self.sections.items()) or '(旧格式无区段)'}",
            f"采样笔尖: {len(self.tips)} 个",
            f"纹理图案: {len(self.patterns)} 个",
        ]
        for t in self.tips:
            name = t.uuid or t.name or ""
            lines.append(f"  [{t.index}] {t.width}x{t.height} {t.depth_bits}bit {name}")
        return "\n".join(lines)
