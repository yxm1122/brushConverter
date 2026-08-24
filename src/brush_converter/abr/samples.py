"""ABR samp 区的采样笔尖提取（v1/v2 旧格式与 v6.1/v6.2 新格式）。"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from .rle import rle_decode

MAX_SIZE = 10000  # 与 GIMP_BRUSH_MAX_SIZE 同量级的保护上限


@dataclass
class BrushTip:
    """一个采样笔尖。gray 为 uint8 (h, w) 不透明蒙版，255 = 墨、0 = 透明。"""

    index: int
    width: int
    height: int
    depth_bits: int
    gray: np.ndarray
    uuid: str | None = None
    name: str | None = None
    spacing: int | None = None
    source_offset: int = 0
    meta: dict = field(default_factory=dict)


class AbrParseError(ValueError):
    pass


def _to_gray8(raw: np.ndarray, bytes_per_pixel: int) -> np.ndarray:
    """把原始像素数据归一为 uint8 灰度。"""
    if bytes_per_pixel == 1:
        return raw.astype(np.uint8)
    if bytes_per_pixel == 2:
        # 16 位样本按小端存储（与 GIMP 行为一致），映射到 0..255
        u16 = raw.view("<u2").astype(np.float64) / 65535.0
        return (u16 * 255.0 + 0.5).astype(np.uint8)
    raise AbrParseError(f"不支持的位深: {bytes_per_pixel} 字节/像素")


def _decode_pixels(buf, compress: int, width: int, height: int,
                   bytes_per_pixel: int) -> np.ndarray:
    """解压并转换为「不透明蒙版」灰度（255=墨）。

    compress 0=原始、1=PackBits RLE。返回 (h, w) uint8 蒙版。
    """
    size = width * height * bytes_per_pixel
    if compress == 0:
        if len(buf) < size:
            raise AbrParseError("原始像素数据被截断")
        raw = np.frombuffer(buf[:size], dtype=np.uint8)
    elif compress == 1:
        if bytes_per_pixel != 1:
            raise AbrParseError("RLE 压缩仅支持 8 位笔尖")
        raw = rle_decode(buf, size, height)
    else:
        raise AbrParseError(f"未知压缩模式: {compress}")
    gray = _to_gray8(raw, bytes_per_pixel)
    # ABR 采样字节语义：0 = 不透明(墨)、255 = 透明（与 Krita 的
    # convertToQImage 一致，Krita 对 ABR 与 GBR 都做 255 - byte 反转）。
    # 这里统一转为「不透明蒙版」：255 = 墨，便于预览与下游生成 GBR。
    mask = (255 - gray.reshape(height, width)).astype(np.uint8)
    return mask


def _parse_v12(data: memoryview | bytes, count: int, version: int,
               base_name: str) -> list[BrushTip]:
    """旧格式（主版本 1/2）：count 支笔刷依次排列。"""
    tips: list[BrushTip] = []
    pos = 4
    for i in range(count):
        if pos + 6 > len(data):
            raise AbrParseError(f"第 {i} 支笔刷头部越界")
        brush_type, size = struct.unpack_from(">HI", data, pos)
        item_start = pos
        pos += 6
        next_pos = pos + size
        if brush_type != 2:  # 1=计算笔尖（无位图），其他未知：跳过
            pos = next_pos
            continue
        _misc, spacing = struct.unpack_from(">IH", data, pos)
        pos += 6
        name = None
        if version == 2:
            (char_count,) = struct.unpack_from(">I", data, pos)
            pos += 4
            name = data[pos : pos + char_count * 2].decode("utf-16-be", "replace")
            pos += char_count * 2
        pos += 1  # antialiasing
        pos += 8  # short bounds ×4
        top, left, bottom, right = struct.unpack_from(">4i", data, pos)
        pos += 16
        (depth_bits,) = struct.unpack_from(">H", data, pos)
        pos += 2
        compress = data[pos]
        pos += 1
        width, height = right - left, bottom - top
        if not (1 <= width <= MAX_SIZE and 1 <= height <= MAX_SIZE):
            raise AbrParseError(f"笔刷尺寸越界: {width}x{height}")
        bpp = depth_bits >> 3
        gray = _decode_pixels(data[pos:next_pos], compress, width, height, bpp)
        tips.append(BrushTip(
            index=len(tips), width=width, height=height,
            depth_bits=depth_bits, gray=gray, name=name or f"{base_name}-{i + 1:03d}",
            spacing=spacing, source_offset=item_start,
        ))
        pos = next_pos
    return tips


def _read_pascal_uuid(data, pos: int) -> str | None:
    """尽力读取 v6.2 笔尖项的 UUID（1 字节长度 + ASCII）。失败返回 None。"""
    try:
        length = data[pos]
        if not (20 <= length <= 40):
            return None
        raw = bytes(data[pos + 1 : pos + 1 + length])
        text = raw.decode("ascii")
        if all(c in "0123456789abcdefABCDEF-" for c in text):
            return text
    except Exception:
        pass
    return None


def _parse_v6(data: memoryview | bytes, section_end: int,
              subversion: int, base_name: str) -> list[BrushTip]:
    """新格式（主版本 6/10）：samp 区内逐项解析。"""
    skip = 47 if subversion == 1 else 301
    tips: list[BrushTip] = []
    pos = 0
    while pos + 4 <= section_end:
        (brush_size,) = struct.unpack_from(">I", data, pos)
        item_start = pos
        next_pos = pos + 4 + brush_size + (-brush_size % 4)
        if next_pos > section_end + 3:  # 允许最后一段轻微越界（无填充）
            raise AbrParseError(f"笔刷项长度越界: item@{item_start} size={brush_size}")
        uuid = _read_pascal_uuid(data, pos + 4)
        rect_off = pos + 4 + skip
        top, left, bottom, right = struct.unpack_from(">4i", data, rect_off)
        (depth_bits,) = struct.unpack_from(">H", data, rect_off + 16)
        compress = data[rect_off + 18]
        width, height = right - left, bottom - top
        if not (1 <= width <= MAX_SIZE and 1 <= height <= MAX_SIZE):
            raise AbrParseError(f"笔刷尺寸越界: {width}x{height} (item@{item_start})")
        bpp = depth_bits >> 3
        if not (1 <= bpp <= 2):
            raise AbrParseError(f"不支持的位深: {depth_bits} bits")
        data_start = rect_off + 19
        gray = _decode_pixels(data[data_start:next_pos], compress, width, height, bpp)
        tips.append(BrushTip(
            index=len(tips), width=width, height=height,
            depth_bits=depth_bits, gray=gray, uuid=uuid,
            name=uuid or f"{base_name}-{len(tips) + 1:03d}",
            source_offset=item_start,
        ))
        pos = next_pos
        if pos >= section_end:
            break
    return tips


def parse_samples(samp: memoryview | bytes, version: int, subversion: int,
                  base_name: str) -> list[BrushTip]:
    """从 samp 区数据提取全部采样笔尖。"""
    if version in (1, 2):
        return _parse_v12(samp, subversion, version, base_name)
    if version in (6, 10) and subversion in (1, 2):
        return _parse_v6(samp, len(samp), subversion, base_name)
    raise AbrParseError(f"不支持的 ABR 版本: {version}.{subversion}")
