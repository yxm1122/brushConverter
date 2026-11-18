"""生成 Krita 笔刷预设（paintop preset）的 XML。

目标格式：Krita 5.x 内嵌资源（embedded_resources="2"）——
笔尖以 base64 内嵌在 <resources> 中，brush_definition 用 png_brush 引用。

默认参数集严格取自用户手动转换的 Krita 5.x 参考预设（199 项参数，
145 internal + 54 string），保证参数名与类型和 Krita 实际输出一致。

纹理亮度/对比度有两条实现路径（见文件中部「纹理亮度/对比度：公式开关」一节）：
  - calibrated （默认，v1.2.0 起）任务 12 拟合出的 PS 纹理前置变换的精确逆；
  - legacy     （历史方案，v1.1.2 及更早）经验公式，可用环境变量一键回退：
        BRUSHCONVERTER_TEXTURE_FORMULA=legacy python cli.py convert ...
回退理由：新公式是研究产物，一旦退化必须能立刻回到旧行为。
转正依据（A/B 掩码空间 70.75% → 0.00%）与限制见 docs/验收报告/13-报告.md。
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import sys
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


def _cdata(value: str) -> str:
    """把任意文本包成 CDATA 段；文本里的 `]]>` 会提前结束 CDATA，需拆分拼接。"""
    return "<![CDATA[" + value.replace("]]>", "]]]]><![CDATA[>") + "]]>"


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


def _fuzzy_random_sensor() -> str:
    """纯随机旋转传感器：只保留 fuzzy/fuzzystroke，不随笔迹方向。

    Photoshop angleDynamics.bVTy==0（控制=关）且 jitter>0 时是纯随机角度抖动，
    Krita 用 fuzzy 随机曲线表达；随机幅度由 RotationValue（=jitter/100）缩放。
    """
    return (
        '<!DOCTYPE params> <params id="sensorslist"> '
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


# ---------------------------------------------------------------------------
# 纹理亮度/对比度：公式开关与两条实现（任务 13）
#
# PS 侧真值来自任务 12（docs/研究报告/lut-拟合.md）：
#   对比度中心 p = 127（0..255 尺度），因子 k = 1+C/100（C<0）或 1/(1-C/100)（C>=0）；
#   顺序随对比度符号翻转：C>=0 先亮度后对比度，C<0 先对比度后亮度。
# Krita 侧真值来自 KisTextureMaskInfo::recalculateMask()（tools/texture_mask.py 精确复刻）：
#   mask -= brightness；mask = (mask-0.5)*contrast + 0.5；再 clamp 0..1。
# calibrated 就是把「Krita 的亮度/对比度两个参数」反解成让 Krita 掩码等于 PS 前置掩码的值。
# ---------------------------------------------------------------------------
_TEXTURE_FORMULA_ENV = "BRUSHCONVERTER_TEXTURE_FORMULA"
_TEXTURE_FORMULA_VALUES = ("legacy", "calibrated")
#: 默认公式：v1.2.0 起转正为 calibrated（A/B 掩码空间 0.00% vs 旧公式 70.75%）
_TEXTURE_FORMULA_DEFAULT = "calibrated"
#: 已经警告过的非法环境变量值（同一个非法值只提示一次，避免批量转换刷屏）
_TEXTURE_FORMULA_WARNED: set[str] = set()


def _texture_formula() -> str:
    """读取纹理亮度/对比度公式开关。

    - 未设置环境变量 → calibrated（默认，v1.2.0 起转正）；
    - legacy / calibrated（忽略大小写与首尾空白）→ 原样返回
      （legacy 是 v1.1.2 历史方案，用 BRUSHCONVERTER_TEXTURE_FORMULA=legacy 回退）；
    - 其它非法取值（含空字符串）→ 回退默认公式 calibrated，
      并对同一个非法取值只打印一次警告（stderr）。
    """
    raw = os.environ.get(_TEXTURE_FORMULA_ENV)
    if raw is None:
        return _TEXTURE_FORMULA_DEFAULT
    value = raw.strip().lower()
    if value in _TEXTURE_FORMULA_VALUES:
        return value
    if raw not in _TEXTURE_FORMULA_WARNED:
        _TEXTURE_FORMULA_WARNED.add(raw)
        print(
            f"[警告] 环境变量 {_TEXTURE_FORMULA_ENV}={raw!r} 非法"
            f"（只接受 {'/'.join(_TEXTURE_FORMULA_VALUES)}），"
            f"已回退默认公式 {_TEXTURE_FORMULA_DEFAULT}。",
            file=sys.stderr,
        )
    return _TEXTURE_FORMULA_DEFAULT


def _texture_contrast_factor(ps_contrast: float) -> float:
    """Photoshop 纹理对比度 → 中心乘法因子 k（两条路径共用，未舍入）。

    C < 0：k = 1 + C/100；C >= 0：k = 1/(1-C/100)；C >= 100 用 1e6 逼近完全二值化。
    依据任务 12 拟合（docs/研究报告/lut-拟合.md 第 4.1 节：实测 k 与经验公式一致）。
    """
    c = max(-50.0, min(100.0, ps_contrast))
    if c < 0.0:
        return 1.0 + c / 100.0
    if c >= 100.0:
        return 1_000_000.0
    return 1.0 / (1.0 - c / 100.0)


def _texture_brightness_legacy(ps_brightness: float) -> float:
    """历史方案（v1.1.2，勿改）：Krita 亮度 = 0.10 - PS亮度/250，两位小数，clamp [-1,1]。

    保留此函数是任务 13 的硬性要求：开关必须能一键切回旧行为。
    """
    brightness = 0.10 - ps_brightness / 250.0
    brightness = max(-1.0, min(1.0, round(brightness, 2)))
    if brightness == 0:
        return 0.0
    return brightness


def _texture_contrast_legacy(ps_contrast: float) -> float:
    """历史方案（v1.1.2，勿改）：对比度直接取 PS 中心因子，两位小数，clamp [0,2]。"""
    contrast = max(0.0, min(2.0, round(_texture_contrast_factor(ps_contrast), 2)))
    return contrast


def _texture_brightness_contrast_calibrated(
    ps_brightness: float, ps_contrast: float
) -> tuple[float, float]:
    """现行方案（v1.2.0 起默认）：任务 12 拟合公式的精确逆，闭式推导见下。

    记 PS 亮度值 b（-150..150，0..255 尺度）、对比度因子 k（0..2 可表达），
    Krita 前置掩码（0..255 尺度）为
        U_k = k*t - 255*k*b_k + 127.5*(1-k)          （唯一一次 clamp 在出口）
    PS 前置掩码（0..255 尺度）按任务 12 的符号规则：
        C >= 0（先亮度后对比度） U = k*clip(t+b) + 127*(1-k)
        C <  0（先对比度后亮度） U = clip(k*t + 127*(1-k) + b)
    在未截断区把两式逐项对齐，解出唯一一组 Krita 参数：
        c_k = k
        b_k = (1-k)/(510k) - b/(255k)   （C < 0，亮度在对比度之后 → 反解时要除以 k）
        b_k = (1-k)/(510k) - b/255      （C >= 0，亮度在对比度之前）
    截断区可证明两侧等价（k>=1 时 PS 的「先截断亮度」与 Krita 的「最后统一截断」
    在饱和区给出相同的 0/255），因此该闭式在整个定义域内逐点等价，不需要数值搜索。

    表达不了的两类参数（如实保留并写进文档，不做假补）：
      1) C > 50 时 k > 2，超出 Krita Contrast 上限 2 → 按 2 截断，属结构性失真；
      2) 反解出的 b_k 超出 Krita 亮度滑杆范围 [-1,1] 时按边界截断
         （仅 C<0 且 |PS 亮度| 很大时出现，例如 C=-50 与 b=±150）。
    精度：Krita 用 double 读取预设值（KisTextureOptionData::read 的 getDouble），
    这里保留 3 位小数（Krita UI 显示两位，多留一位是为了让 k=2 时
    亮度舍入误差不超过 1/255，保证掩码等价误差 <= 1 级）。
    """
    k = _texture_contrast_factor(ps_contrast)
    c = max(-50.0, min(100.0, ps_contrast))
    k_eff = max(0.0, min(2.0, k))
    if k_eff <= 0.0:
        # PS 对比度合法范围是 -50..100，k 恒 >= 0.5；这里只做防御性回退。
        return (_texture_brightness_legacy(ps_brightness),
                _texture_contrast_legacy(ps_contrast))
    center_fix = (1.0 - k_eff) / (510.0 * k_eff)
    if c < 0.0:
        brightness = center_fix - ps_brightness / (255.0 * k_eff)
    else:
        brightness = center_fix - ps_brightness / 255.0
    brightness = max(-1.0, min(1.0, round(brightness, 3)))
    if brightness == 0:
        brightness = 0.0
    contrast = max(0.0, min(2.0, round(k_eff, 3)))
    return brightness, contrast


def texture_brightness_contrast(
    ps_brightness: float, ps_contrast: float
) -> tuple[float, float]:
    """PS 纹理亮度/对比度 → Krita Texture/Pattern/Brightness、Contrast。

    由环境变量 BRUSHCONVERTER_TEXTURE_FORMULA 选择实现：
      - calibrated （默认，v1.2.0 起）任务 12 拟合公式的精确逆；
      - legacy     （历史方案）v1.1.2 经验公式，显式设置环境变量即可回退。
    非法取值回退默认公式（calibrated）并只警告一次。
    """
    if _texture_formula() == "calibrated":
        return _texture_brightness_contrast_calibrated(ps_brightness, ps_contrast)
    return _texture_brightness_legacy(ps_brightness), _texture_contrast_legacy(ps_contrast)


@dataclass
class TextureXml:
    """Krita 纹理选项所需的数据（由 convert 从 TextureSettings 组装）。"""

    pattern_filename: str   # 内嵌资源文件名（如 tex_66e2987f.png）
    png_bytes: bytes
    scale: float = 1.0      # 0.01..10，1=100%
    brightness: float = 0.0
    contrast: float = 1.0
    invert: bool = False
    texturing_mode: int = 0
    use_soft_texturing: bool = False
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
    set_internal("Texture/Pattern/Brightness", f"{tex.brightness:g}")
    set_internal("Texture/Pattern/Contrast", f"{tex.contrast:g}")
    set_internal("Texture/Pattern/Invert", "true" if tex.invert else "false")
    set_internal("Texture/Pattern/TexturingMode", str(tex.texturing_mode))
    set_internal("Texture/Pattern/UseSoftTexturing", "true" if tex.use_soft_texturing else "false")
    set_internal("Texture/Strength/Value", f"{max(0.0, min(1.0, tex.strength)):g}")
    # Krita 的 PressureTexture/Strength/ 是「效果强度」选项的启用开关
    # （isChecked，见 KisKritaSensorPack::read：getBool("Pressure"+id)），
    # 与压感无关：PS 纹理深度无论是否接传感器都必须打开，否则该项被整体禁用。
    set_internal("PressureTexture/Strength/", "true")
    if tex.strength_pressure:
        # PS textureDepthDynamics.bVTy==2：强度随压感，启用曲线（Mnm 为最小值）
        set_internal("Texture/Strength/UseCurve", "true")
        if tex.strength_curve:
            set_string("Texture/Strength/commonCurve", tex.strength_curve)
    else:
        # PS bVTy==0：恒定强度，只写 Value（textureDepth/100），不接传感器
        set_internal("Texture/Strength/UseCurve", "false")


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
    """组装完整的 <Preset> XML。曲线参数形如 "x,y;x,y;"（Krita 实际输出带尾随分号）。"""
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
    elif rotation_jitter > 0:
        # PS angleDynamics.bVTy==0（控制=关）但 jitter>0：纯随机角度抖动，
        # 没有传感器。Krita 的「旋转-效果强度」仍需开启（PressureRotation=true），
        # 用只有 fuzzy/fuzzystroke 的传感器表达随机，RotationValue=jitter/100。
        set_string("RotationSensor", _fuzzy_random_sensor())
        set_internal("PressureRotation", "true")
        set_internal("RotationValue", f"{max(0.0, min(100.0, rotation_jitter)) / 100.0:g}")
        set_internal("RotationUseCurve", "true")
        set_internal("RotationUseSameCurve", "false")
        set_internal("RotationcurveMode", "1")
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
    if texture is not None:
        resources.append(("patterns", texture.pattern_filename,
                          texture.pattern_filename, texture.png_bytes))
        _apply_texture(params, texture)
    # embedded_resources="2" 必须与 <resources> 同时出现：计算笔刷
    # （tip_png=None）只带纹理资源时同样要声明，否则 Krita 不读内嵌纹理。
    attrs = ' embedded_resources="2"' if resources else ""
    parts.append(
        '<Preset paintopid="paintbrush" name=' + quoteattr(name) + attrs + '>')
    if resources:
        parts.append(_resources_xml(resources))

    for key, (typ, value) in params.items():
        if typ == "string":
            parts.append(
                f' <param name={quoteattr(key)} type="string">{_cdata(value)}</param>')
        else:
            # internal 值直接进入元素内容，extra_params 还允许调用方传任意字符串
            parts.append(
                f' <param name={quoteattr(key)} type="internal">{escape(value)}</param>')
    parts.append(" </Preset>")
    return "".join(parts)


def _safe_name(name: str) -> str:
    """把笔刷名清洗为安全的资源名（仅保留 word 字符）。"""
    cleaned = re.sub(r"[^\w]+", "_", name, flags=re.UNICODE).strip("_")
    return cleaned or "brush"
