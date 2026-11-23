# -*- coding: utf-8 -*-
"""v1.5.0 任务 134：随机动态批的落地断言（散布 / 旋转抖动 / bVTy 控制源 / 两把尺子）。

证据链（132 落地清单 §4.2、124/126/129）：
- 134-1 散布档 ScatterValue = jitter/200（129 实测 a/D 最大相对差 0.21%、三次重复极差 0.94%）；
- 134-2 旋转档 RotationValue = min(1, j/50)（124 B 级；F-6 未量测，不得写成已验证）；
- 134-3 bVTy 9 值枚举：0=关 / 1=渐隐 / 2=压感 / 3=倾斜 / 4=转轮 / 5=初始方向 /
  6=笔划方向 / 7=速度 / 8=旋转（124 A 级）；5 无 Krita 承载 → None + 告警；
- 134-4 两把尺子分域：散布 0..1000 → /200；旋转 0..100 → /50 饱和；禁止共用系数。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from brush_converter.kpp.preset_xml import (  # noqa: E402
    build_preset_xml,
    rotation_value,
    scatter_value,
)
from brush_converter.mapping import _bvty_sensor  # noqa: E402

pytestmark = [pytest.mark.verify, pytest.mark.verify_params]

#: 散布档：PS jitter 定义域 0..1000（%），端点 1000% 恰为 Krita ScatterValue 上限 5.0。
SCATTER_CASES = [(0.0, "0"), (100.0, "0.5"), (300.0, "1.5"),
                 (500.0, "2.5"), (1000.0, "5")]
#: 旋转档：PS jitter 定义域 0..100（%），j>=50 饱和整圈（R=1.0）。
ROTATION_CASES = [(0.0, "0"), (10.0, "0.2"), (25.0, "0.5"),
                  (50.0, "1"), (100.0, "1")]


def _internal(xml: str, name: str) -> str:
    m = re.search(r'name="%s" type="internal">([^<]*)<' % re.escape(name), xml)
    assert m, "XML 里没有 %s" % name
    return m.group(1)


def _sensor_xml(xml: str, name: str) -> str:
    m = re.search(r'name="%s" type="string"><!\[CDATA\[(.*?)\]\]></param>'
                  % re.escape(name), xml, re.S)
    assert m, "XML 里没有 %s" % name
    return m.group(1)


# ---------------------------------------------------------------------------
# 134-1 散布档：j 0..1000 → ScatterValue=j/200，端点不加 clamp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("jitter,expected", SCATTER_CASES)
def test_scatter_value_is_jitter_over_200(jitter: float, expected: str) -> None:
    xml = build_preset_xml("散布", "", None, scatter=True, scatter_amount=jitter)
    assert _internal(xml, "ScatterValue") == expected
    assert scatter_value(jitter) == pytest.approx(float(expected), abs=1e-12)


def test_scatter_endpoint_equals_krita_max_without_clamp() -> None:
    """1000% 恰好 = Krita 上限 5.0；换算是线性、不截断（不是 clamp 出来的 5）。"""
    assert scatter_value(1000.0) == 5.0
    assert scatter_value(1200.0) == 6.0  # 无 clamp：域外由 ABR 层的 clamp[0,1000] 保证不出现


# ---------------------------------------------------------------------------
# 134-2 旋转档：j 0..100 → RotationValue=min(1, j/50)，两处分支同值
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("jitter,expected", ROTATION_CASES)
def test_rotation_value_is_min_one_j_over_50(jitter: float, expected: str) -> None:
    drawing = build_preset_xml("旋转-方向", "", None,
                               rotation_sensor="drawingangle", rotation_jitter=jitter)
    fuzzy = build_preset_xml("旋转-随机", "", None,
                             rotation_sensor=None, rotation_jitter=jitter)
    assert _internal(drawing, "RotationValue") == expected
    if jitter > 0:
        # j=0 时纯随机分支不进入（没有随机效果可开），RotationValue 保持默认 1
        # 但 PressureRotation=false，效果关闭——不是错误值。
        assert _internal(fuzzy, "RotationValue") == expected
    else:
        assert _internal(fuzzy, "PressureRotation") == "false"
    assert rotation_value(jitter) == pytest.approx(float(expected), abs=1e-12)


def test_rotation_saturates_at_half_domain() -> None:
    """j>=50 饱和整圈：50/100 都写 1.0（旧 j/100 在 j<50 偏小约 2 倍）。"""
    assert rotation_value(50.0) == 1.0
    assert rotation_value(100.0) == 1.0
    assert rotation_value(40.0) == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# 134-4 两把尺子：同名字段、两套定义域、两个系数，禁止合并
# ---------------------------------------------------------------------------


def test_jitter_two_rulers_not_shared() -> None:
    assert scatter_value(100.0) == 0.5
    assert rotation_value(100.0) == 1.0
    # 同一个 j=25：散布档 0.125、旋转档 0.5 —— 若有人共用一个 /100 或 /400，
    # 这两条必有一条变红。
    assert scatter_value(25.0) == pytest.approx(0.125)
    assert rotation_value(25.0) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 134-3 bVTy 9 值枚举（124 A 级表）
# ---------------------------------------------------------------------------

BVTY_CASES = [(None, None), (0, None), (1, None), (2, "pressure"),
              (3, None), (4, None), (5, None), (6, "drawingangle"),
              (7, "speed"), (8, "rotation")]


@pytest.mark.parametrize("bvty,expected", BVTY_CASES)
def test_bvty_9_value_table(bvty: float | None, expected: str | None) -> None:
    assert _bvty_sensor(bvty) == expected


def test_bvty_5_and_6_not_swapped() -> None:
    """核心点：5=初始方向（None + 告警） vs 6=笔划方向（drawingangle），不许写反。"""
    assert _bvty_sensor(5) is None
    assert _bvty_sensor(6) == "drawingangle"
    # 旧实现的错位：6/7 都被当成 drawingangle（7 实际是速度）
    assert _bvty_sensor(7) == "speed"
    assert _bvty_sensor(8) == "rotation"


def test_bvty_6_xml_has_drawingangle_sensor() -> None:
    xml = build_preset_xml("dir", "", None, rotation_sensor="drawingangle",
                           rotation_jitter=50.0)
    sensor = _sensor_xml(xml, "RotationSensor")
    assert 'id="sensorslist"' in sensor
    assert '<ChildSensor id="drawingangle"' in sensor
    assert 'id="speed"' not in sensor
    assert 'id="rotation"' not in sensor


def test_bvty_7_speed_sensor_xml() -> None:
    xml = build_preset_xml("speed", "", None, rotation_sensor="speed",
                           rotation_jitter=50.0)
    sensor = _sensor_xml(xml, "RotationSensor")
    assert '<ChildSensor id="speed"/>' in sensor
    assert 'id="drawingangle"' not in sensor
    assert _internal(xml, "PressureRotation") == "true"
    assert _internal(xml, "RotationValue") == "1"


def test_bvty_8_rotation_sensor_xml() -> None:
    xml = build_preset_xml("rot", "", None, rotation_sensor="rotation",
                           rotation_jitter=25.0)
    sensor = _sensor_xml(xml, "RotationSensor")
    assert '<ChildSensor id="rotation"/>' in sensor
    assert 'id="drawingangle"' not in sensor
    assert _internal(xml, "RotationValue") == "0.5"


# ---------------------------------------------------------------------------
# 真实样本：bVTy=5（初始方向）必须出现在告警里，不能被静默吞掉
# ---------------------------------------------------------------------------

ABR = REPO_ROOT / "tests" / "测试用.abr"


@pytest.mark.skipif(not ABR.exists(), reason="缺少 tests/测试用.abr")
def test_bvty5_initial_direction_warns() -> None:
    from brush_converter.abr import AbrFile
    from brush_converter.mapping import map_presets

    presets = map_presets(AbrFile.parse(str(ABR)))
    target = next(p for p in presets if "炭笔 25" in p.name)
    assert any("bVTy=5" in w and "初始方向" in w for w in target.warnings)
    # 该笔刷 angleDynamics.jitter=0，旧实现只在 jitter>0 时告警 ⇒ 这条是本次修复点
    assert target.rotation_jitter == 0.0
    assert target.rotation_sensor is None
