# -*- coding: utf-8 -*-
"""任务 133（v1.5.0）：计算笔刷 → 按 PS 硬度剖面生成采样笔尖的断言用例。

证据链（不是本文件自证，全部来自任务 130 实验）：
- 130 表 B（10–90% 过渡带）：h0 −0.90px/−1.14%、h50 +0.04px/+0.12%、h100 +0.50px；
  改前 auto_brush（130 表 A）是 −15.60px/−19.81%、+7.33px/+22.63%；
- 硬前置：Krita 尺寸字段必须 = PS 实际足印（h0/50/100 → 290/250/204 = 1.45/1.25/1.02×Dmtr）；
- 极性（130 §三）：Krita 的 L 模式 png_brush 是「深=墨」，所以位图必须写 255−α，
  外围 α=0 ⇒ 边框整圈 255（正方形 + 边框 255 字面成立，且不动 _square_tip）。

本文件钉死三件事：
1. 生成位图与 130 记录的内嵌 PNG **逐字节一致**（sha256 三方对照），防止后续任何人
   改插值/取整/极性把已验证的像素结论弄失效；
2. 尺寸/间距语义：尺寸字段 = 位图边长 = PS 足印，间距乘回「标称基准/位图边长」；
3. 结构性质：正方形、外圈整圈 255、补平方（_square_tip）为 no-op、md5 与内嵌字节一致。
"""

from __future__ import annotations

import base64
import hashlib
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from brush_converter.convert import (  # noqa: E402
    _computed_brush_def,
    _png_bytes,
    _render_preset,
    _square_tip,
    convert,
    convert_presets,
)
from brush_converter.hardness_tip import (  # noqa: E402
    build_tip,
    footprint_ratio,
    spacing_basis,
    tip_edge,
    verify_generated_tip,
)
from brush_converter.kpp.kpp_writer import read_preset_xml  # noqa: E402
from brush_converter.mapping import BrushPreset  # noqa: E402
from tools.kpp_params import extract_params, read_kpp_xml  # noqa: E402

pytestmark = [pytest.mark.verify, pytest.mark.verify_params]

ABR_COMPUTED = REPO_ROOT / "tests" / "定量测试专用.abr"

#: 130 实验记录的内嵌笔尖 PNG sha256（.tmp/130/results.json build.tips[*].png_sha256；
#: 本仓库不保存 .tmp，故把三个哈希固化在这里作为「逐字节一致」的锚点）。
SHA130 = {
    0.0: "f4f98b8168a382ea0282d9ba42628fc4e975be2afa660c1c09bc1d5e7913da8b",
    50.0: "042b1c6fb00f2a554fcb3edc43ef9eacd935a438ab1f96a0fa05e0612e6a2e0b",
    100.0: "cf5d2fb7f633bc2bcc48870604dee536fc89d239d8e662b4056dd6b0151ca9a4",
}
SHA130_EDGE = {0.0: 290, 50.0: 250, 100.0: 204}
SHA130_BYTES = {0.0: 11500, 50.0: 9529, 100.0: 2796}


# ---------------------------------------------------------------------------
# 1. 位图与 130 记录逐字节一致（极性 + 取整 + 插值全在这一条里）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hardness", [0.0, 50.0, 100.0])
def test_tip_png_matches_130_experiment_byte_for_byte(hardness):
    """Dmtr=200 的 h=0/50/100 位图必须与 130 的内嵌 PNG 字节完全相同。

    旧实现（auto_brush+fade）对同一配置的 10–90% 过渡带是 −15.60px/−19.81%（h0）、
    +7.33px/+22.63%（h50）；这条用例守住「改后仍是 130 验证过的那个位图」。
    """
    tip = build_tip(hardness, 200.0, 100.0)
    assert tip.edge == SHA130_EDGE[hardness]
    png = _png_bytes(tip.stored)
    assert len(png) == SHA130_BYTES[hardness]
    assert hashlib.sha256(png).hexdigest() == SHA130[hardness]


def test_tip_is_square_border255_and_square_tip_is_noop():
    """正方形 + 外圈整圈 255；_square_tip 必须逐字节不变（铁律 17：补平方不动）。"""
    tip = build_tip(0.0, 200.0, 100.0)
    info = verify_generated_tip(tip)
    assert info["shape"] == [290, 290] and info["square"] is True
    assert info["border_uniform"] is True and info["border_value"] == 255
    # 外圈整圈都是 255（不是只有四个角）
    stored = tip.stored
    border = np.concatenate([stored[0, :], stored[-1, :], stored[:, 0], stored[:, -1]])
    assert np.all(border == 255)
    # 补平方没被触发：正方形输入 → 返回值与输入逐字节相同
    padded = _square_tip(stored)
    assert padded.shape == stored.shape
    assert np.array_equal(padded, stored)


def test_tip_mask_is_inverse_of_stored():
    """mask（255=墨，预览/实验口径）= 255 − stored；极性与 130 §三一致。"""
    tip = build_tip(50.0, 200.0, 100.0)
    assert np.array_equal(tip.mask, 255 - tip.stored)
    assert int(tip.mask.max()) == 255
    assert int(tip.stored[0, 0]) == 255


def test_polarity_is_not_direct_alpha():
    """不能写成 α 直写（130 首轮极性写反：差异 52.58%、max_delta 255，5 组作废）。"""
    tip = build_tip(50.0, 200.0, 100.0)
    core = int(tip.stored[tip.edge // 2, tip.edge // 2])
    assert core <= 4, "墨芯应接近 0（深=墨）；写反会得到 255"


# ---------------------------------------------------------------------------
# 2. 尺寸 / 间距语义
# ---------------------------------------------------------------------------


def test_edge_formula_matches_130_footprints():
    """L = 2·ceil(r_vis)+2：h0/50/100@Dmtr=200 → 290/250/204（PS 足印 1.45/1.25/1.02×）。"""
    assert [tip_edge(h, 200.0) for h in (0.0, 50.0, 100.0)] == [290, 250, 204]
    # 大直径也要按同一公式走（海怪样本里有 Dmtr=463 的计算笔刷）
    assert tip_edge(0.0, 463.0) == 670
    # 小直径是量化最差的地方：Dmtr=19 的 h0 足印约 27px，公式给 30px（已登记遗留）
    assert tip_edge(0.0, 19.0) == 30


def test_footprint_ratio_interpolates_between_measured_points():
    """r_vis/R = 1.44 / 1.24 / 1.01，中间硬度线性内插；None 走 50（旧 fade 默认 0.5）。"""
    assert footprint_ratio(0.0) == pytest.approx(1.44)
    assert footprint_ratio(50.0) == pytest.approx(1.24)
    assert footprint_ratio(100.0) == pytest.approx(1.01)
    assert footprint_ratio(25.0) == pytest.approx(1.34)
    assert footprint_ratio(None) == pytest.approx(1.24)
    # 单调用 clamp 到 [0,100]
    assert footprint_ratio(-10.0) == pytest.approx(1.44)
    assert footprint_ratio(200.0) == pytest.approx(1.01)


def test_spacing_basis_uses_nominal_diameter_over_bitmap_edge():
    """PS 计算笔刷 100% 间距基准 = 标称直径（131 表 3）；Krita 基准 = 位图边长。"""
    assert spacing_basis(0.0, 200.0, 100.0) == pytest.approx(200.0 / 290.0)
    assert spacing_basis(100.0, 200.0, 100.0) == pytest.approx(200.0 / 204.0)
    assert spacing_basis(0.0, 200.0, 50.0) == pytest.approx(200.0 * 0.5 / 290.0)
    # 比值必须 <1：软边足印总大于标称直径，不乘回就会把步长放大
    assert spacing_basis(0.0, 200.0) < 1.0


def _fixture_preset(**kw) -> BrushPreset:
    data = dict(name="bc133 hardness h100", spacing=0.05, diameter=200.0,
                hardness=100.0, roundness=100.0, is_computed=True)
    data.update(kw)
    return BrushPreset(**data)


def test_computed_preset_renders_png_brush_with_footprint_size():
    """合成夹具走发行版生成链：png_brush + scale=1 + 尺寸 204（H=100@Dmtr=200）。"""
    bp = _fixture_preset()
    with tempfile.TemporaryDirectory(prefix="bc133_") as td:
        result = convert_presets(None, [bp], td, make_bundle=False)
        kpp_path = Path(td) / "kpp" / result.kpp_files[0][0]
        xml = read_kpp_xml(kpp_path)
        params = extract_params(xml)
        assert params["brush_type"] == "png_brush"
        assert params["scale"] == pytest.approx(1.0)
        assert params["size"] == pytest.approx(204.0)
        # 间距 0.05 × 200/204（换算基准变了，不是手感系数）
        assert params["spacing"] == pytest.approx(0.05 * 200.0 / 204.0)
        # 内嵌 PNG 的 md5 与 brush_definition 里的 md5sum 一致，且等于生成字节的 md5
        tip = build_tip(100.0, 200.0, 100.0)
        tip_png = _png_bytes(tip.stored)
        assert hashlib.md5(tip_png).hexdigest() in xml
        resource = re.search(r"<resource [^>]*type=\"brushes\"[^>]*>", xml)
        assert resource is not None
        b64 = re.search(r"<!\[CDATA\[([A-Za-z0-9+/=]+)\]\]>", xml.split("<resource ", 1)[1])
        assert b64 is not None
        assert base64.b64decode(b64.group(1)) == tip_png


def test_computed_preset_preview_uses_generated_mask():
    """预览像素必须来自生成位图（255=墨口径），不是旧的 _circle_mask。"""
    bp = _fixture_preset()
    _fname, xml, preview = _render_preset(bp, 0)
    tip = build_tip(100.0, 200.0, 100.0)
    center = int(round(tip.mask[tip.edge // 2, tip.edge // 2]))
    # 预览是 200×200 缩放图；中心 alpha 应接近墨芯亮度
    assert int(preview[100, 100, 3]) == pytest.approx(center, abs=8)


@pytest.mark.parametrize("hardness,edge", [(0.0, 290), (50.0, 250), (100.0, 204)])
def test_no_auto_brush_in_production_rendering(hardness, edge):
    """生产渲染不再产出 auto_brush（决策：计算笔刷一次性全换采样）。"""
    bp = _fixture_preset(hardness=hardness)
    _fname, xml, _preview = _render_preset(bp, 0)
    assert 'type="png_brush"' in xml
    assert 'type="auto_brush"' not in xml
    assert "hfade" not in xml
    assert 'diameter="' not in xml


def test_auto_brush_helper_still_available_for_experiments():
    """旧 auto_brush 生成器保留给 130 复跑与 107 回归用例（不是功能开关）。"""
    assert 'hfade="0.5"' in _computed_brush_def(_fixture_preset(hardness=50.0))


@pytest.mark.skipif(not ABR_COMPUTED.exists(), reason="缺少定量测试专用.abr")
def test_real_computed_abr_converts_to_sampled_brush():
    """真实 ABR 里的计算笔刷（Dmtr=200/Hrdn=100/Rndn=100）→ png_brush，尺寸 204。"""
    with tempfile.TemporaryDirectory(prefix="bc133_abr_") as td:
        result = convert(str(ABR_COMPUTED), td, make_bundle=False)
        assert len(result.kpp_files) == 1
        params = extract_params(read_kpp_xml(Path(td) / "kpp" / result.kpp_files[0][0]))
        assert params["brush_type"] == "png_brush"
        assert params["size"] == pytest.approx(204.0)


# ---------------------------------------------------------------------------
# 3. 椭圆（圆度 <100）与缺省值
# ---------------------------------------------------------------------------


def test_roundness_squashes_short_axis_not_footprint():
    """Rndn 压缩短轴：长轴足印仍由 Hrdn 的 r_vis 决定，边长不变。"""
    round_tip = build_tip(50.0, 200.0, 100.0)
    flat_tip = build_tip(50.0, 200.0, 50.0)
    assert flat_tip.edge == round_tip.edge  # 边长（长轴）不变
    ink = flat_tip.mask > 127
    rows = np.count_nonzero(ink.any(axis=1))
    cols = np.count_nonzero(ink.any(axis=0))
    assert rows < cols  # 短轴方向被压缩（y 压缩）
    assert rows / cols == pytest.approx(0.5, abs=0.08)


def test_hardness_none_defaults_to_50():
    """硬度缺省（None）与 h=50 必须生成同一位图（旧代码 fade 默认 0.5）。"""
    a = build_tip(None, 200.0, 100.0)
    b = build_tip(50.0, 200.0, 100.0)
    assert a.edge == b.edge
    assert np.array_equal(a.stored, b.stored)


# ---------------------------------------------------------------------------
# 4. 半径缩放不变性（133 首件补测：PS 9 组 + Krita 9 组，见报告 §五）
# ---------------------------------------------------------------------------


def _alpha_at_radius(tip, radius_px: float) -> int:
    center = (tip.edge - 1) / 2.0
    return int(tip.mask[int(round(center)), int(round(center + radius_px))])


@pytest.mark.parametrize("hardness", [0.0, 50.0])
def test_tip_profile_scales_linearly_with_diameter(hardness):
    """归一化剖面不随直径变：D=100/200/400 在同 u·R 处的 α 差 ≤3/255。

    依据：133 首件补测的 PS 实测（归一化过渡带 h0 差 0.05%、h50 差 ≤4%）。
    h=100 是例外（PS 过渡带是 AA 绝对带宽 ~1.9px、不随直径缩放），见报告「遗留」，
    所以这条只钉 h=0/50。
    """
    ref = build_tip(hardness, 200.0, 100.0)
    for d in (100.0, 400.0):
        tip = build_tip(hardness, d, 100.0)
        for u in (0.2, 0.4, 0.6, 0.8):
            a_ref = _alpha_at_radius(ref, u * 100.0)   # 标称半径 100
            a = _alpha_at_radius(tip, u * (d / 2.0))
            assert abs(a - a_ref) <= 3, (hardness, d, u, a_ref, a)


def test_edge_formula_at_other_diameters():
    """半径改变时边长按同一公式放大：D=100/400 → 146 / 578（h=0）。"""
    assert [tip_edge(0.0, d) for d in (100.0, 200.0, 400.0)] == [146, 290, 578]
    # 足印（α>0 范围）应对应 2·r_vis，不是画布边长
    for d in (100.0, 400.0):
        tip = build_tip(0.0, d, 100.0)
        ink = np.nonzero(tip.mask.max(axis=0) > 0)[0]
        assert int(ink[-1] - ink[0] + 1) == pytest.approx(2 * 1.44 * d / 2.0, abs=3)
