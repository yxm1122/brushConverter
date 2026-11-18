"""转换管线：.abr → Krita 预设（.kpp）与资源包（.bundle）。"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .abr import AbrFile
from .mapping import BrushPreset, TextureSettings, map_presets, _TEXTURING_MODE
from .kpp import TextureXml, build_preset_xml, write_kpp, write_bundle
from .kpp.preset_xml import texture_brightness_contrast
from .kpp.kpp_writer import render_tip_preview


def _png_bytes(gray: np.ndarray) -> bytes:
    """灰度蒙版（255=墨）→ PNG 字节（8bit L 通道）。"""
    buf = io.BytesIO()
    Image.fromarray(gray, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _square_tip(gray: np.ndarray) -> np.ndarray:
    """将采样笔尖居中填充到正方形画布，避免 Krita 非正方形尺寸显示偏差。"""
    if gray.ndim != 2:
        raise ValueError("笔尖蒙版必须是二维灰度数组")
    h, w = gray.shape
    side = max(h, w)
    if h == side and w == side:
        return gray
    # ABR/Krita 笔尖蒙版约定 255=白色笔迹（墨），0=透明；
    # 用白色填充是刻意的：Krita 对采样笔尖按 PNG 尺寸计算预设大小，
    # 透明边框会让「预设编辑器显示尺寸」与「实际绘图区大小」不一致。
    # 2026-08-26 与用户在 Krita 实测确认（白色边框 + 居中），不要改成 0。
    canvas = np.full((side, side), 255, dtype=gray.dtype)
    y = (side - h) // 2
    x = (side - w) // 2
    canvas[y:y + h, x:x + w] = gray
    return canvas


def _texture_png(texture: TextureSettings) -> bytes:
    """纹理位图 → PNG 字节（RGB 或灰度，原样像素）。"""
    buf = io.BytesIO()
    img = texture.image
    if img.ndim == 3 and img.shape[2] == 3:
        Image.fromarray(img, mode="RGB").save(buf, format="PNG")
    else:
        Image.fromarray(img, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _texture_xml(bp: BrushPreset) -> TextureXml | None:
    """把 Photoshop 纹理参数映射为 Krita 纹理选项（无位图时返回 None）。

    映射目标是 Krita 模式合成后的最终效果；校准常数来自
    research/测试结果2 与 research/krita结果，而不是 Photoshop 中间纹理值。
    """
    tex = bp.texture
    if tex is None or tex.image is None:
        return None
    filename = f"tex_{tex.uuid[:8]}.png" if tex.uuid else "texture.png"
    # Krita 源码的 KisTextureMaskInfo 直接执行：
    #   maskValue -= brightness
    #   maskValue = ((maskValue - 0.5) * contrast) + 0.5
    # 因此 PS 纹理亮度/对比度 → Krita 两个参数的换算集中在
    # preset_xml.texture_brightness_contrast() 里，由环境变量
    # BRUSHCONVERTER_TEXTURE_FORMULA 选择路径：
    #   calibrated （默认，v1.2.0 起）任务 12 拟合公式的精确逆；
    #   legacy     （历史方案）v1.1.2 经验公式（亮度 0.10 - PS/250，对比度 PS 中心因子）。
    # 公式、推导与限制见 docs/parameter-mapping.md 第 7 节。
    brightness, contrast = texture_brightness_contrast(tex.brightness, tex.contrast)
    return TextureXml(
        pattern_filename=filename,
        png_bytes=_texture_png(tex),
        scale=max(0.01, min(10.0, tex.scale / 100.0)),
        brightness=brightness,
        contrast=contrast,
        invert=tex.invert,
        texturing_mode=_TEXTURING_MODE.get(tex.blend_mode, 0),
        # Krita 文档明确指出：除 Height/Linear Height 外，Soft Texturing
        # 更接近 Photoshop；PS 专用 Height 模式本身不启用该优化。
        use_soft_texturing=(tex.blend_mode not in {
            "Hght", "height", "HghtPS", "linearHeight",
            "linearHeightPhotoshop", "linearHeightPS",
        }),
        strength=max(0.0, min(100.0, tex.depth)) / 100.0,
        strength_curve=f"0,{max(0.0, min(100.0, tex.depth_min)) / 100.0:g};1,1;"
        if tex.pressure else None,
        strength_pressure=tex.pressure,
    )


def _safe_filename(name: str, index: int) -> str:
    """把中文名清洗为安全的 .kpp 文件名（前缀 3 位序号）。"""
    cleaned = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")
    cleaned = cleaned or "brush"
    return f"{index:03d}_{cleaned}.kpp"


def _circle_mask(hardness: float, roundness: float,
                 size: int = 256) -> np.ndarray:
    """按硬度/圆度生成一个圆形灰度蒙版（用于计算笔刷预览）。

    预览始终把笔尖铺满画布的 90%，与 PS 主直径无关：真实尺寸由预设里
    auto_brush 的 diameter 决定，缩略图只反映形状与边缘软硬。
    """
    s = size
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float64)
    cy = cx = (s - 1) / 2.0
    radius = (s - 1) / 2.0 * 0.9  # 占画布 90%
    # 圆度可以合法为 0（线状笔尖），除数必须夹住，否则 dist 变成 inf/nan
    ratio = max(roundness / 100.0, 1e-3)
    dx = (xx - cx) / radius
    dy = (yy - cy) / (radius * ratio)
    dist = np.sqrt(dx * dx + dy * dy)
    # 硬度 → 边缘过渡带占半径的比例（0.02..1）：硬度 100 = 几乎硬边，
    # 0 = 从中心线性衰减。注意这里不能用 (1-falloff) 之类的混合：
    # 硬笔刷的 falloff 远大于 1，会把整个圆盘压成一个小点。
    soft = max(0.02, (100.0 - hardness) / 100.0)
    mask = np.clip((1.0 - dist) / soft, 0.0, 1.0)
    return (mask * 255.0).astype(np.uint8)


@dataclass
class ConvertResult:
    kpp_files: list[tuple[str, bytes]] = field(default_factory=list)
    bundle_path: str | None = None
    out_dir: str = ""
    presets: list[BrushPreset] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def convert(abr_path: str, out_dir: str, make_bundle: bool = True,
            make_standalone: bool = True) -> ConvertResult:
    """CLI 便捷入口：解析单个 .abr 文件并转换全部预设。

    返回 ConvertResult；GUI 走 convert_presets() 以支持子集转换。
    """
    abr = AbrFile.parse(abr_path)
    presets = map_presets(abr)
    return convert_presets(abr, presets, out_dir, make_bundle=make_bundle,
                           make_standalone=make_standalone)


def _render_preset(bp: BrushPreset, index: int) -> tuple[str, str, np.ndarray]:
    """渲染单个预设 → (文件名, 预设 XML, 预览 RGBA)。"""
    if bp.tip_gray is None and not bp.is_computed:
        raise ValueError(f"{bp.name}: 无笔尖位图")

    if bp.is_computed:
        tip_png = None
        brush_def = _computed_brush_def(bp)
        preview = render_tip_preview(
            _circle_mask(bp.hardness if bp.hardness is not None else 100.0,
                         bp.roundness))
    else:
        # Krita 对非正方形 png_brush 的编辑器尺寸显示存在偏差；
        # 与映射时的 max(width,height) 基准保持一致，内嵌正方形笔尖。
        square_tip = _square_tip(bp.tip_gray)
        tip_png = _png_bytes(square_tip)
        res_name = _safe_filename(bp.name, index).rsplit(".", 1)[0]
        brush_def = _sampled_brush_def(res_name, tip_png, bp)
        preview = render_tip_preview(bp.tip_gray)

    xml = build_preset_xml(
        name=bp.name,
        brush_definition=brush_def,
        tip_png=tip_png,
        tip_name=_safe_filename(bp.name, index).rsplit(".", 1)[0] if tip_png else None,
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
        texture=_texture_xml(bp),
    )
    return _safe_filename(bp.name, index), xml, preview


def convert_presets(abr: AbrFile, presets: list[BrushPreset], out_dir: str,
                    make_bundle: bool = True, make_standalone: bool = True) -> ConvertResult:
    """把给定预设子集转成 .kpp / .bundle。

    make_bundle / make_standalone 至少一个为 True，对应「.bundle」与「.kpp」两种产物；
    两者都为 False 时不写任何文件，必须显式报错而不是假装转换成功。
    """
    if not make_bundle and not make_standalone:
        raise ValueError("make_bundle 与 make_standalone 至少需要一个为 True")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    result = ConvertResult(out_dir=str(out), presets=presets)
    kpp_dir = out / "kpp"
    if make_standalone:
        kpp_dir.mkdir(parents=True, exist_ok=True)

    for i, bp in enumerate(presets):
        if bp.tip_gray is None and not bp.is_computed:
            result.skipped.append(f"{bp.name}: 无笔尖位图")
            continue
        fname, xml, preview = _render_preset(bp, i)
        result.kpp_files.append((fname, _build_kpp_bytes(xml, preview)))
        if make_standalone:
            write_kpp(str(kpp_dir / fname), xml, preview)

    if make_bundle and result.kpp_files:
        stem = getattr(abr, "name", None) or "converted"
        result.bundle_path = write_bundle(str(out / f"{stem}.bundle"), result.kpp_files)
    return result


def _sampled_brush_def(res_name: str, tip_png: bytes, bp: BrushPreset) -> str:
    """生成采样笔尖的 png_brush <Brush> 定义（filename + md5sum 引用内嵌 PNG）。"""
    import hashlib
    from .kpp.preset_xml import sampled_brush_definition
    md5 = hashlib.md5(tip_png).hexdigest()
    return sampled_brush_definition(res_name + ".png", md5, bp.spacing, bp.angle, bp.scale)


def _computed_brush_def(bp: BrushPreset) -> str:
    """生成计算笔刷的 auto_brush <Brush> 定义（程序化圆形笔尖）。"""
    from .kpp.preset_xml import auto_brush_definition
    return auto_brush_definition(bp.diameter or 30.0, bp.spacing, bp.angle,
                                 bp.roundness, bp.hardness)


def _build_kpp_bytes(xml: str, preview_rgba: np.ndarray) -> bytes:
    """把预设 XML + 预览 RGBA 编码为完整 .kpp 字节。"""
    from .kpp.kpp_writer import build_kpp
    return build_kpp(xml, preview_rgba)
