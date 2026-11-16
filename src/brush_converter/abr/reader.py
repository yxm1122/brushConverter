"""ABR 文件级读取：头部 + 8BIM 区段遍历。"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from .patterns import PatternParseError, PatternTexture, parse_patterns
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
    # 结构异常的诊断线索：为「解析出 0 支」提供可解释的原因，而不是静默返回空表
    diagnostics: list[str] = field(default_factory=list)

    @property
    def looks_unsupported(self) -> bool:
        """解析成功但没读到任何笔刷，且结构可疑（很可能是格式变体不受支持）。"""
        return not self.tips and not self.sections

    @classmethod
    def parse(cls, path: str | os.PathLike) -> "AbrFile":
        path = os.fspath(path)
        with open(path, "rb") as f:
            data = f.read()
        base_name = os.path.splitext(os.path.basename(path))[0]
        try:
            return cls._from_bytes(data, base_name)
        except (struct.error, IndexError) as e:
            # 截断/损坏的文件会让 struct 解包抛 struct.error、切片越界抛
            # IndexError，二者都不是 ValueError；统一归一，CLI/GUI 才能按
            # 「解析失败」友好提示而不是抛未捕获异常退出。
            raise ValueError(f"ABR 结构损坏：{e}") from e

    @classmethod
    def _from_bytes(cls, data: bytes, base_name: str) -> "AbrFile":
        if len(data) < 4:
            raise ValueError("文件过小，不是 ABR")
        version, subversion = struct.unpack_from(">HH", data, 0)

        sections: dict[str, AbrSection] = {}
        diagnostics: list[str] = []
        if version in (6, 10):
            pos = 4
            while pos + 12 <= len(data):
                sig, key = data[pos : pos + 4], data[pos + 4 : pos + 8]
                if sig != b"8BIM":
                    # 不是「安全的结尾」就是结构不符：记录现场，避免静默丢数据
                    remainder = len(data) - pos
                    if remainder > 4:
                        diagnostics.append(
                            f"区段扫描在偏移 {pos} 处中断：期望 8BIM，实际 {sig!r}"
                            f"（剩余 {remainder} 字节未解析）")
                    break
                (length,) = struct.unpack_from(">I", data, pos + 8)
                start = pos + 12
                if start + length > len(data):
                    diagnostics.append(
                        f"区段 {key.decode('latin1')!r} 声明长度 {length}B "
                        f"超出文件（仅剩 {len(data) - start}B），已按剩余长度截断")
                    length = len(data) - start  # 最后一段可能无填充
                sections[key.decode("latin1")] = AbrSection(
                    key=key.decode("latin1"), offset=start, length=length,
                    data=data[start : start + length],
                )
                # 4 字节对齐填充：补足量是 (-length) % 4，不是 length % 4
                # （后者只在 length % 4 为 0/2 时碰巧相等）。用错会把下一段
                # 的 8BIM 签名读偏 2 字节，导致后续区段被整段丢弃。
                pos = start + length + (-length % 4)
        elif version not in (1, 2):
            # v1/v2 是旧格式（整文件即笔尖序列），不是异常，不能报诊断噪音
            diagnostics.append(f"主版本 {version} 不是已知的 6/10，未解析区段结构")

        abr = cls(version=version, subversion=subversion, sections=sections,
                  name=base_name, diagnostics=diagnostics)
        samp = sections.get("samp")
        if samp is not None:
            if not samp.length:
                diagnostics.append("samp 区为空，未包含任何采样笔尖")
            abr.tips = parse_samples(samp.data, version, subversion, base_name)
        elif version in (1, 2):
            abr.tips = parse_samples(data, version, subversion, base_name)
        elif version in (6, 10):
            diagnostics.append("缺少 samp 区，无法读取采样笔尖")
        patt = sections.get("patt")
        if patt is not None:
            try:
                abr.patterns = parse_patterns(patt.data)
            except (PatternParseError, struct.error, IndexError) as e:
                # 纹理图案解析失败不应让整个文件不可用：记录原因后继续，
                # 笔刷仍可转换，只是相关预设会得到「纹理(图案缺失)」警告。
                abr.patterns = {}
                abr.diagnostics.append(f"patt 区解析失败：{e}")
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
        if not self.tips:
            lines.append("警告: 未读取到任何笔刷")
        for d in self.diagnostics:
            lines.append(f"诊断: {d}")
        return "\n".join(lines)
