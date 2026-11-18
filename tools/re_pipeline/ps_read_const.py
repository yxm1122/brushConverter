# -*- coding: utf-8 -*-
"""ps_read_const.py —— 读取 Photoshop.exe 里被候选函数引用的浮点常量（只读，纯字节取证）。

用途：任务 11A 在动态追踪中发现 H3 (0x141a935f0) 里有一段
    divss xmm1, xmm0                 ; minimumDepth / depth
    mulss xmm1, [rip + 0x69725dc]    ; -> 0x148405d68
    addss xmm1, [rip + 0x698c8b8]    ; -> 0x14842004c
    cvttss2si esi, xmm1
    mov   dword ptr [rbx + 0xc], esi ; minimumDepth = (int)(K1*minDepth/depth + K2)
本脚本把这两个常量（以及 H1/H3 共用的 double 100.0）从文件字节里解出来，
替代「靠反汇编器在线读常量」的做法，可离线复算。

用法：
  python tools/re_pipeline/ps_read_const.py --exe "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe"
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pe_scan import PeImage

# (标签, VA) —— 地址来自 native_xref.py disasm 的 objdump 风格注释
TARGETS = [
    ("H3 minimumDepth 缩放系数 K1 (mulss)", 0x148405D68),
    ("H3 minimumDepth 偏移量 K2 (addss)", 0x14842004C),
    ("H1/H3 共用的 double 100.0", 0x1483D7978),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    args = ap.parse_args()
    pe = PeImage(args.exe)
    print("image_base=%#x  file=%s" % (pe.image_base, args.exe))
    with open(pe.path, "rb") as f:
        for label, va in TARGETS:
            off = pe.va_to_offset(va)
            if off is None:
                print("%-46s VA=%#x  (not mapped)" % (label, va))
                continue
            f.seek(off)
            raw = f.read(8)
            f32 = struct.unpack("<f", raw[:4])[0]
            f64 = struct.unpack("<d", raw[:8])[0]
            print("%-46s VA=%#x file_off=%d bytes=%s float32=%.9g double=%.17g"
                  % (label, va, off, raw.hex(), f32, f64))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
