# -*- coding: utf-8 -*-
"""Krita 纹理掩码复刻的单元测试（任务 07 + 07A）。

覆盖：
- 任务 07：恒等、反相、亮度单调（方向与源码一致：越大越暗）、对比度中心与拉伸、
  截断无 NaN、2×2 手工可验算数值、纹理 RGBA -> 灰度掩码的整除公式。
- 任务 07A：5 种混合模式（0/1/10/14/15）各给 2×2 具体数值断言、深度强度量化、
  未知枚举显式抛 NotImplementedError、compute_mask 全流程。

所有期望值都能手算复核：深度 0.1 的 8-bit 强度 = floor(0.1*255+0.5)/255 = 26/255，
Height 系权重 = 10*强度 = 260/255。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# pytest 以 tests/ 为 rootdir 收集，仓库根需要手工加入 sys.path 才能 import tools.*
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.texture_mask import (  # noqa: E402
    MODE_HARD_MIX_PHOTOSHOP,
    MODE_HEIGHT_PHOTOSHOP,
    MODE_LINEAR_HEIGHT_PHOTOSHOP,
    MODE_MULTIPLY,
    MODE_SUBTRACT,
    SUPPORTED_MODES,
    apply_brightness,
    apply_contrast,
    apply_depth,
    apply_invert,
    apply_three,
    blend,
    compute_mask,
    texture_mask_from_rgba,
)

# 本文件的全部用例都挂在 verify 标记下（任务 07/07A 的 §四 验收标准）
pytestmark = pytest.mark.verify

# 5 种模式的 2×2 手工样例（base=笔尖 alpha，tex=处理后的纹理掩码）
BASE_2X2 = np.array([[1.0, 0.5], [0.25, 0.8]], dtype=np.float32)
TEX_2X2 = np.array([[0.5, 0.5], [0.4, 0.25]], dtype=np.float32)


# ============================ 任务 07：三步 ============================

def test_identity_three_steps():
    """恒等：brightness=0、contrast=1、invert=False 时输出与输入逐像素相等。"""
    rng = np.random.default_rng(20261118)
    mask = rng.random((8, 8)).astype(np.float32)
    out = apply_three(mask, invert=False, brightness=0.0, contrast=1.0)
    assert out.dtype == np.float32
    assert np.allclose(out, mask, atol=1e-6)


def test_invert_is_exact_complement():
    """反相：x 与 1-x 精确对应（对应源码 L199-201）。"""
    mask = np.array([[0.0, 0.25], [0.5, 1.0]], dtype=np.float32)
    out = apply_invert(mask, True)
    assert np.allclose(out, 1.0 - mask, atol=1e-6)
    # enabled=False 时必须原样返回，不能悄悄反相
    assert np.allclose(apply_invert(mask, False), mask, atol=1e-6)


def test_brightness_is_subtraction_and_monotonic():
    """亮度单调：源码是 maskValue -= brightness，因此亮度越大输出越暗（越小）。

    注意：任务书 §三 第 2 条把「≥」写反了，硬性要求第 2 条与源码均要求
    「maskValue -= brightness」，本测试按源码断言递减，理由见验收报告决策记录。
    """
    mask = np.array([[0.0, 0.5], [0.7, 1.0]], dtype=np.float32)
    out0 = apply_brightness(mask, 0.0)
    out2 = apply_brightness(mask, 0.2)
    assert np.all(out2 <= out0 + 1e-6)
    assert out2.mean() < out0.mean()
    assert np.allclose(out2, np.clip(mask - 0.2, 0.0, 1.0), atol=1e-6)


def test_contrast_center_and_stretch_numbers():
    """对比度中心：contrast=1 恒等；contrast=2 时以 0.5 为中心拉开（具体数字）。"""
    mask = np.array([[0.4, 0.5], [0.6, 0.25]], dtype=np.float32)
    assert np.allclose(apply_contrast(mask, 1.0), mask, atol=1e-6)
    out2 = apply_contrast(mask, 2.0)
    # (0.4-0.5)*2+0.5=0.3; (0.5-0.5)*2+0.5=0.5; (0.6-0.5)*2+0.5=0.7; (0.25-0.5)*2+0.5=0.0
    assert np.allclose(out2, np.array([[0.3, 0.5], [0.7, 0.0]], dtype=np.float32), atol=1e-6)
    # contrast=0 时全部压到中心 0.5
    assert np.allclose(apply_contrast(mask, 0.0), np.full_like(mask, 0.5), atol=1e-6)


def test_three_steps_2x2_manual_numbers():
    """2×2 手工可验算：亮度 0.1 后截断，再反相。"""
    mask = np.array([[0.0, 0.5], [0.25, 1.0]], dtype=np.float32)
    # 亮度 0.1：[-0.1, 0.4, 0.15, 0.9] -> 截断 [0.0, 0.4, 0.15, 0.9]
    out = apply_three(mask, invert=False, brightness=0.1, contrast=1.0)
    assert np.allclose(out, np.array([[0.0, 0.4], [0.15, 0.9]], dtype=np.float32), atol=1e-6)
    # 再反相：1-x -> [1.0, 0.6, 0.85, 0.1]
    out_inv = apply_three(mask, invert=True, brightness=0.1, contrast=1.0)
    assert np.allclose(out_inv, np.array([[1.0, 0.6], [0.85, 0.1]], dtype=np.float32), atol=1e-6)


def test_clip_and_no_nan_under_extremes():
    """极端参数下输出仍在 [0,1] 且无 NaN。"""
    rng = np.random.default_rng(7)
    mask = rng.random((16, 16)).astype(np.float32)
    before = mask.copy()
    for brightness in (-3.0, -0.5, 0.0, 0.5, 3.0):
        for contrast in (0.0, 1.0, 2.0, 50.0):
            for invert in (False, True):
                out = apply_three(mask, invert=invert, brightness=brightness, contrast=contrast)
                assert out.dtype == np.float32
                assert np.isfinite(out).all()
                assert out.min() >= 0.0 and out.max() <= 1.0
    # 输入数组不得被就地修改
    assert np.array_equal(mask, before)


def test_texture_mask_from_rgba_uses_integer_gray_formula():
    """纹理 RGBA -> 掩码：gray=(11R+16G+5B)//32（源码 L189 的整数除法）。"""
    img = np.zeros((2, 2, 4), dtype=np.uint8)
    img[0, 0] = (255, 0, 0, 255)   # gray=(255*11)//32=87 -> 87/255
    img[0, 1] = (0, 255, 0, 255)   # gray=(255*16)//32=127 -> 127/255
    img[1, 0] = (0, 0, 255, 255)   # gray=(255*5)//32=39 -> 39/255
    img[1, 1] = (0, 0, 0, 0)       # alpha=0 -> (g/255)*0+(1-0)=1.0
    out = texture_mask_from_rgba(img)
    assert np.allclose(out, np.array([[87 / 255, 127 / 255], [39 / 255, 1.0]],
                                     dtype=np.float32), atol=1e-6)
    assert out.dtype == np.float32


# ======================== 任务 07A：混合模式 ========================

def test_blend_multiply_2x2_numbers():
    """模式 0 Multiply：src*dst*strength（strength=1 时就是逐点相乘）。"""
    out = blend(BASE_2X2, TEX_2X2, MODE_MULTIPLY, strength=1.0)
    assert np.allclose(out, np.array([[0.5, 0.25], [0.1, 0.2]], dtype=np.float32), atol=1e-6)

    # strength=0.5 -> 8-bit 强度 = floor(0.5*255+0.5)/255 = 128/255
    s = 128 / 255
    expected = BASE_2X2 * TEX_2X2 * np.float32(s)
    out2 = blend(BASE_2X2, TEX_2X2, MODE_MULTIPLY, strength=0.5)
    assert np.allclose(out2, expected, atol=1e-6)


def test_blend_subtract_2x2_numbers():
    """模式 1 Subtract：max(0, dst-(src+(1-s)))，s=1 时即 dst-src 并下限 0。"""
    out = blend(BASE_2X2, TEX_2X2, MODE_SUBTRACT, strength=1.0)
    # [1-0.5=0.5, 0.5-0.5=0, 0.25-0.4<0 -> 0, 0.8-0.25=0.55]
    assert np.allclose(out, np.array([[0.5, 0.0], [0.0, 0.55]], dtype=np.float32), atol=1e-6)
    # strength=0（强度 0）时 inv=1：dst-src-1 全部被下限截成 0
    assert np.allclose(blend(BASE_2X2, TEX_2X2, MODE_SUBTRACT, strength=0.0),
                       np.zeros((2, 2), dtype=np.float32), atol=1e-6)


def test_blend_hard_mix_photoshop_2x2_numbers():
    """模式 10 Hard Mix (Photoshop)：(src + dst*s > 1) ? 1 : 0（严格大于）。"""
    out = blend(BASE_2X2, TEX_2X2, MODE_HARD_MIX_PHOTOSHOP, strength=1.0)
    # 0.5+1.0=1.5>1 ->1；0.5+0.5=1.0 不>1 ->0；0.4+0.25=0.65 ->0；0.25+0.8=1.05 ->1
    assert np.allclose(out, np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32), atol=1e-6)

    # strength=0.5：dst*128/255，只有 [0,0] 的 0.5+0.502 仍 >1
    out2 = blend(BASE_2X2, TEX_2X2, MODE_HARD_MIX_PHOTOSHOP, strength=0.5)
    assert np.allclose(out2, np.array([[1.0, 0.0], [0.0, 0.0]], dtype=np.float32), atol=1e-6)


def test_blend_height_photoshop_2x2_numbers():
    """模式 14 Height (Photoshop)：clamp(dst*(10s)-src, 0, 1)。

    depth=0.1 -> 8-bit 强度 26/255 -> 权重 10*26/255 = 260/255。
    """
    out = blend(BASE_2X2, TEX_2X2, MODE_HEIGHT_PHOTOSHOP, strength=0.1)
    w = 260 / 255
    expected = np.clip(BASE_2X2 * np.float32(w) - TEX_2X2, 0.0, 1.0)
    # 手算：[1*w-0.5, 0.5*w-0.5, 0.25*w-0.4<0->0, 0.8*w-0.25]
    assert np.allclose(out, np.array([[w - 0.5, 0.5 * w - 0.5], [0.0, 0.8 * w - 0.25]],
                                     dtype=np.float32), atol=1e-6)
    assert np.allclose(out, expected, atol=1e-6)


def test_blend_linear_height_photoshop_2x2_numbers():
    """模式 15 Linear Height (Photoshop)：clamp(max((1-src)*md, md-src),0,1)，md=dst*260/255。"""
    out = blend(BASE_2X2, TEX_2X2, MODE_LINEAR_HEIGHT_PHOTOSHOP, strength=0.1)
    w = 260 / 255
    md = BASE_2X2 * np.float32(w)
    expected = np.clip(np.maximum((1.0 - TEX_2X2) * md, md - TEX_2X2), 0.0, 1.0)
    # 手算四格：0.51960784 / 0.25490196 / 0.15294118 / 0.61176471
    assert np.allclose(out, np.array([[260 / 255 - 0.5, 0.5 * 260 / 255 * 0.5],
                                      [0.6 * 0.25 * 260 / 255, 0.75 * 0.8 * 260 / 255]],
                                     dtype=np.float32), atol=1e-7)
    assert np.allclose(out, expected, atol=1e-6)


def test_unknown_mode_raises_with_enum_number():
    """未实现模式必须显式抛 NotImplementedError，且错误信息里带枚举号。"""
    assert sorted(SUPPORTED_MODES) == [0, 1, 10, 14, 15]
    for mode in (2, 3, 4, 12, 13, 16, -1):
        with pytest.raises(NotImplementedError) as excinfo:
            blend(BASE_2X2, TEX_2X2, mode)
        assert str(mode) in str(excinfo.value)


def test_apply_depth_quantizes_strength():
    """深度按源码的 8-bit 强度量化后乘到掩码上（Multiply 类线性模式等价形式）。"""
    mask = np.array([[0.0, 0.4], [0.8, 1.0]], dtype=np.float32)
    out = apply_depth(mask, 0.1)
    assert np.allclose(out, mask * np.float32(26 / 255), atol=1e-6)  # floor(.1*255+.5)=26
    assert np.allclose(apply_depth(mask, 1.0), mask, atol=1e-6)      # 100% 深度恒等
    assert np.allclose(apply_depth(mask, 0.0), np.zeros_like(mask), atol=1e-6)
    assert np.allclose(apply_depth(mask, -1.0), np.zeros_like(mask), atol=1e-6)  # 越界 clip


def test_compute_mask_full_pipeline():
    """compute_mask 串起完整流程：Multiply + 100% 深度 = 三步处理后的掩码。"""
    rng = np.random.default_rng(123)
    tex = rng.random((12, 10)).astype(np.float32)
    three = apply_three(tex, invert=True, brightness=0.1, contrast=1.33)
    got = compute_mask(tex, invert=True, brightness=0.1, contrast=1.33,
                       mode=MODE_MULTIPLY, depth=1.0)
    assert got.dtype == np.float32
    assert np.allclose(got, three, atol=1e-6)
    # 传入 base 时按混合公式走
    base = np.full_like(tex, 0.5)
    got_base = compute_mask(tex, invert=False, brightness=0.0, contrast=1.0,
                            mode=MODE_MULTIPLY, depth=1.0, base=base)
    assert np.allclose(got_base, tex * 0.5, atol=1e-6)


def test_compute_mask_extremes_no_nan_and_range():
    """compute_mask 在极端参数下输出仍为 float32、[0,1]、无 NaN。"""
    rng = np.random.default_rng(99)
    tex = rng.random((20, 20)).astype(np.float32)
    for mode in sorted(SUPPORTED_MODES):
        for depth in (0.0, 0.1, 1.0):
            for brightness in (-2.0, 0.0, 2.0):
                out = compute_mask(tex, invert=False, brightness=brightness,
                                   contrast=2.0, mode=mode, depth=depth)
                assert out.dtype == np.float32
                assert np.isfinite(out).all()
                assert out.min() >= 0.0 and out.max() <= 1.0
    with pytest.raises(NotImplementedError):
        compute_mask(tex, invert=False, brightness=0.0, contrast=1.0, mode=4, depth=0.5)
