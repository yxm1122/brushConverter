# -*- coding: utf-8 -*-
"""v1.5.0（任务 135）纹理批落地回归：预缩放 / hardMix 满覆盖 / 对比度去 clamp /
minimumDepth 比值 / 删除 legacy 开关。

对应 132 落地清单 4.3 的六条与附加项，逐条给「可复跑断言 + 旧基准错在哪」：

- 135-1 预缩放（128 实验 24/24 = 0.00%/max≤1）：scale>100 时用 PS 同核
  （源对齐周期双线性）把纹理重采样到 round(边长×s/100) 再内嵌，Scale=1.0；
  s≤100 保持原路径。旧基准错在哪：移除前 scale 被 max(0.01,min(10.0,…)) 截断、
  >100% 交给 Krita 的 Qt 中心对齐重采样，与 PS 差 0.5(k-1) 画布 px 相位，
  真实纹理上 90%+ 超阈（128 raw 16/16 超阈）——那不是目标行为。
- 135-2 hardMix 满覆盖（128A §二 hard 3/3 = 0.00%/max 0）：base≡1 的笔尖上
  Strength=1.0 + Brightness ±1/255（符号随 Invert）。旧基准错在哪：旧路径
  按 depth 写 Strength（d10 → 95.573%/max255）且不补 8-bit 边界差。
- 135-4 对比度去 min(2.0,k)（125 A 级）：k 原值直写、亮度仍截断 [-1,1]。
  旧基准错在哪：C>50 时旧 clamp 把 k 压到 2，C=84（k=6.25）输出错误；
  C=100 的 b_k 在 k=1e6 下必须用 9 位小数，3 位会翻 1 个 mask 级、max_delta 128。
- 135-5 minimumDepth 比值（126 C-9）：曲线 0 端点 = s_m/s_d。
  旧基准错在哪：旧实现写绝对值 s_m，Krita 强度是乘性的（Value×curve），
  低压力端被再乘一次 Value（d=50 差 2×、d=10 差 10×）。
  PS 侧作用点未量测（128A F-3）⇒ 只落比值、不写死常数项、不写「已达标」。
- 135-6 删开关：calibrated 唯一路径（正向断言在 test_texture_formula_switch.py）。

tEXt 标记与 tex_<sha256前12> 命名：预缩放后的内嵌字节同样走 _texture_png_array，
标记内容与资源名都按新字节派生，绝不写死旧值。
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from brush_converter.convert import (  # noqa: E402
    _png_size,
    _prescale_texture_image,
    _render_preset,
    _texture_png_array,
    _texture_strength,
    _texture_strength_min,
    _texture_xml,
    _tip_is_full_coverage,
)
from brush_converter.kpp.preset_xml import (  # noqa: E402
    TextureXml,
    build_preset_xml,
    texture_brightness_contrast,
)
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402

pytestmark = pytest.mark.verify

TOL = 2
RATIO_LIMIT = 0.02

#: 任务 12 的参数化模型（与 test_texture_formula_switch.py 同口径）
PS_PARAMS = {"pivot": 127.0, "k_fitted": {}, "order": "sign_c"}


def _image_gray(seed: int = 7, size: int = 64) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(size, size), dtype=np.uint8)


def _image_rgb(seed: int = 7, size: int = 32) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(size, size, 3), dtype=np.uint8)


def _texture(**kwargs) -> TextureSettings:
    base = dict(name="x", uuid="x", image=_image_gray())
    base.update(kwargs)
    return TextureSettings(**base)


def _texture_xml_of(texture: TextureSettings,
                    full_coverage: bool | None = None) -> TextureXml:
    tex = _texture_xml(BrushPreset(name="x", texture=texture),
                       full_coverage=full_coverage)
    assert tex is not None
    return tex


def _xml_of(texture: TextureSettings, full_coverage: bool | None = None) -> str:
    return build_preset_xml(
        name="x", brush_definition='<Brush type="auto_brush" spacing="0.1"/>',
        tip_png=None, texture=_texture_xml_of(texture, full_coverage),
    )


def _param(xml: str, name: str) -> str:
    m = re.search(
        r'<param name="' + re.escape(name) + r'" type="(?:internal|string)">(.*?)</param>',
        xml, re.S)
    assert m is not None, f"XML 缺少参数 {name}"
    raw = m.group(1)
    if raw.startswith("<![CDATA[") and raw.endswith("]]>"):
        raw = raw[len("<![CDATA["):-len("]]>")]
    return raw


def _text_chunks(png: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    pos = 8
    while pos + 8 <= len(png):
        length = int.from_bytes(png[pos:pos + 4], "big")
        ctype = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"tEXt":
            keyword, _, text = data.partition(bytes((0,)))
            out[keyword.decode("latin-1")] = text.decode("latin-1")
    return out


# ---------------------------------------------------------------------------
# 135-1 预缩放
# ---------------------------------------------------------------------------
def test_prescale_target_sizes_and_kernel():
    """目标边长 = round(边长 × s/100)；s<=100 不走预缩放；与 128 的 prescale_gray 逐像素一致。"""
    from tools.exp_texture_scale import prescale_gray

    img = _image_gray(size=64)
    for scale_pct, side in ((150, 96), (200, 128), (300, 192), (400, 256)):
        out = _prescale_texture_image(img, scale_pct)
        assert out.shape == (side, side)
        assert out.dtype == np.uint8
        ref = np.rint(prescale_gray(img.astype(np.float64), side))
        assert np.array_equal(out, np.clip(ref, 0, 255).astype(np.uint8))
    # 非方纹理：高/宽各算各的
    rect = _image_gray(size=64)[:40, :]
    out_rect = _prescale_texture_image(rect, 250)
    assert out_rect.shape == (100, 160)
    # RGB 逐通道同核
    rgb = _image_rgb(size=32)
    out_rgb = _prescale_texture_image(rgb, 200)
    assert out_rgb.shape == (64, 64, 3)
    for c in range(3):
        assert np.array_equal(out_rgb[:, :, c],
                              _prescale_texture_image(rgb[:, :, c], 200))
    # s<=100（以及目标尺寸不变）→ None：调用方保持原路径
    assert _prescale_texture_image(img, 100) is None
    assert _prescale_texture_image(img, 50) is None


def test_scale_gt_100_prescaled_and_scale_one():
    """135-1：s=200 内嵌 2× 新 PNG、Scale=1.0、资源名/标记按新字节派生（不写死旧值）。"""
    img = _image_gray(size=64)
    tex = _texture_xml_of(_texture(scale=200.0, image=img))
    assert tex.scale == 1.0
    assert _png_size(tex.png_bytes) == (128, 128)
    decoded = np.asarray(Image.open(io.BytesIO(tex.png_bytes)))
    assert np.array_equal(decoded, _prescale_texture_image(img, 200))
    raw_png = _texture_png_array(img)
    assert tex.pattern_filename == (
        "tex_" + hashlib.sha256(tex.png_bytes).hexdigest()[:12] + ".png")
    assert tex.pattern_filename != (
        "tex_" + hashlib.sha256(raw_png).hexdigest()[:12] + ".png")
    texts = _text_chunks(tex.png_bytes)
    assert texts["brushconverter"] == (
        "sha256=" + hashlib.sha256(np.ascontiguousarray(decoded).tobytes()).hexdigest())
    again = _texture_xml_of(_texture(scale=200.0, image=img))
    assert again.png_bytes == tex.png_bytes
    assert again.pattern_filename == tex.pattern_filename


def test_scale_le_100_keeps_raw_path_without_clamp():
    """s<=100 保持原路径：原字节、Scale=s/100；Scale 的 [0.01,10] 截断已删除。"""
    img = _image_gray(size=32)
    for scale_pct, expect in ((50.0, 0.5), (37.5, 0.375), (100.0, 1.0)):
        tex = _texture_xml_of(_texture(scale=scale_pct, image=img))
        assert tex.scale == pytest.approx(expect)
        assert tex.png_bytes == _texture_png_array(img)
    xml = build_preset_xml(
        "x", "", None,
        texture=TextureXml(pattern_filename="tex_aaaaaaaaaaaa.png", png_bytes=b"fake",
                           scale=12.5))
    assert _param(xml, "Texture/Pattern/Scale") == "12.5"


def test_scale_1000_prescaled_not_clamped_to_ten():
    """旧 clamp 上界 10 的样本：scale=2000% 现在走预缩放（20×），不是 Scale=10。"""
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    tex = _texture_xml_of(_texture(scale=2000.0, image=img))
    assert tex.scale == 1.0
    assert _png_size(tex.png_bytes) == (80, 80)


def test_prescale_with_nonzero_bc_order_regression():
    """附加回归：预缩放 + 非 0 亮度/对比度（128 §6.2 未测顺序）只做一次记录式复测。

    口径：b/c 仍是 calibrated 闭式解，不烘焙进 PNG（内嵌字节 = 预缩放后的原始
    图案）；顺序假定 = 先重采样、后 b/c（Krita 侧本来就是「先取图案再算 b/c」，
    128 §6.2 判方向一致）。掩码空间与 PS 参数化模型比对，判据 ≤2%/max≤2。
    仍未测的部分（PS 是否先 b/c 再重采样）不在本轮范围，报告已登记。
    """
    from tools.lut_fit import bc_transform
    from tools.texture_mask import apply_three

    img = _image_gray(size=64, seed=11)
    ps_b, ps_c = -27, 17
    tex = _texture_xml_of(_texture(scale=200.0, brightness=ps_b, contrast=ps_c,
                                   invert=True, depth=100.0, blend_mode="Mul ",
                                   image=img))
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(tex.png_bytes))),
                          _prescale_texture_image(img, 200))
    assert (tex.brightness, tex.contrast) == texture_brightness_contrast(ps_b, ps_c)
    prescaled = _prescale_texture_image(img, 200).astype(np.float64).ravel()
    mask_t = np.tile(prescaled, 4)[:256]
    ps_mask = np.floor(bc_transform(mask_t, ps_b, ps_c, PS_PARAMS) + 0.5)
    kr_mask = np.floor(
        apply_three((mask_t / 255.0).astype(np.float32), False,
                    tex.brightness, tex.contrast).astype(np.float64) * 255.0 + 0.5)
    delta = np.abs(kr_mask - ps_mask)
    assert float(np.count_nonzero(delta > TOL)) / delta.size <= RATIO_LIMIT
    assert int(delta.max()) <= TOL


# ---------------------------------------------------------------------------
# 135-2 hardMix 满覆盖
# ---------------------------------------------------------------------------
def test_tip_is_full_coverage_detection():
    assert _tip_is_full_coverage(np.zeros((8, 8), dtype=np.uint8)) is True
    assert _tip_is_full_coverage(np.full((8, 8), 255, dtype=np.uint8)) is False
    assert _tip_is_full_coverage(np.array([[0, 0], [0, 1]], dtype=np.uint8)) is False
    assert _tip_is_full_coverage(None) is False
    assert _tip_is_full_coverage(np.zeros((0, 0), dtype=np.uint8)) is False


@pytest.mark.parametrize("invert,expected_text", [
    (True, "0.00392157"),
    (False, "-0.00392157"),
])
def test_hardmix_full_coverage_bias(invert, expected_text):
    """满覆盖：Strength=1.0（不随 depth）+ Brightness ±1/255（符号随 Invert）。"""
    texture = _texture(blend_mode="hardMix", depth=39.0, invert=invert,
                       brightness=0, contrast=0,
                       image=np.zeros((8, 8), dtype=np.uint8))
    tex = _texture_xml_of(texture, full_coverage=True)
    assert tex.strength == 1.0
    assert tex.brightness == pytest.approx(
        texture_brightness_contrast(0, 0)[0] + (1.0 / 255.0 if invert else -1.0 / 255.0))
    xml = _xml_of(texture, full_coverage=True)
    assert _param(xml, "Texture/Pattern/Brightness") == expected_text
    assert _param(xml, "Texture/Strength/Value") == "1"


@pytest.mark.parametrize("invert", [True, False])
def test_hardmix_full_coverage_krita_model_outputs_all_ones(invert):
    """模型校验：发出去的参数让 Krita hardMix 在 base≡1 时恒定输出 1（= 128A 实测 PS 真值）。"""
    from tools.texture_mask import compute_mask

    tex = _texture_xml_of(
        _texture(blend_mode="hardMix", depth=39.0, invert=invert,
                 image=np.zeros((8, 8), dtype=np.uint8)), full_coverage=True)
    out = compute_mask(np.arange(256) / 255.0, invert, tex.brightness, tex.contrast,
                       tex.texturing_mode, tex.strength, base=np.ones(256))
    assert np.all(out == 1.0)


def test_hardmix_not_full_coverage_keeps_old_path():
    """base<1 不下发偏置（128A base<1 残差 10%–40%，只登记不修）。"""
    tex = _texture_xml_of(_texture(blend_mode="hardMix", depth=39.0,
                                   image=np.full((8, 8), 128, dtype=np.uint8)),
                          full_coverage=False)
    assert tex.strength == pytest.approx(0.39)
    assert tex.brightness == 0.0
    assert tex.strength_curve is None


def test_hardmix_full_coverage_pressure_not_biased():
    """满覆盖 + 压感：偏置的阈值随 s 变、不成立 ⇒ 本轮不下发（报告登记）。"""
    texture = _texture(blend_mode="hardMix", depth=50.0, depth_min=10.0,
                       pressure=True, invert=True,
                       image=np.zeros((8, 8), dtype=np.uint8))
    tex = _texture_xml_of(texture, full_coverage=True)
    assert tex.strength == pytest.approx(0.5)
    assert tex.brightness == 0.0
    assert tex.strength_curve == "0,0.2;1,1;"


def test_render_preset_uses_actual_embedded_tip_for_full_coverage():
    """生产路径：满覆盖判定用实际内嵌的那张笔尖位图（不是只看 ABR 原始蒙版）。"""
    full = BrushPreset(name="full", tip_gray=np.zeros((16, 16), dtype=np.uint8),
                       texture=_texture(blend_mode="hardMix", depth=39.0, invert=True,
                                        image=np.zeros((8, 8), dtype=np.uint8)))
    _, xml_full, _ = _render_preset(full, 0)
    assert _param(xml_full, "Texture/Strength/Value") == "1"
    assert _param(xml_full, "Texture/Pattern/Brightness") == "0.00392157"

    soft = BrushPreset(name="soft",
                       tip_gray=np.full((16, 16), 128, dtype=np.uint8),
                       texture=_texture(blend_mode="hardMix", depth=39.0, invert=True,
                                        image=np.zeros((8, 8), dtype=np.uint8)))
    _, xml_soft, _ = _render_preset(soft, 0)
    assert _param(xml_soft, "Texture/Strength/Value") == "0.39"
    assert _param(xml_soft, "Texture/Pattern/Brightness") == "0"

    computed = BrushPreset(name="computed", is_computed=True, hardness=100.0,
                           diameter=30.0, roundness=100.0,
                           texture=_texture(blend_mode="hardMix", depth=39.0, invert=True,
                                            image=np.zeros((8, 8), dtype=np.uint8)))
    _, xml_computed, _ = _render_preset(computed, 0)
    assert _param(xml_computed, "Texture/Strength/Value") == "0.39"


# ---------------------------------------------------------------------------
# 135-4 对比度：C>50 的 k 原值直写
# ---------------------------------------------------------------------------
def test_contrast_above_50_written_raw_in_xml():
    for ps_c, text in ((75, "4"), (84, "6.25"), (100, "1000000")):
        tex = _texture_xml_of(_texture(contrast=ps_c))
        xml = _xml_of(_texture(contrast=ps_c))
        assert tex.contrast == pytest.approx(float(text))
        assert _param(xml, "Texture/Pattern/Contrast") == text
    # 1e6 不许写成科学计数法（Krita 数值解析的稳妥口径）
    assert "1e+06" not in _xml_of(_texture(contrast=100))


# ---------------------------------------------------------------------------
# 135-5 minimumDepth：曲线 0 端点写比值 s_m/s_d
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("blend_mode,ps_depth,depth_min,expected_curve", [
    ("Mul ", 50.0, 10.0, "0,0.2;1,1;"),
    ("linearHeight", 50.0, 10.0, "0,0.2;1,1;"),
    ("Sbtr", 10.0, 10.0, "0,1;1,1;"),
    ("Mul ", 0.0, 10.0, "0,1;1,1;"),
    ("Mul ", 50.0, 100.0, "0,1;1,1;"),
])
def test_minimum_depth_curve_writes_ratio(blend_mode, ps_depth, depth_min,
                                          expected_curve):
    texture = _texture(blend_mode=blend_mode, depth=ps_depth, depth_min=depth_min,
                       pressure=True,
                       image=np.full((8, 8), 128, dtype=np.uint8))
    tex = _texture_xml_of(texture)
    assert tex.strength_curve == expected_curve
    assert _param(_xml_of(texture), "Texture/Strength/commonCurve") == expected_curve


def test_minimum_depth_ratio_is_multiplicative_equivalent():
    """比值语义：Value × curve(0) 必须等于绝对强度 s_m（Krita 乘性）。"""
    for blend_mode in ("Mul ", "linearHeight", "Sbtr"):
        tex = _texture_xml_of(_texture(blend_mode=blend_mode, depth=50.0,
                                       depth_min=10.0, pressure=True,
                                       image=np.full((8, 8), 128, dtype=np.uint8)))
        ratio = float(tex.strength_curve.split(";")[0].split(",")[1])
        s_m = _texture_strength_min(10.0, blend_mode)
        assert _texture_strength(50.0, blend_mode) > 0.0
        assert tex.strength * ratio == pytest.approx(s_m, abs=1e-12)


# ---------------------------------------------------------------------------
# 135-6 删除 legacy：开关符号清零（变量名本身也已从代码/用例删除）
# ---------------------------------------------------------------------------
def test_legacy_switch_gone_from_texture_pipeline():
    from brush_converter.kpp import preset_xml as px
    from brush_converter.convert import convert as _convert  # noqa: F401
    assert not hasattr(px, "_TEXTURE_FORMULA_ENV")
    assert not hasattr(px, "_texture_formula")
    assert not hasattr(px, "_texture_brightness_legacy")
    assert not hasattr(px, "_texture_contrast_legacy")
    # 唯一路径的固定值（旧 0.10 基线已不存在）
    tex = _texture_xml_of(_texture(brightness=-27, contrast=-17))
    assert (tex.brightness, tex.contrast) == pytest.approx((0.128, 0.83))
