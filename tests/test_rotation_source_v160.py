# -*- coding: utf-8 -*-
"""v1.6.0 候选 · 任务 149A：旋转传感器「单加法随机源」回归（F-6 修法落地）。

背景（证据链）
--------------
* **问题**：Krita 的 `RotationSensor` 里同时写 `fuzzy`（每 dab 随机）与 `fuzzystroke`
  （每笔划一次随机）两个**加法型**源。Krita 把同一选项下所有加法源**求和**
  （`research/KisCurveOption.cpp:112-114`：`if (s->isAdditive()) components.additive +=
  valueFromCurve`），再乘 `RotationValue`（同文件 :70）参与模 2 环绕 ⇒ 随机跨度
  = **2×360°×R**；PS 的 `angleDynamics` 只有逐 dab 随机，没有「整笔同随机」这一层，
  跨度应为 **360°×R**。
* **实测（148-报告 §3.3，A 级）**：现映射 j=25 的角半跨度 136.20° vs PS 92.51°
  （+38.20%，超 10% 随机档判据）；只留 fuzzy 的修法候选相对差
  −5.84%/−2.77%/−1.95%（j=25/50/100），三点全 PASS。149A 用改后 `src/` 产物实机复测，
  数字与复算命令见 `docs/验收报告/149A-报告.md`。
* **旧基准错在哪**：`858df4d`（v0.x「calibrate … rotation mapping」）起，fuzzy 与
  fuzzystroke 被当作「随机度曲线两条都要写满 1」的等价参数；但它们是两个**独立 draw
  相加**，不是二选一。要表达「随机幅度由 RotationValue 缩放」，只需一个加法源。

本用例锁死三件事
----------------
1. 三个旋转传感器构造器（drawingangle / speed·rotation / 纯随机）产出的
   `RotationSensor` 里**有且只有 1 个加法随机源**（只有 fuzzy，没有 fuzzystroke）；
2. 数值映射不变：j=25/50/100 → `RotationValue` 0.5/1/1，`rotation_value()` 同值；
3. bVTy→sensor 语义不变：6→drawingangle、7→speed、8→rotation、纯随机分支无控制源。
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
)

pytestmark = [pytest.mark.verify, pytest.mark.verify_params]

#: 加法型随机源（Krita `isAdditive()==true`）：同一 RotationSensor 里只允许出现 1 个。
ADDITIVE_RANDOM_SENSORS = ("fuzzy", "fuzzystroke")
#: 三个旋转传感器构造器的四种触发组合（label, rotation_sensor 参数值）。
ROTATION_VARIANTS = [
    ("bVTy=6 drawingangle", "drawingangle"),
    ("bVTy=7 speed", "speed"),
    ("bVTy=8 rotation", "rotation"),
    ("bVTy=0 纯随机（无控制源）", None),
]


def _sensor_xml(xml: str, name: str = "RotationSensor") -> str:
    m = re.search(r'name="%s" type="string"><!\[CDATA\[(.*?)\]\]></param>' % name, xml, re.S)
    assert m, "XML 里没有 %s" % name
    return m.group(1)


def _internal(xml: str, name: str) -> str:
    m = re.search(r'name="%s" type="internal">([^<]*)<' % re.escape(name), xml)
    assert m, "XML 里没有 %s" % name
    return m.group(1)


def _sensor_ids(sensor_xml: str) -> list[str]:
    return re.findall(r'<ChildSensor id="([a-z]+)"', sensor_xml)


# ---------------------------------------------------------------------------
# 1) 单加法随机源（F-6 修法的直接断言）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,rotation_sensor", ROTATION_VARIANTS)
def test_rotation_sensor_has_exactly_one_additive_random_source(label, rotation_sensor) -> None:
    """三个构造器都只能有 1 个加法随机源（fuzzy）；fuzzystroke 必须缺席。"""
    xml = build_preset_xml("旋转-单源", "", None,
                           rotation_sensor=rotation_sensor, rotation_jitter=25.0)
    sensor = _sensor_xml(xml)
    ids = _sensor_ids(sensor)
    random_ids = [i for i in ids if i in ADDITIVE_RANDOM_SENSORS]
    assert random_ids == ["fuzzy"], (
        "%s：RotationSensor 的加法随机源应为恰好 1 个 fuzzy，实际 %r（ids=%r）"
        % (label, random_ids, ids))
    assert "fuzzystroke" not in xml, "%s：XML 里仍出现 fuzzystroke" % label
    # 结构等价：整份预设里只有 1 处 fuzzy ChildSensor，且其曲线仍是满量程 0,0;1,1
    assert xml.count('<ChildSensor id="fuzzy">') == 1
    assert '<ChildSensor id="fuzzy"> <curve>0,0;1,1;</curve> </ChildSensor>' in xml


@pytest.mark.parametrize("label,rotation_sensor", ROTATION_VARIANTS)
def test_rotation_sensor_keeps_its_control_source(label, rotation_sensor) -> None:
    """去掉第二个随机源不能改动控制源语义（bVTy→sensor 映射不变）。"""
    xml = build_preset_xml("旋转-控制源", "", None,
                           rotation_sensor=rotation_sensor, rotation_jitter=25.0)
    ids = _sensor_ids(_sensor_xml(xml))
    if rotation_sensor is not None:
        assert ids[0] == rotation_sensor, (label, ids)
    # 纯随机分支不得出现任何控制源（不随笔迹方向/速度/旋转）
    for control in ("drawingangle", "speed", "rotation"):
        assert (control in ids) == (rotation_sensor == control), (label, control, ids)
    assert _internal(xml, "PressureRotation") == "true"
    assert _internal(xml, "RotationUseCurve") == "true"
    assert _internal(xml, "RotationUseSameCurve") == "false"
    assert _internal(xml, "RotationcurveMode") == "1"


# ---------------------------------------------------------------------------
# 2) 数值映射不变：RotationValue = min(1, j/50)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("jitter,expected", [(0.0, "0"), (10.0, "0.2"), (25.0, "0.5"),
                                             (50.0, "1"), (100.0, "1")])
def test_rotation_value_unchanged_after_single_source_fix(jitter, expected) -> None:
    """j=25/50/100 → 0.5/1/1（j≥50 饱和整圈）；三处分支同值，两把尺子仍分域。"""
    for rotation_sensor in ("drawingangle", "speed", "rotation"):
        xml = build_preset_xml("旋转-数值", "", None,
                               rotation_sensor=rotation_sensor, rotation_jitter=jitter)
        assert _internal(xml, "RotationValue") == expected, (rotation_sensor, jitter)
    if jitter > 0:
        xml = build_preset_xml("旋转-数值-随机", "", None,
                               rotation_sensor=None, rotation_jitter=jitter)
        assert _internal(xml, "RotationValue") == expected
    assert rotation_value(jitter) == pytest.approx(float(expected), abs=1e-12)


def test_rotation_value_switch_does_not_touch_scatter_ruler() -> None:
    """修法只动随机源个数；散布档（÷200）与旋转档（÷50 饱和）仍是两把尺子。"""
    from brush_converter.kpp.preset_xml import scatter_value
    assert scatter_value(100.0) == 0.5
    assert rotation_value(100.0) == 1.0
    assert rotation_value(25.0) == 0.5


# ---------------------------------------------------------------------------
# 3) j=0 的关闭语义没被改动
# ---------------------------------------------------------------------------


def test_rotation_off_without_jitter_or_sensor() -> None:
    """jitter=0 且无控制源：旋转选项保持关闭（RotationSensor 留默认占位）。"""
    xml = build_preset_xml("旋转-关", "", None)
    assert _internal(xml, "PressureRotation") == "false"
    assert "fuzzystroke" not in xml
