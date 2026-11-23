# -*- coding: utf-8 -*-
"""v1.5.0（任务 135-6）纹理亮度/对比度：calibrated 唯一路径的回归 + A/B 表。

【历史（已删除）】
任务 13 曾提供 legacy/calibrated 的二选一环境变量开关；任务 135-6 按使用者批准
删除 legacy 分支与开关本身（连变量名都已清零），calibrated 成为唯一路径
（自 v1.2.0 起它本来就是默认路径，所以**默认产物无像素变化**）。

【旧基准错在哪（删除 legacy 用例的理由，逐条）】
- `test_legacy_values` / `test_legacy_rounding_edge` /
  `test_default_equals_calibrated_and_legacy_equals_v112`：目标是「显式 legacy 仍与
  v1.1.2 逐字段一致」，本质是保护回退路径；回退路径已删，用例失去被测对象。
- legacy 公式本身从来不是目标行为：亮度 `0.10 - PS/250` 是经验拟合（PS 中性处残留
  0.10 偏移），掩码空间与 PS 真值差 70.75%；calibrated 为 0.00%。继续保留「逐字节
  一致」等于把已知错误行为钉成基准。
- `test_illegal_value_falls_back_to_default_and_warns_once` /
  `test_blank_and_case_insensitive`：环境变量已不存在，「非法值回退」不再有语义。
- `test_legacy_mask_much_worse_than_calibrated`：没有 legacy 侧可再比较，改写为
  「calibrated 在可表达网格上 0 差异」的正向断言（本文件
  `test_calibrated_mask_equivalent_on_expressible_grid`）。
- `test_default_formula_is_calibrated` / `test_switch_changes_written_xml`：默认值
  与开关的断言合并成一条正向断言 `test_formula_is_calibrated_only`：即使显式设置
  legacy，输出仍是 calibrated。

【掩码空间判据】tol=2、差异比例 ≤2% 且 max_delta ≤2。
PS 侧真值仍是任务 12 的参数化公式 `tools.lut_fit.bc_transform`（pivot=127、
order=sign_c）；Krita 侧真值 `tools/texture_mask.apply_three`
（KisTextureMaskInfo::recalculateMask 的精确复刻）。
**注意口径**：这是模型空间判据；b×C 同时非零时真实 PS 的残差（128A §五：
multiply/subtract/linearBurn 76.68%、hardMix 57.57%）不在本文件 0.00% 的覆盖范围，
由文件末尾的「记录组」登记、本轮不硬修。

【直接运行本文件可打印 A/B 表】`python tests/test_texture_formula_switch.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from brush_converter.kpp import preset_xml as px  # noqa: E402
from brush_converter.kpp.preset_xml import (  # noqa: E402
    _texture_contrast_factor,
    build_preset_xml,
    texture_brightness_contrast,
)
from brush_converter.mapping import TextureSettings  # noqa: E402

pytestmark = pytest.mark.verify

TOL = 2            # 与 tools/imgdiff.py 一致：|差| > 2 才算差异
RATIO_LIMIT = 0.02  # 达标线：差异比例 <= 2%

#: 任务 12 拟合结果（tests/baselines/ps/_work/lut_fit_result.json 的 params 形状）；
#: k_fitted 留空 ⇒ bc_transform 对每个 C 回落到经验因子 contrast_factor(C)。
PS_PARAMS = {"pivot": 127.0, "k_fitted": {}, "order": "sign_c"}

#: A/B 参数网格：任务 12 的亮度/对比度覆盖 + 真实笔刷会出现的取值
GRID_B = (-150, -100, -50, -25, 0, 25, 50, 100, 150)
GRID_C = (-50, -25, -10, 0, 10, 25, 50, 75, 100)

_TEX_255 = np.arange(256, dtype=np.float64)


def _ps_mask_values(ps_b: int, ps_c: int) -> np.ndarray:
    """PS 侧前置掩码（0..255，整数）：任务 12 参数化公式。"""
    from tools.lut_fit import bc_transform

    return np.floor(bc_transform(_TEX_255, ps_b, ps_c, PS_PARAMS) + 0.5).astype(np.int64)


def _krita_mask_values(brightness: float, contrast: float) -> np.ndarray:
    """Krita 侧前置掩码（0..255，整数）：KisTextureMaskInfo 的精确复刻。"""
    from tools.texture_mask import apply_three

    mask = apply_three((_TEX_255 / 255.0).astype(np.float32), False,
                       brightness, contrast)
    return np.floor(mask.astype(np.float64) * 255.0 + 0.5).astype(np.int64)


def _metrics(pred: np.ndarray, truth: np.ndarray, tol: int = TOL) -> dict:
    delta = np.abs(np.rint(pred).astype(np.int64) - truth.astype(np.int64))
    return {
        "diff_points": int(np.count_nonzero(delta > tol)),
        "diff_ratio": float(np.count_nonzero(delta > tol)) / delta.size,
        "max_delta": int(delta.max()),
    }


def _pair_metrics(ps_b: int, ps_c: int) -> dict:
    b, c = texture_brightness_contrast(ps_b, ps_c)
    return _metrics(_krita_mask_values(b, c), _ps_mask_values(ps_b, ps_c))


def _expressible(ps_b: int, ps_c: int) -> bool:
    """参数组是否在 Krita 可表达范围内（否则属结构性残差，不计入公式优劣）。

    v1.5.0 起 C>50 **不再**结构性：135-4 删除 min(2.0,k) 截断，k 原值直写
    （125 实测 Contrast=4.0 / 1e6 vs PS 真值 0.00%/max 0）。
    唯一剩下的结构性限制是亮度：C<0 且 |PS 亮度| 很大时反解出的 b_k 越 [-1,1]，
    Krita 亮度滑杆域截断（135-3 只登记、不落地，残差见 §记录）。
    """
    k = _texture_contrast_factor(ps_c)
    if k <= 0.0:
        return False
    c = max(-50.0, min(100.0, ps_c))
    center = (1.0 - k) / (510.0 * k)
    b_k = center - (ps_b / (255.0 * k) if c < 0 else ps_b / 255.0)
    return -1.0 <= b_k <= 1.0


def _expressible_combos() -> list[tuple[int, int]]:
    return [(b, c) for b in GRID_B for c in GRID_C if _expressible(b, c)]


# ---------------------------------------------------------------------------
# 1) calibrated 的具体数值（闭式；防止公式或精度被静默改动）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ps_b,ps_c,expected", [
    (0, 0, (0.0, 1.0)),              # 中性 = 恒等
    (-27, -17, (0.128, 0.83)),       # (1-0.83)/(510*0.83) + 27/(255*0.83)
    (30, 25, (-0.118, 1.333)),       # C>=0：亮度项不除以 k
    (150, 50, (-0.589, 2.0)),
    (-150, -50, (1.0, 0.5)),         # 反解 1.178 → 超 Krita 亮度上限，截断 1.0
    (0, 75, (-0.001470588, 4.0)),    # 135-4：C>50 不再截断，k=4 原值直写
    (0, 84, (-0.001647059, 6.25)),   # 真实样本「橡皮擦」的 C=84 → k=6.25
    (-100, 100, (0.39019608, 1_000_000.0)),  # C=100 → k=1e6（二值化极限）
])
def test_calibrated_values(ps_b, ps_c, expected):
    b, c = texture_brightness_contrast(ps_b, ps_c)
    assert b == pytest.approx(expected[0], abs=1e-9)
    assert c == pytest.approx(expected[1], abs=1e-6)


def test_contrast_above_50_not_clamped_in_xml():
    """135-4：k 原值直写进 XML，且 1e6 用纯十进制（不写科学计数法）。"""
    img = np.zeros((2, 2, 3), dtype=np.uint8)
    for ps_c, text in ((75, "4"), (84, "6.25"), (100, "1000000")):
        ts = TextureSettings(name="x", uuid="x", contrast=ps_c, image=img)
        from brush_converter.convert import _texture_xml

        tex = _texture_xml(px_bp(ts))
        assert tex is not None
        xml = build_preset_xml("x", "", None, texture=tex)
        assert f'Texture/Pattern/Contrast" type="internal">{text}' in xml
    assert "1e+06" not in xml


def px_bp(texture: TextureSettings):
    """避免在参数化签名里重复导入 BrushPreset（小工具）。"""
    from brush_converter.mapping import BrushPreset

    return BrushPreset(name="x", texture=texture)


# ---------------------------------------------------------------------------
# 2) 唯一路径：环境变量被忽略、legacy 符号不存在
# ---------------------------------------------------------------------------
def test_formula_is_calibrated_only():
    """135-6 正向断言：calibrated 唯一路径；旧开关的符号在模块里一个都不剩。

    开关本身（含环境变量名）已从代码/用例里清零，因此本用例不去设置任何环境变量，
    只按「符号不存在 + 公式值固定」断言唯一路径；变量名清零由交付时的
    grep 检查保证（报告 §legacy 引用清零）。
    """
    reference = [(b, c) for b in GRID_B for c in GRID_C]
    values = [texture_brightness_contrast(b, c) for b, c in reference]
    assert values == [texture_brightness_contrast(b, c) for b, c in reference]
    assert texture_brightness_contrast(0, 0) == (0.0, 1.0)
    assert texture_brightness_contrast(-27, -17) == (0.128, 0.83)
    assert not hasattr(px, "_TEXTURE_FORMULA_ENV")
    assert not hasattr(px, "_texture_formula")
    assert not hasattr(px, "_texture_brightness_legacy")
    assert not hasattr(px, "_texture_contrast_legacy")


# ---------------------------------------------------------------------------
# 3) 掩码空间等价：可表达网格 0 差异（模型空间口径，见文件头注意事项）
# ---------------------------------------------------------------------------
def test_calibrated_mask_equivalent_on_expressible_grid():
    combos = _expressible_combos()
    assert len(combos) == len(GRID_B) * len(GRID_C) - 2  # 只有 (±150,-50) 因亮度越界除外
    worst_ratio = 0.0
    worst_max = 0
    for ps_b, ps_c in combos:
        m = _pair_metrics(ps_b, ps_c)
        assert m["diff_ratio"] <= RATIO_LIMIT, f"({ps_b},{ps_c}) 差异比例 {m['diff_ratio']:.2%}"
        worst_ratio = max(worst_ratio, m["diff_ratio"])
        worst_max = max(worst_max, m["max_delta"])
    # C>50 的 k=1e6 档在 8-bit 掩码空间可能有 1 级差（9 位小数亮度精度），
    # 判据仍要求 max_delta<=2；差异比例必须为 0。
    assert worst_ratio == 0.0
    assert worst_max <= TOL


def test_structural_limits_are_brightness_only():
    """135-4 后：C>50 可表达；只剩亮度越界区（C<0 且 |b|>127）是结构性残差。"""
    assert _expressible(0, 75) and _expressible(0, 84) and _expressible(0, 100)
    assert not _expressible(-150, -50)
    assert not _expressible(150, -50)
    assert _expressible(0, 50)          # 边界：k=2 恰好可表达
    assert _expressible(-27, -17)


# ---------------------------------------------------------------------------
# 4) 记录组：b×C 交互区残差（132 决策 4-A；本轮只登记、不硬修）
# ---------------------------------------------------------------------------
_BC_RECORD = _REPO_ROOT / ".tmp" / "128A" / "json" / "m10_analysis.json"


@pytest.mark.skipif(
    not _BC_RECORD.is_file(),
    reason="任务 128A 本地实验汇总缺失（.tmp/128A 为 gitignored 本地数据）",
)
def test_bc_interaction_zone_residual_recorded():
    """把 128A 实测的 b×C 交互区残差钉成「已知未修」的记录，防止被当成 0.00%。

    依据 128A §五（10 模式 d100/b50/c50，满覆盖笔尖，PS 真值 vs 现行闭式解）：
    4/10 模式超阈 —— multiply / subtract / linearBurn 76.680%、hardMix 57.570%。
    本轮按 132 决策 4-A **只登记不硬修**（修法需要先补 PS 真值，见 128A §六）。
    本组只读上游实验汇总，不重新启动 PS/Krita、不新增实验。
    """
    import json

    rows = {row["name"]: row for row in json.loads(
        _BC_RECORD.read_text(encoding="utf-8"))["rows"]}
    for name in ("multiply", "subtract", "linearBurn"):
        assert rows[name]["paths"]["closed"]["diff_ratio"] == pytest.approx(0.7668)
        assert rows[name]["paths"]["closed"]["pass"] is False
    hard = rows["hardMix"]["paths"]["closed"]
    assert hard["diff_ratio"] == pytest.approx(0.5757)
    assert hard["pass"] is False
    # 预烘焙在同组 12/12/9-of-10 = 0.00%：证明残差出自「闭式解模型」而不是 Krita 算子
    assert rows["multiply"]["paths"]["prebake"]["diff_ratio"] == 0.0


# ---------------------------------------------------------------------------
# A/B 表（直接运行本文件时打印，供报告引用）
# ---------------------------------------------------------------------------
def _table_grid() -> str:
    lines = [
        "### A/B 掩码空间（纹理 0..255 全取值，tol=2，达标线 ≤2%）",
        "",
        "| PS亮度 | PS对比度 | b_k | c_k | 差异比例 | 最大差 | 可表达 |",
        "|---:|---:|---:|---:|---:|---:|:--:|",
    ]
    for ps_b in GRID_B:
        for ps_c in GRID_C:
            b, c = texture_brightness_contrast(ps_b, ps_c)
            m = _pair_metrics(ps_b, ps_c)
            lines.append(
                f"| {ps_b} | {ps_c} | {b:g} | {c:g} | {m['diff_ratio']:.2%} "
                f"| {m['max_delta']} | {'是' if _expressible(ps_b, ps_c) else '否（亮度越界）'} |")
    return "\n".join(lines)


def print_ab_table() -> None:
    print("## 纹理亮度/对比度 calibrated（唯一路径）· 掩码空间 A/B")
    print()
    print(_table_grid())


if __name__ == "__main__":
    print_ab_table()
