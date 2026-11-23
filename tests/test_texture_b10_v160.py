# -*- coding: utf-8 -*-
"""v1.6.0（任务 153 / N6-B10）纹理亮度越界区预烘焙落地回归。

背景与证据（详见 docs/验收报告/152-报告.md §2.3/§四-9、153-报告.md）：
- PS 的纹理亮度/对比度运算**顺序随对比度符号翻转**：
  C<0 先对比度（中心 127.5、k=1+C/100）后亮度（v+b）；C>=0 先亮度后对比度。
- 现行闭式解在「C=−50 且 |PS 亮度|>127」把反解出的 b_k 截断到 ±1，掩码空间
  8/12 超阈（最大 61.125%/max23，128A F-4 + 152 复测一致）。
- 153 落地：命中越界区 → 把亮度/对比度/反相按 PS 8bit 链烘进内嵌 PNG，
  Krita 三字段写中性值（B=0、C=1、Invert=false）。152 实测 12/12 = 0.000%/max0。
- 代价（使用者 2026-11-23 已接受）：该笔纹理亮度/对比度/反相字段失去可编辑
  语义；旧 .kpp 必须重转；资源名/哈希/内嵌字节都按新像素重算。

旧基准错在哪：旧用例（test_texture_formula_switch.py 的
test_structural_limits_are_brightness_only / B9 记录组）把「闭式解截断后的
0.00%/max1」当成越界区的可接受结果，且把 128A 因「m10 真值文件不带纹理后缀」
造成的错配数字（multiply 76.680% 等）钉成已知残差；152 已证明那是测量错配。
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
    _ps_texture_mask_8bit,
    _render_preset,
    _texture_gray_8bit,
    _texture_png_array,
    _texture_xml,
)
from brush_converter.kpp.preset_xml import (  # noqa: E402
    _texture_brightness_calibrated_raw,
    texture_bc_out_of_range,
    texture_brightness_contrast,
)
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402

pytestmark = pytest.mark.verify

TOL = 2
#: B10 越界样本（128A F-4 的 6 组 / 纹理）+ 边界样本
B10_COMBOS = ((-150, -50), (-140, -50), (-128, -50), (128, -50), (140, -50), (150, -50))
#: 区内（不应触发烘焙）样本
IN_RANGE = ((-127, -50), (0, -50), (127, -50), (-27, -17), (50, 50), (0, 0), (0, 50))


def _image_gray(seed: int = 11, size: int = 64) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(size, size), dtype=np.uint8)


def _image_rgb(seed: int = 11, size: int = 64) -> np.ndarray:
    """灰度内容的 RGB 三通道（152 验证的两组纹理都是这种内容）。"""
    gray = _image_gray(seed, size)
    return np.stack([gray, gray, gray], axis=2)


def _texture(**kwargs) -> TextureSettings:
    base = dict(name="x", uuid="x", image=_image_gray(), blend_mode="Mltp",
                depth=100.0, invert=True)
    base.update(kwargs)
    return TextureSettings(**base)


def _bp(texture: TextureSettings, tip: np.ndarray | None = None) -> BrushPreset:
    if tip is None:
        tip = np.zeros((64, 64), dtype=np.uint8)  # 全 0 = 满覆盖（base≡1）
    return BrushPreset(name="x", spacing=0.25, tip_gray=tip, texture=texture)


def _xml_of(texture: TextureSettings, tip: np.ndarray | None = None) -> str:
    from brush_converter.kpp.preset_xml import build_preset_xml

    tex = _texture_xml(_bp(texture, tip))
    assert tex is not None
    return build_preset_xml(
        name="x", brush_definition='<Brush type="auto_brush" spacing="0.1"/>',
        tip_png=None, texture=tex)


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


def _ps_mask_ref(image: np.ndarray, b: int, c: int, invert: bool) -> np.ndarray:
    """测试内自带的 PS 8bit 链参考（独立于生产实现，用于逐像素对照）。"""
    gray = _texture_gray_8bit(image).astype(np.float64)
    if c < 0:
        k = max(0.5, 1.0 + c / 100.0)
        v = np.clip(np.rint((gray - 127.5) * k + 127.5), 0, 255)
        v = np.clip(np.rint(v + b), 0, 255)
    else:
        k = 1_000_000.0 if c >= 100 else 1.0 / (1.0 - c / 100.0)
        v = np.clip(np.rint(gray + b), 0, 255)
        v = np.clip(np.rint((v - 127.5) * k + 127.5), 0, 255)
    if invert:
        v = 255.0 - v
    return np.clip(v, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# 1) 越界判定（B10 定义 + 边界）
# ---------------------------------------------------------------------------
def test_out_of_range_detection_and_boundary():
    """越界判定 = calibrated 亮度反解越出 [-1,1]；等价「C=−50 且 |b|>127」。"""
    for b, c in B10_COMBOS:
        assert texture_bc_out_of_range(b, c) is True
        assert abs(_texture_brightness_calibrated_raw(b, c)) > 1.0
    for b, c in IN_RANGE:
        assert texture_bc_out_of_range(b, c) is False
    # 精确边界：C=−50 时 k=0.5 ⇒ b=−127 在域内（0.9980）、b=−128 越界（1.0059）；
    # 正侧对称：+127 在域内（−0.9941）、+128 越界（−1.0020）。
    assert texture_bc_out_of_range(-127, -50) is False
    assert texture_bc_out_of_range(-128, -50) is True
    assert texture_bc_out_of_range(127, -50) is False
    assert texture_bc_out_of_range(128, -50) is True
    # 正对比度侧反解值恒在域内（k>=1 把 b/255 压小），不会触发 B10 烘焙
    for c in (0, 50, 75, 84, 100):
        assert texture_bc_out_of_range(150, c) is False


# ---------------------------------------------------------------------------
# 2) 预烘焙链：逐像素 = PS 8bit 参考链（C<0 与 C>=0 两支）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("b,c", B10_COMBOS)
@pytest.mark.parametrize("invert", [True, False])
def test_bake_mask_equals_ps_chain_reference(b, c, invert):
    img = _image_gray()
    got = _ps_texture_mask_8bit(img, b, c, invert)
    ref = _ps_mask_ref(img, b, c, invert)
    assert np.array_equal(got, ref), f"({b},{c},invert={invert}) 与参考链不一致"
    assert got.dtype == np.uint8


def test_bake_mask_positive_contrast_branch():
    """C>=0 分支（先亮度后对比度）用 m10 的 b=50/c=50 组验证；该组不触发烘焙但链要正确。"""
    img = _image_rgb()
    got = _ps_texture_mask_8bit(img, 50, 50, True)
    ref = _ps_mask_ref(img, 50, 50, True)
    assert np.array_equal(got, ref)
    # C=100（k=1e6）也要能算完并两端化
    v = _ps_texture_mask_8bit(_image_gray(), 0, 100, False)
    assert set(np.unique(v)) <= {0, 255}


def test_gray_conversion_rgb_gray_content_equals_l():
    """RGB（三通道相同的灰度内容）与二维灰度的掩码逐像素一致。"""
    gray = _image_gray()
    rgb = np.stack([gray, gray, gray], axis=2)
    assert np.array_equal(_texture_gray_8bit(rgb), gray)
    assert np.array_equal(_texture_gray_8bit(gray), gray)


# ---------------------------------------------------------------------------
# 3) 命中烘焙：中性字段 + 内嵌 PNG 字节/哈希 + 二次调用确定性
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("b,c", B10_COMBOS)
def test_out_of_range_bakes_neutral_bc_and_invert(b, c):
    img = _image_rgb()
    tex = _texture(image=img, brightness=b, contrast=c, invert=True)
    got = _texture_xml(_bp(tex))
    assert got is not None
    assert got.brightness == 0.0
    assert got.contrast == 1.0
    assert got.invert is False
    decoded = np.asarray(Image.open(io.BytesIO(got.png_bytes)).convert("L"))
    assert np.array_equal(decoded, _ps_mask_ref(img, b, c, True))
    # 内嵌字节不再等于原图（证明真的烘过）
    assert got.png_bytes != _texture_png_array(np.asarray(Image.fromarray(img, "RGB")))


def test_resource_name_hash_md5_and_text_marker_from_new_bytes():
    """资源名 / PatternMD5Sum / tEXt 标记都按烘后新字节派生，绝不写死旧值。"""
    tex = _texture(brightness=-150, contrast=-50, invert=True)
    got = _texture_xml(_bp(tex))
    assert got is not None
    expect_name = "tex_" + hashlib.sha256(got.png_bytes).hexdigest()[:12] + ".png"
    assert got.pattern_filename == expect_name
    xml = _xml_of(tex)
    assert _param(xml, "Texture/Pattern/Name") == expect_name
    assert _param(xml, "Texture/Pattern/PatternFileName") == expect_name
    assert _param(xml, "Texture/Pattern/PatternMD5Sum") == hashlib.md5(got.png_bytes).hexdigest()
    decoded = np.asarray(Image.open(io.BytesIO(got.png_bytes)).convert("L"))
    texts = _text_chunks(got.png_bytes)
    assert texts["brushconverter"] == (
        "sha256=" + hashlib.sha256(np.ascontiguousarray(decoded).tobytes()).hexdigest())
    # 旧资源名（未烘原图）必须不同：旧 .kpp 必须重转
    raw_name = "tex_" + hashlib.sha256(_texture_png_array(tex.image)).hexdigest()[:12] + ".png"
    assert got.pattern_filename != raw_name
    # 确定性：同一输入两次转换逐字节一致
    again = _texture_xml(_bp(tex))
    assert again is not None and again.png_bytes == got.png_bytes


def test_xml_neutral_fields():
    xml = _xml_of(_texture(brightness=-150, contrast=-50, invert=True))
    assert _param(xml, "Texture/Pattern/Brightness") == "0"
    assert _param(xml, "Texture/Pattern/Contrast") == "1"
    assert _param(xml, "Texture/Pattern/Invert") == "false"
    # 其它字段不被烘焙波及：模式/强度/缩放/启用
    assert _param(xml, "Texture/Pattern/Enabled") == "true"
    assert _param(xml, "Texture/Strength/Value") == "1"


# ---------------------------------------------------------------------------
# 4) 区内组合保持原路径（不烘、字段仍走闭式解）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("b,c", IN_RANGE)
def test_in_range_combo_keeps_raw_texture_and_calibrated_fields(b, c):
    img = _image_gray()
    tex = _texture(image=img, brightness=b, contrast=c, invert=True)
    got = _texture_xml(_bp(tex))
    assert got is not None
    assert got.png_bytes == _texture_png_array(img)
    exp_b, exp_c = texture_brightness_contrast(b, c)
    assert got.brightness == pytest.approx(exp_b)
    assert got.contrast == pytest.approx(exp_c)
    assert got.invert is True


# ---------------------------------------------------------------------------
# 5) 与其它路径的组合：scale>100、hardMix 满覆盖
# ---------------------------------------------------------------------------
def test_scale_gt_100_then_bake():
    """先预缩放（135-1）后烘焙：掩码尺寸 = round(边长×s/100)，Scale=1.0。"""
    from brush_converter.convert import _prescale_texture_image

    img = _image_gray(size=32)
    tex = _texture(image=img, scale=200.0, brightness=-150, contrast=-50, invert=True)
    got = _texture_xml(_bp(tex))
    assert got is not None
    assert got.scale == 1.0
    decoded = np.asarray(Image.open(io.BytesIO(got.png_bytes)).convert("L"))
    assert decoded.shape == (64, 64)
    prescaled = _prescale_texture_image(img, 200.0)
    assert np.array_equal(decoded, _ps_mask_ref(prescaled, -150, -50, True))


def test_hardmix_full_coverage_baked_offsets_minus_one_lsb():
    """B10 + hardMix + 满覆盖：Strength 仍 1.0，偏置改为 -1/255（烘后 Invert=false）。"""
    from tools import texture_mask as tm

    tex = _texture(brightness=-150, contrast=-50, invert=True, blend_mode="hardMix")
    got = _texture_xml(_bp(tex))
    assert got is not None
    assert got.texturing_mode == 10
    assert got.strength == 1.0
    assert got.brightness == pytest.approx(-1.0 / 255.0)
    assert got.contrast == 1.0
    assert got.invert is False
    mask = np.asarray(Image.open(io.BytesIO(got.png_bytes)).convert("L")).astype(np.float32) / 255.0
    base = np.ones_like(mask, dtype=np.float32)  # 满覆盖笔尖
    out = tm.compute_mask(mask, invert=False, brightness=got.brightness,
                          contrast=got.contrast, mode=10, depth=1.0, base=base)
    assert np.all(out == 1), "PS 在 base≡1 的 hardMix 恒 255；烘后应恒 1"


def test_non_full_coverage_hardmix_has_no_bias():
    """hardMix + 非满覆盖（base<1）不加偏置（与 135-2 边界一致）。"""
    tip = np.full((64, 64), 255, dtype=np.uint8)  # 全 255 = 透明（非满覆盖）
    tex = _texture(brightness=-150, contrast=-50, invert=True, blend_mode="hardMix")
    got = _texture_xml(_bp(tex, tip=tip))
    assert got is not None
    assert got.brightness == 0.0
    assert got.contrast == 1.0
    assert got.invert is False
    assert got.strength == pytest.approx(1.0)  # depth=100 → 1.0（无偏置时也）
    # 非 hardMix 的 B10 只走烘焙，不动 strength
    tex2 = _texture(brightness=-150, contrast=-50, invert=True, blend_mode="Mltp")
    got2 = _texture_xml(_bp(tex2, tip=tip))
    assert got2 is not None and got2.strength == pytest.approx(1.0)
    assert got2.brightness == 0.0 and got2.invert is False


# ---------------------------------------------------------------------------
# 6) 生产路径端到端（_render_preset，convert_presets 用的同一函数）
# ---------------------------------------------------------------------------
def test_render_preset_end_to_end_fields():
    tex = _texture(brightness=-140, contrast=-50, invert=True)
    fname, xml, preview = _render_preset(_bp(tex), 0)
    assert fname.endswith(".kpp")
    assert _param(xml, "Texture/Pattern/Brightness") == "0"
    assert _param(xml, "Texture/Pattern/Contrast") == "1"
    assert _param(xml, "Texture/Pattern/Invert") == "false"
    assert _param(xml, "Texture/Pattern/TexturingMode") == "0"
    name = _param(xml, "Texture/Pattern/Name")
    assert re.fullmatch(r"tex_[0-9a-f]{12}\.png", name)
    assert preview.ndim == 3


# ---------------------------------------------------------------------------
# 7) 记录：B10 修复的是「掩码生成」，不是模式算子
# ---------------------------------------------------------------------------
def test_baked_mask_matches_ps_truth_for_multiply():
    """multiply + base=1 + d=100 时 Krita alpha = 掩码；烘后掩码=PS 真值（152 实渲 12/12 0.000%）。

    与闭式解（截断后）对照：±140/±150 必须明显不同（这正是 B10 残差所在）；
    ±128 只是刚越界（截断误差 1 级），登记为「边界组」，烘焙与否都 ≤2%/max1。
    """
    img = _image_gray()
    for b, c in B10_COMBOS:
        got = _ps_texture_mask_8bit(img, b, c, True)
        b_trunc, c_k = texture_brightness_contrast(b, c)
        assert abs(b_trunc) == pytest.approx(1.0)
        clip = np.clip((img.astype(np.float64) / 255.0 - b_trunc) - 0.5, -1.0, 2.0)
        closed = np.clip(np.rint((1.0 - np.clip(clip * c_k + 0.5, 0, 1)) * 255), 0, 255)
        diff = np.abs(closed.astype(np.int16) - got.astype(np.int16))
        if abs(b) >= 140:
            assert diff.max() > TOL, f"({b},{c}) 烘焙后掩码应与截断闭式解明显不同"
        else:  # ±128 边界组
            assert diff.max() <= 1
