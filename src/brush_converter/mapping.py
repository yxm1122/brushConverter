"""ABR 描述符 → 笔刷预设参数的映射。

映射策略（对照用户手动转换的 Krita 5.x 参考预设逐项校准）：
  - 尺寸：scale = Dmtr / 笔尖宽度（尊重 Photoshop 主直径）
  - 间距 / 角度：直接映射
  - 压感→大小：minimumDiameter（szVr.bVTy==2 压力）
  - 压感→不透明度：opVr.Mnm（opVr.bVTy==2）
  - 压感→流量：prVr.Mnm（prVr.bVTy==2）
  - 压感→宽高比：minimumRoundness（roundnessDynamics.bVTy==2）
  - 旋转：angleDynamics.bVTy==6 → 随笔迹方向（drawingangle）
  - 散布：useScatter → 启用开关；scatterDynamics.jitter → 散布量
    （经验换算 ScatterValue = jitter/400，见 preset_xml.py 散布分支）
    bVTy==2 → 压感控制，bVTy==0 → 恒定；bothAxes → 两轴
  - 计算笔刷 → Krita auto_brush
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .abr import AbrFile
from .abr import descriptors as D


@dataclass
class BrushPreset:
    name: str
    spacing: float = 0.25          # 0..1（Krita spacing 为直径占比）
    angle: float = 0.0             # 度
    roundness: float = 100.0       # 0..100（100 = 圆）
    diameter: float | None = None  # px（计算笔刷用）
    hardness: float | None = None  # 0..100（计算笔刷用）
    scale: float = 1.0             # 采样笔刷的缩放（Dmtr / 笔尖宽度）
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


def _bvty_sensor(bvty: float | None) -> str | None:
    """Photoshop bVTy 控制源 → Krita 传感器 id。"""
    if bvty is None:
        return None
    b = int(bvty)
    if b == 2:          # 压力
        return "pressure"
    if b in (6, 7):     # 初始方向 / 方向
        return "drawingangle"
    if b == 3:          # 倾斜
        return "tilt"
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

    tip_by_uuid: dict[str, object] = {}
    for t in abr.tips:
        if t.uuid:
            tip_by_uuid[_norm_uuid(t.uuid)] = t

    out: list[BrushPreset] = []
    for p in presets:
        name = D.get_text(p, "Nm  ") or D.get_text(p, "name") or "Unnamed"
        brsh = D.get_obj(p, "Brsh") or {}

        spacing = (D.get_number(brsh, "Spcn") or 25.0) / 100.0
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
        if tip is not None and diameter is not None:
            scale = diameter / max(1, tip.width)

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

        warnings = _collect_warnings(p)

        out.append(BrushPreset(
            name=name,
            spacing=spacing,
            angle=angle,
            roundness=roundness,
            diameter=diameter,
            hardness=hardness,
            scale=scale,
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
            warnings=warnings,
            meta={"Dmtr": diameter, "Hrdn": hardness, "Rndn": roundness, "Spcn": spacing * 100.0},
        ))
    return _dedupe(out)


def _collect_warnings(p: dict[str, D.Value]) -> list[str]:
    """收集当前预设里未映射的参数（供 GUI 弹出提醒）。"""
    warnings: list[str] = []
    if D.get_bool(p, "useTexture"):
        warnings.append("纹理")
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
