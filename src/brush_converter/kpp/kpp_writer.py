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
    if gray.ndim != 2:
        raise ValueError(f"笔尖预览需要二维灰度数组，实际 shape={gray.shape}")
    if gray.size == 0:
        raise ValueError("笔尖预览需要非空蒙版")
    if gray.dtype != np.uint8:
        raise ValueError(f"笔尖预览必须是 uint8 蒙版，实际 dtype={gray.dtype}")
    if size <= 0:
        raise ValueError(f"预览尺寸必须为正: {size}")
    h, w = gray.shape
    scale = min(size / h, size / w)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    img = Image.fromarray(gray, mode="L").resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(Image.merge("RGBA", (img, img, img, img)),
                 ((size - nw) // 2, (size - nh) // 2))
    return np.asarray(canvas, dtype=np.uint8)


def build_kpp(preset_xml: str, preview_rgba: np.ndarray) -> bytes:
    """构造完整 .kpp 字节。preview_rgba 必须为 (h, w, 4) uint8 RGBA。

    IHDR 固定声明 8bit RGBA，因此这里必须校验形状/dtype，否则 float 或
    RGB 数组会悄悄生成结构损坏的 PNG（Krita 读不出预览）。
    """
    preview = np.ascontiguousarray(preview_rgba)
    if preview.ndim != 3 or preview.shape[2] != 4:
        raise ValueError(f"预览必须是 (h, w, 4) RGBA，实际 shape={preview.shape}")
    if preview.dtype != np.uint8:
        raise ValueError(f"预览必须是 uint8，实际 dtype={preview.dtype}")
    h, w = preview.shape[:2]
    if h <= 0 or w <= 0:
        raise ValueError(f"预览尺寸非法: {w}x{h}")
    out = bytearray()
    out += b"\x89PNG\r\n\x1a\n"

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8bit RGBA（已校验）
    out += _chunk(b"IHDR", ihdr)

    out += _itxt_chunk("preset", preset_xml, compressed=True)
    out += _text_chunk("version", "5.0")

    # IDAT：每行前置 filter 字节 0
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += preview[y].tobytes()  # 连续 uint8 内存，无需逐元素拷贝
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))

    out += _chunk(b"IEND", b"")
    return bytes(out)


def read_preset_xml(kpp_bytes: bytes) -> str:
    """从 .kpp 字节回读预设 XML（iTXt keyword="preset"）。

    与 build_kpp() 严格对称，供写出后回读校验（verify_bundle）与 GUI 摘要
    （纹理计数）使用。任何结构不符都抛 ValueError：宁可报错，不要把
    「读不出来」当成「没有纹理」。

    校验三件事：PNG 签名、块长度不越界、iTXt 块自身 CRC32 正确。
    """
    if not kpp_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG 容器（缺少 PNG 签名）")
    pos = 8
    total = len(kpp_bytes)
    while pos + 8 <= total:
        length = struct.unpack(">I", kpp_bytes[pos:pos + 4])[0]
        ctype = kpp_bytes[pos + 4:pos + 8]
        end = pos + 8 + length
        if end + 4 > total:
            raise ValueError(f"PNG 块 {ctype!r} 被截断（需要 {length} 字节）")
        payload = kpp_bytes[pos + 8:end]
        crc = struct.unpack(">I", kpp_bytes[end:end + 4])[0]
        if ctype == b"iTXt":
            if crc != (zlib.crc32(ctype + payload) & 0xFFFFFFFF):
                raise ValueError("iTXt 块 CRC 校验失败（字节已损坏）")
            keyword, _, rest = payload.partition(b"\x00")
            if keyword == b"preset":
                if len(rest) < 2:
                    raise ValueError("iTXt 预设块缺少压缩标志")
                flag = rest[0]
                body = rest[2:]
                # language tag + translated keyword：本工具写出时都是空串
                _lang, _, body = body.partition(b"\x00")
                _trans, _, data = body.partition(b"\x00")
                if flag == 1:
                    try:
                        data = zlib.decompress(data)
                    except zlib.error as exc:
                        raise ValueError(f"iTXt 预设块解压失败：{exc}") from exc
                elif flag != 0:
                    raise ValueError(f"iTXt 压缩标志非法：{flag}")
                return data.decode("utf-8")
        pos = end + 4
    raise ValueError("未找到 iTXt 预设块（不是 .kpp）")


def write_kpp(path: str, preset_xml: str, preview_rgba: np.ndarray) -> str:
    with open(path, "wb") as f:
        f.write(build_kpp(preset_xml, preview_rgba))
    return path
