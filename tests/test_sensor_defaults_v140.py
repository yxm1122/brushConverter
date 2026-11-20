# -*- coding: utf-8 -*-
"""任务 112 人工复验发现的回归：无动态的笔刷不得在 Krita 侧默认接压感。

根因（2026-11-20）：preset_xml 的基础模板曾把 17 组传感器都初始化成
`pressure + UseCurve=true`，转换器只覆盖它映射到的那几组；PS 侧「传递」关闭、
ABR 里没有 opVr/prVr 的笔刷，转到 Krita 后 OpacitySensor/FlowSensor 等仍是
pressure，界面显示「压感已勾选、最小值为 0」。

修复：基础模板默认「空传感器 + 关曲线」，只在 ABR 明确映射到对应动态时开启。
本文件把「无动态全关 / 有压感才开 / 散布按需开」钉死。
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from brush_converter.kpp.preset_xml import build_preset_xml  # noqa: E402

pytestmark = [pytest.mark.verify, pytest.mark.verify_params]


def _params(xml: str) -> dict[str, str]:
    root = ET.fromstring(xml)
    return {p.get("name"): (p.text or "") for p in root.iter("param")}


def _assert_sensor_off(p: dict[str, str], name: str) -> None:
    assert 'id="pressure"' not in p[name + "Sensor"], name
    assert p[name + "UseCurve"].strip() == "false", name
    assert p["Pressure" + name].strip() == "false", name


def _assert_sensor_on(p: dict[str, str], name: str) -> None:
    assert 'id="pressure"' in p[name + "Sensor"], name
    assert p[name + "UseCurve"].strip() == "true", name
    assert p["Pressure" + name].strip() == "true", name


def test_no_dynamics_all_sensors_off() -> None:
    p = _params(build_preset_xml("no-dyn", "", None))
    for name in ("Opacity", "Flow", "Size", "Ratio", "Rotation", "Scatter", "Spacing"):
        _assert_sensor_off(p, name)
    assert 'id="pressure"' not in p["Texture/Strength/Sensor"]
    assert p["Texture/Strength/UseCurve"].strip() == "false"


def test_pressure_opacity_flow_size_ratio_on() -> None:
    p = _params(build_preset_xml(
        "pressure", "", None,
        size_curve="0,0;1,1;",
        opacity_curve="0,0;1,1;",
        flow_curve="0,0;1,1;",
        ratio_curve="0,0;1,1;",
    ))
    for name in ("Size", "Opacity", "Flow", "Ratio"):
        _assert_sensor_on(p, name)
    # 未映射的传感器仍必须关闭
    for name in ("Scatter", "Spacing", "Rotation"):
        _assert_sensor_off(p, name)


def test_scatter_sensor_only_when_pressure() -> None:
    on = _params(build_preset_xml(
        "scatter-on", "", None, scatter=True, scatter_pressure=True, scatter_amount=100.0))
    assert 'id="pressure"' in on["ScatterSensor"]
    assert on["ScatterUseCurve"].strip() == "true"

    off = _params(build_preset_xml(
        "scatter-off", "", None, scatter=True, scatter_pressure=False, scatter_amount=100.0))
    assert 'id="pressure"' not in off["ScatterSensor"]
    assert off["ScatterUseCurve"].strip() == "false"