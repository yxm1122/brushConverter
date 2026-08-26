"""ABR patt 区段（纹理图案）解析。

格式为实测逆向（对照 psd-tools 的 Pattern / VirtualMemoryArrayList 结构，
并在此基础上修正 ABR 的差异点）：

    patt 区段 = 若干条「长度前缀」的 Pattern 记录，每条 pad 到 4 字节：
        u32 len | 记录体（len 字节） | pad 到 4

    记录体：
        u32 version(=1) | u32 颜色模式(3=RGB, 1=灰度) | 2×i16 (宽,高 提示) |
        u32 名字符数(含结尾 NUL 字符) | 名字 UTF-16BE（NUL 算 1 个字符） |
        u8 名字节数(=36) | UUID(ASCII) |
        VMA 列表: u32 version(=3) | u32 body_len | body {
            u32×4 矩形(top,left,bottom,right) | u32 通道数 | (通道数+2) 个通道块 }

    通道块：
        u32 is_written(0=空) | u32 len | u32 depth(位深) | u32×4 矩形 |
        u16 pixel_depth | u8 compression(0=RAW, 1=RLE) | 数据(len-23)

    RLE(1) 为 PSD 版：先是 height 个 u16 行字节数，随后每行是 PackBits 流。

    注意：
    - ABR 中图案 id 用 u8 长度 + ASCII UUID（psd-tools 的 PSD Pattern 是
      Pascal 字符串，二者不同）；名字符数包含结尾 NUL。
    - 图案按 UUID 关联笔刷预设（desc 的 Txtr.Idnt），不能按名字
      （本仓库样本有 3 张同名 "Shape 2.png" 但内容不同）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

MAX_SIZE = 30000  # 与 samp 区同量级的保护上限


class PatternParseError(ValueError):
    pass


@dataclass
class PatternTexture:
    """一个 Photoshop 纹理图案。

    image : uint8 数组。color_mode=3 → (h, w, 3) RGB；color_mode=1 → (h, w) 灰度。
            像素值为原样（无 ABR 笔尖那种 255-raw 反转）。
    """

    name: str
    uuid: str
    image: np.ndarray
    width: int
    height: int
    color_mode: int
    compression: int = 0  # 首通道压缩方式（0=RAW, 1=RLE），仅作参考


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def _rle_decode_u16(buf: bytes, expected_size: int, height: int) -> bytes:
    """PSD 版 RLE 解码：height 个 u16 行字节数 + 每行 PackBits。"""
    if len(buf) < height * 2:
        raise PatternParseError("RLE 行长度表被截断")
    counts = struct.unpack_from(f">{height}H", buf, 0)
    offset = height * 2
    out = bytearray()
    for line_len in counts:
        if line_len <= 0:
            raise PatternParseError(f"非法扫描行长度: {line_len}")
        line_end = offset + line_len
        if line_end > len(buf):
            raise PatternParseError("RLE 数据被截断")
        j = offset
        while j < line_end:
            n = buf[j]
            j += 1
            if n >= 128:
                n -= 256
            if n < 0:
                if n == -128:
                    continue
                run = -n + 1
                if j + 1 > line_end:
                    raise PatternParseError("RLE 解码越界（重复段）")
                out.extend(bytes([buf[j]]) * run)
                j += 1
            else:
                run = n + 1
                if j + run > line_end:
                    raise PatternParseError("RLE 解码越界（字面量段）")
                out.extend(buf[j : j + run])
                j += run
        offset = line_end
    if len(out) != expected_size:
        raise PatternParseError(
            f"RLE 解码尺寸不符: 得到 {len(out)}, 期望 {expected_size}"
        )
    return bytes(out)


def _decode_channel(data: bytes, depth: int, width: int, height: int,
                    compression: int) -> np.ndarray:
    """解压单通道为 (height, width) uint8。"""
    if depth != 8:
        raise PatternParseError(f"不支持的通道位深: {depth} bits")
    size = width * height
    if compression == 0:
        if len(data) < size:
            raise PatternParseError("RAW 通道数据被截断")
        raw = data[:size]
    elif compression == 1:
        raw = _rle_decode_u16(data, size, height)
    else:
        raise PatternParseError(f"不支持的通道压缩方式: {compression}")
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width)


def _parse_vma_channels(body: bytes, want: int):
    """解析 VMA body，返回 (矩形, 通道数, 前 want 个有数据的通道)。

    通道块按出现顺序收集；RGB 取前 3 个、灰度取第 1 个，其余 is_written=0
    的空块直接跳过。
    """
    if len(body) < 20:
        raise PatternParseError("VMA body 过短")
    rect = struct.unpack_from(">4I", body, 0)
    nchans = _u32(body, 16)
    b = 20
    chans: list[tuple[int, tuple, int, bytes]] = []
    for _ in range(nchans + 2):
        if b + 4 > len(body):
            break
        is_written = _u32(body, b)
        b += 4
        if is_written == 0:
            continue
        if b + 4 > len(body):
            break
        length = _u32(body, b)
        b += 4
        if length == 0:
            continue
        if length < 23:
            raise PatternParseError(f"通道长度非法: {length}")
        depth = _u32(body, b)
        crect = struct.unpack_from(">4I", body, b + 4)
        pixel_depth, compression = struct.unpack_from(">HB", body, b + 20)
        data = body[b + 23 : b + length]
        b += length
        chans.append((depth, crect, compression, data))
        if len(chans) >= want:
            break
    return rect, nchans, chans


def parse_patterns(data: bytes) -> dict[str, PatternTexture]:
    """解析整个 patt 区段，返回 {uuid: PatternTexture}。

    游标必须精确走完区段（每条记录 4 字节对齐），否则视为格式错误。
    """
    out: dict[str, PatternTexture] = {}
    pos = 0
    index = 0
    while pos + 4 <= len(data):
        length = _u32(data, pos)
        if length <= 0:
            raise PatternParseError(f"图案记录长度非法: {length} @ {pos}")
        record_end = pos + 4 + length
        if record_end > len(data):
            raise PatternParseError(
                f"图案记录越界: @{pos} len={length} 超出区段")
        p = pos + 4
        version = _u32(data, p)
        if version != 1:
            raise PatternParseError(f"未知图案版本: {version} (pattern {index})")
        p += 4
        color_mode = _u32(data, p)
        p += 4
        p += 4  # 2×i16 point（宽高提示，以 VMA 矩形为准）
        name_chars = _u32(data, p)
        p += 4
        if not (1 <= name_chars <= 4096) or p + name_chars * 2 > record_end:
            raise PatternParseError(f"图案名字长度非法: {name_chars} @ {p}")
        name = data[p : p + name_chars * 2].decode("utf-16-be", "replace")
        name = name.rstrip("\x00")
        p += name_chars * 2
        id_len = data[p]
        p += 1
        if not (16 <= id_len <= 64) or p + id_len > record_end:
            raise PatternParseError(f"图案 id 长度非法: {id_len}")
        uuid = data[p : p + id_len].decode("ascii", "replace")
        p += id_len

        if p + 8 > record_end:
            raise PatternParseError("缺少 VMA 列表头")
        vma_version = _u32(data, p)
        if vma_version != 3:
            raise PatternParseError(f"未知 VMA 版本: {vma_version} (pattern {index})")
        body_len = _u32(data, p + 4)
        body = data[p + 8 : p + 8 + body_len]
        if len(body) != body_len:
            raise PatternParseError("VMA 列表长度越界")

        want = {1: 1, 3: 3}.get(color_mode)
        if want is None:
            raise PatternParseError(f"不支持的图案颜色模式: {color_mode}")
        rect, nchans, chans = _parse_vma_channels(body, want)
        if len(chans) < want:
            raise PatternParseError(
                f"图案通道不足: 需要 {want} 个，实际 {len(chans)} (pattern {index})")

        width = rect[3] - rect[1]
        height = rect[2] - rect[0]
        if not (1 <= width <= MAX_SIZE and 1 <= height <= MAX_SIZE):
            raise PatternParseError(f"图案尺寸越界: {width}x{height}")

        planes = []
        for depth, crect, compression, cdata in chans[:want]:
            w = crect[3] - crect[1]
            h = crect[2] - crect[0]
            if (w, h) != (width, height):
                raise PatternParseError(f"通道矩形与图案不一致: {crect}")
            planes.append(_decode_channel(cdata, depth, w, h, compression))
        if want == 3:
            image = np.stack(planes, axis=-1)
        else:
            image = planes[0]

        out[uuid] = PatternTexture(
            name=name, uuid=uuid, image=image,
            width=width, height=height, color_mode=color_mode,
            compression=chans[0][2],
        )
        pos = (record_end + 3) & ~3
        index += 1

    if pos != len(data):
        raise PatternParseError(
            f"patt 区段解析未对齐: 停在 {pos}, 区段长度 {len(data)}")
    return out
