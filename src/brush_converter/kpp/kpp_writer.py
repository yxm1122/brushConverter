"""写出 Krita .kpp 预设文件。

.kpp = PNG（200×200 预览）+ iTXt 块（keyword="preset"，zlib 压缩 XML）
       + tEXt 块（keyword="version" = "5.0"）。

手动构造 PNG 块，保证与 Krita 生成的 .kpp 结构一致。
"""

from __future__ import annotations

import struct
import zlib

import numpy as np
from PIL import Image


def _chunk(ctype: bytes, payload: bytes) -> bytes:
    """构造一个标准 PNG 块（长度 + 类型 + 数据 + CRC32）。"""
    return (
        struct.pack(">I", len(payload))
        + ctype
        + payload
        + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    )


def _itxt_chunk(keyword: str, text: str, compressed: bool = True) -> bytes:
    """构造 iTXt 块（Krita 用它存预设 XML，keyword="preset"）。"""
    if compressed:
        data = zlib.compress(text.encode("utf-8"), 9)
        flag = 1
    else:
        data = text.encode("utf-8")
        flag = 0
    payload = (
        keyword.encode("latin1") + b"\x00"
        + bytes([flag, 0])            # compression flag + method
        + b"\x00"                     # language tag
        + b"\x00"                     # translated keyword
        + data
    )
    return _chunk(b"iTXt", payload)


def _text_chunk(keyword: str, text: str) -> bytes:
    """构造 tEXt 块（Krita 用它存 version="5.0"）。"""
    payload = keyword.encode("latin1") + b"\x00" + text.encode("utf-8")
    return _chunk(b"tEXt", payload)


def render_tip_preview(gray: np.ndarray, size: int = 200) -> np.ndarray:
    """把灰度蒙版（255=墨）渲染为白笔刷在透明底上的 RGBA 预览。"""
    h, w = gray.shape
    scale = min(size / h, size / w)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    img = Image.fromarray(gray, mode="L").resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(Image.merge("RGBA", (img, img, img, img)),
                 ((size - nw) // 2, (size - nh) // 2))
    return np.asarray(canvas, dtype=np.uint8)


def build_kpp(preset_xml: str, preview_rgba: np.ndarray) -> bytes:
    """构造完整 .kpp 字节。preview_rgba 为 (h, w, 4) uint8 RGBA。"""
    h, w = preview_rgba.shape[:2]
    out = bytearray()
    out += b"\x89PNG\r\n\x1a\n"

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8bit RGBA
    out += _chunk(b"IHDR", ihdr)

    out += _itxt_chunk("preset", preset_xml, compressed=True)
    out += _text_chunk("version", "5.0")

    # IDAT：每行前置 filter 字节 0
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += bytes(preview_rgba[y])
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))

    out += _chunk(b"IEND", b"")
    return bytes(out)


def write_kpp(path: str, preset_xml: str, preview_rgba: np.ndarray) -> str:
    with open(path, "wb") as f:
        f.write(build_kpp(preset_xml, preview_rgba))
    return path
