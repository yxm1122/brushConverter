"""ABR 描述符 → 笔刷预设参数的映射。

映射策略（对照用户手动转换的 Krita 5.x 参考预设逐项校准）：
  - 尺寸：scale = Dmtr / 笔尖宽度（尊重 Photoshop 主直径）
  - 间距 / 角度：间距 = Spcn/100，采样笔刷再乘「短边/长边」补回 PS 的间距基准
    （任务 107；补白把 Krita 基准换成长边，见 BrushPreset.spacing_basis）；角度直接映射
  - 压感→大小：minimumDiameter（szVr.bVTy==2 压力）
  - 压感→不透明度：opVr.Mnm（opVr.bVTy==2）
  - 压感→流量：prVr.Mnm（prVr.bVTy==2）
  - 压感→宽高比：minimumRoundness（roundnessDynamics.bVTy==2）
  - 旋转：angleDynamics.bVTy==6 → 随笔迹方向（drawingangle）；jitter 作为 Krita RotationValue 效果强度，随机度曲线固定 ±180°
  - 散布：useScatter → 启用开关；scatterDynamics.jitter → 散布量
    （经验换算 ScatterValue = jitter/400，见 preset_xml.py 散布分支）
    bVTy==2 → 压感控制，bVTy==0 → 恒定；bothAxes → 两轴
  - 纹理：按 Krita 模式合成校准亮度/对比度；Linear Height (Photoshop) 使用独立基线
    与反向对比度映射，Krita UI 数值按两位小数输出
  - 计算笔刷 → Krita auto_brush
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .abr import AbrFile
from .abr import descriptors as D


# Photoshop 纹理混合模式（enum BlnM 成员）→ Krita TexturingMode 数值。
# 未列出的模式回退 Multiply(0)，并在警告里注明（见 _collect_warnings）。
_TEXTURING_MODE = {
    # Krita KisTextureOptionData::TexturingMode enum（Krita master/6.x）
    "Mul ": 0,           # MULTIPLY
    "Sbtr": 1,           # SUBTRACT
    "Lght": 2,           # LIGHTNESS
    "Grdn": 3,           # GRADIENT
    "Drkn": 4,           # DARKEN
    "Ovrl": 5,           # OVERLAY
    "CDdg": 6,           # COLOR_DODGE
    "CBrn": 7,           # COLOR_BURN
    "LDdg": 8,           # LINEAR_DODGE
    "LBur": 9,           # LINEAR_BURN
    "HdMx": 10,          # HARD_MIX_PHOTOSHOP
    "HdMxS": 11,         # HARD_MIX_SOFTER_PHOTOSHOP
    "Hght": 14,          # HEIGHT_PHOTOSHOP（测试样本实际值）
    "hardMix": 10,       # HARD_MIX_PHOTOSHOP（测试样本实际值）
    # Photoshop 专用模式必须优先使用 Krita 的 (Photoshop) 项；
    # ABR 的 linearHeight 即 Photoshop Linear Height。
    "linearHeight": 15,  # LINEAR_HEIGHT_PHOTOSHOP
    "height": 14,        # HEIGHT_PHOTOSHOP
    "linearHeightPhotoshop": 15,  # LINEAR_HEIGHT_PHOTOSHOP
    "linearHeightPS": 15,
    "HghtPS": 14,
    # PS 2025 把纹理混合模式写成 stringID（不是 4CC），下面两条由此前一律静默回退
    # Multiply(0)。任务 35（B 批）实测转正：PS 真值 ↔ Krita 对应模式渲染 6/6 组 0.00%/max≤1。
    "linearDodge": 8,    # LINEAR_DODGE（等价 4CC 键 LDdg）
    "linearBurn": 9,     # LINEAR_BURN（等价 4CC 键 LBur）
    # --- v0.4 任务 34（A 批实测转正，证据 docs/研究报告/模式-A.md）---
    # 依据：PS 真值 Alpha 与 Mltp 真值同哈希（6/6 组）、与 Krita 对应模式真机渲染
    # 掩码空间 0.00%、max_delta ≤1。显式登记后不再打印「回退Multiply」警告。
    # 注意：base=1（不透明笔尖章）时 MULTIPLY/DARKEN/LINEAR_BURN 三算子像素相同，
    # 选 Multiply 是因为名字语义与既有默认，不是因为这三种能区分。
    "Mltp": 0,           # Multiply（PS 4CC 写法；ABR 里另一种写法 "Mul " 见上）
    "Nrml": 0,           # Normal 普通（实测 Alpha 通路 ≡ 正片叠底）
    "Bhnd": 0,           # Behind 背后（同上）
    "Clar": 0,           # Clear 清除（同上）
    "H   ": 0,           # Hue 色相（颜色域模式，Alpha 通路无独立传递律）
    "Strt": 0,           # Saturation 饱和度（同上）
    "Clr ": 0,           # Color 颜色（同上）
    "Lmns": 0,           # Luminosity 明度（同上）
    # Difference 实测并不是「差值」：PS 侧 Alpha ≡ 减去（d<100 与 Sbtr 同哈希、
    # d=100 同为 255−u）。映射到 Krita SUBTRACT(1) 后 6/6 组 0.00%/max≤1；
    # 旧行为是静默回退 Multiply，实测差异 69.04%~99.29%。按像素校准，不是按名字对应。
    "Dfrn": 1,           # SUBTRACT
    # --- v0.4 任务 36（C 批实测转正，证据 docs/研究报告/模式-C.md）---
    # 口径：PS 真值 vs「任务 33 已渲染的 Krita 同组图」逐像素比（tol=2，门槛 2%/max2）。
    # 前三条：PS 侧真值与 Mltp 真值逐字节相同，且 vs Krita Multiply 渲染 6/6 组
    # 0.00%、max_delta<=1。PS 把这几个「层域」模式名在纹理笔迹上按默认算子处理，
    # 映射成 Multiply 是当前唯一有像素证据的选择（显式登记后不再打印「回退Multiply」）。
    "passThrough": 0,     # 穿透：6/6 组 0.00%/max 0~1
    "lighterColor": 0,    # 较亮颜色：6/6 组 0.00%/max 0~1
    "darkerColor": 0,     # 较暗颜色：6/6 组 0.00%/max 0~1
    # 减去的第二种写法（PS 层域名，与 4CC "Sbtr" 同义）：PS 侧真值与 Sbtr 真值逐字节
    # 相同，且 vs Krita Subtract 渲染 6/6 组 0.00%/max 0~1。旧代码无此键 → 回退
    # Multiply(0)，属映射错误（把「减去」当「正片叠底」），本批修正。
    "blendSubtraction": 1,  # SUBTRACT：6/6 组 0.00%/max 0~1
}


@dataclass
class TextureSettings:
    """一个笔刷预设的纹理设置（ABR desc 的纹理参数 + patt 位图）。

    scale/brightness/contrast/depth 为 Photoshop 原始语义：
    scale: textureScale %（0..100）；brightness/contrast: -100..100；
    depth: textureDepth %（0..100）。
    """

    name: str
    uuid: str
    scale: float = 100.0
    invert: bool = False
    brightness: int = 0       # Photoshop 纹理亮度：-150..150
    contrast: int = 0         # Photoshop 纹理对比度：-50..100
    depth: float = 100.0
    depth_min: float = 0.0      # 压感→深度曲线的最小值（textureDepthDynamics.Mnm）
    pressure: bool = False      # 深度随压感（bVTy==2）
    blend_mode: str = "Mul "    # PS 4CC 成员（'linearHeight' 等）
    image: np.ndarray | None = None  # (h,w,3) RGB 或 (h,w) 灰度，来自 patt


@dataclass
class BrushPreset:
    name: str
    spacing: float = 0.25          # 0..1（Krita spacing 为直径占比）
    angle: float = 0.0             # 度
    roundness: float = 100.0       # 0..100（100 = 圆）
    diameter: float | None = None  # px（计算笔刷用）
    hardness: float | None = None  # 0..100（计算笔刷用）
    scale: float = 1.0             # 采样笔刷的缩放（Dmtr / 笔尖宽度）
    #: 间距基准补偿系数 = 原始笔尖短边 / 长边（方形=1，计算笔刷=1）。
    #: PS 对采样笔尖的间距基准是「渲染后短边」，而补白后的正方形画布把 Krita 的
    #: 尺寸/间距基准换成长边 → 非方形笔尖的步长被放大 1/该比值（任务 106 §4 实测：
    #: Krita/PS 步长比 ≈ 长宽比 1.28，方形组 1.01）。换算在 convert._sampled_brush_def。
    spacing_basis: float = 1.0
    tip_gray: np.ndarray | None = None   # 采样笔尖蒙版（255=墨）
    uuid: str | None = None
    is_computed: bool = False
    size_curve: str | None = None       # "x,y;x,y;"
    opacity_curve: str | None = None
    flow_curve: str | None = None
    ratio_curve: str | None = None
    rotation_sensor: str | None = None  # "pressure" | "drawingangle" | None
    rotation_jitter: float = 0.0
    scatter: bool = False                 # useScatter → 散布选项启用
    scatter_pressure: bool = False        # scatterDynamics.bVTy==2 → 散布量随压感
    scatter_both_axes: bool = False
    scatter_amount: float | None = None   # PS 散布量（scatterDynamics.jitter，0..1000%）
    texture: TextureSettings | None = None  # 纹理设置（useTexture 且找到图案时非空）
    warnings: list[str] = field(default_factory=list)  # 未映射参数的中文名
    meta: dict = field(default_factory=dict)


def _norm_uuid(s: str) -> str:
    """规范化 UUID：去 NUL、去空格、转小写、截到 36 字符。"""
    return s.replace("\x00", "").strip().lower()[:36]


def _dyn(obj: dict[str, D.Value] | None) -> dict[str, float]:
    """取出动态对象（szVr/opVr 等）里的数值字段：bVTy/fStp/jitter/Mnm。"""
    if obj is None:
        return {}
    return {
        k: float(v.value)
        for k, v in obj.items()
        if v.type in ("long", "doub", "UntF")
    }


def _texture_blend_mode(p: dict[str, D.Value]) -> str:
    """取 textureBlendMode 枚举成员（如 'linearHeight'）。

    描述符解析器里 enum 存成 type_id=类 id('BlnM')、value=成员 id，
    语义值在 value 上（与 toolOptions.Md 的 'Nrml' 同理）。
    """
    v = p.get("textureBlendMode")
    if v is not None and v.type == "enum" and isinstance(v.value, str):
        return v.value
    return "Mul "


def _map_texture(p: dict[str, D.Value],
                 patterns: dict[str, object]) -> TextureSettings | None:
    """从 useTexture 预设提取纹理设置（含 patt 位图查找，按 UUID）。"""
    tex_obj = D.get_obj(p, "Txtr") or {}
    tex_uuid = D.get_text(tex_obj, "Idnt")
    tex_name = D.get_text(tex_obj, "Nm  ")
    # 与采样笔尖一致，按规范化 UUID 匹配 patt 图案
    pat = patterns.get(_norm_uuid(tex_uuid)) if tex_uuid else None
    tdd = _dyn(D.get_obj(p, "textureDepthDynamics"))
    min_depth = D.get_number(p, "minimumDepth")
    # 注意：必须按 None 判断缺失，不能用 `or <默认值>`——0 是合法值
    # （纹理强度 0 = 关闭、缩放 0、间距 0 = 连续笔迹），or 会把它们改成默认值。
    scale = D.get_number(p, "textureScale")
    brightness = D.get_number(p, "textureBrightness")
    contrast = D.get_number(p, "textureContrast")
    depth = D.get_number(p, "textureDepth")
    return TextureSettings(
        name=tex_name or (getattr(pat, "name", None) or tex_uuid or "texture"),
        uuid=tex_uuid or "",
        scale=100.0 if scale is None else scale,
        invert=D.get_bool(p, "InvT") or False,
        brightness=int(0 if brightness is None else brightness),
        contrast=int(0 if contrast is None else contrast),
        depth=100.0 if depth is None else depth,
        depth_min=float(tdd.get("Mnm ", 0.0 if min_depth is None else min_depth)),
        pressure=tdd.get("bVTy") == 2,
        blend_mode=_texture_blend_mode(p),
        image=getattr(pat, "image", None),
    )


def _bvty_sensor(bvty: float | None) -> str | None:
    """Photoshop bVTy 控制源 → Krita 传感器 id。"""
    if bvty is None:
        return None
    b = int(bvty)
    if b == 2:          # 压力
        return "pressure"
    if b in (6, 7):     # 初始方向 / 方向
        return "drawingangle"
    # 3=倾斜：Krita 预设的 RotationSensor 只实现了 pressure/drawingangle，
    # 没有 tilt 分支；返回 None 让上层按「未映射」提示，而不是写一个
    # 下游不会消费的传感器值（那样旋转效果会被静默丢弃）。
    if b == 3:
        return None
    return None         # 0=off, 1=fade, 4=wheel, 5=rotation 暂不映射


def map_presets(abr: AbrFile) -> list[BrushPreset]:
    """把已解析的 ABR 映射为一组 BrushPreset（含去重）。

    无 desc 区段（罕见旧文件）时退化为按笔尖逐个生成默认预设。
    """
    if "desc" not in abr.sections:
        return [
            BrushPreset(name=f"brush-{t.index + 1:03d}",
                        spacing=0.25, tip_gray=t.gray, uuid=t.uuid,
                        scale=1.0)
            for t in abr.tips
        ]

    top = D.parse_descriptor_block(abr.sections["desc"].data)
    presets = D.iter_brush_presets(top)

    # 图案 uuid 与 desc 里的 Txtr.Idnt 都做同样的规范化，避免大小写/
    # NUL 填充差异导致纹理明明存在却报「图案缺失」。
    patterns = {_norm_uuid(k): v
                for k, v in (getattr(abr, "patterns", None) or {}).items()}

    tip_by_uuid: dict[str, object] = {}
    for t in abr.tips:
        if t.uuid:
            tip_by_uuid[_norm_uuid(t.uuid)] = t

    out: list[BrushPreset] = []
    for p in presets:
        name = D.get_text(p, "Nm  ") or D.get_text(p, "name") or "Unnamed"
        brsh = D.get_obj(p, "Brsh") or {}

        spcn = D.get_number(brsh, "Spcn")
        spacing = (25.0 if spcn is None else spcn) / 100.0
        angle = D.get_number(brsh, "Angl") or 0.0
        roundness = D.get_number(brsh, "Rndn")
        roundness = roundness if roundness is not None else 100.0
        diameter = D.get_number(brsh, "Dmtr")
        hardness = D.get_number(brsh, "Hrdn")

        uuid_raw = None
        sd = brsh.get("sampledData")
        if sd is not None and sd.type == "TEXT":
            uuid_raw = str(sd.value)
        tip = tip_by_uuid.get(_norm_uuid(uuid_raw)) if uuid_raw else None

        scale = 1.0
        spacing_basis = 1.0
        if tip is not None and diameter is not None:
            # Krita sampled brush 的 scale 以方形笔尖画布为基准；非正方形
            # 若仍使用 width，会导致预设编辑器显示尺寸与实际绘图区不一致。
            scale = diameter / max(1, tip.width, tip.height)
            # 间距基准补偿（任务 107）：补白把 Krita 的间距基准由「短边」改成「长边」，
            # 这里记下短边/长边比值，由 convert._sampled_brush_def 乘回 spacing。
            # 严格正方形（宽=高）比值为 1 → 行为不变；近方形按实际比值微调
            # （如 84/86 = 0.977）；计算笔刷为 1。补白本身不动（铁律 17）。
            spacing_basis = min(tip.width, tip.height) / max(1, tip.width, tip.height)

        # 各动态的 bVTy / 数值
        sz = _dyn(D.get_obj(p, "szVr"))
        ang = _dyn(D.get_obj(p, "angleDynamics"))
        rnd = _dyn(D.get_obj(p, "roundnessDynamics"))
        opv = _dyn(D.get_obj(p, "opVr"))
        prv = _dyn(D.get_obj(p, "prVr"))
        sct = _dyn(D.get_obj(p, "scatterDynamics"))

        min_diameter = D.get_number(p, "minimumDiameter")
        min_roundness = D.get_number(p, "minimumRoundness")

        size_curve = None
        if sz.get("bVTy") == 2:
            m = 0.0 if min_diameter is None else max(0.0, min(100.0, min_diameter))
            size_curve = f"0,{m / 100.0:g};1,1;"

        opacity_curve = None
        if opv.get("bVTy") == 2:
            m = max(0.0, min(100.0, opv.get("Mnm ", 0.0)))
            opacity_curve = f"0,{m / 100.0:g};1,1;"

        flow_curve = None
        if prv.get("bVTy") == 2:
            m = max(0.0, min(100.0, prv.get("Mnm ", 0.0)))
            flow_curve = f"0,{m / 100.0:g};1,1;"

        ratio_curve = None
        if rnd.get("bVTy") == 2:
            m = 0.0 if min_roundness is None else max(0.0, min(100.0, min_roundness))
            ratio_curve = f"0,{m / 100.0:g};1,1;"

        rotation_sensor = _bvty_sensor(ang.get("bVTy"))
        rotation_jitter = ang.get("jitter", 0.0)

        scatter = D.get_bool(p, "useScatter") or False
        scatter_pressure = sct.get("bVTy") == 2
        scatter_both_axes = D.get_bool(p, "bothAxes") or False
        # PS 散布量存于 scatterDynamics.jitter（#Prc，0..1000%），顶层 Spcn 是间距
        scatter_amount = sct.get("jitter") if (scatter and "jitter" in sct) else None

        texture = _map_texture(p, patterns) if D.get_bool(p, "useTexture") else None
        warnings = _collect_warnings(p, texture)
        # 旋转控制源 bVTy=1(渐隐)/3(倾斜)/4(转轮)/5(旋转) 尚未映射；
        # 若 jitter>0 说明 PS 中该效果实际存在，丢失效果需要提示。
        if rotation_jitter > 0 and ang.get("bVTy") not in (0, 2, 6, 7, None):
            warnings.append(f"旋转控制源(bVTy={int(ang['bVTy'])} 未映射)")

        out.append(BrushPreset(
            name=name,
            spacing=spacing,
            angle=angle,
            roundness=roundness,
            diameter=diameter,
            hardness=hardness,
            scale=scale,
            spacing_basis=spacing_basis,
            tip_gray=tip.gray if tip is not None else None,
            uuid=uuid_raw,
            is_computed=(tip is None),
            size_curve=size_curve,
            opacity_curve=opacity_curve,
            flow_curve=flow_curve,
            ratio_curve=ratio_curve,
            rotation_sensor=rotation_sensor,
            rotation_jitter=rotation_jitter,
            scatter=scatter,
            scatter_pressure=scatter_pressure,
            scatter_both_axes=scatter_both_axes,
            scatter_amount=scatter_amount,
            texture=texture,
            warnings=warnings,
            meta={"Dmtr": diameter, "Hrdn": hardness, "Rndn": roundness, "Spcn": spacing * 100.0},
        ))
    return _dedupe(out)


def _collect_warnings(p: dict[str, D.Value],
                          texture: TextureSettings | None = None) -> list[str]:
    """收集当前预设里未映射的参数（供 GUI 弹出提醒）。"""
    warnings: list[str] = []
    if D.get_bool(p, "useTexture"):
        # 纹理主体已映射：只对异常/回退/无对应项保留轻量警告
        if texture is None or texture.image is None:
            warnings.append("纹理(图案缺失)")
        elif texture.blend_mode not in _TEXTURING_MODE:
            warnings.append(f"纹理混合模式({texture.blend_mode!r} 回退Multiply)")
        if D.get_bool(p, "protectTexture"):
            warnings.append("纹理(protectTexture 未映射)")
        # TxtC(Texture Each Tip) / interpretation 在 Krita 无对应，但为本文件默认值，
        # 静默忽略（与用户确认的方案一致）。
    if D.get_bool(p, "useColorDynamics"):
        warnings.append("颜色动态")
    if D.get_bool(p, "Wtdg"):
        warnings.append("湿边")
    if D.get_bool(p, "Nose"):
        warnings.append("喷嘴")
    if D.get_bool(p, "useBrushPose"):
        warnings.append("笔刷姿态")
    dual = D.get_obj(p, "dualBrush")
    if dual is not None and D.get_bool(dual, "useDualBrush"):
        warnings.append("双笔刷")
    return warnings


def _dedupe(presets: list[BrushPreset]) -> list[BrushPreset]:
    """去掉完全重复的预设（同 name+uuid+Dmtr），保留首个。"""
    seen: set[tuple] = set()
    result: list[BrushPreset] = []
    for bp in presets:
        key = (bp.name, bp.uuid, bp.diameter)
        if key in seen:
            continue
        seen.add(key)
        result.append(bp)
    return result
