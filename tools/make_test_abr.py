# -*- coding: utf-8 -*-
"""生成「1 支」测试用 .abr（任务 110：.abr 写入可行性门）。

背景
----
仓库里的 ABR 支撑一直是**只读**的（src/brush_converter/abr/ 全是解析器）。
本脚本反过来写：把 `reader.py` / `descriptors.py` / `samples.py` 的读法
逐项倒过来，产出 Photoshop 能打开的 .abr。两种模式：

  zero      从零构造（默认）
            文件头 + samp（1 个采样笔尖）+ desc（1 个预设）+ phry（1 个组）
            全部字节由本脚本合成，不复制任何样本数据。
  template  模板改写（退路）
            以现有 .abr 为模板，取出 1 支笔刷的 samp 区段原文与 desc 预设，
            只替换名称/直径/硬度/间距/角度等键值后重新打包成「只含 1 支」的新文件。
            模板里的纹理（patt）不复制：生成文件把 useTexture 置 false，
            避免把商用纹理带进仓库。

格式要点（与解析器一一对应，写错就等于白做）
--------------------------------------------
1. 文件头 = 2 字节主版本 + 2 字节子版本。新格式主版本 6，本脚本写子版本 2
   （与三个现有样本一致；reader.py:71 只认 6/10 + 子版本 1/2）。
2. 8BIM 区段 = `8BIM` + 4 字节键 + u32 长度 + 数据 + 对齐填充。
   **填充量 = (-length) % 4，不是 length % 4**（reader.py:97 的注释专门
   记录了这个历史坑：用错会让后续区段被整段丢弃）。
3. samp 区（v6.2）= 逐项「u32 项长 + 项体 + 对齐」。项体前 301 字节是
   笔尖头（reader.py:136 的 skip=301），字节布局由三个现有样本实测得出：
       [0]      u8 UUID 长度（36）
       [1..36]  UUID ASCII
       [37..40] u32 0x00010000
       [41..44] u32 3
       [45..48] u32 项长-49
       [49..64] 4×i32 矩形（top, left, bottom, right）
       [65..68] u32 56（样本恒为 56，照抄）
       [69..288] 220 字节 0（样本恒为 0，即全部动态取默认）
       [289..292] u32 1
       [293..296] u32 像素数据长度+15（样本实测公式）
       [297..300] u32 8（位深）
       [301..316] 4×i32 矩形（与 [49..64] 相同）
       [317..318] u16 位深（8）
       [319]     u8 压缩（0=原始，1=RLE）
       [320..]   像素数据（0=墨、255=透明，与 samples.py:64 的反转一致）
       末尾      8 个 0 字节的尾块（样本逐项验证：RLE 与 RAW 都有；漏掉会被
                 PS 判「该文件与这个版本的 Photoshop 不兼容」——任务 110 实测）
4. desc 区 = u32 版本(16) + 顶层描述符。描述符 = 名字 + 类 id + 条目数 +
   `{键 id, 类型四字符码, 值}` 序列。**enum 的值顺序是「先 typeID、后
   value」**（descriptors.py:160 的注释；写反会让 PS 读成另一对枚举）。
5. phry 区 = 「层级」描述符：Grup{Nm, zuid} + 每个预设一个 `preset` 空对象 +
   `groupEnd`。三个样本里 preset 条目的个数恒等于 desc 里的预设数。

用法
----
    # 1) 从零构造 1 支（默认输出 tests/abr-generated/one-test.abr，写后自检）
    python tools/make_test_abr.py

    # 2) 模板改写（退路；模板只读，不改样本）
    python tools/make_test_abr.py --mode template --template tests/测试用.abr \
        --preset-index 0 --out tests/abr-generated/one-test-template.abr

    # 3) 只做自检（读回并逐键比对清单里记录的期望值）
    python tools/make_test_abr.py --check tests/abr-generated/one-test.abr

    # 4) 任务 111：一次生成整套 19 支测试笔刷（六参数覆盖矩阵 + 1 支对照笔）
    python tools/make_test_abr.py --suite

    # 5) 整套重新自检（不写文件，只读回逐键比对）
    python tools/make_test_abr.py --check-suite

只写 --out 指定的 .abr 与同名 .json 自检清单；不改 src/、tests/ 既有文件。

任务 111 的扩展（本文件在 110 基础上只做了「补齐参数键」这一层）
------------------------------------------------------------------
110 只能写 5 个参数（直径/硬度/间距/角度 + 采样笔尖形状）。111 的六参数
覆盖矩阵还需要：圆度 Rndn、散布 useScatter/scatterDynamics.jitter、
纹理（patt 区 + Txtr/textureScale），以及「计算笔刷」（无 samp 笔尖、
Brsh 类为 computedBrush）。这些键的写法全部照抄三个现有样本实测结构
（不复制样本像素：笔尖与纹理都由本脚本合成）。新增写入能力清单：
  - 采样笔尖支持非正方形（宽×高任意，椭圆蒙版）→ 暴露任务 107 的
    「间距基准 = 短边/长边」补偿；
  - 计算笔刷（computedBrush）+ 圆度 Rndn；
  - 散布：useScatter + Cnt + bothAxes + scatterDynamics.jitter；
  - 纹理：patt 区（灰度、RAW、64×64 确定性噪点，自造不入商用素材）
    + useTexture/Txtr/textureScale/textureDepth/textureBlendMode。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from brush_converter.abr import AbrFile                   # noqa: E402
from brush_converter.abr import descriptors as D         # noqa: E402
from brush_converter.mapping import map_presets           # noqa: E402

# ---------------------------------------------------------------- 常量

DEFAULT_OUT = _REPO_ROOT / "tests" / "abr-generated" / "one-test.abr"
DEFAULT_TEMPLATE = _REPO_ROOT / "tests" / "测试用.abr"
#: 任务 111 整套测试笔刷的输出目录（与 110 的 one-test.abr 同目录，总数 ≤20 支）
SUITE_OUT_DIR = _REPO_ROOT / "tests" / "abr-generated"

# 从零构造时的默认笔刷参数（写入 desc 的键值就是这五项，自检逐项核对）
DEFAULT_NAME = "BC测试笔刷110"
DEFAULT_DIAMETER = 64.0     # 像素
DEFAULT_HARDNESS = 80.0     # 百分比（写入 Hrdn；采样笔尖的硬度同时体现在像素里）
DEFAULT_SPACING = 25.0      # 百分比
DEFAULT_ANGLE = 0.0         # 角度
DEFAULT_TIP_SIZE = 64       # 笔尖位图边长（像素）

# 任务 111：整套笔刷共用的固定值（改这里等于改整套测试集，清单要同步改）
SUITE_PATTERN_NAME = "BC-noise64-v140"          # 自造纹理图案名（PS 图案面板可见）
SUITE_PATTERN_UUID = "b5c0de11-1400-4a5b-8c6d-000000000111"
SUITE_PATTERN_SIZE = 64                          # 纹理边长（灰度噪点）
SUITE_PATTERN_SEED = "brushConverter-v140"       # 确定性噪点种子（可复现）
SUITE_GROUP_NAME = "BC-v140 测试笔刷组"
SUITE_GROUP_UUID = "b5c0de11-1400-4a5b-8c6d-0000000001a0"

# 从零构造用的固定 UUID（可复现；PS 的 UUID 是 8-4-4-4-12 十六进制串）
DEFAULT_UUID = "a1b2c3d4-1100-4a5b-8c6d-000000000110"
DEFAULT_GROUP_UUID = "a1b2c3d4-1100-4a5b-8c6d-0000000001a0"
DEFAULT_GROUP_NAME = "测试笔刷组"

# v6.2 的笔尖头长度（reader.py:136：subversion==2 时 skip=301）
SAMP_HEADER_V62 = 301
# 笔尖头里「矩形 + 位深 + 压缩」紧随其后的固定长度
RECT_DEPTH_COMP = 19
# 像素数据之后的固定尾块：8 个 0 字节。
# 三个样本共 21 个笔尖项逐项验证（RLE 与 RAW 都有）；任务 110 实测漏掉它，
# PS 2025 会直接报「无法完成请求，因为该文件与这个版本的 Photoshop 不兼容」。
SAMP_DATA_TRAILER = b"\x00" * 8


# ---------------------------------------------------------------- 描述符写入

@dataclass
class DescValue:
    """一个描述符值（type 为四字符码，取值语义与 descriptors.Value 对应）。"""

    type: str
    value: object = None
    unit: str | None = None       # UntF 的单位（如 #Pxl / #Prc / #Ang）
    type_id: str | None = None    # enum 的 typeID（如 BlnM）
    class_id: str | None = None   # Objc 的类 id（如 sampledBrush）
    items: list = field(default_factory=list)  # Objc 的条目 / VlLs 的值列表
    data: bytes | None = None     # tdta / alis 的原始数据


def text(s: str) -> DescValue:
    return DescValue("TEXT", s)


def untf(v: float, unit: str) -> DescValue:
    return DescValue("UntF", float(v), unit=unit)


def boolean(b: bool) -> DescValue:
    return DescValue("bool", bool(b))


def integer(v: int) -> DescValue:
    return DescValue("long", int(v))


def double(v: float) -> DescValue:
    return DescValue("doub", float(v))


def enum(type_id: str, value: str) -> DescValue:
    """enum 值：先 typeID、后 value（顺序写反 = 解析器/PS 都会读错）。"""
    return DescValue("enum", value, type_id=type_id)


def objc(class_id: str, items: list[tuple[str, DescValue]]) -> DescValue:
    return DescValue("Objc", class_id=class_id, items=list(items))


def vlls(items: list[DescValue]) -> DescValue:
    return DescValue("VlLs", items=list(items))


def _id_bytes(code: str) -> bytes:
    """描述符的键/类 id：4 字符写 u32(0)+fourCC，长 id 写 u32(长度)+ASCII。"""
    raw = code.encode("latin1")
    if len(raw) == 4:
        return struct.pack(">I", 0) + raw
    return struct.pack(">I", len(raw)) + raw


def _unicode_bytes(s: str) -> bytes:
    """unicode 字符串：u32 字符数 + UTF-16BE。

    Photoshop 自己写的字符串**结尾带一个 NUL 字符**（空名字也占 1 个字符），
    解析器 read_unicode 会 rstrip NUL（descriptors.py:130），所以这里照 PS 的
    写法补 NUL，读回才与样本一致。
    """
    s = s.rstrip("\x00") + "\x00"
    return struct.pack(">I", len(s)) + s.encode("utf-16-be")


def encode_value(v: DescValue) -> bytes:
    """把一个 DescValue 编码成「类型四字符码 + 值」。"""
    if v.type == "TEXT":
        return b"TEXT" + _unicode_bytes(str(v.value))
    if v.type == "UntF":
        return b"UntF" + str(v.unit).encode("latin1") + struct.pack(">d", float(v.value))
    if v.type == "bool":
        return b"bool" + bytes([1 if v.value else 0])
    if v.type == "long":
        return b"long" + struct.pack(">i", int(v.value))
    if v.type == "doub":
        return b"doub" + struct.pack(">d", float(v.value))
    if v.type == "enum":
        # 规范顺序：typeID 在前，enum 值在后（descriptors.py:160）
        return b"enum" + _id_bytes(str(v.type_id)) + _id_bytes(str(v.value))
    if v.type == "Objc":
        return b"Objc" + encode_descriptor(str(v.class_id), v.items)
    if v.type == "VlLs":
        out = [b"VlLs", struct.pack(">I", len(v.items))]
        out.extend(encode_value(x) for x in v.items)
        return b"".join(out)
    if v.type == "tdta":
        return b"tdta" + struct.pack(">I", len(v.data or b"")) + (v.data or b"")
    if v.type == "alis":
        return b"alis" + struct.pack(">I", len(v.data or b"")) + (v.data or b"")
    raise ValueError(f"本生成器不支持写这种描述符类型: {v.type}")


def encode_descriptor(class_id: str, items: list[tuple[str, DescValue]]) -> bytes:
    """描述符 = 名字 + 类 id + 条目数 + 逐条「键 id + 值」。"""
    out = [_unicode_bytes(""), _id_bytes(class_id), struct.pack(">I", len(items))]
    for key, val in items:
        out.append(_id_bytes(key))
        out.append(encode_value(val))
    return b"".join(out)


def encode_desc_section(items: list[tuple[str, DescValue]]) -> bytes:
    """desc/phry 区段 = u32 版本(16) + 顶层描述符。"""
    return struct.pack(">I", 16) + encode_descriptor("null", items)


# ---------------------------------------------------------------- 把解析结果转回可写模型

def value_from_parsed(v: D.Value) -> DescValue:
    """把解析器读出的 Value 转成生成器可编码的 DescValue（模板模式用）。"""
    if v.type in ("TEXT", "bool", "long", "doub"):
        return DescValue(v.type, v.value)
    if v.type == "UntF":
        return DescValue("UntF", float(v.value), unit=v.unit)
    if v.type == "enum":
        return DescValue("enum", v.value, type_id=v.type_id)
    if v.type == "Objc":
        return DescValue("Objc", class_id=v.class_id,
                         items=[(k, value_from_parsed(x)) for k, x in v.value.items()])
    if v.type == "VlLs":
        return DescValue("VlLs", items=[value_from_parsed(x) for x in v.value])
    if v.type in ("tdta", "alis"):
        return DescValue(v.type, data=v.data)
    raise ValueError(f"模板里出现本生成器不支持改写的类型: {v.type}")


def items_from_parsed(d: dict[str, D.Value]) -> list[tuple[str, DescValue]]:
    return [(k, value_from_parsed(v)) for k, v in d.items()]


# ---------------------------------------------------------------- 8BIM 区段

def encode_section(key: str, data: bytes) -> bytes:
    """`8BIM` + 4 字节键 + u32 长度 + 数据 + 对齐填充。

    填充量必须是 (-len) % 4。样本实测：用 len % 4 时只有长度为 0/2 (mod 4)
    才碰巧相等，其余会把后续区段的 8BIM 签名读偏 2 字节（reader.py:94-97）。
    """
    if len(key) != 4:
        raise ValueError(f"8BIM 区段键必须是 4 字符: {key!r}")
    pad = (-len(data)) % 4
    return b"8BIM" + key.encode("latin1") + struct.pack(">I", len(data)) + data + b"\x00" * pad


# ---------------------------------------------------------------- 采样笔尖

def make_ellipse_tip(width: int = DEFAULT_TIP_SIZE, height: int | None = None,
                     hardness: float = DEFAULT_HARDNESS) -> bytes:
    """生成一个椭圆（非正方形）软边笔尖的原始像素（每像素 1 字节）。

    任务 111 追加：间距组需要一个**非方形**采样笔尖，才能暴露任务 107 的
    「间距基准 = 短边/长边」补偿（106 §4：PS 用渲染后短边作间距基准，
    Krita 补白后用的是长边）。这里用椭圆而不是矩形，是为了在 PS/Krita
    两侧都能一眼看出笔迹方向，且不依赖任何商用样本像素。
    宽高相等时与 110 的 make_round_tip 数学公式相同（保持 one-test.abr 可复现）。
    """
    if height is None:
        height = width
    rx = width / 2.0
    ry = height / 2.0
    # 硬度 100% 时仍保留 1 像素过渡，避免出现锯齿硬边
    feather = max(1.0, (1.0 - max(0.0, min(100.0, hardness)) / 100.0) * min(rx, ry))
    pixels = bytearray(width * height)
    for y in range(height):
        dy = (y + 0.5 - ry) / ry
        for x in range(width):
            dx = (x + 0.5 - rx) / rx
            # 归一化椭圆距离（中心 0、边界 1），再用「到边界的真实像素距离」算 alpha
            norm = math.hypot(dx, dy)
            inside = (1.0 - norm) * min(rx, ry)
            if inside <= 0:
                alpha = 0.0
            else:
                alpha = min(1.0, inside / feather)
            pixels[y * width + x] = 255 - int(round(alpha * 255.0))
    return bytes(pixels)


def make_round_tip(size: int = DEFAULT_TIP_SIZE, hardness: float = DEFAULT_HARDNESS) -> bytes:
    """生成一个圆形软边笔尖的原始像素（每像素 1 字节）。

    ABR 的采样字节语义是 **0=墨、255=透明**（samples.py:64-67 会做 255-raw），
    所以这里先算不透明度 alpha（255=墨），再存 255-alpha。
    硬度控制边缘过渡宽度：100% ≈ 硬边，0% ≈ 从中心开始的渐变。
    """
    # 110 的原始实现（等宽高）保留成独立路径，保证 one-test.abr 逐字节可复现
    radius = size / 2.0
    # 硬度 100% 时仍保留 1 像素过渡，避免出现锯齿硬边
    feather = max(1.0, (1.0 - max(0.0, min(100.0, hardness)) / 100.0) * radius)
    pixels = bytearray(size * size)
    for y in range(size):
        for x in range(size):
            dx = x + 0.5 - radius
            dy = y + 0.5 - radius
            dist = math.hypot(dx, dy)
            # 圆外为 0；圆内按距离线性过渡；中心保持全不透明
            inside = radius - dist
            if inside <= 0:
                alpha = 0.0
            else:
                alpha = min(1.0, inside / feather)
            pixels[y * size + x] = 255 - int(round(alpha * 255.0))
    return bytes(pixels)


def encode_samp_item(uuid: str, width: int, height: int, pixels: bytes,
                     compression: int = 0) -> bytes:
    """编码一个 v6.2 采样笔尖项（含 u32 项长与对齐填充）。

    头部 301 字节的布局见模块文档第 3 条；其中 [45]/[293] 两个长度字段
    按样本实测公式填写，[65]=56、[289]=1、[297]=8 照抄样本常量。
    """
    raw_uuid = uuid.encode("ascii")
    if not (0 < len(raw_uuid) <= 255):
        raise ValueError("UUID 长度非法")
    # PS 的采样项：像素之后固定跟 8 个 0 字节（样本 21 项全部如此）
    pixels = pixels + SAMP_DATA_TRAILER
    datalen = len(pixels)

    body = bytearray()
    body.append(len(raw_uuid))                      # [0] UUID 长度
    body += raw_uuid                                # [1..] UUID
    body += struct.pack(">I", 0x00010000)           # [37] 样本恒为 0x00010000
    body += struct.pack(">I", 3)                    # [41] 样本恒为 3
    body += struct.pack(">I", 0)                    # [45] 占位：项长-49
    body += struct.pack(">4i", 0, 0, height, width)  # [49] 矩形
    body += struct.pack(">I", 56)                   # [65] 样本恒为 56
    body += b"\x00" * 220                           # [69..288] 动态默认值
    body += struct.pack(">I", 1)                    # [289] 样本恒为 1
    body += struct.pack(">I", 0)                    # [293] 占位：数据长+15
    body += struct.pack(">I", 8)                    # [297] 位深
    body += struct.pack(">4i", 0, 0, height, width)  # [301] 矩形（第二份）
    body += struct.pack(">H", 8)                    # [317] 位深
    body.append(compression)                        # [319] 压缩
    body += pixels                                  # [320..] 像素

    if len(body) != SAMP_HEADER_V62 + RECT_DEPTH_COMP + datalen:
        raise AssertionError("采样笔尖头长度不符")
    # [45] = 项长 - 49；[293] = 像素数据长 + 15（三个样本逐项验证过）
    struct.pack_into(">I", body, 45, len(body) - 49)
    struct.pack_into(">I", body, 293, datalen + 15)

    pad = (-len(body)) % 4
    return struct.pack(">I", len(body)) + bytes(body) + b"\x00" * pad


# ---------------------------------------------------------------- 纹理图案（patt）

def make_noise_pattern(size: int = SUITE_PATTERN_SIZE,
                       seed: str = SUITE_PATTERN_SEED) -> bytes:
    """生成确定性的灰度噪点纹理（size×size，每像素 1 字节）。

    不依赖 numpy、不读取任何外部图片：用 sha256 链式哈希展开成字节流，
    同一 seed/size 永远得到同一份像素（可复现），也避免把商用纹理带进仓库。
    纯高频噪点用来暴露「纹理缩放」的重采样差异（与任务 106 的 exp 纹理同类）。
    """
    out = bytearray()
    block = hashlib.sha256(f"{seed}:{size}".encode("utf-8")).digest()
    counter = 0
    while len(out) < size * size:
        block = hashlib.sha256(block + counter.to_bytes(4, "big")).digest()
        out += block
        counter += 1
    return bytes(out[: size * size])


def encode_pattern_record(name: str, uuid: str, width: int, height: int,
                          pixels: bytes) -> bytes:
    """编码一条灰度（颜色模式 1）纹理图案记录（含 u32 记录长与 4 字节对齐）。

    结构照抄 patterns.py 的实测注释与样本（测试用.abr 的 "Stone" 80×80 灰度图案）：
        u32 len | u32 version(1) | u32 颜色模式(1=灰度) | 2×i16 (宽,高 提示)
        | u32 名字符数(含 NUL) | 名字 UTF-16BE | u8 名字节数 + UUID ASCII
        | u32 VMA 版本(3) | u32 body 长 | body
    body = 4×u32 矩形(0,0,高,宽) + u32 通道数(样本恒 24)
           + (24+2) 个通道块；灰度只写第 1 个通道块，其余 is_written=0。
    通道块 = u32 is_written(1) | u32 块长(23+像素) | u32 depth(8)
             | 4×u32 矩形 | u16 像素位深(8) | u8 压缩(0=RAW) | 像素。
    """
    if len(pixels) != width * height:
        raise ValueError("图案像素数与宽高不符")
    ch = bytearray()
    ch += struct.pack(">I", 1)                       # is_written = 1
    ch += struct.pack(">I", 23 + len(pixels))        # 通道块长度
    ch += struct.pack(">I", 8)                       # depth
    ch += struct.pack(">4I", 0, 0, height, width)    # 通道矩形
    ch += struct.pack(">HB", 8, 0)                   # 像素位深, 压缩=RAW
    ch += pixels
    ch += struct.pack(">I", 0) * 25                  # 其余 25 个空通道块
    body = (struct.pack(">4I", 0, 0, height, width)
            + struct.pack(">I", 24) + bytes(ch))

    name_b = (name + "\x00").encode("utf-16-be")
    uuid_b = uuid.encode("ascii")
    rec = (struct.pack(">II", 1, 1)                   # version=1, 颜色模式=灰度
           + struct.pack(">hh", width, height)        # 宽高提示
           + struct.pack(">I", len(name) + 1) + name_b
           + bytes([len(uuid_b)]) + uuid_b
           + struct.pack(">II", 3, len(body)) + body)
    pad = (-len(rec)) % 4
    return struct.pack(">I", len(rec)) + rec + b"\x00" * pad


# ---------------------------------------------------------------- 预设描述符

def _dyn(bvty: int = 0, jitter: float = 0.0, minimum: float = 0.0) -> DescValue:
    """Python 侧最常见的动态对象（brVr）：控制源 + 抖动 + 最小直径。"""
    return objc("brVr", [
        ("bVTy", integer(bvty)),
        ("fStp", integer(25)),
        ("jitter", untf(jitter, "#Prc")),
        ("Mnm ", untf(minimum, "#Prc")),
    ])


def build_preset(name: str, diameter: float, hardness: float, spacing: float,
                 angle: float, tip_uuid: str, tip_name: str,
                 roundness: float = 100.0,
                 scatter_jitter: float | None = None,
                 scatter_both_axes: bool = False,
                 texture: dict | None = None) -> DescValue:
    """造一个「采样笔刷」预设（键集与样本 海怪笔刷#0 一致，另加 Hrdn）。

    键集说明：海怪样本的第一个预设只有 14 个顶层键（PS 自己写的、PS 能打开），
    证明 PS 对缺省键有默认值，不需要把 40 多个键全写出来。
    Hrdn 不是 PS 给采样笔刷写的键（采样笔刷的硬度烘在像素里），这里补上
    是为了让本项目解析器能读回「硬度」并与写入值比对；多一个键 PS 会忽略
    （模板路线生成的文件同样带 Hrdn，PS 实测能打开）。

    任务 111 追加三个可选参数（默认值 = 110 的原行为，写成字节逐字节不变）：
      roundness       圆度（Rndn，%，100=圆；角度组用 30 让椭圆可测）；
      scatter_jitter  散布量（%，None=不启用；写了才加 useScatter 一族键）；
      texture         纹理设置 dict（None=不启用；写了才加 useTexture 一族键）。
    """
    brsh = objc("sampledBrush", [
        ("Dmtr", untf(diameter, "#Pxl")),
        ("Hrdn", untf(hardness, "#Prc")),
        ("Angl", untf(angle, "#Ang")),
        ("Rndn", untf(roundness, "#Prc")),
        ("Nm  ", text(tip_name)),
        ("Spcn", untf(spacing, "#Prc")),
        ("Intr", boolean(True)),
        ("flipX", boolean(False)),
        ("flipY", boolean(False)),
        ("sampledData", text(tip_uuid)),
    ])
    items: list[tuple[str, DescValue]] = [
        ("Nm  ", text(name)),
        ("Brsh", brsh),
        ("useTipDynamics", boolean(False)),
        ("useScatter", boolean(scatter_jitter is not None)),
        ("dualBrush", objc("dualBrush", [("useDualBrush", boolean(False))])),
        ("brushGroup", objc("brushGroup", [("useBrushGroup", boolean(False))])),
        ("useTexture", boolean(texture is not None)),
        ("usePaintDynamics", boolean(False)),
        ("useColorDynamics", boolean(False)),
        ("Wtdg", boolean(False)),
        ("Nose", boolean(False)),
        ("Rpt ", boolean(False)),
        ("useBrushSize", boolean(False)),
        ("useBrushPose", boolean(False)),
    ]
    # 散布键只在启用时追加（保持 110 默认键序不变）
    if scatter_jitter is not None:
        items.extend(_scatter_items(scatter_jitter, scatter_both_axes))
    # 纹理键只在启用时追加
    if texture is not None:
        items.extend(_texture_items(texture))
    return objc("brushPreset", items)


def _scatter_items(jitter: float, both_axes: bool) -> list[tuple[str, DescValue]]:
    """散布一族键：useScatter 已在外层写，这里补计数/双轴/散布动态。

    样本写法（测试用.abr preset 3）：useScatter + Cnt(doub) + bothAxes(bool)
    + countDynamics(brVr) + scatterDynamics(brVr)。
    散布量真正来源是 scatterDynamics.jitter（映射层注释与矩阵同口径）。
    """
    return [
        ("Cnt ", double(1.0)),
        ("bothAxes", boolean(both_axes)),
        ("countDynamics", _dyn()),
        ("scatterDynamics", _dyn(jitter=jitter)),
    ]


def _texture_items(texture: dict) -> list[tuple[str, DescValue]]:
    """纹理一族键（键名与样本一致；缺省键由 PS 取默认值）。"""
    return [
        ("brushProjection", boolean(False)),
        ("TxtC", boolean(True)),
        ("interpretation", boolean(True)),
        ("textureBlendMode", enum("BlnM", str(texture["blend"]))),
        ("textureDepth", untf(float(texture["depth"]), "#Prc")),
        ("minimumDepth", untf(0.0, "#Prc")),
        ("textureDepthDynamics", _dyn()),
        ("Txtr", objc("Ptrn", [
            ("Nm  ", text(str(texture["name"]))),
            ("Idnt", text(str(texture["uuid"]))),
        ])),
        ("textureScale", untf(float(texture["scale"]), "#Prc")),
        ("InvT", boolean(bool(texture.get("invert", False)))),
        ("protectTexture", boolean(False)),
        ("textureBrightness", integer(0)),
        ("textureContrast", integer(0)),
    ]


def build_computed_preset(name: str, diameter: float, hardness: float, spacing: float,
                          angle: float, roundness: float = 100.0,
                          scatter_jitter: float | None = None,
                          scatter_both_axes: bool = False,
                          texture: dict | None = None) -> DescValue:
    """造一个「计算笔刷」预设（Brsh 类 = computedBrush，无采样笔尖）。

    键集照抄 tests/定量测试专用.abr 的 computedBrush：
    Dmtr/Hrdn/Angl/Rndn/Spcn/Intr/flipX/flipY（无 sampledData、无 Brsh.Nm）。
    计算笔刷的尺寸/硬度/圆度是**真参数**（不是烘在像素里），所以尺寸组、
    硬度组、角度组、散布组、缩放组都用它做载体。
    """
    brsh = objc("computedBrush", [
        ("Dmtr", untf(diameter, "#Pxl")),
        ("Hrdn", untf(hardness, "#Prc")),
        ("Angl", untf(angle, "#Ang")),
        ("Rndn", untf(roundness, "#Prc")),
        ("Spcn", untf(spacing, "#Prc")),
        ("Intr", boolean(True)),
        ("flipX", boolean(False)),
        ("flipY", boolean(False)),
    ])
    items: list[tuple[str, DescValue]] = [
        ("Nm  ", text(name)),
        ("Brsh", brsh),
        ("useTipDynamics", boolean(False)),
        ("useScatter", boolean(scatter_jitter is not None)),
        ("dualBrush", objc("dualBrush", [("useDualBrush", boolean(False))])),
        ("brushGroup", objc("brushGroup", [("useBrushGroup", boolean(False))])),
        ("useTexture", boolean(texture is not None)),
        ("usePaintDynamics", boolean(False)),
        ("useColorDynamics", boolean(False)),
        ("Wtdg", boolean(False)),
        ("Nose", boolean(False)),
        ("Rpt ", boolean(False)),
        ("useBrushSize", boolean(False)),
        ("useBrushPose", boolean(False)),
    ]
    if scatter_jitter is not None:
        items.extend(_scatter_items(scatter_jitter, scatter_both_axes))
    if texture is not None:
        items.extend(_texture_items(texture))
    return objc("brushPreset", items)


def build_phry(group_name: str, group_uuid: str, preset_count: int) -> bytes:
    """phry 区：Grup{Nm, zuid} + 每个预设一个 preset 空对象 + groupEnd。

    样本实测：preset 条目数恒等于 desc 预设数（测试用 6/6、海怪 20/20）。
    """
    entries = [objc("Grup", [("Nm  ", text(group_name)), ("zuid", text(group_uuid))])]
    entries.extend(objc("preset", []) for _ in range(preset_count))
    entries.append(objc("groupEnd", []))
    return encode_desc_section([("hierarchy", vlls(entries))])


# ---------------------------------------------------------------- 从零构造

def build_abr_zero(name: str, diameter: float, hardness: float, spacing: float,
                   angle: float, tip_size: int, uuid: str,
                   kind: str = "sampled",
                   tip_width: int | None = None, tip_height: int | None = None,
                   tip_hardness: float | None = None,
                   roundness: float = 100.0,
                   scatter_jitter: float | None = None,
                   scatter_both_axes: bool = False,
                   texture: dict | None = None) -> bytes:
    """从零拼一个 v6.2 ABR：samp + [patt] + desc(1 个预设) + phry(1 个组)。

    默认参数（kind=sampled、方形笔尖、无散布/纹理）与 110 的产物逐字节一致，
    保证 one-test.abr 可复现；任务 111 只通过新参数追加能力。
    区段顺序照抄三个样本：samp → patt → desc → phry。
    """
    if kind == "computed":
        # 计算笔刷：samp 区长度为 0（样本 定量测试专用.abr 实测 PS 能打开）
        preset = build_computed_preset(name, diameter, hardness, spacing, angle,
                                       roundness=roundness,
                                       scatter_jitter=scatter_jitter,
                                       scatter_both_axes=scatter_both_axes,
                                       texture=texture)
        sections = [encode_section("samp", b"")]
    else:
        w = tip_width if tip_width is not None else tip_size
        h = tip_height if tip_height is not None else tip_size
        hard = hardness if tip_hardness is None else tip_hardness
        if (w, h) == (tip_size, tip_size) and tip_hardness is None:
            pixels = make_round_tip(tip_size, hardness)   # 110 原路径
        else:
            pixels = make_ellipse_tip(w, h, hard)
        samp = encode_samp_item(uuid, w, h, pixels, compression=0)
        preset = build_preset(name, diameter, hardness, spacing, angle, uuid,
                              name + " 形状", roundness=roundness,
                              scatter_jitter=scatter_jitter,
                              scatter_both_axes=scatter_both_axes,
                              texture=texture)
        sections = [encode_section("samp", samp)]

    if texture is not None:
        pattern = make_noise_pattern(int(texture.get("size", SUITE_PATTERN_SIZE)),
                                     str(texture.get("seed", SUITE_PATTERN_SEED)))
        psize = int(texture.get("size", SUITE_PATTERN_SIZE))
        record = encode_pattern_record(str(texture["name"]), str(texture["uuid"]),
                                       psize, psize, pattern)
        sections.append(encode_section("patt", record))

    desc = encode_desc_section([("Brsh", vlls([preset]))])
    phry = build_phry(DEFAULT_GROUP_NAME, DEFAULT_GROUP_UUID, 1)
    sections.extend([encode_section("desc", desc), encode_section("phry", phry)])
    return struct.pack(">HH", 6, 2) + b"".join(sections)


# ---------------------------------------------------------------- 模板改写

def _find_preset_tree(preset: dict[str, D.Value]) -> list[tuple[str, DescValue]]:
    return items_from_parsed(preset)


def _set_key(items: list[tuple[str, DescValue]], key: str, val: DescValue) -> None:
    """按键替换；键不存在就追加（保持原有条目顺序）。"""
    for i, (k, _v) in enumerate(items):
        if k == key:
            items[i] = (key, val)
            return
    items.append((key, val))


def _get_obj_items(items: list[tuple[str, DescValue]], key: str) -> list[tuple[str, DescValue]]:
    for k, v in items:
        if k == key and v.type == "Objc":
            return v.items
    raise KeyError(f"预设里缺少对象键 {key!r}")


def _set_obj_key(items: list[tuple[str, DescValue]], obj_key: str,
                 key: str, val: DescValue) -> None:
    _set_key(_get_obj_items(items, obj_key), key, val)


def _drop_key(items: list[tuple[str, DescValue]], key: str) -> None:
    for i, (k, _v) in enumerate(items):
        if k == key:
            del items[i]
            return


def _samp_item_slice(samp: bytes, tip_offset: int) -> bytes:
    """按笔尖在 samp 区里的偏移，切出「u32 项长 + 项体 + 填充」的原文。"""
    (size,) = struct.unpack_from(">I", samp, tip_offset)
    end = tip_offset + 4 + size + (-size % 4)
    end = min(end, len(samp))
    return samp[tip_offset:end]


def build_abr_from_template(template_path: Path, preset_index: int, name: str,
                            diameter: float, hardness: float, spacing: float,
                            angle: float, uuid: str) -> tuple[bytes, dict]:
    """以现有 .abr 为模板，重打包「只含 1 支笔刷」的新文件。

    取什么：模板第 preset_index 个预设的描述符树 + 它 sampledData 指向的
    samp 笔尖项原文。改什么：名称/直径/硬度/间距/角度。不复制 patt
    （纹理置 false），避免把商用纹理带进仓库。
    """
    abr = AbrFile.parse(template_path)
    if "desc" not in abr.sections or "samp" not in abr.sections:
        raise ValueError("模板缺少 desc 或 samp 区，不能走模板改写")
    top = D.parse_descriptor_block(abr.sections["desc"].data)
    presets = D.iter_brush_presets(top)
    if not presets:
        raise ValueError("模板 desc 里没有 Brsh 预设")
    if not (0 <= preset_index < len(presets)):
        raise ValueError(f"--preset-index 越界：模板只有 {len(presets)} 个预设")
    preset = presets[preset_index]

    # 1) 找到预设对应的采样笔尖（按 sampledData UUID 关联）
    sd = D.get_obj(preset, "Brsh")
    if sd is None:
        raise ValueError("模板预设没有 Brsh 对象（可能是计算笔刷，无法取像素）")
    sd_uuid = D.get_text(sd, "sampledData")
    tip = next((t for t in abr.tips if (t.uuid or "").lower() == (sd_uuid or "").lower()), None)
    if tip is None:
        raise ValueError("模板预设没有关联的采样笔尖（sampledData 对不上 samp 区）")
    item_bytes = _samp_item_slice(abr.sections["samp"].data, tip.source_offset)

    # 2) 复制描述符树并替换五项键值
    items = _find_preset_tree(preset)
    _set_key(items, "Nm  ", text(name))
    _set_obj_key(items, "Brsh", "Dmtr", untf(diameter, "#Pxl"))
    _set_obj_key(items, "Brsh", "Hrdn", untf(hardness, "#Prc"))
    _set_obj_key(items, "Brsh", "Spcn", untf(spacing, "#Prc"))
    _set_obj_key(items, "Brsh", "Angl", untf(angle, "#Ang"))
    # 模板的笔尖 UUID 原样保留（samp 项就是它的像素），只改名字
    _set_obj_key(items, "Brsh", "Nm  ", text(name + " 形状"))

    # 3) 纹理不随文件复制：关掉 useTexture，并去掉引用图案的键
    _set_key(items, "useTexture", boolean(False))
    for key in ("Txtr", "TxtC", "textureBlendMode", "textureDepth", "minimumDepth",
                "textureDepthDynamics", "textureScale", "InvT", "protectTexture",
                "textureBrightness", "textureContrast", "interpretation"):
        _drop_key(items, key)

    desc = encode_desc_section([("Brsh", vlls([objc("brushPreset", items)]))])
    phry = build_phry(DEFAULT_GROUP_NAME, DEFAULT_GROUP_UUID, 1)
    data = (struct.pack(">HH", 6, 2)
            + encode_section("samp", item_bytes)
            + encode_section("desc", desc)
            + encode_section("phry", phry))
    meta = {"tip_uuid": tip.uuid, "tip_size": (tip.width, tip.height),
            "template_preset": preset_index}
    return data, meta


# ---------------------------------------------------------------- 自检（解析器回读）

def verify(path: Path, expect: dict) -> tuple[bool, list[tuple[str, object, object, bool]]]:
    """用本项目解析器读回，逐项比对期望值。返回 (是否全过, 表格行)。"""
    rows: list[tuple[str, object, object, bool]] = []

    def ck(label: str, got, want) -> None:
        rows.append((label, got, want, got == want))

    abr = AbrFile.parse(path)
    ck("主版本", abr.version, 6)
    ck("子版本", abr.subversion, 2)
    ck("区段", list(abr.sections), expect["sections"])
    want_diag = expect.get("diagnostics", [])
    ck("诊断信息", abr.diagnostics, want_diag)
    kind = expect.get("kind", "sampled")
    ck("采样笔尖数", len(abr.tips), 0 if kind == "computed" else 1)
    if len(abr.tips) == 1:
        tip = abr.tips[0]
        ck("笔尖尺寸(WxH)", (tip.width, tip.height), tuple(expect["tip_size"]))
        ck("笔尖位深", tip.depth_bits, 8)
        ck("笔尖 UUID", (tip.uuid or "").lower(), expect["uuid"].lower())

    presets = map_presets(abr)
    ck("预设数", len(presets), 1)
    if len(presets) == 1:
        p = presets[0]
        ck("名称", p.name, expect["name"])
        ck("直径", p.diameter, expect["diameter"])
        ck("硬度", p.hardness, expect["hardness"])
        ck("间距(%)", p.spacing * 100.0, expect["spacing"])
        ck("角度", p.angle, expect["angle"])
        ck("圆度", p.roundness, expect.get("roundness", 100.0))
        ck("笔尖已关联", p.is_computed, kind == "computed")
        if kind == "sampled":
            # 蒙版数组形状是 (高, 宽)，期望值里存的是 [宽, 高]，这里要反过来
            w, h = expect["tip_size"][0], expect["tip_size"][1]
            ck("蒙版尺寸", None if p.tip_gray is None else tuple(p.tip_gray.shape), (h, w))
            if p.tip_gray is not None:
                if expect.get("check_tip_center"):
                    # 从零构造的椭圆笔尖：中心必须是墨（255）、角落必须是透明（0）
                    ck("蒙版中心值", int(p.tip_gray[p.tip_gray.shape[0] // 2,
                                                   p.tip_gray.shape[1] // 2]), 255)
                    ck("蒙版角落值", int(p.tip_gray[0, 0]), 0)
                else:
                    # 模板笔尖形状任意：只要求「有墨也有透明」，否则 PS/Krita 里看不见
                    ck("蒙版含墨(最大值)", int(p.tip_gray.max()), 255)
                    ck("蒙版含透明(最小值)", int(p.tip_gray.min()), 0)

        # 散布（任务 111）：期望里 scatter 为 None 表示未启用
        want_scatter = expect.get("scatter")
        ck("散布启用", p.scatter, want_scatter is not None)
        if want_scatter is not None:
            ck("散布量(%)", p.scatter_amount, want_scatter["jitter"])
            ck("散布双轴", p.scatter_both_axes, want_scatter["both_axes"])

        # 纹理（任务 111）：期望里 texture 为 None 表示未启用
        want_tex = expect.get("texture")
        ck("纹理启用", p.texture is not None, want_tex is not None)
        if want_tex is not None and p.texture is not None:
            ck("纹理缩放(%)", p.texture.scale, want_tex["scale"])
            ck("纹理图案名", p.texture.name, want_tex["name"])
            ck("纹理混合模式", p.texture.blend_mode, want_tex["blend"])
            ck("纹理深度(%)", p.texture.depth, want_tex["depth"])
            ck("纹理位图", None if p.texture.image is None else tuple(p.texture.image.shape[:2]),
               (want_tex["size"], want_tex["size"]))
            ck("图案数(patt)", len(abr.patterns), 1)


    ok = all(r[3] for r in rows)
    return ok, rows


def print_table(rows: list[tuple[str, object, object, bool]]) -> None:
    print("自检表（本项目解析器回读 vs 写入/期望）：")
    print(f"  {'项目':<12} {'回读值':<28} {'期望值':<28} 结果")
    for label, got, want, ok in rows:
        print(f"  {label:<12} {str(got):<28} {str(want):<28} {'通过' if ok else '失败'}")
    print(f"  合计 {sum(1 for r in rows if r[3])}/{len(rows)} 项通过")


# ---------------------------------------------------------------- 任务 111：整套测试笔刷

def _texture_spec(scale: float, depth: float = 100.0, blend: str = "Mltp") -> dict:
    """任务 111 的纹理规格（固定用自造噪点图案，只让 scale 变化）。"""
    return {
        "name": SUITE_PATTERN_NAME,
        "uuid": SUITE_PATTERN_UUID,
        "scale": float(scale),
        "depth": float(depth),
        "blend": blend,
        "size": SUITE_PATTERN_SIZE,
        "seed": SUITE_PATTERN_SEED,
    }


def _tip_uuid(index: int) -> str:
    """整套笔刷里每个采样笔尖的固定 UUID（同索引每次生成不变，便于复现）。"""
    return f"b5c0de22-1400-4a5b-8c6d-{index:012d}"


def test_suite() -> list[dict]:
    """任务 111 的 19 支测试笔刷规格（中文注释即清单依据）。

    约定：**组内只有一个参数不同**；组间基础可以不同（参数载体不同），
    control-default 是无动态/无纹理的全局对照。参数组与清单
    `docs/测试笔刷验证清单.md` 一一对应，改这里必须同步改清单。
    """
    items: list[dict] = []

    # 0) 全默认对照笔（与 110 的 one-test.abr 同参数，只换名字与 UUID）
    items.append(dict(
        file="control-default.abr", name="BC-v140-control-default",
        group="control", param="无（对照）", value=None,
        diameter=64.0, hardness=80.0, spacing=25.0, angle=0.0,
        kind="sampled", tip_size=64, tip_hardness=80.0, uuid=_tip_uuid(1),
        note="不改任何参数：圆形 64px 软边采样笔尖，硬度 80、间距 25%、角度 0、无散布/纹理",
    ))

    # 1) 间距组：非方形采样笔尖（128×100，短/长 = 0.78125 ≈ 106 的 143/183），
    #    只改 Spcn 10/50/100 → 暴露任务 107 的「间距基准 = 短边/长边」补偿。
    for v in (10.0, 50.0, 100.0):
        items.append(dict(
            file=f"spacing-{int(v):03d}.abr", name=f"BC-v140-spacing-{int(v):03d}",
            group="spacing", param="间距 Spcn", value=v,
            diameter=112.0, hardness=80.0, spacing=v, angle=0.0,
            kind="sampled", tip_width=128, tip_height=100, tip_hardness=100.0,
            uuid=_tip_uuid(10 + int(v)), note="非方形采样笔尖 128×100（短/长 0.78125），硬边；只改间距",
        ))

    # 2) 尺寸组：计算笔刷，直径 20/100/300，其余固定（硬度 100、间距 100）
    for v in (20.0, 100.0, 300.0):
        items.append(dict(
            file=f"size-{int(v):03d}.abr", name=f"BC-v140-size-{int(v):03d}",
            group="size", param="尺寸 Dmtr", value=v,
            diameter=v, hardness=100.0, spacing=100.0, angle=0.0,
            kind="computed", note="计算笔刷（圆度 100、硬度 100、间距 100）；只改直径",
        ))

    # 3) 硬度组：计算笔刷 D=200，硬度 0/50/100，其余固定
    for v in (0.0, 50.0, 100.0):
        items.append(dict(
            file=f"hardness-{int(v):03d}.abr", name=f"BC-v140-hardness-{int(v):03d}",
            group="hardness", param="硬度 Hrdn", value=v,
            diameter=200.0, hardness=v, spacing=100.0, angle=0.0,
            kind="computed", note="计算笔刷 D=200、间距 100；只改硬度",
        ))

    # 4) 散布组：计算笔刷 D=100、间距 50，散布 0/100/300（单轴，笔迹横向为 x）
    for v in (0.0, 100.0, 300.0):
        items.append(dict(
            file=f"scatter-{int(v):03d}.abr", name=f"BC-v140-scatter-{int(v):03d}",
            group="scatter", param="散布 scatterDynamics.jitter", value=v,
            diameter=100.0, hardness=100.0, spacing=50.0, angle=0.0,
            kind="computed", scatter=v, scatter_both_axes=False,
            note="计算笔刷 D=100、间距 50、单轴散布；只改散布量",
        ))

    # 5) 角度组：计算笔刷 D=200、圆度 30（椭圆可测主轴），角度 0/45/90
    for v in (0.0, 45.0, 90.0):
        items.append(dict(
            file=f"angle-{int(v):03d}.abr", name=f"BC-v140-angle-{int(v):03d}",
            group="angle", param="角度 Angl", value=v,
            diameter=200.0, hardness=100.0, spacing=100.0, angle=v,
            kind="computed", roundness=30.0,
            note="计算笔刷 D=200、圆度 30%（椭圆）；只改角度",
        ))

    # 6) 缩放组：计算笔刷 D=200、间距 1000（单笔尖），纹理 50/100/200
    for v in (50.0, 100.0, 200.0):
        items.append(dict(
            file=f"scale-{int(v):03d}.abr", name=f"BC-v140-scale-{int(v):03d}",
            group="scale", param="纹理缩放 textureScale", value=v,
            diameter=200.0, hardness=100.0, spacing=1000.0, angle=0.0,
            kind="computed", texture=_texture_spec(v),
            note="计算笔刷 D=200、间距 1000（单笔尖）、纹理深度 100/正片叠底；只改纹理缩放",
        ))

    return items


def _spec_to_expect(spec: dict, out_name: str) -> dict:
    """把规格转成 --check 用的期望值清单。"""
    texture = spec.get("texture")
    kind = spec.get("kind", "sampled")
    if kind == "computed":
        tip_size = [0, 0]
        diagnostics = ["samp 区为空，未包含任何采样笔尖"]
    else:
        w = spec.get("tip_width") or spec.get("tip_size", DEFAULT_TIP_SIZE)
        h = spec.get("tip_height") or spec.get("tip_size", DEFAULT_TIP_SIZE)
        tip_size = [w, h]
        diagnostics = []
    sections = ["samp"] + (["patt"] if texture else []) + ["desc", "phry"]
    return {
        "mode": "zero",
        "kind": kind,
        "name": spec["name"],
        "diameter": float(spec["diameter"]),
        "hardness": float(spec["hardness"]),
        "spacing": float(spec["spacing"]),
        "angle": float(spec["angle"]),
        "roundness": float(spec.get("roundness", 100.0)),
        "uuid": spec.get("uuid", ""),
        "tip_size": tip_size,
        "sections": sections,
        "diagnostics": diagnostics,
        "scatter": (None if spec.get("scatter") is None else
                    {"jitter": float(spec["scatter"]),
                     "both_axes": bool(spec.get("scatter_both_axes", False))}),
        "texture": (None if texture is None else
                    {"scale": float(texture["scale"]),
                     "name": texture["name"],
                     "blend": texture["blend"],
                     "depth": float(texture["depth"]),
                     "size": int(texture["size"])}),
        "check_tip_center": kind == "sampled",
        "out": out_name,
    }


def generate_spec(spec: dict) -> tuple[bytes, dict]:
    """按规格生成 .abr 字节与期望值清单（suite 与单支 CLI 共用）。"""
    kind = spec.get("kind", "sampled")
    texture = spec.get("texture")
    data = build_abr_zero(
        spec["name"], float(spec["diameter"]), float(spec["hardness"]),
        float(spec["spacing"]), float(spec["angle"]),
        int(spec.get("tip_size", DEFAULT_TIP_SIZE)), spec.get("uuid", DEFAULT_UUID),
        kind=kind,
        tip_width=spec.get("tip_width"), tip_height=spec.get("tip_height"),
        tip_hardness=spec.get("tip_hardness"),
        roundness=float(spec.get("roundness", 100.0)),
        scatter_jitter=(None if spec.get("scatter") is None else float(spec["scatter"])),
        scatter_both_axes=bool(spec.get("scatter_both_axes", False)),
        texture=texture,
    )
    return data, _spec_to_expect(spec, Path(spec["file"]).name)


def run_suite(out_dir: Path) -> int:
    """生成整套 19 支测试笔刷并逐支自检；任一支失败返回 1。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    print("整套测试笔刷（任务 111，共 %d 支；含 110 的 one-test.abr 后 ≤20 支）" % len(test_suite()))
    print(f"  {'文件':<22} {'笔名':<26} {'组':<9} {'取值':<8} 结果")
    failed = 0
    for spec in test_suite():
        data, expect = generate_spec(spec)
        out_path = out_dir / spec["file"]
        out_path.write_bytes(data)
        expect_path = out_path.with_suffix(".json")
        expect_path.write_text(json.dumps(expect, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        ok, rows = verify(out_path, expect)
        passed = sum(1 for r in rows if r[3])
        value = "" if spec.get("value") is None else f"{spec['value']:g}"
        result = f"{'通过' if ok else '失败'} {passed}/{len(rows)}"
        print(f"  {spec['file']:<22} {spec['name']:<26} {spec['group']:<9} {value:<8} {result}")
        if not ok:
            failed += 1
            for label, got, want, good in rows:
                if not good:
                    print(f"      ! {label}: 回读={got!r} 期望={want!r}")
    print(f"整套自检：{len(test_suite()) - failed}/{len(test_suite())} 支通过")
    return 0 if failed == 0 else 1


def check_suite(out_dir: Path) -> int:
    """对整套已生成文件重跑自检（不写文件）。"""
    failed = 0
    items = test_suite()
    for spec in items:
        out_path = out_dir / spec["file"]
        meta_path = out_path.with_suffix(".json")
        if not out_path.exists() or not meta_path.exists():
            print(f"  {spec['file']}: 缺文件或缺自检清单")
            failed += 1
            continue
        expect = json.loads(meta_path.read_text(encoding="utf-8"))
        expect["tip_size"] = tuple(expect["tip_size"])
        ok, rows = verify(out_path, expect)
        passed = sum(1 for r in rows if r[3])
        print(f"  {spec['file']:<22} {'通过' if ok else '失败'} {passed}/{len(rows)}")
        if not ok:
            failed += 1
            for label, got, want, good in rows:
                if not good:
                    print(f"      ! {label}: 回读={got!r} 期望={want!r}")
    print(f"整套重新自检：{len(items) - failed}/{len(items)} 支通过")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------- 入口

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 1 支测试用 .abr（任务 110 可行性门）")
    ap.add_argument("--mode", choices=("zero", "template"), default="zero",
                    help="zero=从零构造（默认）；template=以现有 .abr 为模板改写")
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE),
                    help="模板 .abr（只读；template 模式用）")
    ap.add_argument("--preset-index", type=int, default=0,
                    help="模板里取第几个预设（template 模式用，默认 0）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出 .abr 路径")
    ap.add_argument("--name", default=DEFAULT_NAME, help="笔刷名称")
    ap.add_argument("--diameter", type=float, default=DEFAULT_DIAMETER, help="直径（像素）")
    ap.add_argument("--hardness", type=float, default=DEFAULT_HARDNESS, help="硬度（百分比）")
    ap.add_argument("--spacing", type=float, default=DEFAULT_SPACING, help="间距（百分比）")
    ap.add_argument("--angle", type=float, default=DEFAULT_ANGLE, help="角度")
    ap.add_argument("--tip-size", type=int, default=DEFAULT_TIP_SIZE,
                    help="从零构造时笔尖位图边长（像素）")
    ap.add_argument("--uuid", default=DEFAULT_UUID, help="从零构造时笔尖 UUID")
    ap.add_argument("--kind", choices=("sampled", "computed"), default="sampled",
                    help="从零构造的笔刷类型（computed=计算笔刷，无采样笔尖）")
    ap.add_argument("--tip-width", type=int, default=None,
                    help="从零构造时笔尖位图宽（像素；与 --tip-height 不同即非方形）")
    ap.add_argument("--tip-height", type=int, default=None, help="从零构造时笔尖位图高（像素）")
    ap.add_argument("--tip-hardness", type=float, default=None,
                    help="烘进笔尖像素的硬度（默认跟 --hardness；采样笔刷专用）")
    ap.add_argument("--roundness", type=float, default=100.0, help="圆度（%，100=圆）")
    ap.add_argument("--scatter", type=float, default=None,
                    help="散布量（%%）；给了才启用 useScatter 一族键")
    ap.add_argument("--scatter-both-axes", action="store_true", help="散布沿两轴")
    ap.add_argument("--texture-scale", type=float, default=None,
                    help="纹理缩放（%%）；给了才启用纹理并写 patt 区（自造噪点图案）")
    ap.add_argument("--texture-depth", type=float, default=100.0, help="纹理深度（百分比）")
    ap.add_argument("--texture-blend", default="Mltp",
                    help="纹理混合模式（PS 成员 id，如 Mltp/Hght/linearHeight）")
    ap.add_argument("--suite", action="store_true",
                    help="任务 111：生成整套 19 支测试笔刷到 tests/abr-generated/")
    ap.add_argument("--suite-out", default=str(SUITE_OUT_DIR),
                    help="整套输出目录（默认 tests/abr-generated/）")
    ap.add_argument("--check-suite", action="store_true",
                    help="任务 111：重新自检整套笔刷（不写文件）")
    ap.add_argument("--check", default=None,
                    help="只自检：读回指定 .abr（同目录同名 .json 提供期望值）")
    ap.add_argument("--no-check", action="store_true", help="写完不做自检")
    args = ap.parse_args(argv)

    if args.suite:
        return run_suite(Path(args.suite_out).resolve())
    if args.check_suite:
        return check_suite(Path(args.suite_out).resolve())

    if args.check:
        check_path = Path(args.check).resolve()
        meta_path = check_path.with_suffix(".json")
        if not meta_path.exists():
            print(f"缺少自检清单：{meta_path}", file=sys.stderr)
            return 2
        expect = json.loads(meta_path.read_text(encoding="utf-8"))
        for key in ("tip_size",):
            expect[key] = tuple(expect[key])
        ok, rows = verify(check_path, expect)
        print_table(rows)
        return 0 if ok else 1

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "zero":
        tip_w = args.tip_width if args.tip_width is not None else args.tip_size
        tip_h = args.tip_height if args.tip_height is not None else args.tip_size
        texture = (None if args.texture_scale is None else
                   _texture_spec(args.texture_scale, args.texture_depth, args.texture_blend))
        data = build_abr_zero(args.name, args.diameter, args.hardness, args.spacing,
                              args.angle, args.tip_size, args.uuid,
                              kind=args.kind,
                              tip_width=args.tip_width, tip_height=args.tip_height,
                              tip_hardness=args.tip_hardness,
                              roundness=args.roundness,
                              scatter_jitter=args.scatter,
                              scatter_both_axes=args.scatter_both_axes,
                              texture=texture)
        expect = {
            "mode": "zero",
            "kind": args.kind,
            "name": args.name,
            "diameter": args.diameter,
            "hardness": args.hardness,
            "spacing": args.spacing,
            "angle": args.angle,
            "roundness": args.roundness,
            "uuid": args.uuid,
            "tip_size": [tip_w, tip_h],
            "sections": (["samp"] + (["patt"] if texture else []) + ["desc", "phry"]),
            "diagnostics": (["samp 区为空，未包含任何采样笔尖"]
                            if args.kind == "computed" else []),
            "scatter": (None if args.scatter is None else
                        {"jitter": args.scatter, "both_axes": args.scatter_both_axes}),
            "texture": (None if texture is None else
                        {"scale": texture["scale"], "name": texture["name"],
                         "blend": texture["blend"], "depth": texture["depth"],
                         "size": texture["size"]}),
            "check_tip_center": args.kind == "sampled",
        }
    else:
        data, meta = build_abr_from_template(Path(args.template).resolve(),
                                             args.preset_index, args.name,
                                             args.diameter, args.hardness,
                                             args.spacing, args.angle, args.uuid)
        expect = {
            "mode": "template",
            "name": args.name,
            "diameter": args.diameter,
            "hardness": args.hardness,
            "spacing": args.spacing,
            "angle": args.angle,
            "uuid": meta["tip_uuid"],
            "tip_size": list(meta["tip_size"]),
            "sections": ["samp", "desc", "phry"],
            "template": Path(args.template).name,
            "template_preset": meta["template_preset"],
        }

    out_path.write_bytes(data)
    print(f"已写出：{out_path}（{len(data)} 字节，模式={args.mode}）")

    if args.no_check:
        return 0
    expect["out"] = out_path.name
    expect_path = out_path.with_suffix(".json")
    expect_path.write_text(json.dumps(expect, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"已写出自检清单：{expect_path.name}")
    ok, rows = verify(out_path, expect)
    print_table(rows)
    if not ok:
        print("自检失败：回读值与写入值不一致（详见上表）", file=sys.stderr)
        return 1
    print("自检通过：1 支笔刷，键值逐项一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
