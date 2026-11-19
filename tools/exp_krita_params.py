# -*- coding: utf-8 -*-
"""实验（Krita 侧）：按 PS 参数生成 .kpp，并串行渲染到 tests/baselines/exp/krita/。

【本脚本做什么】
实验矩阵（与 PS 侧完全一致，命名逐字照用）：
  矩阵 A（缩放 × 深度，b=c=中性）：mode ∈ {multiply, linearHeight} × depth ∈ {10,60,100}
                                  × scale ∈ {50,100,150,200} → 24 组
  矩阵 B（亮度/对比度，固定 multiply/d100/s100）：(b,c) ∈ {(-50,0),(50,0),(0,50),(0,-50)}
                                  → 4 组（(0,0) 已在 A 里）
  参考图：同一预设 useTexture=false → 1 张
两个 variant：
  conv（转换后）——复用项目现成转换链（mapping + preset_xml），把 ABR 源预设的纹理参数
                   换成矩阵目标值后生成 .kpp，即「用户实际会拿到的预设」；
  raw （PS 原数值直传）——不走亮度/对比度标定，把 PS 数字直接写进 Krita 纹理字段：
                   scale=PS scale/100（Krita 1.0 表示 100%）、strength=depth/100、
                   TexturingMode 用任务 07A 枚举（multiply=0 / linearHeight=15）、
                   brightness=0、contrast=1.0（中性）。仅矩阵 A 需要 raw：矩阵 B 的 b/c
                   与 Krita 量纲不同（Krita 亮度 -1..1、对比度 0..2），raw 无意义。

【几何口径】所有图完全一致：画布 100x100、压感 1.0、笔划 stroke=100（横线），
由 tools/krita_render_runner.py 的 GUI 路线落笔（真实 Krita 进程 + PyKrita 启动插件），
串行执行——同一时刻只跑一个 Krita 会话。

【用法】
  python tools/exp_krita_params.py --build            # 生成全部 .kpp（含参考图）
  python tools/exp_krita_params.py --render --limit 2 # 冒烟（串行渲染前 N 张）
  python tools/exp_krita_params.py --render --resume  # 渲染全部，跳过已有的
  python tools/exp_krita_params.py --manifest         # 统计 PNG 并写 manifest.json
  python tools/exp_krita_params.py --dump-fields      # 回读 .kpp XML 打印纹理字段（证据）
  python tools/exp_krita_params.py --diff-variants    # conv 与 raw 的逐张差异统计
  python tools/exp_krita_params.py --fixa-compare      # fixA 变体 vs PS 真值 / conv 旧图（追加小任务）
  python tools/exp_krita_params.py --all              # build → render → manifest
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import json
import os
import re
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brush_converter.abr import AbrFile                      # noqa: E402
from brush_converter.mapping import map_presets               # noqa: E402
from brush_converter.kpp.preset_xml import (                  # noqa: E402
    TextureXml,
    build_preset_xml,
    texture_brightness_contrast,
)
from brush_converter.kpp.kpp_writer import (                  # noqa: E402
    build_kpp,
    render_tip_preview,
)
from brush_converter import convert as conv_mod               # noqa: E402
import krita_render_runner as runner                          # noqa: E402

# ------------------------------------------------------------------ 常量

ABR_PATH = REPO / "tests" / "定量测试专用.abr"
TEXTURE_PATH = REPO / "research" / "测试用纹理.png"
OUT_DIR = REPO / "tests" / "baselines" / "exp" / "krita"
WORK_DIR = OUT_DIR / "_work"
KPP_DIR = WORK_DIR / "kpp"
RENDER_LOG = WORK_DIR / "render_log.json"
DIFF_JSON = WORK_DIR / "variant_diff.json"
MANIFEST = OUT_DIR / "manifest.json"

SIZE = (100, 100)          # 画布尺寸（所有图一致）
# 笔划长度（所有图一致）。取 60 的理由：定量测试专用.abr 的间距 Spcn=1000 →
# 转换链得到 Krita spacing=10（=1000%），200px 笔刷的 dab 间距达 2000px，
# 于是 paintLine 只产生「起始 dab」一个圆盘（半径 100px，实测）。插件把笔划
# 起点算成 x0=(画布宽-stroke)//2=20，圆心落在 (20,50)，到画布最远角 93.5px < 100px，
# 因此单 dab 就能全覆盖画布（实测覆盖 10000/10000），满足「全画布覆盖」的硬要求。
# 若取 stroke=100 则 x0=1，圆盘右端在画布内留下 320 个透明像素（覆盖 0.968）。
STROKE = 60
PRESSURE = 1.0             # 压感（所有图一致）
TIMEOUT_S = 75.0           # 单张 GUI 渲染超时（实测 11–14 秒）

MODE_KEYS = ("multiply", "linearHeight")
#: PS 纹理混合模式 4CC → 项目 mapping 里的键（"Mul " 是 ABR 里正片叠底的成员名）
MODE_PS = {"multiply": "Mul ", "linearHeight": "linearHeight"}
#: 任务 07A / mapping._TEXTURING_MODE 的 Krita 枚举号
MODE_ENUM = {"multiply": 0, "linearHeight": 15}

DEPTHS = (10, 60, 100)
SCALES = (50, 100, 150, 200)
#: 矩阵 B：(b, c)，固定 multiply / depth=100 / scale=100
MATRIX_B = ((-50, 0), (50, 0), (0, 50), (0, -50))

#: fixA 追加任务的 (depth, scale) 组合（linearHeight，b=c=0）
FIXA_PAIRS = ((10, 100), (10, 50), (10, 150), (10, 200), (60, 100), (100, 100))
#: fixA 的高度系强度修正系数：PS 高度系权重 W=12 vs Krita W=10 → strength × 1.2
FIXA_HEIGHT_STRENGTH_FACTOR = 1.2


# ------------------------------------------------------------------ 配置模型


@dataclass(frozen=True)
class Config:
    """一张实验图的全部参数。variant ∈ {conv, raw, ref}。"""

    variant: str
    mode: str
    depth: int
    scale: int
    brightness: int
    contrast: int

    @property
    def png_name(self) -> str:
        """严格按规格拼文件名。"""
        if self.variant == "ref":
            return "exp__krita__ref__notex.png"
        return (
            "exp__krita__%s__%s__d%d_s%d_b%d_c%d.png"
            % (self.variant, self.mode, self.depth, self.scale,
               self.brightness, self.contrast)
        )

    @property
    def stem(self) -> str:
        return self.png_name[: -len(".png")]

    @property
    def kpp_name(self) -> str:
        return self.stem + ".kpp"


def dab_center(size=SIZE, stroke=STROKE) -> list[int]:
    """落笔起点（也是本实验唯一 dab 的圆心）：插件用 x0=max(1,(宽-stroke)//2), y0=高//2。"""
    width, height = size
    return [max(1, (width - int(stroke)) // 2), height // 2]


def build_configs() -> list[Config]:
    """按「conv A → conv B → raw A → ref」的确定顺序给出全部配置。"""
    configs: list[Config] = []
    for mode in MODE_KEYS:
        for depth in DEPTHS:
            for scale in SCALES:
                configs.append(Config("conv", mode, depth, scale, 0, 0))
    for brightness, contrast in MATRIX_B:
        configs.append(Config("conv", "multiply", 100, 100, brightness, contrast))
    for mode in MODE_KEYS:
        for depth in DEPTHS:
            for scale in SCALES:
                configs.append(Config("raw", mode, depth, scale, 0, 0))
    # fixA（追加小任务）：linearHeight 高度系 strength 按 PS/Krita 权重 12:10 修正 ×1.2，
    # 其余字段与 conv 完全一致；这里只做 lead 点名的 6 张（d10 四档缩放 + d60/d100 的 s100）
    for depth, scale in FIXA_PAIRS:
        configs.append(Config("fixA", "linearHeight", depth, scale, 0, 0))
    configs.append(Config("ref", "", 0, 0, 0, 0))
    return configs


# ------------------------------------------------------------------ 基础资源


def load_base():
    """解析 ABR 并校验实验前提；返回 (BrushPreset, 纹理原始字节, 纹理 md5)。"""
    if not ABR_PATH.is_file():
        raise SystemExit("ABR 不存在：%s" % ABR_PATH)
    if not TEXTURE_PATH.is_file():
        raise SystemExit("纹理不存在：%s" % TEXTURE_PATH)
    abr = AbrFile.parse(str(ABR_PATH))
    presets = map_presets(abr)
    # 实验口径依赖「定量测试专用.abr 只有 1 支带纹理预设」，不满足就明确失败（不猜）
    if len(presets) != 1:
        raise SystemExit(
            "实验前提不成立：定量测试专用.abr 解析出 %d 支预设（应为 1 支）：%r"
            % (len(presets), [p.name for p in presets])
        )
    bp = presets[0]
    if bp.texture is None:
        raise SystemExit("实验前提不成立：该预设没有纹理设置")
    tex_bytes = TEXTURE_PATH.read_bytes()
    md5 = hashlib.md5(tex_bytes).hexdigest()
    return bp, tex_bytes, md5


def brush_parts(bp):
    """笔刷几何部分（与纹理无关）：返回 (brush_definition, 预览 RGBA)。"""
    if bp.tip_gray is None and not bp.is_computed:
        raise SystemExit("笔尖位图缺失且不是计算笔刷，无法生成预设")
    if bp.is_computed:
        brush_def = conv_mod._computed_brush_def(bp)
        preview = render_tip_preview(
            conv_mod._circle_mask(
                bp.hardness if bp.hardness is not None else 100.0, bp.roundness
            )
        )
    else:  # 本实验的 ABR 是计算笔刷；保留采样分支以保证脚本通用
        from brush_converter import convert as _c

        tip_png = _c._png_bytes(_c._square_tip(bp.tip_gray))
        brush_def = _c._sampled_brush_def("tip", tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)
    return brush_def, preview


TEX_RESOURCE_PREFIX = "expkrita_tex_"


def texture_resource_name(md5: str) -> str:
    """内嵌纹理资源名：由「实际内嵌字节」的 md5 派生，保证唯一、不与环境撞名。

    故意避开转换链默认的 tex_<uuid>.png：实测该名字在本机 Krita 资源库/缓存里
    已有同名图案，会遮蔽内嵌字节，导致渲染用错纹理。
    """
    return "%s%s.png" % (TEX_RESOURCE_PREFIX, md5[:8])


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    """构造一个标准 PNG 块（长度 + 类型 + 数据 + CRC32）。"""
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def embedded_texture_bytes() -> tuple[bytes, str]:
    """源纹理字节 → 可被 Krita 稳定加载的内嵌纹理字节，返回 (bytes, md5)。

    为什么不是原文件的逐字节拷贝（实测证据见报告「纹理加载异常」一节）：
    research/测试用纹理.png 的 md5 5e748763… 与用户 Krita 图案库里已安装的
    同名图案（测试用纹理.png，同一文件）完全相同。Krita 5.3.3 在这种情况
    （内嵌纹理 md5 与已安装图案相同）下会把纹理判为不可用：预设照常加载、
    Texture/Pattern/Enabled=true，但落笔时纹理被静默禁用（Alpha 恒为 255）。
    同一像素、但 md5 不同的字节（实测只改 pHYs 数值）可以正常加载。

    这里采用最小改动：保留源文件全部块，仅在 IEND 之前追加一个 tEXt 元数据块
    （keyword=brushconverter-exp，内容=源文件 sha256），使 md5 唯一。
    去掉该块即可逐字节还原源文件；像素数据（IHDR/IDAT）完全未动。
    """
    src = TEXTURE_PATH.read_bytes()
    sha = hashlib.sha256(src).hexdigest()
    if src[-8:-4] != b"IEND":
        raise SystemExit("源纹理不是以 IEND 结尾的标准 PNG，无法安全追加元数据块")
    text = b"brushconverter-exp" + bytes([0]) + b"sha256=" + sha.encode("ascii")
    extra = _png_chunk(b"tEXt", text)
    out = src[:-12] + extra + src[-12:]
    if len(out) != len(src) + len(extra) or out[:8] != src[:8] or out[-12:] != src[-12:]:
        raise SystemExit("内嵌纹理解析异常（首尾/长度不匹配）")
    return out, hashlib.md5(out).hexdigest()


# ------------------------------------------------------------------ .kpp 生成


def conv_texture_xml(bp, cfg: Config, tex_bytes: bytes) -> TextureXml:
    """conv：走项目现成转换链（_texture_xml），只替换内嵌位图为源文件字节。"""
    tex = dataclasses.replace(
        bp.texture,
        scale=float(cfg.scale),
        depth=float(cfg.depth),
        brightness=int(cfg.brightness),
        contrast=int(cfg.contrast),
        blend_mode=MODE_PS[cfg.mode],
    )
    bp2 = dataclasses.replace(bp, texture=tex)
    return _finish_conv_xml(conv_mod._texture_xml(bp2), tex_bytes)


def _finish_conv_xml(tex_xml: TextureXml, png_bytes: bytes) -> TextureXml:
    """把转换链算出的纹理设置里的位图字节换成 research/测试用纹理.png 的原始字节。

    规格要求「Krita 侧 preset 的纹理必须是同一文件的字节」，而转换链默认会
    把 numpy 像素重新编码成 PNG（字节不同）。这里只换字节，其余字段全部保留
    转换链的计算结果（亮度/对比度标定、scale/100、strength、模式枚举、软纹理策略）。
    """
    if tex_xml is None:
        raise SystemExit("转换链未产出纹理 XML（内部错误，不是预期的实验路径）")
    tex_xml.png_bytes = png_bytes
    tex_xml.pattern_filename = texture_resource_name(hashlib.md5(png_bytes).hexdigest())
    return tex_xml


def raw_texture_xml(bp, cfg: Config, tex_bytes: bytes) -> TextureXml:
    """raw：PS 数字直传（不做亮度/对比度标定，不套转换链的参数推导）。

    量纲换算（必做，否则字段被 Krita 静默 clamp，参数矩阵退化）：
      scale  ：PS 百分比 → Krita 倍率（1.0 = 100%），即 scale/100；
      depth  ：PS 深度 0..100 → Krita 效果强度 0..1，即 depth/100；
      mode   ：任务 07A 的 Krita 枚举，multiply=0、linearHeight=15；
      b / c  ：中性（0 / 1.0）——矩阵 A 的 b=c=0。
    invert 不是数值参数（ABR 源预设为 true），两 variant 一律照抄，不参与矩阵。
    """
    tex = bp.texture
    return TextureXml(
        pattern_filename=texture_resource_name(hashlib.md5(tex_bytes).hexdigest()),
        png_bytes=tex_bytes,
        scale=cfg.scale / 100.0,
        brightness=0.0,
        contrast=1.0,
        invert=bool(tex.invert),
        texturing_mode=MODE_ENUM[cfg.mode],
        use_soft_texturing=False,
        strength=cfg.depth / 100.0,
        strength_curve=None,
        strength_pressure=False,
    )


def fixa_strength(depth: float) -> float:
    """fixA 的高度系强度：min(1.0, 1.2 × depth/100)（PS W=12、Krita W=10）。"""
    return min(1.0, FIXA_HEIGHT_STRENGTH_FACTOR * float(depth) / 100.0)


def fixa_texture_xml(bp, cfg: Config, tex_bytes: bytes) -> TextureXml:
    """fixA：在 conv 结果的基础上**只改** Texture/Strength/Value（×1.2、封顶 1.0）。

    scale / mode(=15) / brightness / contrast / invert 与同参数 conv 完全一致，
    这样与 PS 的差异只可能来自「高度系权重」这一条假设。
    """
    tex_xml = conv_texture_xml(bp, cfg, tex_bytes)
    tex_xml.strength = fixa_strength(cfg.depth)
    return tex_xml


def assemble_xml(bp, brush_def: str, tex_xml: TextureXml | None) -> str:
    """与 convert._render_preset 同口径组装预设 XML（这里只把纹理参数换成目标值）。"""
    return build_preset_xml(
        name=bp.name,
        brush_definition=brush_def,
        tip_png=None,
        tip_name=None,
        size_curve=bp.size_curve,
        opacity_curve=bp.opacity_curve,
        flow_curve=bp.flow_curve,
        ratio_curve=bp.ratio_curve,
        rotation_sensor=bp.rotation_sensor,
        rotation_jitter=bp.rotation_jitter,
        scatter=bp.scatter,
        scatter_pressure=bp.scatter_pressure,
        scatter_both_axes=bp.scatter_both_axes,
        scatter_amount=bp.scatter_amount,
        texture=tex_xml,
    )


def build_all() -> list[Config]:
    """生成全部 .kpp（conv / raw / ref），返回配置清单。"""
    bp, src_bytes, src_md5 = load_base()
    tex_bytes, tex_md5 = embedded_texture_bytes()
    brush_def, preview = brush_parts(bp)
    KPP_DIR.mkdir(parents=True, exist_ok=True)
    configs = build_configs()
    print("base: preset=%r computed=%s texture=%r uuid=%s" % (
        bp.name, bp.is_computed,
        getattr(bp.texture, "name", None), bp.texture.uuid))
    print("texture 源文件: %s bytes=%d md5=%s" % (
        TEXTURE_PATH.relative_to(REPO), len(src_bytes), src_md5))
    print("texture 内嵌  : bytes=%d md5=%s（源文件 + 1 个 tEXt 块，像素未动）" % (
        len(tex_bytes), tex_md5))
    for cfg in configs:
        if cfg.variant == "ref":
            tex_xml = None
        elif cfg.variant == "conv":
            tex_xml = conv_texture_xml(bp, cfg, tex_bytes)
        elif cfg.variant == "fixA":
            tex_xml = fixa_texture_xml(bp, cfg, tex_bytes)
        else:
            tex_xml = raw_texture_xml(bp, cfg, tex_bytes)
        xml = assemble_xml(bp, brush_def, tex_xml)
        data = build_kpp(xml, preview)
        path = KPP_DIR / cfg.kpp_name
        path.write_bytes(data)
        print("[生成] %-58s %6d bytes" % (cfg.kpp_name, len(data)))
    print("共生成 %d 个 .kpp → %s" % (len(configs), KPP_DIR.relative_to(REPO)))
    return configs


# ------------------------------------------------------------------ XML 回读


def read_preset_xml(kpp_path: Path) -> str:
    """从 .kpp（PNG 容器）里读出预设 XML：优先 tools/kpp_params.py 的实现。"""
    try:
        import kpp_params  # tools/ 目录已在 sys.path

        return kpp_params.read_kpp_xml(str(kpp_path))
    except Exception:
        pass
    # 兜底：自行解析 iTXt / zTXt（与 kpp_params 同口径）
    import struct
    import zlib

    data = kpp_path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SystemExit("不是 PNG 容器：%s" % kpp_path)
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"iTXt":
            keyword, rest = payload.split(b"\x00", 1)
            flag = rest[0]
            rest = rest[2:]
            _lang, rest = rest.split(b"\x00", 1)
            _trans, text = rest.split(b"\x00", 1)
            if flag == 1:
                text = zlib.decompress(text)
            return text.decode("utf-8")
        if ctype == b"zTXt":
            keyword, rest = payload.split(b"\x00", 1)
            return zlib.decompress(rest[1:]).decode("utf-8")
    raise SystemExit("找不到预设 XML：%s" % kpp_path)


def parse_kpp(kpp_path: Path) -> dict:
    """返回 {参数名: 值} 与内嵌资源列表。"""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(read_preset_xml(kpp_path))
    params = {}
    for node in root.findall("param"):
        params[node.get("name")] = (node.text or "")
    resources = []
    for res in root.iter("resource"):
        raw = base64.b64decode((res.text or "").strip() or "")
        resources.append({
            "name": res.get("name"),
            "type": res.get("type"),
            "filename": res.get("filename"),
            "bytes": len(raw),
            "md5": hashlib.md5(raw).hexdigest(),
            "declared_md5": res.get("md5sum"),
        })
    return {"params": params, "resources": resources}


TEXTURE_FIELD_KEYS = (
    "Texture/Pattern/Enabled",
    "Texture/Pattern/Scale",
    "Texture/Pattern/Brightness",
    "Texture/Pattern/Contrast",
    "Texture/Pattern/TexturingMode",
    "Texture/Pattern/Invert",
    "Texture/Pattern/UseSoftTexturing",
    "Texture/Pattern/Name",
    "Texture/Pattern/PatternMD5Sum",
    "Texture/Strength/Value",
    "Texture/Strength/UseCurve",
    "PressureTexture/Strength/",
)


def dump_fields(configs: list[Config] | None = None) -> list[dict]:
    """回读全部 .kpp 的纹理字段，打印成表并返回结构化行（报告证据）。"""
    configs = configs or build_configs()
    _, tex_bytes, tex_md5 = (None, None, None)
    try:
        tex_md5 = hashlib.md5(TEXTURE_PATH.read_bytes()).hexdigest()
    except Exception:
        pass
    rows = []
    header = ("variant", "mode", "d", "s", "b", "c",
              "Scale", "Bright", "Contrast", "Mode", "Invert", "Soft",
              "Strength", "Curve", "TexMD5", "ResMD5", "ResBytes", "匹配")
    print("%-6s %-12s %4s %4s %4s %4s %8s %7s %8s %5s %6s %5s %8s %6s %10s %10s %8s %4s" % header)
    for cfg in configs:
        kpp = KPP_DIR / cfg.kpp_name
        if not kpp.is_file():
            print("[缺失] %s" % cfg.kpp_name)
            continue
        parsed = parse_kpp(kpp)
        p = parsed["params"]
        m = re.search(r"type=\"patterns\"", read_preset_xml(kpp))  # 是否存在纹理资源
        tex_res = [r for r in parsed["resources"] if r["type"] == "patterns"]
        res_md5 = tex_res[0]["md5"] if tex_res else ""
        res_bytes = tex_res[0]["bytes"] if tex_res else 0
        match = (tex_md5 is not None and res_md5 == tex_md5)
        rows.append({
            "kpp": cfg.kpp_name,
            "variant": cfg.variant,
            "mode": cfg.mode,
            "depth": cfg.depth,
            "scale": cfg.scale,
            "brightness": cfg.brightness,
            "contrast": cfg.contrast,
            "fields": {k: p.get(k, "<无此字段>") for k in TEXTURE_FIELD_KEYS},
            "texture_resource": tex_res[0] if tex_res else None,
            "texture_md5_matches_source_file": bool(match),
        })
        print("%-6s %-12s %4s %4s %4s %4s %8s %7s %8s %5s %6s %5s %8s %6s %10s %10s %8s %4s" % (
            cfg.variant, cfg.mode or "-", cfg.depth, cfg.scale,
            cfg.brightness, cfg.contrast,
            p.get("Texture/Pattern/Scale", "-"),
            p.get("Texture/Pattern/Brightness", "-"),
            p.get("Texture/Pattern/Contrast", "-"),
            p.get("Texture/Pattern/TexturingMode", "-"),
            p.get("Texture/Pattern/Invert", "-"),
            p.get("Texture/Pattern/UseSoftTexturing", "-"),
            p.get("Texture/Strength/Value", "-"),
            p.get("Texture/Strength/UseCurve", "-"),
            (p.get("Texture/Pattern/PatternMD5Sum", "-") or "-")[:10],
            (res_md5 or "-")[:10],
            res_bytes,
            "是" if match else ("-" if cfg.variant == "ref" else "否"),
        ))
    out = WORK_DIR / "kpp_fields.json"
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("字段回读明细已写 %s" % out.relative_to(REPO))
    return rows


# ------------------------------------------------------------------ 渲染


def load_render_log() -> dict:
    if RENDER_LOG.is_file():
        try:
            return json.loads(RENDER_LOG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_render_log(log: dict) -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_LOG.write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


def render_all(configs: list[Config], resume: bool = False,
               only: list[str] | None = None, limit: int | None = None,
               timeout: float = TIMEOUT_S) -> int:
    """串行渲染（同一时刻只跑一个 Krita GUI 会话）。返回失败张数。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not KPP_DIR.is_dir():
        raise SystemExit("请先执行 --build（找不到 %s）" % KPP_DIR)
    todo: list[Config] = []
    for cfg in configs:
        if only and not any(token in cfg.stem for token in only):
            continue
        if resume and (OUT_DIR / cfg.png_name).is_file():
            continue
        if not (KPP_DIR / cfg.kpp_name).is_file():
            raise SystemExit("缺少 .kpp：%s（请先 --build）" % cfg.kpp_name)
        todo.append(cfg)
    if limit:
        todo = todo[:limit]
    if not todo:
        print("没有需要渲染的图（检查 --only / --resume）")
        return 0

    log = load_render_log()
    failed = 0
    started = time.time()
    for index, cfg in enumerate(todo, 1):
        kpp = KPP_DIR / cfg.kpp_name
        out = OUT_DIR / cfg.png_name
        t0 = time.time()
        result = runner.render_one(
            str(kpp), str(out), size=SIZE, stroke=STROKE,
            pressure=PRESSURE, timeout=timeout, route="gui")
        elapsed = result.get("elapsed_s")
        if elapsed is None:
            elapsed = round(time.time() - t0, 3)
        ok = bool(result.get("ok"))
        if not ok:
            failed += 1
        alpha = result.get("alpha_nonzero")
        # 外壳会额外写一份 <out>.result.json（插件原始结果），清单已收录关键字段，
        # 这里清掉，避免基准目录里混入中间文件
        side_result = Path(str(out) + ".result.json")
        if side_result.is_file():
            try:
                side_result.unlink()
            except OSError:
                pass
        log[cfg.png_name] = {
            "variant": cfg.variant,
            "mode": cfg.mode,
            "depth": cfg.depth,
            "scale": cfg.scale,
            "brightness": cfg.brightness,
            "contrast": cfg.contrast,
            "preset_file": cfg.kpp_name,
            "preset": result.get("preset"),
            "status": "rendered" if ok else "failed",
            "elapsed_s": elapsed,
            "alpha_nonzero": alpha,
            "timeout": result.get("timeout"),
            "reason": None if ok else (result.get("reason") or "")[:400],
            "rendered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_render_log(log)
        print("[%d/%d] %s %-58s %6.2fs alpha非零=%s%s" % (
            index, len(todo), "通过" if ok else "失败", cfg.png_name,
            elapsed or 0.0, alpha,
            "" if ok else "；原因：" + (result.get("reason") or "")[:160]))
        sys.stdout.flush()
    total = time.time() - started
    print("本次渲染 %d 张 / 成功 %d / 失败 %d / 总耗时 %.1f 秒 / 平均 %.2f 秒/张"
          % (len(todo), len(todo) - failed, failed, total,
             total / len(todo) if todo else 0.0))
    return failed


# ------------------------------------------------------------------ 统计与清单


def alpha_stats(png_path: Path) -> dict:
    """Alpha 统计：非零占比、min/max/mean、取值种数。"""
    from PIL import Image

    with Image.open(png_path) as im:
        rgba = im.convert("RGBA")
        size = rgba.size
        alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    total = int(alpha.size)
    nonzero = int(np.count_nonzero(alpha))
    return {
        "size": [int(size[0]), int(size[1])],
        "alpha_nonzero": nonzero,
        "alpha_total": total,
        "alpha_nonzero_ratio": round(nonzero / total, 6) if total else 0.0,
        "alpha_min": int(alpha.min()) if total else 0,
        "alpha_max": int(alpha.max()) if total else 0,
        "alpha_mean": round(float(alpha.mean()), 3) if total else 0.0,
        "alpha_unique": int(len(np.unique(alpha))),
    }


def file_sha256(png_path: Path) -> str:
    return hashlib.sha256(png_path.read_bytes()).hexdigest()


def fields_value(fields: dict, cfg: Config, key: str):
    """从回读的 XML 字段里取数值（ref 无纹理字段时返回 None）。"""
    raw = (fields.get(cfg.png_name) or {}).get(key)
    if raw is None or raw == "<无此字段>":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return raw
    return int(value) if value.is_integer() else value


def build_manifest(extra_fields: dict[str, dict] | None = None) -> dict:
    """统计全部 PNG 并写 tests/baselines/exp/krita/manifest.json。"""
    configs = build_configs()
    log = load_render_log()
    fields = extra_fields
    if fields is None:
        fields = {}
        for cfg in configs:
            kpp = KPP_DIR / cfg.kpp_name
            if kpp.is_file():
                parsed = parse_kpp(kpp)
                fields[cfg.png_name] = {
                    k: parsed["params"].get(k) for k in TEXTURE_FIELD_KEYS
                }
    items = []
    flagged = []
    for cfg in configs:
        png = OUT_DIR / cfg.png_name
        entry = log.get(cfg.png_name, {})
        if not png.is_file():
            items.append({
                "file": cfg.png_name,
                "variant": cfg.variant,
                "mode": cfg.mode or None,
                "depth": cfg.depth,
                "scale": cfg.scale,
                "brightness": cfg.brightness,
                "contrast": cfg.contrast,
                "status": "missing",
                "note": "未产出：请检查 render_log.json 里的失败原因",
            })
            continue
        stats = alpha_stats(png)
        item = {
            "file": cfg.png_name,
            "variant": cfg.variant,
            "mode": cfg.mode or None,
            "depth": cfg.depth,
            "scale": cfg.scale,
            "brightness": cfg.brightness,
            "contrast": cfg.contrast,
            "status": "rendered" if png.is_file() else "missing",
            "size": "%dx%d" % tuple(stats["size"]),
            "pressure": PRESSURE,
            "stroke": STROKE,
            "dab_center": dab_center(),
            "preset_file": cfg.kpp_name,
            "preset": entry.get("preset"),
            "elapsed_s": entry.get("elapsed_s"),
            "alpha_nonzero": stats["alpha_nonzero"],
            "alpha_total": stats["alpha_total"],
            "alpha_nonzero_ratio": stats["alpha_nonzero_ratio"],
            "alpha_min": stats["alpha_min"],
            "alpha_max": stats["alpha_max"],
            "alpha_mean": stats["alpha_mean"],
            "alpha_unique": stats["alpha_unique"],
            "sha256": file_sha256(png),
            "bytes": png.stat().st_size,
            # 三个直取字段（lead 要求）：后续分析不必再翻 XML；参考图无纹理 → None
            "krita_scale": (None if cfg.variant == "ref" else
                            fields_value(fields, cfg, "Texture/Pattern/Scale")),
            "krita_depth_strength": (None if cfg.variant == "ref" else
                                     fields_value(fields, cfg, "Texture/Strength/Value")),
            "krita_mode_enum": (None if cfg.variant == "ref" else
                                fields_value(fields, cfg, "Texture/Pattern/TexturingMode")),
            "kpp_fields": fields.get(cfg.png_name) or {},
            "renderer": "krita-gui-plugin",
            "route": "gui",
            "note": "Krita 5.3.3 GUI 进程 + PyKrita 启动插件，透明画布落笔后导出",
        }
        if stats["alpha_nonzero_ratio"] < 0.97:
            item["coverage_warning"] = (
                "Alpha 非零占比 %.4f < 0.97：该组合存在透明像素（纹理模式/深度导致掩码归零），"
                "比对时须先看掩码层" % stats["alpha_nonzero_ratio"]
            )
            flagged.append(cfg.png_name)
        items.append(item)

    rendered = [i for i in items if i.get("status") == "rendered"]
    by_variant: dict[str, int] = {}
    for i in rendered:
        by_variant[i["variant"]] = by_variant.get(i["variant"], 0) + 1
    manifest = {
        "schema": "brushconverter.exp.krita/v1",
        "generated_by": "tools/exp_krita_params.py",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "task": "实验（Krita 侧）：纹理参数矩阵 A/B + 无纹理参考",
        "naming": "exp__krita__<variant>__<modeKey>__d<depth>_s<scale>_b<b>_c<c>.png；参考图 exp__krita__ref__notex.png",
        "geometry": {
            "size": "100x100", "stroke": STROKE, "pressure": PRESSURE,
            "note": ("全部图几何完全一致（同一常量），用于掩码层比对；"
                     "因源预设间距 Spcn=1000（Krita spacing=10=1000%，dab 间距 2000px），"
                     "实际落笔为单个起始 dab：圆心 (20,50)、半径 100px，覆盖整个 100x100 画布"),
        },
        "texture": {
            "source": "research/测试用纹理.png",
            "sha256": hashlib.sha256(TEXTURE_PATH.read_bytes()).hexdigest(),
            "md5": hashlib.md5(TEXTURE_PATH.read_bytes()).hexdigest(),
            "bytes": TEXTURE_PATH.stat().st_size,
            "embedded_sha256": hashlib.sha256(embedded_texture_bytes()[0]).hexdigest(),
            "embedded_md5": embedded_texture_bytes()[1],
            "embedded_bytes": len(embedded_texture_bytes()[0]),
            "embedded_resource_name": texture_resource_name(embedded_texture_bytes()[1]),
            "note": ("内嵌纹理 = 源文件全部字节 + 在 IEND 前追加 1 个 tEXt 元数据块"
                     "（像素未动，去掉该块可逐字节还原源文件）。原因：源文件 md5"
                     "与用户 Krita 图案库中已安装的同名图案相同，Krita 5.3.3 会把"
                     "该内嵌纹理解析失败并静默禁用纹理（实测 Alpha 恒 255）。"),
        },
        "matrix": {
            "A": {"modes": list(MODE_KEYS), "depths": list(DEPTHS),
                  "scales": list(SCALES), "brightness": 0, "contrast": 0,
                  "count_per_variant": len(MODE_KEYS) * len(DEPTHS) * len(SCALES)},
            "B": {"mode": "multiply", "depth": 100, "scale": 100,
                  "bc": [list(pair) for pair in MATRIX_B],
                  "count": len(MATRIX_B), "variant": "conv"},
            "ref": {"useTexture": False, "count": 1},
            "fixA": {
                "variant": "fixA", "mode": "linearHeight",
                "pairs": [list(pair) for pair in FIXA_PAIRS],
                "strength_formula": "min(1.0, 1.2 * depth/100)",
                "note": ("追加小任务：只改 Texture/Strength/Value（高度系 ×1.2），其余字段与 conv 完全相同；"
                         "对照 tests/baselines/exp/ps/ 的同参数 PS 真值"),
            },
        },
        "summary": {
            "total_items": len(items),
            "rendered": len(rendered),
            "missing": len([i for i in items if i.get("status") == "missing"]),
            "by_variant": by_variant,
            "coverage_below_0_97": flagged,
            "coverage_below_0_97_count": len(flagged),
        },
        "items": items,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    print("清单已写 %s：共 %d 条 / 已渲染 %d / 缺失 %d / 覆盖率<0.97 共 %d 张"
          % (MANIFEST.relative_to(REPO), len(items), len(rendered),
             manifest["summary"]["missing"], len(flagged)))
    for name in flagged:
        print("[覆盖警告] %s" % name)
    return manifest


# ------------------------------------------------------------------ conv vs raw 差异


def diff_variants() -> dict:
    """逐对比较 conv 与 raw（矩阵 A）的 Alpha 差异，返回并写出结果。"""
    from PIL import Image

    rows = []
    for mode in MODE_KEYS:
        for depth in DEPTHS:
            for scale in SCALES:
                conv_cfg = Config("conv", mode, depth, scale, 0, 0)
                raw_cfg = Config("raw", mode, depth, scale, 0, 0)
                a_path = OUT_DIR / conv_cfg.png_name
                b_path = OUT_DIR / raw_cfg.png_name
                if not (a_path.is_file() and b_path.is_file()):
                    rows.append({"mode": mode, "depth": depth, "scale": scale,
                                 "status": "missing"})
                    continue
                with Image.open(a_path) as ia:
                    a = np.asarray(ia.convert("RGBA"), dtype=np.int16)
                with Image.open(b_path) as ib:
                    b = np.asarray(ib.convert("RGBA"), dtype=np.int16)
                da = np.abs(a[:, :, 3] - b[:, :, 3])
                total = da.size
                rows.append({
                    "mode": mode,
                    "depth": depth,
                    "scale": scale,
                    "conv_file": conv_cfg.png_name,
                    "raw_file": raw_cfg.png_name,
                    "identical": bool(np.array_equal(a, b)),
                    "alpha_max_abs_diff": int(da.max()),
                    "alpha_mean_abs_diff": round(float(da.mean()), 4),
                    "alpha_diff_pixels": int(np.count_nonzero(da)),
                    "alpha_diff_ratio": round(float(np.count_nonzero(da)) / total, 6),
                    "status": "compared",
                })
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "对数值口径：Alpha 通道逐像素差（|conv-raw|），分母=全部像素；identical 表示 RGBA 全等",
        "rows": rows,
    }
    DIFF_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print("%-12s %4s %4s %8s %8s %10s %10s" % (
        "mode", "d", "s", "max|Δ|", "mean|Δ|", "差异像素", "差异占比"))
    for row in rows:
        if row["status"] != "compared":
            print("%-12s %4s %4s   缺失" % (row["mode"], row["depth"], row["scale"]))
            continue
        print("%-12s %4d %4d %8d %8.4f %10d %10.4f%s" % (
            row["mode"], row["depth"], row["scale"],
            row["alpha_max_abs_diff"], row["alpha_mean_abs_diff"],
            row["alpha_diff_pixels"], row["alpha_diff_ratio"],
            "  (RGBA 全等)" if row["identical"] else ""))
    print("差异明细已写 %s" % DIFF_JSON.relative_to(REPO))
    return payload


# ------------------------------------------------------------------ 纹理相位证据


def phase_check() -> dict:
    """纹理相位证据：把 64x64 纹理按 100% 铺满 100x100，用 tools/texture_mask.py
    算出 Krita 预期掩码，与 conv multiply/d100/s100/b0_c0 的实测 Alpha 比对。

    用途：确认 Krita 的纹理锚点是否在画布 (0,0)（与 PS 的一致前提）。
    """
    from PIL import Image

    import texture_mask as tm

    cfg = Config("conv", "multiply", 100, 100, 0, 0)
    png = OUT_DIR / cfg.png_name
    if not png.is_file():
        raise SystemExit("相位检验缺少实测图：%s" % png)
    tex = np.asarray(Image.open(TEXTURE_PATH).convert("RGBA"), dtype=np.uint8)
    alpha = np.asarray(Image.open(png).convert("RGBA").getchannel("A"), dtype=np.int16)
    h, w = alpha.shape
    th, tw = tex.shape[:2]

    def predict(ox: int, oy: int, invert: bool = True) -> np.ndarray:
        """按相位偏移 (ox, oy) 平铺纹理并算 Krita 预期掩码（0..255 整数量化）。"""
        shifted = np.roll(np.roll(tex, -oy, axis=0), -ox, axis=1)
        reps = (h // th + 1, w // tw + 1, 1)
        tiled = np.tile(shifted, reps)[:h, :w]
        base_mask = tm.texture_mask_from_rgba(tiled, invert=False)
        mask = tm.compute_mask(base_mask, invert=invert, brightness=0.0,
                               contrast=1.0, mode=0, depth=1.0)
        return np.rint(mask * 255.0).astype(np.int16)

    def stats(pred: np.ndarray) -> dict:
        d = np.abs(pred - alpha)
        return {"diff_ratio_tol0": round(float((d > 0).mean()), 6),
                "diff_ratio_tol2": round(float((d > 2).mean()), 6),
                "max_abs_diff": int(d.max()),
                "mean_abs_diff": round(float(d.mean()), 4)}

    anchor0 = stats(predict(0, 0, True))
    anchor0_no_inv = stats(predict(0, 0, False))
    best = None
    for oy in range(th):
        for ox in range(tw):
            pred = predict(ox, oy, True)
            d = np.abs(pred - alpha)
            key = (float(d.mean()), int((d > 2).sum()))
            if best is None or key < best[0]:
                best = (key, ox, oy, stats(pred))
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "image": cfg.png_name,
        "method": (
            "纹理 64x64 按 Texture/Pattern/Scale=1（100%）平铺到 100x100；"
            "tools/texture_mask.py 复刻 Krita 掩码：invert=true、brightness=0、"
            "contrast=1、mode=Multiply(0)、depth(strength)=1.0；base 取全 1"
            "（单 dab 覆盖整画布，参考图实测 Alpha 全 255）"),
        "anchor_0_0_invert_true": anchor0,
        "anchor_0_0_invert_false": anchor0_no_inv,
        "best_phase": {"offset_x": best[1], "offset_y": best[2], "stats": best[3]},
    }
    if anchor0["diff_ratio_tol2"] < 0.02 and anchor0["max_abs_diff"] <= 3:
        payload["verdict"] = (
            "锚点=画布(0,0) 成立：tol=2 差异比例 %.4f%%、最大差 %d"
            % (anchor0["diff_ratio_tol2"] * 100.0, anchor0["max_abs_diff"]))
    else:
        payload["verdict"] = (
            "锚点=画布(0,0) 不成立：tol=2 差异比例 %.4f%%、最大差 %d；"
            "最佳相位偏移≈(%d,%d)"
            % (anchor0["diff_ratio_tol2"] * 100.0, anchor0["max_abs_diff"],
               best[1], best[2]))
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "phase_check.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("相位检验（%s）：" % cfg.png_name)
    print("  锚点(0,0)+invert=true  : %s" % json.dumps(anchor0, ensure_ascii=False))
    print("  锚点(0,0)+invert=false : %s" % json.dumps(anchor0_no_inv, ensure_ascii=False))
    print("  最佳相位 %r: %s" % ((best[1], best[2]), json.dumps(best[3], ensure_ascii=False)))
    print("  结论：%s" % payload["verdict"])
    print("  明细已写 %s" % (WORK_DIR / "phase_check.json").relative_to(REPO))
    return payload


# ------------------------------------------------------------------ fixA 实测验证


def fixa_compare() -> dict:
    """fixA 实测验证：linearHeight 高度系 strength×1.2 后，与 PS 真值、conv 旧图逐张比。

    指标（Alpha 通道、分母=全部 10000 像素，与 `tools/imgdiff.py` 的 tol=2 口径一致）：
      diff_ratio_tol2 = |Δ|>2 的像素占比；mean_abs_diff；max_abs_diff。
    对照：同参数 conv 旧图（`exp__krita__conv__linearHeight__...`）的三项同样列出。
    """
    from PIL import Image

    ps_dir = OUT_DIR.parent / "ps"
    configs = [c for c in build_configs() if c.variant == "fixA"]

    def alpha(path: Path) -> np.ndarray:
        with Image.open(path) as im:
            return np.asarray(im.convert("RGBA").getchannel("A"), dtype=np.int16)

    def metrics(a: np.ndarray, b: np.ndarray) -> dict:
        d = np.abs(a - b)
        return {"diff_ratio_tol2": round(float((d > 2).mean()), 6),
                "mean_abs_diff": round(float(d.mean()), 4),
                "max_abs_diff": int(d.max())}

    rows = []
    for cfg in configs:
        fixa_png = OUT_DIR / cfg.png_name
        ps_name = ("exp__ps__%s__d%d_s%d_b%d_c%d.png"
                   % (cfg.mode, cfg.depth, cfg.scale, cfg.brightness, cfg.contrast))
        ps_png = ps_dir / ps_name
        conv_png = OUT_DIR / dataclasses.replace(cfg, variant="conv").png_name
        missing = [str(p.relative_to(REPO)) for p in (fixa_png, ps_png, conv_png)
                   if not p.is_file()]
        if missing:
            rows.append({"file": cfg.png_name, "status": "missing", "missing": missing})
            continue
        ps_a, fixa_a, conv_a = alpha(ps_png), alpha(fixa_png), alpha(conv_png)
        rows.append({
            "file": cfg.png_name,
            "variant": "fixA",
            "mode": cfg.mode,
            "depth": cfg.depth,
            "scale": cfg.scale,
            "brightness": cfg.brightness,
            "contrast": cfg.contrast,
            "ps_file": ps_name,
            "conv_file": str(conv_png.name),
            "krita_depth_strength": round(fixa_strength(cfg.depth), 6),
            "fixa_vs_ps": metrics(fixa_a, ps_a),
            "conv_vs_ps": metrics(conv_a, ps_a),
            "fixa_vs_conv": metrics(fixa_a, conv_a),
            "fixa_alpha_nonzero_ratio": round(float((fixa_a > 0).mean()), 6),
            "status": "compared",
        })

    compared = [r for r in rows if r["status"] == "compared"]
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": ("fixA = linearHeight、Texture/Strength/Value=min(1.0, 1.2*depth/100)，"
                 "其余字段与 conv 完全相同；指标为 Alpha 通道 |Δ|>2 像素占比 / mean|Δ| / max|Δ|，"
                 "分母 10000 像素"),
        "summary": {
            "count": len(compared),
            "fixa_vs_ps_max_ratio": max((r["fixa_vs_ps"]["diff_ratio_tol2"] for r in compared), default=None),
            "fixa_vs_ps_max_mean": max((r["fixa_vs_ps"]["mean_abs_diff"] for r in compared), default=None),
            "fixa_vs_ps_max_max": max((r["fixa_vs_ps"]["max_abs_diff"] for r in compared), default=None),
            "conv_vs_ps_max_mean": max((r["conv_vs_ps"]["mean_abs_diff"] for r in compared), default=None),
        },
        "rows": rows,
    }
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "fixa_compare.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print("%-46s %10s %12s %10s | %10s %12s %10s | %8s" % (
        "file", "fixA:超阈", "fixA:mean|Δ|", "fixA:max|Δ|",
        "conv:超阈", "conv:mean|Δ|", "conv:max|Δ|", "strength"))
    for row in rows:
        if row["status"] != "compared":
            print("%-46s 缺失：%s" % (row["file"], row.get("missing")))
            continue
        f, c = row["fixa_vs_ps"], row["conv_vs_ps"]
        print("%-46s %9.4f%% %12.2f %10d | %9.4f%% %12.2f %10d | %8s" % (
            row["file"], f["diff_ratio_tol2"] * 100, f["mean_abs_diff"], f["max_abs_diff"],
            c["diff_ratio_tol2"] * 100, c["mean_abs_diff"], c["max_abs_diff"],
            row["krita_depth_strength"]))
    print("明细已写 %s" % (WORK_DIR / "fixa_compare.json").relative_to(REPO))
    return payload


# ------------------------------------------------------------------ 入口


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Krita 侧参数实验：生成 .kpp + 串行渲染 + 清单（矩阵 A/B + 无纹理参考）")
    parser.add_argument("--build", action="store_true", help="生成全部 .kpp")
    parser.add_argument("--render", action="store_true", help="串行渲染（GUI 路线）")
    parser.add_argument("--manifest", action="store_true", help="统计 PNG 并写 manifest.json")
    parser.add_argument("--dump-fields", action="store_true", help="回读 .kpp 打印纹理字段")
    parser.add_argument("--diff-variants", action="store_true", help="conv 与 raw 差异统计")
    parser.add_argument("--phase-check", action="store_true",
                        help="纹理相位证据：实测 Alpha vs texture_mask 预期掩码")
    parser.add_argument("--fixa-compare", action="store_true",
                        help="fixA 实测验证：fixA vs PS 真值 / conv 旧图")
    parser.add_argument("--all", action="store_true", help="build → render → manifest")
    parser.add_argument("--resume", action="store_true", help="配合 --render：跳过已有的图")
    parser.add_argument("--only", default=None, help="配合 --render：只渲染文件名包含这些子串的图（逗号分隔）")
    parser.add_argument("--limit", type=int, default=None, help="配合 --render：最多渲染 N 张")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S, help="单张渲染超时秒数")
    ns = parser.parse_args(argv)

    configs = build_configs()
    rc = 0
    if ns.build or ns.all:
        build_all()
    if ns.dump_fields:
        dump_fields(configs)
    if ns.render or ns.all:
        only = [t for t in (ns.only or "").split(",") if t.strip()] or None
        failed = render_all(configs, resume=ns.resume, only=only,
                            limit=ns.limit, timeout=ns.timeout)
        rc = 1 if failed else 0
    if ns.manifest or ns.all:
        build_manifest()
    if ns.diff_variants:
        diff_variants()
    if ns.phase_check:
        phase_check()
    if ns.fixa_compare:
        fixa_compare()
    if not any((ns.build, ns.render, ns.manifest, ns.dump_fields,
                ns.diff_variants, ns.phase_check, ns.fixa_compare, ns.all)):
        parser.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
