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
from dataclasses import dataclass
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

    # 纹理选项默认值（对照 Krita 5.x 导出样本 ref_5.0.xml / ref_勾线笔.xml）
    texture_defaults = {
        "Texture/Pattern/Brightness": ("internal", "0"),
        "Texture/Pattern/Contrast": ("internal", "1"),
        "Texture/Pattern/CutoffLeft": ("internal", "0"),
        "Texture/Pattern/CutoffPolicy": ("internal", "0"),
        "Texture/Pattern/CutoffRight": ("internal", "255"),
        "Texture/Pattern/Enabled": ("internal", "false"),
        "Texture/Pattern/Invert": ("internal", "false"),
        "Texture/Pattern/MaximumOffsetX": ("internal", "2"),
        "Texture/Pattern/MaximumOffsetY": ("internal", "2"),
        "Texture/Pattern/Name": ("string", ""),
        "Texture/Pattern/NeutralPoint": ("internal", "0.5"),
        "Texture/Pattern/OffsetX": ("internal", "0"),
        "Texture/Pattern/OffsetY": ("internal", "0"),
        "Texture/Pattern/PatternFileName": ("string", ""),
        "Texture/Pattern/PatternMD5": ("string", ""),  # 5.0 曾写原始二进制（bug），这里留空
        "Texture/Pattern/PatternMD5Sum": ("string", ""),  # hex md5，Krita 5.1+ 读取
        "Texture/Pattern/Scale": ("internal", "1"),
        "Texture/Pattern/TexturingMode": ("internal", "0"),
        "Texture/Pattern/UseSoftTexturing": ("internal", "false"),
        "Texture/Pattern/isRandomOffsetX": ("internal", "false"),
        "Texture/Pattern/isRandomOffsetY": ("internal", "false"),
        "Texture/Strength/Sensor": ("string", _SENSOR_XML),
        "Texture/Strength/UseCurve": ("internal", "true"),
        "Texture/Strength/UseSameCurve": ("internal", "true"),
        "Texture/Strength/Value": ("internal", "1"),
        "Texture/Strength/commonCurve": ("string", _CURVE_ID),
        "Texture/Strength/curveMode": ("internal", "0"),
    }
    p.update(texture_defaults)

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


def _drawing_angle_sensor() -> str:
    """「随笔迹方向旋转」传感器。Krita 界面将随机度曲线显示为 -180°..+180°，
    但 XML 曲线值使用归一化范围 0..1；因此完整随机范围必须序列化为末点 1。"""
    return (
        '<!DOCTYPE params> <params id="sensorslist"> '
        '<ChildSensor id="drawingangle" lockedAngleMode="0" fanCornersEnabled="0" '
        'fanCornersStep="30" angleOffset="0"/> '
        '<ChildSensor id="fuzzy"> <curve>0,0;1,1;</curve> </ChildSensor> '
        '<ChildSensor id="fuzzystroke"> <curve>0,0;1,1;</curve> </ChildSensor> '
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


def _resource_xml(res_type: str, name: str, filename: str, png: bytes) -> str:
    """单个内嵌资源元素（embedded_resources="2"，type=brushes/patterns）。"""
    md5sum = hashlib.md5(png).hexdigest()
    b64 = base64.b64encode(png).decode("ascii")
    return (
        f'<resource name={quoteattr(name)} filename={quoteattr(filename)} '
        f'type={quoteattr(res_type)} md5sum={quoteattr(md5sum)}><![CDATA[{b64}]]></resource> '
    )


def _resources_xml(resources: list[tuple[str, str, str, bytes]]) -> str:
    """生成 <resources> 元素：笔尖 PNG 与纹理 PNG 一起以 base64 内嵌。"""
    return "<resources> " + "".join(
        _resource_xml(res_type, name, filename, png)
        for res_type, name, filename, png in resources
    ) + "</resources>"


@dataclass
class TextureXml:
    """Krita 纹理选项所需的数据（由 convert 从 TextureSettings 组装）。"""

    pattern_filename: str   # 内嵌资源文件名（如 tex_66e2987f.png）
    png_bytes: bytes
    scale: float = 1.0      # 0.01..10，1=100%
    brightness: int = 0
    contrast: int = 1
    invert: bool = False
    texturing_mode: int = 0
    strength: float = 1.0   # 0..1（PS textureDepth/100）
    strength_curve: str | None = None  # "x,y;x,y;"
    strength_pressure: bool = False


def _apply_texture(params: dict[str, tuple[str, str]], tex: TextureXml) -> None:
    """把 TextureXml 写进参数集（Texture/… 分支）。"""
    def set_internal(key: str, value: str) -> None:
        params[key] = ("internal", value)

    def set_string(key: str, value: str) -> None:
        params[key] = ("string", value)

    md5sum = hashlib.md5(tex.png_bytes).hexdigest()
    set_internal("Texture/Pattern/Enabled", "true")
    set_string("Texture/Pattern/Name", tex.pattern_filename)
    set_string("Texture/Pattern/PatternFileName", tex.pattern_filename)
    set_string("Texture/Pattern/PatternMD5Sum", md5sum)
    set_string("Texture/Pattern/PatternMD5", "")  # 留空，避免 Krita 5.0 写二进制 bug
    set_internal("Texture/Pattern/Scale", f"{max(0.01, min(10.0, tex.scale)):g}")
    set_internal("Texture/Pattern/Brightness", str(tex.brightness))
    set_internal("Texture/Pattern/Contrast", str(tex.contrast))
    set_internal("Texture/Pattern/Invert", "true" if tex.invert else "false")
    set_internal("Texture/Pattern/TexturingMode", str(tex.texturing_mode))
    set_internal("Texture/Strength/Value", f"{max(0.0, min(1.0, tex.strength)):g}")
    if tex.strength_pressure:
        set_internal("PressureTexture/Strength/", "true")
        set_internal("Texture/Strength/UseCurve", "true")
        if tex.strength_curve:
            set_string("Texture/Strength/commonCurve", tex.strength_curve)


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
    texture: TextureXml | None = None,
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
        # Photoshop angle jitter 直接映射为 Krita「旋转-效果强度」；
        # Krita 的 fuzzy/fuzzystroke 曲线本身负责 ±180° 随机方向；
        # 曲线 XML 使用归一化 0..1，不能直接写 UI 显示的 180。
        set_string("RotationSensor", _drawing_angle_sensor())
        set_internal("PressureRotation", "true")
        set_internal("RotationValue", f"{max(0.0, min(100.0, rotation_jitter)) / 100.0:g}")
        set_internal("RotationUseCurve", "true")
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

    parts: list[str] = []
    resources: list[tuple[str, str, str, bytes]] = []
    if tip_png is not None:
        res_name = tip_name or _safe_name(name)
        resources.append(("brushes", res_name, res_name + ".png", tip_png))
        parts.append(
            '<Preset paintopid="paintbrush" name=' + quoteattr(name)
            + ' embedded_resources="2"> '
        )
    else:
        parts.append('<Preset paintopid="paintbrush" name=' + quoteattr(name) + '>')
    if texture is not None:
        resources.append(("patterns", texture.pattern_filename,
                          texture.pattern_filename, texture.png_bytes))
        _apply_texture(params, texture)
    if resources:
        parts.append(_resources_xml(resources))

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
