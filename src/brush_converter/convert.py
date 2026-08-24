"""转换管线：.abr → Krita 预设（.kpp）与资源包（.bundle）。"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .abr import AbrFile
from .mapping import BrushPreset, map_presets
from .kpp import build_preset_xml, write_kpp, write_bundle
from .kpp.kpp_writer import render_tip_preview


def _png_bytes(gray: np.ndarray) -> bytes:
    """灰度蒙版（255=墨）→ PNG 字节（8bit L 通道）。"""
    buf = io.BytesIO()
    Image.fromarray(gray, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _safe_filename(name: str, index: int) -> str:
    """把中文名清洗为安全的 .kpp 文件名（前缀 3 位序号）。"""
    cleaned = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")
    cleaned = cleaned or "brush"
    return f"{index:03d}_{cleaned}.kpp"


def _circle_mask(diameter: float, hardness: float, roundness: float,
                 size: int = 256) -> np.ndarray:
    """按直径/硬度/圆度生成一个圆形灰度蒙版（用于计算笔刷预览）。"""
    s = size
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float64)
    cy = cx = (s - 1) / 2.0
    radius = diameter / 2.0 * (s / max(diameter, 1.0)) * 0.9  # 占画布 90%
    ratio = roundness / 100.0
    dx = (xx - cx) / radius
    dy = (yy - cy) / (radius * ratio)
    dist = np.sqrt(dx * dx + dy * dy)
    # 硬度 → 边缘软度；硬度 100 = 硬边，0 = 全软
    soft = max(0.02, (100.0 - hardness) / 100.0)
    falloff = 1.0 / (soft * 3.0 + 0.01)
    mask = np.clip(1.0 - dist, 0.0, 1.0)
    mask = np.clip(mask * falloff + (1.0 - falloff) * (dist <= 1.0), 0.0, 1.0)
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
            _circle_mask(bp.diameter or 30.0, bp.hardness if bp.hardness is not None else 100.0,
                         bp.roundness, 256))
    else:
        tip_png = _png_bytes(bp.tip_gray)
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
    )
    return _safe_filename(bp.name, index), xml, preview


def convert_presets(abr: AbrFile, presets: list[BrushPreset], out_dir: str,
                    make_bundle: bool = True, make_standalone: bool = True) -> ConvertResult:
    """把给定预设子集转成 .kpp / .bundle。

    make_bundle / make_standalone 至少一个为 True，对应「.bundle」与「.kpp」两种产物。
    """
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
