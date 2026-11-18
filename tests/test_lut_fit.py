# -*- coding: utf-8 -*-
"""黑盒拟合工具（任务 12）的单元测试。

覆盖：
- 1D LUT：已知线性关系的合成数据 -> 拟合表与真值一致；训练集缺输入值时的线性插值。
- 亮度/对比度前置：恒等、整数平移、对比度因子（-50 -> 0.5、+50 -> 2）、
  以及数据判定出来的「顺序随对比度符号翻转」规则。
- 参数化公式：用已知线性关系的合成数据（四模式）拟合后，预测与真值逐点一致；
  深度量化与高度系权重 W=12 的实测律。
- 误差口径：与 tools/imgdiff.py 一致的 tol / 差异比例 / 最大差值。
- 采样网格与命名：覆盖 4 模式 x >=3 深度 x 亮度{0,±50,±150} x 对比度{0,±50}，命名四段合规。

本文件用合成数据回归，不依赖 tests/baselines/ps/ 下的真实图（只有相位检查会在缺纹理时 skip）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.lut_fit import (  # noqa: E402
    HEIGHT_WEIGHT,
    MODES,
    baseline_krita_params,
    bc_transform,
    contrast_factor,
    depth_strength,
    fit_bc,
    fit_lut,
    fit_parametric,
    grid_combos,
    mappability,
    metrics,
    mode_predict,
    new_file_name,
    parametric_predict,
    pixel_split,
    predict_lut,
    tiled_texture,
)

SIDE = 100


def _synthetic_texture(seed: int = 20261118) -> np.ndarray:
    """构造一张覆盖 0..255 的 100x100 合成纹理（每 64 像素一循环，取值均匀）。"""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, size=(64, 64), dtype=np.int16)
    yy = np.arange(SIDE) % 64
    xx = np.arange(SIDE) % 64
    return base[yy][:, xx]


def _record(t: np.ndarray, a: np.ndarray, mode: str, depth: int, b: int, c: int) -> dict:
    """构造与 tools/lut_fit.py 的 build_dataset 同构的一条配对记录。"""
    return {
        "file": f"ps__合成__{mode}__d{depth}_b{b}_c{c}.png",
        "group": "synthetic",
        "mode": mode,
        "depth": depth,
        "b": b,
        "c": c,
        "t": t.astype(np.int16),
        "a": a.astype(np.int16),
        "trivial": False,
        "saturated": False,
    }


def _clip255(x) -> np.ndarray:
    return np.clip(np.rint(np.asarray(x, dtype=np.float64)), 0, 255).astype(np.int16)


def _truth_formula(t: np.ndarray, mode: str, depth: int, b: int, c: int,
                   pivot: float = 127.0) -> np.ndarray:
    """独立复刻「实测律」生成合成真值（与实现同一个公式，用来做回归护栏）。"""
    k = contrast_factor(c)
    if c >= 0:
        u = pivot + (np.clip(t.astype(np.float64) + b, 0.0, 255.0) - pivot) * k
    else:
        u = pivot + (t.astype(np.float64) - pivot) * k + b
    u = np.clip(u, 0.0, 255.0)
    q, s = depth_strength(depth)
    if mode == "正片叠底":
        out = 255.0 + (u - 255.0) * s
    elif mode == "减去":
        out = 255.0 - u * s
    elif mode == "高度":
        out = HEIGHT_WEIGHT * q - u
    elif mode == "线性高度":
        md = HEIGHT_WEIGHT * q
        out = np.maximum((255.0 - u) * md / 255.0, md - u)
    else:
        raise KeyError(mode)
    return _clip255(np.clip(out, 0.0, 255.0))


# ---------------------------------------------------------------------------
# 1) 1D LUT
# ---------------------------------------------------------------------------
def test_fit_lut_recovers_linear_truth():
    """已知线性关系 y = 0.5x + 20（含截断）-> 拟合表与真值一致（逐点差 <= 1）。"""
    t = np.repeat(np.arange(256, dtype=np.int16), 16)
    a = _clip255(0.5 * t + 20.0)
    table = fit_lut(t, a)
    truth = _clip255(0.5 * np.arange(256) + 20.0)
    delta = np.abs(table - truth.astype(np.float64))
    assert table.shape == (256,)
    assert delta.max() <= 1.0
    pred = predict_lut(table, t)
    assert np.abs(pred - a.astype(np.float64)).max() <= 1.0


def test_fit_lut_interpolates_missing_inputs():
    """训练集只覆盖偶数输入值 -> 奇数点由线性插值补齐，仍能还原线性真值。"""
    t_vals = np.arange(0, 256, 2, dtype=np.int16)
    a_vals = _clip255(2.0 * t_vals)
    t = np.repeat(t_vals, 8)
    a = np.repeat(a_vals, 8)
    table = fit_lut(t, a)
    assert not np.isnan(table).any()
    for x in (1, 3, 127, 255):
        assert abs(table[x] - min(2.0 * x, 255.0)) <= 1.5


# ---------------------------------------------------------------------------
# 2) 亮度/对比度前置（含顺序规则）
# ---------------------------------------------------------------------------
def test_contrast_factor_law():
    assert contrast_factor(0) == 1.0
    assert contrast_factor(-50) == pytest.approx(0.5)
    assert contrast_factor(-25) == pytest.approx(0.75)
    assert contrast_factor(25) == pytest.approx(4.0 / 3.0, abs=1e-9)
    assert contrast_factor(50) == pytest.approx(2.0)
    assert contrast_factor(100) > 1000  # 用极大值逼近二值化


def test_bc_transform_identity_and_shift():
    t = np.arange(256, dtype=np.int16)
    params = {"pivot": 127.5, "k_fitted": {"0": 1.0}, "order": "M1"}
    assert np.array_equal(bc_transform(t, 0, 0, params), t.astype(np.float64))  # 恒等
    shifted = bc_transform(t, 50, 0, params)  # 亮度 +50 是整数平移并截断
    assert np.array_equal(shifted, np.clip(t.astype(np.float64) + 50, 0, 255))
    assert shifted[0] == 50 and shifted[255] == 255


def test_bc_transform_contrast_centered():
    """对比度以中心 p 缩放：c=-50 -> factor 0.5，c=+50 -> factor 2。"""
    t = np.arange(256, dtype=np.int16)
    params = {"pivot": 127.5, "k_fitted": {"-50": 0.5, "50": 2.0}, "order": "M1"}
    low = bc_transform(t, 0, -50, params)
    high = bc_transform(t, 0, 50, params)
    assert low[128] == pytest.approx(127.75)  # 中心附近几乎不动
    assert low[0] == pytest.approx(63.75) and low[255] == pytest.approx(191.25)
    assert high[0] == pytest.approx(0.0) and high[255] == pytest.approx(255.0)  # 末端截断
    assert high[64] == pytest.approx(0.5) and high[128] == pytest.approx(128.5)


def test_bc_sign_rule_matches_measured_quadrants():
    """实测顺序规则：c>=0 先亮度后对比度；c<0 先对比度后亮度。

    四个象限都用「该规则」生成合成真值，拟合后必须逐点吻合（差 <= 1）。
    """
    t = _synthetic_texture()
    cases = [(50, 50), (-50, -50), (50, -50), (-50, 50), (150, 50), (-150, -50)]
    records = []
    for b, c in cases:
        a = _truth_formula(t, "正片叠底", 100, b, c)
        records.append(_record(t, a, "正片叠底", 100, b, c))
    records.append(_record(t, _truth_formula(t, "正片叠底", 100, 0, 0), "正片叠底", 100, 0, 0))
    params = fit_bc(records, verbose=False)
    _, evalmask = pixel_split()
    for rec in records:
        pred = parametric_predict(rec["t"].ravel()[evalmask], rec["mode"], rec["depth"],
                                  rec["b"], rec["c"], params)
        m = metrics(pred, rec["a"].ravel()[evalmask].astype(np.int32), tol=1)
        assert m["max_delta"] <= 2, f"b={rec['b']} c={rec['c']} 最大差 {m['max_delta']}"


def test_fit_bc_recovers_synthetic_slope():
    """用合成数据（正片叠底 d100、c=+50/-50、pivot=127）拟合，斜率应约为 2.0 / 0.5。"""
    t_map = _synthetic_texture()
    records = []
    for c in (0, 50, -50):
        a = _truth_formula(t_map, "正片叠底", 100, 0, c)
        records.append(_record(t_map, a, "正片叠底", 100, 0, c))
    params = fit_bc(records, verbose=False)
    assert params["k_fitted"]["0"] == pytest.approx(1.0)
    assert params["k_fitted"]["50"] == pytest.approx(2.0, abs=0.02)
    assert params["k_fitted"]["-50"] == pytest.approx(0.5, abs=0.02)
    assert params["pivot"] == pytest.approx(127.0, abs=1.0)


# ---------------------------------------------------------------------------
# 3) 参数化公式：深度量化 / 高度权重 / 合成线性数据回归
# ---------------------------------------------------------------------------
def test_depth_quantization():
    """深度 percentUnit 的 8bit 量化：d2/4/5/6/10/12 -> q 5/10/13/15/26/31。"""
    expect = {2: 5, 4: 10, 5: 13, 6: 15, 10: 26, 12: 31, 100: 255, 60: 153, 30: 77}
    for depth, q in expect.items():
        assert depth_strength(depth)[0] == q, f"d{depth} 量化错"
        assert depth_strength(depth)[1] == pytest.approx(q / 255.0)


def test_mode_predict_known_laws():
    """模式合成项的四条实测律（含深度 0 无纹理效果）。"""
    u = np.array([0.0, 64.0, 128.0, 255.0])
    params = {"height_weight": HEIGHT_WEIGHT}
    assert np.allclose(mode_predict(u, "正片叠底", 100, params), u)
    assert np.allclose(mode_predict(u, "减去", 100, params), 255.0 - u)
    assert np.allclose(mode_predict(u, "正片叠底", 0, params), 255.0)
    assert np.allclose(mode_predict(u, "减去", 0, params), 255.0)
    # 高度 d10：A = 12*q = 312，out = clip(312 - u)
    assert np.allclose(mode_predict(u, "高度", 10, params), np.clip(312.0 - u, 0, 255))
    # 线性高度 d5：md = 156，out = max((255-u)*156/255, 156-u)
    expect = np.maximum((255.0 - u) * 156.0 / 255.0, 156.0 - u)
    assert np.allclose(mode_predict(u, "线性高度", 5, params), np.clip(expect, 0, 255))


def test_parametric_recovers_synthetic_linear_truth():
    """四模式已知律的合成真值 -> 拟合后预测与真值逐点一致（只差 8bit 取整）。"""
    t = _synthetic_texture()
    records = [
        _record(t, _truth_formula(t, "正片叠底", 100, 0, 0), "正片叠底", 100, 0, 0),
        _record(t, _truth_formula(t, "减去", 60, 0, 0), "减去", 60, 0, 0),
        _record(t, _truth_formula(t, "减去", 100, 50, -50), "减去", 100, 50, -50),
        _record(t, _truth_formula(t, "高度", 10, 0, 0), "高度", 10, 0, 0),
        _record(t, _truth_formula(t, "高度", 5, -50, 50), "高度", 5, -50, 50),
        _record(t, _truth_formula(t, "线性高度", 5, 0, 0), "线性高度", 5, 0, 0),
        _record(t, _truth_formula(t, "线性高度", 12, 0, 0), "线性高度", 12, 0, 0),
    ]
    params = fit_parametric(records, verbose=False)
    assert params["height_weight"] == pytest.approx(HEIGHT_WEIGHT, abs=0.01)
    _, evalmask = pixel_split()
    for rec in records:
        pred = parametric_predict(rec["t"].ravel()[evalmask], rec["mode"], rec["depth"],
                                  rec["b"], rec["c"], params)
        truth = rec["a"].ravel()[evalmask].astype(np.int32)
        m = metrics(pred, truth, tol=1)
        assert m["max_delta"] <= 2, f"{rec['mode']} d{rec['depth']} 最大差值 {m['max_delta']}"


# ---------------------------------------------------------------------------
# 4) 误差口径
# ---------------------------------------------------------------------------
def test_metrics_matches_imgdiff_convention():
    truth = np.array([[0, 10], [20, 255]], dtype=np.int32)
    pred = np.array([[0, 12], [20, 250]], dtype=np.int32)  # 两处差异：2 与 5
    m = metrics(pred, truth, tol=2)  # tol=2 只算严格大于 2 的点
    assert m["pixels"] == 4
    assert m["diff_pixels"] == 1
    assert m["diff_ratio"] == pytest.approx(0.25)
    assert m["max_delta"] == 5
    m2 = metrics(pred, truth, tol=0)
    assert m2["diff_pixels"] == 2


def test_pixel_split_covers_all_values_and_is_disjoint():
    train, eval_ = pixel_split()
    assert train.shape == (SIDE * SIDE,) and eval_.shape == train.shape
    assert not np.any(train & eval_)
    assert train.sum() + eval_.sum() == SIDE * SIDE
    assert abs(train.sum() - eval_.sum()) == 0


# ---------------------------------------------------------------------------
# 5) 可映射性统计
# ---------------------------------------------------------------------------
def test_mappability_detects_pointwise_and_spatial():
    # 每个输入值在 16 行里各出现一次，才能暴露「同一输入值不同输出」的空间效应
    t = np.tile(np.arange(256, dtype=np.int16), (16, 1))
    a_pointwise = _clip255(255 - t)
    rec_ok = _record(t, a_pointwise, "减去", 100, 0, 0)
    a_spatial = a_pointwise.copy()
    a_spatial[3] = np.clip(a_spatial[3].astype(np.int32) + 5, 0, 255).astype(np.int16)
    rec_bad = _record(t, a_spatial, "减去", 100, 0, 0)
    ok = mappability([rec_ok])
    assert ok["max_spread"] == 0 and ok["pointwise_exact"] == 1
    bad = mappability([rec_bad])
    assert bad["max_spread"] == 5 and bad["pointwise_exact"] == 0


# ---------------------------------------------------------------------------
# 6) 采样网格与命名（任务书的覆盖要求）
# ---------------------------------------------------------------------------
def test_sample_grid_covers_task_requirement():
    combos = grid_combos()
    assert len(combos) >= 40, "任务书要求新样本 >= 40 张"
    modes = {c[0] for c in combos}
    assert modes == set(MODES)
    for mode in MODES:
        depths = {c[1] for c in combos if c[0] == mode}
        assert len(depths) >= 3, f"{mode} 深度不足 3 档：{sorted(depths)}"
        brightness = {c[2] for c in combos if c[0] == mode}
        contrast = {c[3] for c in combos if c[0] == mode}
        assert {0, 50, -50, 150, -150} <= brightness, f"{mode} 亮度覆盖不足：{sorted(brightness)}"
        assert {0, 50, -50} <= contrast, f"{mode} 对比度覆盖不足：{sorted(contrast)}"
    assert len(set(combos)) == len(combos)


def test_new_file_name_follows_baseline_naming():
    name = new_file_name("减去", 100, 50, -50)
    parts = name.split("__")
    assert len(parts) == 4, f"命名必须是四段：{name}"
    assert parts[0] == "ps"
    assert parts[2] == "减去"
    assert parts[3] == "d100_b50_c-50.png"
    assert " " not in name and "#" not in name


# ---------------------------------------------------------------------------
# 7) 现有经验公式基线（Krita 参数映射，与 src/brush_converter/convert.py 一致）
# ---------------------------------------------------------------------------
def test_baseline_krita_params_matches_convert_py():
    assert baseline_krita_params(0, 0) == (0.1, 1.0)
    assert baseline_krita_params(50, 0) == (round(0.10 - 50 / 250.0, 2), 1.0)
    assert baseline_krita_params(0, -50)[1] == pytest.approx(0.5)
    assert baseline_krita_params(0, 50)[1] == pytest.approx(2.0)
    assert baseline_krita_params(0, 150)[1] == pytest.approx(2.0)  # 夹到 Krita 上限 2
    brightness, _ = baseline_krita_params(-150, 0)
    assert -1.0 <= brightness <= 1.0


def test_tiled_texture_phase_matches_manual_indexing():
    """纹理平铺相位固定为 (0,0)：tiled_texture 必须等于 texture[i%64, j%64]。"""
    try:
        tex = tiled_texture()
    except FileNotFoundError:
        pytest.skip("纹理文件不存在（未跑过 --probe），跳过相位检查")
    assert tex.shape == (SIDE, SIDE)
    from tools.lut_fit import load_texture
    raw = load_texture()
    for i, j in ((0, 0), (5, 7), (63, 63), (64, 64), (99, 99), (3, 70)):
        assert tex[i, j] == raw[i % 64, j % 64]
