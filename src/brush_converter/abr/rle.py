"""Photoshop PackBits RLE 解码。

数据布局（与 PSD 图像通道一致）：
- 先是 height 个 int16（大端）的每行压缩长度
- 随后是各行独立的 PackBits 流：
  * n 为有符号字节；n >= 0：后面 n+1 个字节为字面量
  * n < 0（n != -128）：后 1 个字节重复 -n+1 次
  * n == -128：无操作
"""

from __future__ import annotations

import struct

import numpy as np


def rle_decode(buf: memoryview | bytes, expected_size: int, height: int) -> np.ndarray:
    """解码 ABR 采样笔尖的 RLE 数据。

    Parameters
    ----------
    buf : 压缩数据（含行长度表）
    expected_size : 解压后总字节数（width * height * bytes_per_pixel）
    height : 扫描行数
    Returns
    -------
    np.ndarray : uint8 一维数组，长度 expected_size
    """
    counts = struct.unpack_from(f">{height}h", buf, 0)
    offset = height * 2
    out = np.empty(expected_size, dtype=np.uint8)
    pos = 0
    for line_len in counts:
        if line_len <= 0:
            raise ValueError(f"非法扫描行长度: {line_len}")
        line_end = offset + line_len
        if line_end > len(buf):
            raise ValueError("RLE 数据被截断")
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
                if j + 1 > line_end or pos + run > expected_size:
                    raise ValueError("RLE 解码越界（重复段）")
                out[pos : pos + run] = buf[j]
                j += 1
                pos += run
            else:
                run = n + 1
                if j + run > line_end or pos + run > expected_size:
                    raise ValueError("RLE 解码越界（字面量段）")
                out[pos : pos + run] = np.frombuffer(
                    buf, dtype=np.uint8, count=run, offset=j)
                j += run
                pos += run
        offset = line_end
    if pos != expected_size:
        raise ValueError(f"RLE 解码尺寸不符: 得到 {pos}, 期望 {expected_size}")
    return out
