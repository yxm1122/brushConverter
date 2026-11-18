# -*- coding: utf-8 -*-
"""任务 14 单测：手感曲线提取 + 偏差判定 + 形变检出。

覆盖：
1. 合成「宽度 3→10 线性增加」的假图 → 逐点断言曲线数值（不是只断言形状）；
2. Alpha 阈值：Alpha=5 的抗锯齿像素不计入宽度；
3. 不透明度曲线单位是 0..1（128/255 的断言）；
4. 判定边界：4.9% 合格 / 5.1% 不合格（宽度与不透明度两侧都测）；
5. 横向拉伸 10% → width_dev > 5% 且判不合格（任务书验收项）；
6. 真实基线自比（基线存在时必须 0；不存在则如实 skip）。
"""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import feel_compare  # noqa: E402
import feel_curve  # noqa: E402

pytestmark = pytest.mark.verify


def _make_width_gradient(path, canvas_width=64, canvas_height=24):
    """造一张「每列宽度 3→10 线性增加」的假图，返回每列期望宽度。"""
    widths = [3 + round(7 * x / (canvas_width - 1)) for x in range(canvas_width)]
    image = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    pixels = image.load()
    for x, w in enumerate(widths):
        top = (canvas_height - w) // 2
        for y in range(top, top + w):
            pixels[x, y] = (0, 0, 0, 255)
    image.save(path, "PNG")
    return widths


def _make_uniform_alpha(path, alpha_level, canvas_width=32, canvas_height=16):
    """造一张整列 Alpha 恒定的图（用于不透明度曲线断言）。"""
    image = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, alpha_level))
    pixels = image.load()
    for y in range(canvas_height):
        for x in range(canvas_width):
            pixels[x, y] = (0, 0, 0, alpha_level)
    image.save(path, "PNG")
    return alpha_level


def _curve(widths, opacities):
    """手工构造曲线 dict（与 feel_curve.extract_curves 输出同构）。"""
    return {
        "positions": [(i + 0.5) / len(widths) for i in range(len(widths))],
        "width": [float(v) for v in widths],
        "opacity": [float(v) for v in opacities],
        "meta": {"width_unit": "px", "opacity_unit": "0..1"},
    }


def test_synthetic_width_curve_values(tmp_path):
    """宽度 3→10 的假图：提取出的宽度曲线必须逐点等于期望值（具体数字断言）。"""
    png = tmp_path / "gradient.png"
    expected = _make_width_gradient(png)
    curve = feel_curve.extract_curves(png, points=64)
    assert len(curve["width"]) == 64
    assert curve["width"][0] == pytest.approx(expected[0])      # 3
    assert curve["width"][31] == pytest.approx(expected[31])    # 6
    assert curve["width"][-1] == pytest.approx(expected[-1])    # 10
    assert curve["width"] == pytest.approx([float(v) for v in expected])
    assert curve["meta"]["width_unit"] == "px"
    assert curve["meta"]["opacity_unit"] == "0..1"


def test_alpha_threshold_ignores_antialiasing(tmp_path):
    """Alpha=5（低于阈值 10）的像素不算笔迹：该列宽度必须为 0。"""
    png = tmp_path / "faint.png"
    image = Image.new("RGBA", (16, 8), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(8):
        pixels[0, y] = (0, 0, 0, 5)    # 低于阈值
        pixels[1, y] = (0, 0, 0, 255)  # 高于阈值
    image.save(png, "PNG")
    curve = feel_curve.extract_curves(png, points=16)
    assert curve["width"][0] == pytest.approx(0.0)
    assert curve["width"][1] == pytest.approx(8.0)


def test_opacity_curve_unit_is_0_to_1(tmp_path):
    """不透明度曲线单位是 0..1：Alpha=128 的图应得到 128/255≈0.502。"""
    png = tmp_path / "gray.png"
    _make_uniform_alpha(png, 128)
    curve = feel_curve.extract_curves(png, points=32)
    assert max(curve["opacity"]) == pytest.approx(128 / 255.0, abs=1e-9)
    assert curve["opacity"][0] == pytest.approx(128 / 255.0, abs=1e-9)


def test_resample_point_count(tmp_path):
    png = tmp_path / "gradient.png"
    _make_width_gradient(png)
    curve = feel_curve.extract_curves(png, points=32)
    assert len(curve["positions"]) == 32
    assert len(curve["width"]) == 32
    assert len(curve["opacity"]) == 32


def test_boundary_pass_49_percent():
    """刚好合格：宽度偏差 4.9%、不透明度偏差 0.049（0..1 尺度）。"""
    base = _curve([100.0] * 64, [0.5] * 64)
    test = _curve([104.9] * 64, [0.549] * 64)
    result = feel_compare.compare_curves(test, base)
    assert result["width_dev"] == pytest.approx(0.049, abs=1e-12)
    assert result["opacity_dev"] == pytest.approx(0.049, abs=1e-12)
    assert result["width_pass"] is True
    assert result["opacity_pass"] is True
    assert result["verdict"] == "合格"


def test_boundary_fail_51_percent():
    """刚好不合格：宽度偏差 5.1%、不透明度偏差 0.051（0..1 尺度）。"""
    base = _curve([100.0] * 64, [0.5] * 64)
    test = _curve([105.1] * 64, [0.551] * 64)
    result = feel_compare.compare_curves(test, base)
    assert result["width_dev"] == pytest.approx(0.051, abs=1e-12)
    assert result["opacity_dev"] == pytest.approx(0.051, abs=1e-12)
    assert result["width_pass"] is False
    assert result["opacity_pass"] is False
    assert result["verdict"] == "不合格"


def test_threshold_units_are_documented_consistently():
    """阈值单位口径：宽度无量纲、不透明度 0..1；0..255 换算 = ×255。"""
    assert feel_compare.WIDTH_DEV_THRESHOLD == 0.05
    assert feel_compare.OPACITY_DEV_THRESHOLD_01 == 0.05
    assert feel_compare.OPACITY_DEV_THRESHOLD_255 == pytest.approx(12.75)
    # 任务书那句「按 0..255 尺度则 <5/255≈2%」对应 0..1 尺度的 5/255，此处只做换算登记，
    # 实际判据以 0..1 尺度的 0.05（满量程 5%）为准，见 docs/手感比对说明.md 第 4 节。
    assert round(5 / 255.0, 4) == pytest.approx(0.0196, abs=1e-4)


def test_horizontal_stretch_is_detected(tmp_path):
    """把图横向拉伸 10% 后比对：width_dev > 5% 且判不合格（任务书验收项）。"""
    src = tmp_path / "base.png"
    _make_width_gradient(src)
    stretched = tmp_path / "stretched.png"
    feel_compare.stretch_image_horizontally(str(src), str(stretched), factor=1.10)

    curve_base = feel_curve.extract_curves(src, points=64)
    curve_stretched = feel_curve.extract_curves(stretched, points=64)
    result = feel_compare.compare_curves(curve_stretched, curve_base)
    assert result["width_dev"] > feel_compare.WIDTH_DEV_THRESHOLD
    assert result["verdict"] == "不合格"


def test_baseline_self_compare_is_zero():
    """真实基线自比：每条曲线与自己比必须 width_dev=0、opacity_dev=0、合格。"""
    manifest_path = REPO_ROOT / "tests" / "baselines" / "feel" / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("手感基线尚未生成（缺少 tests/baselines/feel/manifest.json），如实 skip")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    checked = 0
    for item in manifest.get("items", []):
        curve_file = REPO_ROOT / "tests" / "baselines" / "feel" / item["curve_file"]
        if not curve_file.is_file():
            pytest.skip("基线曲线缺失：%s（如实 skip）" % curve_file.name)
        curve = feel_curve.load_curve(str(curve_file))
        result = feel_compare.compare_curves(curve, curve)
        assert result["width_dev"] == pytest.approx(0.0)
        assert result["opacity_dev"] == pytest.approx(0.0)
        assert result["verdict"] == "合格"
        checked += 1
    assert checked > 0
