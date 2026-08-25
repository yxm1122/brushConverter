"""生成 Krita 笔刷预设（paintop preset）的 XML。

目标格式：Krita 5.x 内嵌资源（embedded_resources="2"）——
笔尖以 base64 内嵌在 <resources> 中，brush_definition 用 png_brush 引用。

默认参数集严格取自用户手动转换的 Krita 5.x 参考预设（199 项参数，
145 internal + 54 string），保证参数名与类型和 Krita 实际输出一致。
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
from xml.sax.saxutils import escape, quoteattr

# 每个传感器都有一组参数：Sensor / UseCurve / UseSameCurve / Value / commonCurve / curveMode
_SENSORS = [
    "Darken", "Flow", "LightnessStrength", "Mirror", "Mix", "Opacity",
    "Rate", "Ratio", "Rotation", "Scatter", "Sharpness", "Size",
    "Softness", "Spacing", "h", "s", "v",
]

_SENSOR_XML = '<!DOCTYPE params> <params id="pressure"/> '
_CURVE_ID = "0,0;1,1;"


def _sensor_param_name(s: str, suffix: str) -> str:
    # h/s/v 用小写首字母：hcommonCurve / scurveMode 等
    return f"{s}{suffix}"


def _build_default_params() -> dict[str, tuple[str, str]]:
    """构造默认参数集：name -> (type, inner_value)。"""
    p: dict[str, tuple[str, str]] = {}

    for s in _SENSORS:
        p[_sensor_param_name(s, "Sensor")] = ("string", _SENSOR_XML)
        p[_sensor_param_name(s, "UseCurve")] = ("internal", "true")
        p[_sensor_param_name(s, "UseSameCurve")] = ("internal", "true")
        p[_sensor_param_name(s, "Value")] = ("internal", "1")
        p[_sensor_param_name(s, "commonCurve")] = ("string", _CURVE_ID)
        p[_sensor_param_name(s, "curveMode")] = ("internal", "0")

    # 压力开关（默认关，映射时按需开启）
    for s in _SENSORS:
        p[f"Pressure{s}"] = ("internal", "false")
    p["PressureTexture/Strength/"] = ("internal", "false")

    fixed = {
        "ColorSource/Type": ("string", "plain"),
        "CompositeOp": ("string", "normal"),
        "EraserMode": ("internal", "false"),
        "HorizontalMirrorEnabled": ("internal", "false"),
        "VerticalMirrorEnabled": ("internal", "false"),
        "KisPrecisionOption/AutoPrecisionEnabled": ("internal", "false"),
        "KisPrecisionOption/precisionLevel": ("internal", "5"),
        "PaintOpAction": ("internal", "2"),
        "PaintOpSettings/ignoreSpacing": ("internal", "false"),
        "PaintOpSettings/isAirbrushing": ("internal", "false"),
        "PaintOpSettings/rate": ("internal", "20"),
        "PaintOpSettings/updateSpacingBetweenDabs": ("internal", "false"),
        "Spacing/Isotropic": ("internal", "false"),
        "Scattering/AxisX": ("internal", "false"),
        "Scattering/AxisY": ("internal", "true"),
        "Sharpness/alignoutline": ("internal", "false"),
        "Sharpness/softness": ("internal", "0"),
        "lodSizeThreshold": ("internal", "100"),
        "lodUserAllowed": ("internal", "true"),
        "paintop": ("string", "paintbrush"),
        "brush_definition": ("string", ""),
        # 散布量默认 0（通用 Value 默认是 1，散布局必须例外，否则无散布笔刷会变成 100%）
        "ScatterValue": ("internal", "0"),
    }
    p.update(fixed)
    return p


DEFAULT_PARAMS = _build_default_params()


def _pressure_sensor(curve: str | None = None) -> str:
    """压感传感器 XML。"""
    if curve is None:
        return _SENSOR_XML
    return f'<!DOCTYPE params> <params id="pressure"> <curve>{curve}</curve> </params> '


def _drawing_angle_sensor(jitter: float = 0.0) -> str:
    """「随笔迹方向旋转」传感器（sensorslist + drawingangle + fuzzy）。"""
    j = max(0.0, min(1.0, jitter / 100.0))
    return (
        '<!DOCTYPE params> <params id="sensorslist"> '
        '<ChildSensor id="drawingangle" lockedAngleMode="0" fanCornersEnabled="0" '
        'fanCornersStep="30" angleOffset="0"/> '
        f'<ChildSensor id="fuzzy"> <curve>0,0;1,{j:g};</curve> </ChildSensor> '
        f'<ChildSensor id="fuzzystroke"> <curve>0,0;1,{j:g};</curve> </ChildSensor> '
        '</params> '
    )


def sampled_brush_definition(filename: str, md5sum: str, spacing: float,
                             angle: float, scale: float) -> str:
    """图像笔尖（png_brush）的 <Brush> 定义。

    angle 为度（Photoshop #Ang，可为负），Krita XML 存弧度、UI 范围 [0,360)。
    负角度需先 normalize 到 [0,360)（否则被 Krita 角度控件 clamp 到 0）。
    """
    angle_rad = math.radians(angle % 360.0)
    return (
        f'<Brush type="png_brush" useAutoSpacing="0" autoSpacingCoeff="1" '
        f'filename={quoteattr(filename)} AutoAdjustMidPoint="1" angle="{angle_rad:g}" '
        f'ContrastAdjustment="0" AdjustmentVersion="2" scale="{scale:g}" '
        f'AdjustmentMidPoint="127" md5sum={quoteattr(md5sum)} brushApplication="0" '
        f'BrushVersion="2" ColorAsMask="1" BrightnessAdjustment="0" spacing="{spacing:g}"/>'
    )


def auto_brush_definition(diameter: float, spacing: float, angle: float,
                          roundness: float, hardness: float | None) -> str:
    """计算笔尖（auto_brush）的 <Brush> 定义。

    - angle：度 → 弧度（Krita XML 存弧度，normalizeAngle 为 [0,2π) 弧度版）；
      负角度先 normalize 到 [0,360)，否则 Krita 角度控件（[0,360]）会 clamp 到 0。
    - fade（hfade/vfade）= hardness/100：Krita 里 fade 是「实心区占半径比例」，
      硬度 100% → 1.0（硬边），硬度 0% → 0.0（全软），与 Photoshop 硬度同向。
    """
    ratio = roundness / 100.0
    angle_rad = math.radians(angle % 360.0)
    fade = 0.5 if hardness is None else max(0.0, min(1.0, hardness / 100.0))
    return (
        f'<Brush type="auto_brush" randomness="0" density="1" BrushVersion="2" '
        f'spacing="{spacing:g}" angle="{angle_rad:g}"> '
        f'<MaskGenerator ratio="{ratio:g}" type="circle" vfade="{fade:g}" '
        f'id="default" spikes="2" antialiasEdges="1" hfade="{fade:g}" '
        f'diameter="{diameter:g}"/> </Brush> '
    )


def _resources_xml(tip_png: bytes, name: str, filename: str) -> str:
    """生成 <resources> 元素：把笔尖 PNG 以 base64 内嵌（embedded_resources="2"）。"""
    md5sum = hashlib.md5(tip_png).hexdigest()
    b64 = base64.b64encode(tip_png).decode("ascii")
    return (
        f'<resources> <resource name={quoteattr(name)} filename={quoteattr(filename)} '
        f'type="brushes" md5sum={quoteattr(md5sum)}><![CDATA[{b64}]]></resource> </resources>'
    )


def build_preset_xml(
    name: str,
    brush_definition: str,
    tip_png: bytes | None,
    tip_name: str | None = None,
    size_curve: str | None = None,
    opacity_curve: str | None = None,
    flow_curve: str | None = None,
    ratio_curve: str | None = None,
    rotation_sensor: str | None = None,   # "pressure" | "drawingangle" | None
    rotation_jitter: float = 0.0,
    scatter: bool = False,
    scatter_pressure: bool = False,       # 散布量是否随压感（ScatterSensor=pressure）
    scatter_both_axes: bool = False,
    scatter_amount: float | None = None,  # PS 散布量 0..1000%（scatterDynamics.jitter）
    extra_params: dict[str, str] | None = None,
) -> str:
    """组装完整的 <Preset> XML。曲线参数为 "x,y;x,y;"（不含尾随分号）。"""
    params = dict(DEFAULT_PARAMS)
    params["brush_definition"] = ("string", brush_definition)

    def set_internal(key: str, value: str) -> None:
        params[key] = ("internal", value)

    def set_string(key: str, value: str) -> None:
        params[key] = ("string", value)

    if size_curve is not None:
        set_string("SizecommonCurve", size_curve)
        set_internal("PressureSize", "true")
        set_internal("SizeUseCurve", "true")
    if opacity_curve is not None:
        set_string("OpacitycommonCurve", opacity_curve)
        set_internal("PressureOpacity", "true")
    if flow_curve is not None:
        set_string("FlowcommonCurve", flow_curve)
        set_internal("FlowUseCurve", "true")
        set_internal("PressureFlow", "true")
    if ratio_curve is not None:
        set_string("RatiocommonCurve", ratio_curve)
        set_internal("PressureRatio", "true")
    if rotation_sensor == "drawingangle":
        set_string("RotationSensor", _drawing_angle_sensor(rotation_jitter))
        set_internal("PressureRotation", "true")
        set_internal("RotationUseSameCurve", "false")
        set_internal("RotationcurveMode", "1")
    elif rotation_sensor == "pressure":
        set_string("RotationSensor", _pressure_sensor())
        set_internal("PressureRotation", "true")
    if scatter:
        # PressureScatter 在 Krita 里是「散布选项是否启用」开关（isChecked）
        set_internal("PressureScatter", "true")
        # 散布量随压感才用 pressure 传感器；否则关掉曲线（恒定散布）
        if scatter_pressure:
            set_string("ScatterSensor", _pressure_sensor())
            set_internal("ScatterUseCurve", "true")
        else:
            set_internal("ScatterUseCurve", "false")
        # 散布量换算：PS jitter(0..1000%) → Krita ScatterValue(0..5.0=500%)
        # 实测锚点：PS 145% ≈ Krita 35%（即 ScatterValue 0.35），系数 ≈ 1/4
        if scatter_amount is not None:
            set_internal("ScatterValue", f"{scatter_amount / 400.0:g}")
        # PS bothAxes → Krita 两个轴都开；否则只在 Y 轴（垂直笔迹方向）
        set_internal("Scattering/AxisX", "true" if scatter_both_axes else "false")
        set_internal("Scattering/AxisY", "true")

    if extra_params:
        for k, v in extra_params.items():
            t = params.get(k, ("internal", ""))[0]
            params[k] = (t, v)

    parts = []
    if tip_png is not None:
        res_name = tip_name or _safe_name(name)
        res_xml = _resources_xml(tip_png, res_name, res_name + ".png")
        parts.append(
            '<Preset paintopid="paintbrush" name=' + quoteattr(name)
            + ' embedded_resources="2"> ' + res_xml
        )
    else:
        parts.append('<Preset paintopid="paintbrush" name=' + quoteattr(name) + '>')

    for key, (typ, value) in params.items():
        if typ == "string":
            parts.append(f' <param name={quoteattr(key)} type="string"><![CDATA[{value}]]></param>')
        else:
            parts.append(f' <param name={quoteattr(key)} type="internal">{value}</param>')
    parts.append(" </Preset>")
    return "".join(parts)


def _safe_name(name: str) -> str:
    """把笔刷名清洗为安全的资源名（仅保留 word 字符）。"""
    cleaned = re.sub(r"[^\w]+", "_", name, flags=re.UNICODE).strip("_")
    return cleaned or "brush"
