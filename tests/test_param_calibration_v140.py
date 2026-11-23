# -*- coding: utf-8 -*-
"""任务 107（v1.4.0）：参数修正的断言用例。

三条主线（对应 docs/验收报告/107-报告.md §2 的 A/B/C）：
1. 散布换算：v1.5.0 任务 134 由 jitter/400 改为 **jitter/200**（129 实验实测：
   a/D 最大相对差 0.21%、三次重复极差 0.94%；÷400 为 5/5 超 10%、最大 50.01%）。
   本文件把 0/100/145/300/500/1000% 六个点钉死，防止后续被改回或加 clamp。
2. 间距基准补偿：采样笔尖 spacing 乘「原始短边/长边」（任务 106 §4 证据），
   方形笔尖与计算笔刷必须逐字节/逐值不变。
3. 硬度：**未改**（fade=Hrdn/100 保留），把 0/50/100 三个具体数值钉死，
   并记录「h0 结构性不可达」的判据（报告 §2-C）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from brush_converter.convert import (  # noqa: E402
    _computed_brush_def,
    _sampled_brush_def,
    _square_tip,
)
from brush_converter.kpp.preset_xml import (  # noqa: E402
    auto_brush_definition,
    build_preset_xml,
)
from brush_converter.mapping import BrushPreset, map_presets  # noqa: E402

pytestmark = [pytest.mark.verify, pytest.mark.verify_params]

ABR_SAMPLED = REPO_ROOT / "tests" / "测试用.abr"
ABR_COMPUTED = REPO_ROOT / "tests" / "定量测试专用.abr"


def _spacing_of(brush_def: str) -> float:
    """从 <Brush ... spacing="x"> 取出 spacing 属性。"""
    m = re.search(r'spacing="([^"]+)"', brush_def)
    assert m, brush_def
    return float(m.group(1))


def _scatter_value_of(xml: str) -> float:
    m = re.search(r'name="ScatterValue" type="internal">([^<]*)<', xml)
    assert m, "预设 XML 里没有 ScatterValue"
    return float(m.group(1))


# ---------------------------------------------------------------------------
# 1. 散布换算（v1.5.0 任务 134：÷400 → ÷200，证据 129 A/B 级实测）
# ---------------------------------------------------------------------------


def test_scatter_value_uses_linear_1_over_200():
    """PS jitter(0..1000%) → Krita ScatterValue = jitter/200，具体数值逐个钉死。

    证据（132 落地清单 134-1 / 129-实验-散布分布）：
      - 124 A 级量纲：PS 散布偏移半宽 = j/200·直径，均匀分布；Krita ScatterValue=s
        的偏移 = U(−1,1)·直径·s ⇒ 换算就是 ÷200（÷400 是「取错一半」）；
      - 129 B 级实测：÷200 a/D 最大相对差 0.21%、Krita 三次重复极差 0.94%；
        ÷400 5/5 超 10%、最大 50.01%；107 的 39.7%/51.1% 是系数取错一半 +
        band_height 极值统计伪差，已作废。
      - 1000% → 5.0 恰为 Krita ScatterValue 上限，**不加 clamp**（值相同但多一层逻辑）。
    """
    cases = {
        0.0: 0.0,
        100.0: 0.5,
        145.0: 0.725,
        300.0: 1.5,
        500.0: 2.5,
        1000.0: 5.0,
    }
    for jitter, expected in cases.items():
        xml = build_preset_xml("散布", "", None, scatter=True, scatter_amount=jitter)
        assert _scatter_value_of(xml) == pytest.approx(expected, abs=1e-9), jitter


def test_scatter_zero_is_not_default_one():
    """无散布/散布量为 0 时必须是 0（预设默认 1 的坑，v1.2.0 已修）。"""
    xml = build_preset_xml("无散布", "", None, scatter=False)
    # scatter=False 时不写 ScatterValue，走默认参数 0
    assert 'name="ScatterValue" type="internal">0<' in xml
    xml0 = build_preset_xml("零散布", "", None, scatter=True, scatter_amount=0.0)
    assert _scatter_value_of(xml0) == 0.0


# ---------------------------------------------------------------------------
# 2. 间距基准补偿（B：已改，非方形生效、方形与计算笔刷不变）
# ---------------------------------------------------------------------------


def test_sampled_spacing_multiplies_short_over_long_side():
    """非方形笔尖 183×143：spacing 0.04 → 0.04×143/183 = 0.0312568…"""
    tip = np.full((143, 183), 255, dtype=np.uint8)
    bp = BrushPreset(name="非方形", spacing=0.04, tip_gray=tip,
                     scale=112.0 / 183.0, spacing_basis=143.0 / 183.0)
    # XML 用 %g 输出（6 位有效数字），容差按 1e-6 判
    assert _spacing_of(_sampled_brush_def("t", b"png", bp)) == pytest.approx(
        0.04 * 143.0 / 183.0, abs=1e-6)
    # 补白本身没变：内嵌画布仍是方形
    assert _square_tip(tip).shape == (183, 183)


def test_sampled_spacing_square_tip_unchanged():
    """方形笔尖比值 1 → spacing 与改动前一致（0.04 原样写出）。"""
    tip = np.full((64, 64), 255, dtype=np.uint8)
    bp = BrushPreset(name="方形", spacing=0.04, tip_gray=tip,
                     scale=1.0, spacing_basis=1.0)
    assert _spacing_of(_sampled_brush_def("t", b"png", bp)) == 0.04


def test_computed_brush_spacing_not_compensated():
    """计算笔刷没有补白 → 间距不得被补偿（200px/25% 原样）。"""
    bp = BrushPreset(name="计算", spacing=0.25, diameter=200.0, hardness=50.0,
                     roundness=100.0, is_computed=True, spacing_basis=1.0)
    assert _spacing_of(_computed_brush_def(bp)) == 0.25


@pytest.mark.skipif(not ABR_SAMPLED.exists(), reason="缺少测试用.abr")
def test_spacing_basis_from_real_abr():
    """真实 ABR：非方形 183×143 → 143/183；方形 86×84 → 84/86；近方形 1895×1901 不在这里断言。"""
    presets = {p.name: p for p in map_presets(_load(ABR_SAMPLED))}
    assert presets["KYLE 额外厚实炭笔"].spacing_basis == pytest.approx(143.0 / 183.0, abs=1e-12)
    assert presets["Kyle 的终极粉彩派对"].spacing_basis == pytest.approx(84.0 / 86.0, abs=1e-12)
    # 橡皮擦 83×58：比值 0.699，补偿后 spacing 由 0.01 降到 0.006987…
    eraser = presets["Kyle 的橡皮擦 - 自然边缘"]
    assert eraser.spacing == pytest.approx(0.01, abs=1e-12)
    assert eraser.spacing * eraser.spacing_basis == pytest.approx(0.006987951807228915, abs=1e-12)


@pytest.mark.skipif(not ABR_COMPUTED.exists(), reason="缺少定量测试专用.abr")
def test_computed_brush_basis_is_one():
    comp = map_presets(_load(ABR_COMPUTED))
    assert [p.spacing_basis for p in comp] == [1.0]


# ---------------------------------------------------------------------------
# 3. 硬度（C：实测后不改，fade=Hrdn/100 数值钉死）
# ---------------------------------------------------------------------------


def test_hardness_fade_mapping_unchanged():
    """fade 仍是 Hrdn/100：0→0、50→0.5、100→1（107 实测 h50 可用 fade 0.60 单点修，
    但 h0 结构性不可达，整参数按口径不改）。"""
    assert 'hfade="0"' in auto_brush_definition(200.0, 0.5, 0.0, 100.0, 0.0)
    assert 'hfade="0.5"' in auto_brush_definition(200.0, 0.5, 0.0, 100.0, 50.0)
    assert 'hfade="1"' in auto_brush_definition(200.0, 0.5, 0.0, 100.0, 100.0)
    # 硬度缺省（None）走 0.5，与旧行为一致
    assert 'hfade="0.5"' in auto_brush_definition(200.0, 0.5, 0.0, 100.0, None)


def _load(path: Path):
    from brush_converter.abr import AbrFile

    return AbrFile.parse(str(path))
