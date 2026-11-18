# -*- coding: utf-8 -*-
"""PS 纹理掩码参考实现（任务 11B）的单元测试。

覆盖四类：
1. 方向性：亮度 ±50 分居两侧、对比度 +50 更「拉开」（对应 11A §4 假设 1/2 的方向语义）；
2. 边界：极值不产生 NaN、输出恒在 0..1、depth 101 被 clamp 到 100（11A §3.3 判读 1）；
3. 参数语义复现（11A 观测表）：亮度 5 个观测值原样平移到像素、对比度 5 个观测值
   原样作用于因子表、两套模式号等价、未知模式号抛错；
4. 像素层复现（真值图）：至少 5 组「模式 x 深度 x 亮度/对比度」断言与
   tests/baselines/ps/ 真值图的差异比例与最大差值。

容差说明（不许为了让用例好过而放宽）：
- 参数语义层的断言是**精确相等**（== 0 误差）：11A 观测的就是运行期对象里的 int32 原值，
  参考实现对该层不做任何舍入；
- 真值图层的差异比例判据用项目口径 tools/imgdiff.py::diff_alpha：tol=2、分母=全部像素、
  达标线 2%。参考实现实测为 0.00%（见 docs/研究报告/ps-纹理公式-参考实现.md），
  这里仍按 2% 断言是为了吸收 PNG 取整，不是放宽到「差不多」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.ps_texture_reference import (  # noqa: E402
    K_TABLE,
    KRITA_MODE_HEIGHT,
    KRITA_MODE_LINEAR_HEIGHT,
    KRITA_MODE_MULTIPLY,
    KRITA_MODE_SUBTRACT,
    PS_MODE_HEIGHT,
    PS_MODE_LINEAR_HEIGHT,
    PS_MODE_MULTIPLY,
    PS_MODE_SUBTRACT,
    contrast_factor,
    depth_quantize,
    ps_mask,
    resolve_mode,
)

pytestmark = pytest.mark.verify

BASELINE_DIR = _REPO_ROOT / "tests" / "baselines" / "ps"
TEXTURE_PATH = BASELINE_DIR / "_work" / "texture_noise64.png"

#: 模式中文名 -> PS 运行期号（11A §3.3 表 B 实测）
MODE_CN_TO_PS = {
    "正片叠底": PS_MODE_MULTIPLY,
    "减去": PS_MODE_SUBTRACT,
    "高度": PS_MODE_HEIGHT,
    "线性高度": PS_MODE_LINEAR_HEIGHT,
}


def _mid_texture() -> np.ndarray:
    """64..191 的 100x100 渐变纹理：不触发任何 clip，便于断言线性关系。"""
    row = np.linspace(64, 191, 100)
    return np.tile(row, (100, 1))


def _full_texture() -> np.ndarray:
    """0..255 全覆盖的确定性纹理（与任务 05/12 的 seed 无关，测试不依赖基准库）。"""
    rng = np.random.default_rng(20261118)
    return rng.integers(0, 256, size=(100, 100)).astype(np.int32)


# ===========================================================================
# 一、方向性
# ===========================================================================
def test_brightness_moves_result_to_both_sides():
    """亮度 +50 与 -50 必须分居 0 的两侧（11A §4 假设 1 的方向语义）。

    正片叠底 d100 b0 c0 是恒等变换，因此亮度在像素层就是「加偏移」：
    b=+50 抬高蒙版、b=-50 压低蒙版，两侧差值应为 100/255（无缩放）。
    """
    tex = _mid_texture()
    base = ps_mask(tex, 0, 0, PS_MODE_MULTIPLY, 100)
    plus = ps_mask(tex, 50, 0, PS_MODE_MULTIPLY, 100)
    minus = ps_mask(tex, -50, 0, PS_MODE_MULTIPLY, 100)
    assert float(plus.mean()) > float(base.mean()) > float(minus.mean())
    # 精确到 8bit 量化：均值差 = 100/255
    assert abs(float(plus.mean()) - float(minus.mean()) - 100.0 / 255.0) <= 1.0 / 255.0 + 1e-6


def test_contrast_50_stretches_around_center():
    """对比度 +50 必须比 0 更「拉开」：中心 127、k=2 时标准差翻倍。"""
    tex = _mid_texture()
    c0 = ps_mask(tex, 0, 0, PS_MODE_MULTIPLY, 100)
    c50 = ps_mask(tex, 0, 50, PS_MODE_MULTIPLY, 100)
    cmin50 = ps_mask(tex, 0, -50, PS_MODE_MULTIPLY, 100)
    assert float(c50.std()) > float(c0.std()) > float(cmin50.std())
    # k=2 / k=0.5（任务 12 拟合表），在未截断区标准差严格成比例
    assert abs(float(c50.std()) - 2.0 * float(c0.std())) <= 2.0 / 255.0
    assert abs(float(cmin50.std()) - 0.5 * float(c0.std())) <= 2.0 / 255.0


# ===========================================================================
# 二、边界
# ===========================================================================
def test_extremes_never_nan_and_always_in_range():
    """亮度/对比度/深度全极值组合下无 NaN、输出恒在 0..1。"""
    tex = np.array([0, 1, 127, 128, 254, 255], dtype=np.int32)
    modes = [PS_MODE_MULTIPLY, PS_MODE_SUBTRACT, PS_MODE_HEIGHT, PS_MODE_LINEAR_HEIGHT]
    for mode in modes:
        for brightness in (-150, 0, 150):
            for contrast in (-100, -50, 0, 50, 100):
                for depth in (0, 100):
                    out = ps_mask(tex, brightness, contrast, mode, depth)
                    assert out.dtype == np.float32
                    assert np.all(np.isfinite(out)), (mode, brightness, contrast, depth)
                    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_depth_101_is_clamped_to_100():
    """11A §3.3 判读 1：深度请求 101 在 PS 运行期对象里被存成 100。

    参考实现按同一条规则 clamp，因此 depth=101 与 depth=100 必须逐像素完全相同。
    """
    tex = _full_texture()
    assert depth_quantize(101) == 255
    assert depth_quantize(101) == depth_quantize(100)
    assert depth_quantize(-5) == 0
    for mode in (PS_MODE_MULTIPLY, PS_MODE_SUBTRACT, PS_MODE_HEIGHT, PS_MODE_LINEAR_HEIGHT):
        a = ps_mask(tex, 0, 0, mode, 101)
        b = ps_mask(tex, 0, 0, mode, 100)
        assert np.array_equal(a, b), mode


def test_depth_quantization_matches_task12_law():
    """深度先量化 q=round(2.55*d) 再进入合成（任务 12 §4.2 的实测律）。

    高度模式 b0 c0、输入 t=0 时输出 = 12*q：
    d2/d4/d5/d6 -> 60/120/156/180（d10/d12 的 312/372 会饱和到 255，分别断言）。
    """
    tex = np.zeros((1, 1), dtype=np.int32)
    expected = {2: 60, 4: 120, 5: 156, 6: 180}
    for depth, value in expected.items():
        q = depth_quantize(depth)
        assert q == round(depth * 2.55)
        out = ps_mask(tex, 0, 0, PS_MODE_HEIGHT, depth)
        assert int(round(float(out[0, 0]) * 255.0)) == value, depth
    for depth in (10, 12):  # 12*q = 312 / 372 > 255，饱和
        assert int(round(float(ps_mask(tex, 0, 0, PS_MODE_HEIGHT, depth)[0, 0]) * 255.0)) == 255
    # 线性高度在 t=0 时两支相等：max((255-0)*12q/255, 12q-0) = 12q
    assert int(round(float(ps_mask(tex, 0, 0, PS_MODE_LINEAR_HEIGHT, 5)[0, 0]) * 255.0)) == 156


def test_texture_out_of_range_is_clipped_but_nan_raises():
    """纹理越界 clip（沿用 texture_mask 接口约定），NaN/Inf 抛错。"""
    assert np.array_equal(ps_mask(-5, 0, 0, PS_MODE_MULTIPLY, 100),
                          ps_mask(0, 0, 0, PS_MODE_MULTIPLY, 100))
    assert np.array_equal(ps_mask(300, 0, 0, PS_MODE_MULTIPLY, 100),
                          ps_mask(255, 0, 0, PS_MODE_MULTIPLY, 100))
    with pytest.raises(ValueError, match="NaN/Inf"):
        ps_mask(np.array([float("nan")]), 0, 0, PS_MODE_MULTIPLY, 100)
    with pytest.raises(ValueError, match="NaN/Inf"):
        ps_mask(np.array([float("inf")]), 0, 0, PS_MODE_MULTIPLY, 100)


# ===========================================================================
# 三、参数语义复现（11A 观测表）
# ===========================================================================
def test_brightness_11a_observations_are_raw_int_offsets():
    """11A §3.2 表 A 第 1~5 行（b=0/+50/-50/+150/-150）：原样进对象、无缩放无偏移无 clamp。

    正片叠底 d100 时 PS 输出 = 前置掩版 u，而 u = clip(t+b)：
    因此参考实现的 8bit 输出必须逐像素等于 clip(t+b)，一位不差。
    """
    tex = _full_texture()
    for brightness in (0, 50, -50, 150, -150):
        out = ps_mask(tex, brightness, 0, PS_MODE_MULTIPLY, 100)
        predicted = np.clip(tex + brightness, 0, 255)
        assert np.array_equal(np.rint(out.astype(np.float64) * 255.0).astype(np.int32), predicted), brightness


def test_contrast_11a_observations_use_factor_table_without_clamp():
    """11A §3.2 表 A 第 6~11 行（c=0/+50/-50/+100/-100）：原样进对象、无 clamp。

    像素层按任务 12 的因子表生效：u = 127 + (clip(t+b) - 127) * k
    （c>=0 先亮度后对比度）。c=-100 -> k=0 -> 全部压到中心 127；
    c=+100 -> k>=1e6 -> 二值化（0 / 127 / 255 三值）。
    """
    tex = _full_texture()
    for contrast in (0, 50, -50, 100, -100):
        out = ps_mask(tex, 0, contrast, PS_MODE_MULTIPLY, 100)
        got = np.rint(out.astype(np.float64) * 255.0).astype(np.int32)
        k = contrast_factor(contrast)
        predicted = np.clip(127.0 + (tex.astype(np.float64) - 127.0) * k, 0.0, 255.0)
        predicted = np.rint(predicted).astype(np.int32)
        assert np.array_equal(got, predicted), contrast
    # 极值的形状特征（防止因子表被悄悄改成别的结构）
    flat = ps_mask(tex, 0, -100, PS_MODE_MULTIPLY, 100)
    assert float(flat.min()) == float(flat.max())  # k=0：恒为 127/255
    binary = np.rint(ps_mask(tex, 0, 100, PS_MODE_MULTIPLY, 100).astype(np.float64) * 255.0).astype(np.int32)
    assert set(np.unique(binary)).issubset({0, 127, 255})


def test_ps_and_krita_mode_numbers_are_equivalent():
    """两套模式编号显式映射到同一实现：PS 7/39/40/41 == Krita 0/1/14/15。"""
    pairs = [
        (PS_MODE_MULTIPLY, KRITA_MODE_MULTIPLY),
        (PS_MODE_SUBTRACT, KRITA_MODE_SUBTRACT),
        (PS_MODE_HEIGHT, KRITA_MODE_HEIGHT),
        (PS_MODE_LINEAR_HEIGHT, KRITA_MODE_LINEAR_HEIGHT),
    ]
    tex = _full_texture()
    for ps_id, krita_id in pairs:
        a = ps_mask(tex, 20, -30, ps_id, 60)
        b = ps_mask(tex, 20, -30, krita_id, 60)
        assert np.array_equal(a, b), (ps_id, krita_id)


def test_unknown_mode_raises_with_number():
    """未知模式号必须抛 NotImplementedError 且消息里带号（不许静默回退）。"""
    # 0x2A=42（任务 11 静态注册表的 linearHeight）不在运行期号里，必须抛错；
    # 注意 0x29=41 恰好等于运行期 linearHeight，不能拿来当反例。
    for bad in (99, 42, 30, 10, -1):
        with pytest.raises(NotImplementedError, match=str(bad)):
            ps_mask(np.array([0]), 0, 0, bad, 100)
    with pytest.raises(NotImplementedError):
        ps_mask(np.array([0]), 0, 0, 7.5, 100)


def test_out_of_range_brightness_contrast_rejected_not_clamped():
    """超出 PS 界面范围 → ValueError（范围外无观测，拒绝而不是猜）。"""
    tex = np.array([0, 128, 255])
    assert ps_mask(tex, 150, 100, PS_MODE_MULTIPLY, 100).shape == (3,)
    assert ps_mask(tex, -150, -100, PS_MODE_MULTIPLY, 100).shape == (3,)
    for kwargs in ({"brightness": 151}, {"brightness": -151},
                   {"contrast": 101}, {"contrast": -101}):
        args = {"brightness": 0, "contrast": 0, **kwargs}
        with pytest.raises(ValueError):
            ps_mask(tex, args["brightness"], args["contrast"], PS_MODE_MULTIPLY, 100)
    with pytest.raises(ValueError):
        ps_mask(tex, 50.5, 0, PS_MODE_MULTIPLY, 100)  # PS 存 int32，小数无观测
    with pytest.raises(ValueError):
        ps_mask(tex, 0, 0, PS_MODE_MULTIPLY, float("nan"))


# ===========================================================================
# 四、像素层复现（真值图；缺基准库时 skip，skip 不等于通过）
# ===========================================================================
_TRUTH_CASES = [
    ("ps__拟合A__正片叠底__d100_b0_c0.png", "正片叠底", 100, 0, 0),
    ("ps__拟合A__正片叠底__d100_b50_c50.png", "正片叠底", 100, 50, 50),
    ("ps__拟合A__减去__d60_b0_c0.png", "减去", 60, 0, 0),
    ("ps__拟合A__高度__d5_b0_c0.png", "高度", 5, 0, 0),
    ("ps__拟合A__线性高度__d10_b0_c0.png", "线性高度", 10, 0, 0),
]


def _load_truth(file_name: str):
    """读取纹理（按 (0,0) 相位平铺）与真值图 Alpha；文件缺失返回 None。"""
    from PIL import Image

    truth_path = BASELINE_DIR / file_name
    if not TEXTURE_PATH.is_file() or not truth_path.is_file():
        return None
    with Image.open(TEXTURE_PATH) as im:
        tex = np.asarray(im.convert("L"), dtype=np.uint8)
    yy = np.arange(100) % tex.shape[0]
    xx = np.arange(100) % tex.shape[1]
    tiled = tex[yy][:, xx].astype(np.int32)
    with Image.open(truth_path) as im:
        truth = np.asarray(im.convert("RGBA"), dtype=np.uint8)[:, :, 3].astype(np.int32)
    return tiled, truth


@pytest.mark.parametrize("file_name,mode_cn,depth,brightness,contrast", _TRUTH_CASES)
def test_reference_reproduces_truth_images(file_name, mode_cn, depth, brightness, contrast):
    """真值图逐像素复现：差异比例 <= 2%、最大差值 <= 2（吸收 PNG 取整）。

    依据：任务 12 的参数化公式已达 0.01%；本参考实现按同一结构实现，
    本用例的 5 组实测均为 0.00%（见 docs/研究报告/ps-纹理公式-参考实现.md 的核验表）。
    恒等参考图（正片叠底 d100 b0 c0）额外要求 0 差异。
    """
    loaded = _load_truth(file_name)
    if loaded is None:
        pytest.skip(f"缺基准库文件 {file_name} 或 {TEXTURE_PATH}（skip 不等于通过）")
    tiled, truth = loaded
    out = ps_mask(tiled, brightness, contrast, MODE_CN_TO_PS[mode_cn], depth)
    pred = np.rint(out.astype(np.float64) * 255.0).astype(np.int32)
    delta = np.abs(pred - truth)
    diff_pixels = int(np.count_nonzero(delta > 2))
    ratio = diff_pixels / delta.size
    assert ratio <= 0.02, f"{file_name}: 差异比例 {ratio:.4%} 超过 2%（最大差值 {int(delta.max())}）"
    assert int(delta.max()) <= 2, f"{file_name}: 最大差值 {int(delta.max())} 超过 2"
    if (mode_cn, depth, brightness, contrast) == ("正片叠底", 100, 0, 0):
        assert diff_pixels == 0 and int(delta.max()) == 0, "恒等参考图应逐像素完全相等"
