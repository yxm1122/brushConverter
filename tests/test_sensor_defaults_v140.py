# -*- coding: utf-8 -*-
"""无动态笔刷不得在 Krita 侧默认接压感，且传感器 XML 必须合法。

根因（2026-11-20）：preset_xml 的基础模板曾把 17 组传感器都初始化成
`pressure + UseCurve=true`，转换器只覆盖它映射到的那几组；PS 侧「传递」关闭、
ABR 里没有 opVr/prVr 的笔刷，转到 Krita 后 OpacitySensor/FlowSensor 等仍是
pressure，界面显示「压感已勾选、最小值为 0」。

首次修复改为写空 XML `<params/>`，但 Krita 的 KisKritaSensorPack::read()
在 sensorId 为空时早退：后面的 UseCurve/Value/commonCurve 全部不读，
导致曲线异常、ScatterValue 停在旧值（Krita 日志触发 !sensorId.isEmpty()
断言）。当前修复：所有 *Sensor 都写合法 pressure 传感器，未映射动态用
Pressure<名称>=false + UseCurve=false 关闭；本文件同时钉死“不得出现空
sensor id”。
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
    # 关闭动态靠 Pressure*=false；传感器串必须是合法的 pressure 占位，
    # 不能是空 XML/空 id（否则 Krita read() 会早退，值不会被读取）。
    assert 'id="pressure"' in p[name + "Sensor"], name
    assert "<params/>" not in p[name + "Sensor"], name
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
    assert 'id="pressure"' in p["Texture/Strength/Sensor"]
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
    assert 'id="pressure"' in off["ScatterSensor"]
    assert "<params/>" not in off["ScatterSensor"]
    assert off["ScatterUseCurve"].strip() == "false"
    assert off["PressureScatter"].strip() == "true"


def test_every_sensor_xml_has_nonempty_id() -> None:
    # 回归：曾经写入 <!DOCTYPE params> <params/>，Krita 的 sensorId 为空，
    # 触发 KisKritaSensorPack.cpp:146 的 !sensorId.isEmpty() 并早退。
    p = _params(build_preset_xml("all-defaults", "", None))
    for name, value in p.items():
        if name.endswith("Sensor"):
            assert value.strip(), name
            assert "<params/>" not in value, name
            assert 'id="' in value, name