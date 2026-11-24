"""逐支导出选项契约（任务 167）。

本模块只定义**契约与纯计算**，不改动现有转换行为：

- 稳定逐支身份：源文件内容摘要 + 原始序号 + 预设内容摘要。显示名称不参与
  身份，因此改名、搜索、筛选、排序都不会串设置（任务要求 1）。
- 三项“牺牲可编辑性”的开关：计算笔刷位图化、纹理预缩放、亮度越界预烘焙。
  每项按**条件**计算适用性与布尔默认值；默认沿用现行外观优先处理，不适用
  项默认关闭且不可强开（任务要求 2）。
- 适用性（applicable）、勾选状态（enabled）、实际处理（actual）三层分开
  记录，绝不把“这个开关存在”当成“这次实际执行了”（任务要求 2/4）。
- GUI 与 CLI 共用的显式 JSON 配置格式、版本号与逐类错误（任务要求 3）。
- 冻结的取消回调、进度事件、实际处理摘要接口（任务要求 4）。
- 其它既有处理只登记，并按“Krita 参数不可修改 / 可调范围变化”的定义复核是否构成编辑性损失；不擅自扩大开关名单（任务要求 4）。

与后续任务的关系（166/167 只定义，不改业务管线）：

- 任务 168 把本模块的 enabled 接进 convert.py，并让 actual 区分
  “现行处理 / 原生替代 / 不适用”；任务 167 的 actual 只能给出
  applied（现行处理）、not_applicable（不适用）或
  pending_alternative（请求关闭但替代路径尚未实现）。
- 任务 169 实现安全写出与整批取消；本模块只冻结 CancelToken 与
  ProgressEvent 的语义，不声称取消已经接入转换。
- 任务 170/171 才让 GUI/CLI 真正使用配置；本模块只保证两侧调用同一套
  校验函数即可得到同一结论。

因此：**本模块的摘要不是一次真实转换的运行记录**，其 evidence 固定为
contract-baseline；未接线的功能一律如实标“未接入（任务 N）”。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .kpp.preset_xml import texture_bc_out_of_range
from .mapping import BrushPreset, TextureSettings

__all__ = [
    "ACTUAL_ALTERNATIVE",
    "ACTUAL_APPLIED",
    "ACTUAL_LABELS",
    "ACTUAL_NOT_APPLICABLE",
    "ACTUAL_NOT_PROCESSED",
    "ACTUAL_PENDING_ALTERNATIVE",
    "CONFIG_FORMAT",
    "CONFIG_VERSION",
    "CANCELLED_PHASE",
    "CancelCallback",
    "CancelToken",
    "ConfigIssue",
    "ConversionCancelled",
    "EXISTING_TREATMENT_NOTES",
    "ExistingTreatmentNote",
    "ExportOptionState",
    "ISSUE_BAD_FIELD",
    "ISSUE_BAD_JSON",
    "ISSUE_DUPLICATE_ID",
    "ISSUE_DUPLICATE_NAME",
    "ISSUE_INAPPLICABLE_ON",
    "ISSUE_SOURCE_MISMATCH",
    "ISSUE_UNKNOWN_ID",
    "ISSUE_UNKNOWN_NAME",
    "ISSUE_UNKNOWN_SWITCH",
    "ISSUE_UNREADABLE",
    "ISSUE_VERSION_MISMATCH",
    "OptionConfigError",
    "PHASE_CONVERT",
    "PHASE_DONE",
    "PHASE_PREPARE",
    "PHASE_WRITE",
    "PROGRESS_PHASES",
    "PresetIdentity",
    "PresetOptionRecord",
    "ProcessingSummary",
    "ProgressEvent",
    "SUMMARY_EVIDENCE_CONTRACT",
    "SUMMARY_EVIDENCE_CONVERSION",
    "SUMMARY_FORMAT",
    "SUMMARY_VERSION",
    "SWITCH_BAKE_TEXTURE_BC",
    "SWITCH_IDS",
    "SWITCH_INFO",
    "SWITCH_PRESCALE_TEXTURE",
    "SWITCH_RASTERIZE_COMPUTED",
    "SwitchApplicability",
    "SwitchInfo",
    "SwitchRecord",
    "build_identities",
    "build_processing_summary",
    "default_switch_values",
    "dump_option_config",
    "load_option_config",
    "normalize_source_digest",
    "option_config_payload",
    "preset_content_digest",
    "preset_identity",
    "resolve_option_config",
    "save_option_config",
    "raise_if_cancel_requested",
    "source_digest_of_bytes",
    "source_digest_of_file",
    "switch_applicability",
    "validate_option_config",
]

# ---------------------------------------------------------------------------
# 冻结常量：开关名单、配置格式与版本（任务 167 定稿，后续任务不得擅自改名）
# ---------------------------------------------------------------------------

#: 计算笔刷位图化：现行路径把计算笔刷按 Hrdn 剖面生成采样笔尖位图（v1.5.0
#: 任务 133），代价是 fade/硬度在 Krita 不可再调。关闭后的原生可编辑分支由
#: 任务 168 实现并独立验证；本轮没有替代实现。
SWITCH_RASTERIZE_COMPUTED = "rasterize_computed"
#: 纹理预缩放：纹理缩放 >100% 时先用 PS 同核预缩放内嵌 PNG 并写 Scale=1.0
#: （v1.5.0 任务 135-1），代价是图案资源不再保持源尺寸与原生缩放字段。
SWITCH_PRESCALE_TEXTURE = "prescale_texture"
#: 亮度越界预烘焙：calibrated 反解超出 Krita 亮度域 [-1,1] 时把 PS 亮度/对比度/
#: 反相烘进纹理像素并写中性字段（v1.5.0 任务 153），代价是该笔的纹理亮度/
#: 对比度/反相在 Krita 里不再有 PS 语义。
SWITCH_BAKE_TEXTURE_BC = "bake_texture_bc"

#: 三项开关的冻结顺序；GUI 勾选顺序、配置字段顺序、摘要顺序都以此为准。
SWITCH_IDS: tuple[str, ...] = (
    SWITCH_RASTERIZE_COMPUTED,
    SWITCH_PRESCALE_TEXTURE,
    SWITCH_BAKE_TEXTURE_BC,
)

#: 显式配置文件格式标识（JSON 顶层 format 字段）。
CONFIG_FORMAT = "brushconverter.export-options"
#: 显式配置文件版本（整数字面量；不兼容变更必须升版本，不得静默兼容）。
CONFIG_VERSION = 1

#: 实际处理摘要的格式与版本（GUI 摘要、未来 CLI 输出共用）。
SUMMARY_FORMAT = "brushconverter.processing-summary"
SUMMARY_VERSION = 1

#: 摘要证据来源：本轮只有契约基线，真实转换运行由任务 168/169 产生。
SUMMARY_EVIDENCE_CONTRACT = "contract-baseline"
SUMMARY_EVIDENCE_CONVERSION = "conversion-run"

#: 实际处理的冻结取值。任务 167 只会产出前三个；后两个留给已完成接线的任务。
ACTUAL_APPLIED = "applied"                     # 现行处理已执行
ACTUAL_NOT_APPLICABLE = "not_applicable"       # 该笔不适用，未执行
ACTUAL_PENDING_ALTERNATIVE = "pending_alternative"  # 请求关闭，替代路径未实现
ACTUAL_ALTERNATIVE = "alternative"             # 已走原生/可编辑替代路径（留给任务 168）
ACTUAL_NOT_PROCESSED = "not_processed"         # 整批取消，该项未处理（留给任务 169）

#: 摘要里的中文标签（GUI 可直接使用；不得把 pending 写成“已关闭”）。
ACTUAL_LABELS: dict[str, str] = {
    ACTUAL_APPLIED: "已执行现行处理",
    ACTUAL_NOT_APPLICABLE: "不适用（未处理）",
    ACTUAL_PENDING_ALTERNATIVE: "待替代路径（本轮仍按现行处理）",
    ACTUAL_ALTERNATIVE: "已走替代路径",
    ACTUAL_NOT_PROCESSED: "未处理（整批取消）",
}

#: 进度事件的冻结阶段名。
PHASE_PREPARE = "prepare"
PHASE_CONVERT = "convert"
PHASE_WRITE = "write"
PHASE_DONE = "done"
CANCELLED_PHASE = "cancelled"
PROGRESS_PHASES: tuple[str, ...] = (
    PHASE_PREPARE,
    PHASE_CONVERT,
    PHASE_WRITE,
    PHASE_DONE,
    CANCELLED_PHASE,
)

#: 配置错误码（供 GUI/CLI/测试按机器可读方式断言；message 保持中文人话）。
ISSUE_BAD_FIELD = "bad_field"
ISSUE_BAD_JSON = "bad_json"
ISSUE_UNREADABLE = "unreadable"
ISSUE_VERSION_MISMATCH = "version_mismatch"
ISSUE_SOURCE_MISMATCH = "source_mismatch"
ISSUE_UNKNOWN_ID = "unknown_id"
ISSUE_UNKNOWN_NAME = "unknown_name"
ISSUE_DUPLICATE_ID = "duplicate_id"
ISSUE_DUPLICATE_NAME = "duplicate_name"
ISSUE_UNKNOWN_SWITCH = "unknown_switch"
ISSUE_INAPPLICABLE_ON = "inapplicable_on"


@dataclass(frozen=True)
class SwitchInfo:
    """单个开关的静态说明（GUI 勾选框旁的后果说明来源）。"""

    switch_id: str
    title: str
    condition: str
    cost: str
    alternative_status: str

    def to_dict(self) -> dict[str, str]:
        return {
            "switch_id": self.switch_id,
            "title": self.title,
            "condition": self.condition,
            "cost": self.cost,
            "alternative_status": self.alternative_status,
        }


#: 三项开关的标题/条件/代价。alternative_status 必须如实写“尚未接入”，
#: 不能在任务 167 就把任务 168 的替代路径说成已可用。
SWITCH_INFO: dict[str, SwitchInfo] = {
    SWITCH_RASTERIZE_COMPUTED: SwitchInfo(
        switch_id=SWITCH_RASTERIZE_COMPUTED,
        title="计算笔刷位图化",
        condition="仅计算笔刷（ABR 无自带位图）适用；采样笔尖不适用。",
        cost="位图化后 fade/硬度在 Krita 不可再调，尺寸字段等于 PS 实际足印。",
        alternative_status="原生可编辑分支尚未接入（任务 168）；本轮关闭只记录意愿。",
    ),
    SWITCH_PRESCALE_TEXTURE: SwitchInfo(
        switch_id=SWITCH_PRESCALE_TEXTURE,
        title="纹理预缩放",
        condition="纹理有图案位图、缩放 >100% 且预缩放后目标边长确实变化时适用。",
        cost="内嵌图案被换成预缩放字节、Scale 写 1.0，图案原生尺寸与缩放字段不再保留。",
        alternative_status="保留源纹理与原生缩放的路径尚未接入（任务 168）；本轮关闭只记录意愿。",
    ),
    SWITCH_BAKE_TEXTURE_BC: SwitchInfo(
        switch_id=SWITCH_BAKE_TEXTURE_BC,
        title="亮度越界预烘焙",
        condition="纹理有图案位图且 calibrated 反解亮度超出 Krita [-1,1] 时适用。",
        cost="亮度/对比度/反相被烘进像素并写中性值，这三个字段在 Krita 里不再有 PS 语义。",
        alternative_status="保留可调原生字段的路径尚未接入（任务 168）；本轮关闭只记录意愿。",
    ),
}


# ---------------------------------------------------------------------------
# 稳定逐支身份：源内容摘要 + 原始序号 + 预设内容摘要
# ---------------------------------------------------------------------------

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: 身份格式版本前缀 bc1；后面的三段依次是源摘要前 12 位、原始序号、内容摘要前 8 位。
#: 序号至少 4 位（超过 9999 支时自然加宽，仍保持同一文件内稳定）。
_PRESET_ID_RE = re.compile(r"^bc1-[0-9a-f]{12}-\d{4,}-\d{8}$")


def normalize_source_digest(value: str) -> str:
    """把 sha256:<64hex> 或裸 64 位十六进制规范化为 sha256:<64hex>。"""
    if not isinstance(value, str):
        raise TypeError("源内容摘要必须是字符串")
    text = value.strip().lower()
    hex_part = text[7:] if text.startswith("sha256:") else text
    if not _HEX64.match(hex_part):
        raise ValueError(f"源内容摘要必须是 sha256 的 64 位小写十六进制，实际 {value!r}")
    return f"sha256:{hex_part}"


def source_digest_of_bytes(data: bytes) -> str:
    """源文件原始字节 → sha256:<64hex>。

    注意：身份绑定的是**源文件内容**，不是显示名、不是筛选后的行号，也不是
    ABR 内部的预设序号本身。ABR 被重新保存（字节变化）即视为新源，旧配置
    必然落空并以 unknown_id 报错，而不是悄悄套到别的笔刷上。
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("source_digest_of_bytes 只接受字节")
    return f"sha256:{hashlib.sha256(bytes(data)).hexdigest()}"


def source_digest_of_file(path: str | Path) -> str:
    """读取源文件并返回其内容摘要（用于解析前/后绑定同一份字节）。"""
    return source_digest_of_bytes(Path(path).read_bytes())


def _normalize_uuid(value: str | None) -> str:
    """与 mapping._norm_uuid 同口径：去 NUL、去空白、转小写、截到 36 字符。"""
    return str(value or "").replace("\x00", "").strip().lower()[:36]


def _digest_array(value: np.ndarray | None) -> dict[str, Any] | None:
    """位图数组 → 可进入规范 JSON 的形状/类型/像素摘要。"""
    if value is None:
        return None
    array = np.ascontiguousarray(np.asarray(value))
    return {
        "shape": [int(n) for n in array.shape],
        "dtype": str(array.dtype),
        "sha256": hashlib.sha256(array.tobytes()).hexdigest(),
    }


def _canonical_number(value: Any) -> float | str | None:
    """把数值规范化为可稳定序列化的形式（NaN/Inf 用字符串，避免 JSON 非法）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number):
        return "nan"
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    return number


def _texture_payload(texture: TextureSettings) -> dict[str, Any]:
    """纹理设置 → 身份规范 JSON 的一部分（不含显示名称，名称不参与身份）。"""
    return {
        "uuid": _normalize_uuid(texture.uuid),
        "scale": _canonical_number(texture.scale),
        "invert": bool(texture.invert),
        "brightness": _canonical_number(texture.brightness),
        "contrast": _canonical_number(texture.contrast),
        "depth": _canonical_number(texture.depth),
        "depth_min": _canonical_number(texture.depth_min),
        "pressure": bool(texture.pressure),
        "blend_mode": str(texture.blend_mode),
        "image": _digest_array(texture.image),
    }


def preset_content_digest(preset: BrushPreset) -> str:
    """预设内容摘要：只取会影响产物的源事实，**不含显示名称**。

    为什么排除 name：本任务的核心验收是“改名不串设置”。若把名字放进摘要，
    GUI 里改显示名就会换 ID，恰好制造本任务要消灭的问题。名字只作为身份
    记录里的展示字段，配置也允许仅用 ID 定位同名笔刷。
    """
    payload = {
        "uuid": _normalize_uuid(preset.uuid),
        "is_computed": bool(preset.is_computed),
        "tip": _digest_array(preset.tip_gray),
        "diameter": _canonical_number(preset.diameter),
        "hardness": _canonical_number(preset.hardness),
        "roundness": _canonical_number(preset.roundness),
        "spacing": _canonical_number(preset.spacing),
        "angle": _canonical_number(preset.angle),
        "scale": _canonical_number(preset.scale),
        "spacing_basis": _canonical_number(preset.spacing_basis),
        "curves": {
            "size": preset.size_curve,
            "opacity": preset.opacity_curve,
            "flow": preset.flow_curve,
            "ratio": preset.ratio_curve,
        },
        "rotation": {
            "sensor": preset.rotation_sensor,
            "jitter": _canonical_number(preset.rotation_jitter),
        },
        "scatter": {
            "enabled": bool(preset.scatter),
            "pressure": bool(preset.scatter_pressure),
            "both_axes": bool(preset.scatter_both_axes),
            "amount": _canonical_number(preset.scatter_amount),
        },
        "texture": _texture_payload(preset.texture) if preset.texture else None,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class PresetIdentity:
    """一支笔刷的稳定身份。

    - preset_id：bc1-<源摘要12>-<原始序号>-<内容摘要8>，永久不复用名字；
    - ordinal：**源顺序**下从 1 开始的序号（不是筛选/排序后的列表行号）；
    - name：载入时的显示名，仅用于展示和“按名定位”的便利；改名不影响 ID。
    """

    preset_id: str
    source_digest: str
    ordinal: int
    content_digest: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.preset_id,
            "source_digest": self.source_digest,
            "ordinal": self.ordinal,
            "content_digest": self.content_digest,
            "name": self.name,
        }


def preset_identity(
    preset: BrushPreset,
    ordinal: int,
    source_digest: str,
    *,
    name: str | None = None,
) -> PresetIdentity:
    """构造单支身份。name 只是展示字段，默认取预设载入名。"""
    source = normalize_source_digest(source_digest)
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise ValueError("原始序号必须是从 1 开始的整数")
    content = preset_content_digest(preset)
    preset_id = (
        f"bc1-{source[7:19]}-{ordinal:04d}-{content[7:15]}"
    )
    return PresetIdentity(
        preset_id=preset_id,
        source_digest=source,
        ordinal=ordinal,
        content_digest=content,
        name=preset.name if name is None else name,
    )


def build_identities(
    presets: Sequence[BrushPreset],
    source_digest: str,
    ordinals: Sequence[int] | None = None,
) -> tuple[PresetIdentity, ...]:
    """为一批预设生成稳定身份。

    ordinals 省略时用 1..N（即解析后、去重后的源顺序）。未来若 mapping
    能提供 ABR 原始顺序，调用方可以显式传入；本任务只要求调用方保持“筛选前
    的源顺序”，绝不允许传搜索/排序后的行号。
    """
    source = normalize_source_digest(source_digest)
    total = len(presets)
    if ordinals is None:
        ordinal_list = list(range(1, total + 1))
    else:
        ordinal_list = list(ordinals)
        if len(ordinal_list) != total:
            raise ValueError("ordinals 数量必须与预设数量一致")
    if len(set(ordinal_list)) != len(ordinal_list):
        raise ValueError("ordinals 不能重复（重复序号会让身份冲突）")
    return tuple(
        preset_identity(preset, ordinal, source, name=preset.name)
        for preset, ordinal in zip(presets, ordinal_list)
    )


# ---------------------------------------------------------------------------
# 三项开关：条件适用性、默认值、三层记录
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwitchApplicability:
    """开关对某支笔刷的适用性判定（只依赖源事实，不依赖勾选）。"""

    switch_id: str
    applicable: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "switch_id": self.switch_id,
            "applicable": self.applicable,
            "reason": self.reason,
        }


def _require_switch_id(switch_id: str) -> None:
    if switch_id not in SWITCH_IDS:
        raise ValueError(f"未知开关 ID {switch_id!r}；冻结名单只有 {SWITCH_IDS}")


def _prescale_target_size(image: np.ndarray, scale_pct: float) -> tuple[int, int]:
    """预缩放目标边长，逐字复刻 convert._prescale_texture_image 的口径。

    convert 里用的是内建 round（银行家舍入）与 max(1, ...)。这里必须
    同样实现，否则“适用性”会和真正执行预缩放的判定分叉（边界样例：16 px
    纹理缩放 103.125% → 16.5 → round 到 16，现行管线保持原图，不应判适用）。
    """
    factor = float(scale_pct) / 100.0
    ih, iw = int(image.shape[0]), int(image.shape[1])
    oh = max(1, int(round(ih * factor)))
    ow = max(1, int(round(iw * factor)))
    return oh, ow


def switch_applicability(preset: BrushPreset, switch_id: str) -> SwitchApplicability:
    """按源事实判定一项开关是否适用，并给出中文理由。"""
    _require_switch_id(switch_id)

    if switch_id == SWITCH_RASTERIZE_COMPUTED:
        if preset.is_computed:
            return SwitchApplicability(
                switch_id, True,
                "计算笔刷（无自带位图），现行路径按硬度剖面生成采样笔尖位图")
        return SwitchApplicability(
            switch_id, False, "采样笔尖自带位图，无需位图化")

    texture = preset.texture
    if texture is None or texture.image is None:
        return SwitchApplicability(
            switch_id, False,
            "无纹理图案位图（现行管线不写纹理，预缩放/烘焙都无从发生）")

    if switch_id == SWITCH_PRESCALE_TEXTURE:
        scale_pct = float(texture.scale)
        if not math.isfinite(scale_pct) or scale_pct <= 100.0:
            return SwitchApplicability(
                switch_id, False,
                f"纹理缩放 {scale_pct:g}% ≤ 100%（或不是有限数），现行管线不做预缩放")
        image = np.asarray(texture.image)
        if image.ndim not in (2, 3):
            return SwitchApplicability(
                switch_id, False,
                f"纹理位图维度异常 shape={image.shape}（现行管线会报错，不做预缩放）")
        if image.size == 0 or image.shape[0] < 1 or image.shape[1] < 1:
            return SwitchApplicability(
                switch_id, False, "纹理位图为空，现行管线不做预缩放")
        try:
            oh, ow = _prescale_target_size(image, scale_pct)
        except (OverflowError, ValueError):
            return SwitchApplicability(
                switch_id, False, f"纹理缩放 {scale_pct:g}% 无法计算目标尺寸")
        if (oh, ow) == (image.shape[0], image.shape[1]):
            return SwitchApplicability(
                switch_id, False,
                f"预缩放后边长仍为 {ow}×{oh}（四舍五入到原尺寸），现行管线保持原图")
        return SwitchApplicability(
            switch_id, True,
            f"纹理缩放 {scale_pct:g}% > 100% 且目标边长变为 {ow}×{oh}，现行管线预缩放")

    # SWITCH_BAKE_TEXTURE_BC
    if texture_bc_out_of_range(texture.brightness, texture.contrast):
        return SwitchApplicability(
            switch_id, True,
            "亮度/对比度反解超出 Krita 亮度域 [-1,1]，现行管线预烘焙")
    return SwitchApplicability(
        switch_id, False,
        "亮度/对比度在 Krita 可表达范围内，现行管线写原生字段")


def default_switch_values(preset: BrushPreset) -> dict[str, bool]:
    """三项开关的条件默认值：适用 ⇒ 默认勾选（沿用现行外观优先处理）。"""
    return {
        switch_id: switch_applicability(preset, switch_id).applicable
        for switch_id in SWITCH_IDS
    }


@dataclass(frozen=True)
class SwitchRecord:
    """一项开关的三层记录：适用性、勾选状态、实际处理。"""

    switch_id: str
    applicable: bool
    applicable_reason: str
    enabled: bool
    default_enabled: bool
    actual: str
    actual_reason: str
    pending_alternative: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "applicable": self.applicable,
            "applicable_reason": self.applicable_reason,
            "enabled": self.enabled,
            "default_enabled": self.default_enabled,
            "actual": self.actual,
            "actual_reason": self.actual_reason,
            "pending_alternative": self.pending_alternative,
        }


@dataclass(frozen=True)
class PresetOptionRecord:
    """单支笔刷的完整选项记录（摘要与 GUI 共用）。"""

    identity: PresetIdentity
    switches: Mapping[str, SwitchRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.identity.preset_id,
            "name": self.identity.name,
            "ordinal": self.identity.ordinal,
            "switches": {
                switch_id: self.switches[switch_id].to_dict()
                for switch_id in SWITCH_IDS
            },
        }


def _actual_for(
    applicability: SwitchApplicability, enabled: bool
) -> tuple[str, str, bool]:
    """三层记录的第三层：本轮实际会发生什么（不照抄勾选状态）。"""
    if not applicability.applicable:
        return (
            ACTUAL_NOT_APPLICABLE,
            applicability.reason,
            False,
        )
    if enabled:
        return (
            ACTUAL_APPLIED,
            "现行处理（与任务 167 开工基线一致）",
            False,
        )
    return (
        ACTUAL_PENDING_ALTERNATIVE,
        "已请求关闭，但替代路径尚未接入（任务 168）；本轮实际仍执行现行处理",
        True,
    )


class ExportOptionState:
    """一份源文件在本次载入期间的逐支选项状态。

    生命周期纪律（任务要求 3）：

    - 新建/换文件/重新载入都调用 retarget 或新建实例，**手动覆盖一律
      清空**，条件默认值按新源重算；
    - 搜索、筛选、排序只影响展示，状态只按 preset_id 存取，因此不会因为
      列表行号变化或名字相同而串设置；
    - 不适用项不能通过 set_override 或配置文件强开（会报
      inapplicable_on）。
    """

    def __init__(
        self,
        presets: Sequence[BrushPreset],
        identities: Sequence[PresetIdentity],
        source_digest: str | None = None,
    ) -> None:
        if len(presets) != len(identities):
            raise ValueError("presets 与 identities 数量必须一致")
        if source_digest is None:
            if not identities:
                raise ValueError("identities 为空时必须显式提供 source_digest")
            source_digest = identities[0].source_digest
        self._init_from(list(presets), list(identities), source_digest)

    def _init_from(
        self,
        presets: list[BrushPreset],
        identities: list[PresetIdentity],
        source_digest: str,
    ) -> None:
        source = normalize_source_digest(source_digest)
        if any(item.source_digest != source for item in identities):
            raise ValueError("identities 的源摘要与状态源摘要不一致")
        preset_by_id: dict[str, BrushPreset] = {}
        identity_by_id: dict[str, PresetIdentity] = {}
        for preset, identity in zip(presets, identities):
            if identity.preset_id in preset_by_id:
                raise ValueError(f"身份重复：{identity.preset_id}")
            preset_by_id[identity.preset_id] = preset
            identity_by_id[identity.preset_id] = identity
        self._presets = presets
        self._identities = tuple(identities)
        self._source_digest = source
        self._preset_by_id = preset_by_id
        self._identity_by_id = identity_by_id
        self._order = tuple(item.preset_id for item in identities)
        self._by_name: dict[str, list[PresetIdentity]] = {}
        for identity in identities:
            self._by_name.setdefault(identity.name, []).append(identity)
        self._applicability: dict[str, dict[str, SwitchApplicability]] = {
            preset_id: {
                switch_id: switch_applicability(preset_by_id[preset_id], switch_id)
                for switch_id in SWITCH_IDS
            }
            for preset_id in self._order
        }
        self._defaults: dict[str, dict[str, bool]] = {
            preset_id: {
                switch_id: self._applicability[preset_id][switch_id].applicable
                for switch_id in SWITCH_IDS
            }
            for preset_id in self._order
        }
        self._overrides: dict[tuple[str, str], bool] = {}

    @classmethod
    def create(
        cls,
        presets: Sequence[BrushPreset],
        source_digest: str,
        ordinals: Sequence[int] | None = None,
    ) -> "ExportOptionState":
        """从解析结果直接建立状态（GUI/CLI 的推荐入口）。"""
        identities = build_identities(presets, source_digest, ordinals)
        return cls(presets, identities, source_digest)

    # ---- 只读视图 ----

    @property
    def source_digest(self) -> str:
        return self._source_digest

    @property
    def identities(self) -> tuple[PresetIdentity, ...]:
        return self._identities

    @property
    def preset_ids(self) -> tuple[str, ...]:
        return self._order

    def identity(self, preset_id: str) -> PresetIdentity:
        self._require_preset(preset_id)
        return self._identity_by_id[preset_id]

    def preset(self, preset_id: str) -> BrushPreset:
        self._require_preset(preset_id)
        return self._preset_by_id[preset_id]

    def applicability(self, preset_id: str, switch_id: str) -> SwitchApplicability:
        self._require_preset(preset_id)
        _require_switch_id(switch_id)
        return self._applicability[preset_id][switch_id]

    def default_enabled(self, preset_id: str, switch_id: str) -> bool:
        self._require_preset(preset_id)
        _require_switch_id(switch_id)
        return self._defaults[preset_id][switch_id]

    def enabled(self, preset_id: str, switch_id: str) -> bool:
        """当前勾选状态 = 手动覆盖（若有）否则条件默认值。"""
        self._require_preset(preset_id)
        _require_switch_id(switch_id)
        return self._overrides.get((preset_id, switch_id), self._defaults[preset_id][switch_id])

    def has_override(self, preset_id: str, switch_id: str) -> bool:
        self._require_preset(preset_id)
        _require_switch_id(switch_id)
        return (preset_id, switch_id) in self._overrides

    # ---- 写入与生命周期 ----

    def reset(self) -> None:
        """重新载入同一份源时清空全部手动覆盖（条件默认值重算/保留不变）。"""
        self._overrides.clear()

    def retarget(
        self,
        presets: Sequence[BrushPreset],
        source_digest: str,
        ordinals: Sequence[int] | None = None,
    ) -> None:
        """换文件或重新载入：重建身份与默认值，并**无条件清空**手动覆盖。"""
        identities = build_identities(presets, source_digest, ordinals)
        self._init_from(list(presets), list(identities), source_digest)

    def set_override(self, preset_id: str, switch_id: str, enabled: bool) -> None:
        """设置单项勾选；不适用项设为 True 会报 inapplicable_on。"""
        if type(enabled) is not bool:
            raise TypeError("开关值必须是布尔（True/False）")
        self._require_preset(preset_id)
        _require_switch_id(switch_id)
        if enabled and not self._applicability[preset_id][switch_id].applicable:
            raise OptionConfigError([
                ConfigIssue(
                    ISSUE_INAPPLICABLE_ON,
                    f"开关 {switch_id} 对「{self.identity(preset_id).name}」不适用"
                    f"（{self._applicability[preset_id][switch_id].reason}），不能设为 true",
                    f"preset {preset_id}",
                )
            ])
        if enabled == self._defaults[preset_id][switch_id]:
            self._overrides.pop((preset_id, switch_id), None)
        else:
            self._overrides[(preset_id, switch_id)] = enabled

    def clear_override(self, preset_id: str, switch_id: str) -> None:
        """恢复该项为条件默认值（不触发不适用校验）。"""
        self._require_preset(preset_id)
        _require_switch_id(switch_id)
        self._overrides.pop((preset_id, switch_id), None)

    # ---- 配置读写（GUI/CLI 共用入口） ----

    def apply_config(self, config: Any) -> int:
        """校验并应用一份显式配置（会**替换**此前的全部手动覆盖）。

        返回被配置点名的预设条目数。任何错误都会抛 OptionConfigError，
        且不会留下“部分应用”的中间状态（先完整校验，再一次性替换）。
        """
        resolved = resolve_option_config(
            config,
            self._identities,
            source_digest=self._source_digest,
            presets_by_id=self._preset_by_id,
        )
        self._apply_resolved(resolved)
        return len(resolved.entries)

    def load_config(self, path: str | Path) -> int:
        """从文件加载并应用配置（UTF-8；允许 BOM），语义同 apply_config。"""
        resolved = load_option_config(
            path,
            self._identities,
            source_digest=self._source_digest,
            presets_by_id=self._preset_by_id,
        )
        self._apply_resolved(resolved)
        return len(resolved.entries)

    def _apply_resolved(self, resolved: "ResolvedOptionConfig") -> None:
        self._overrides.clear()
        for entry in resolved.entries:
            for switch_id in SWITCH_IDS:
                if switch_id in entry.switches:
                    enabled = entry.switches[switch_id]
                    if enabled != self._defaults[entry.preset_id][switch_id]:
                        self._overrides[(entry.preset_id, switch_id)] = enabled

    def dump_config(self) -> str:
        """导出当前勾选为显式配置文本（确定性、无时间戳、无本机路径）。"""
        return dump_option_config(self)

    def save_config(self, path: str | Path) -> None:
        """把当前勾选写为配置文件（UTF-8、LF、末尾换行）。"""
        save_option_config(path, self)

    # ---- 记录与摘要 ----

    def record(self, preset_id: str) -> PresetOptionRecord:
        self._require_preset(preset_id)
        switches: dict[str, SwitchRecord] = {}
        for switch_id in SWITCH_IDS:
            applicability = self._applicability[preset_id][switch_id]
            enabled = self.enabled(preset_id, switch_id)
            actual, actual_reason, pending = _actual_for(applicability, enabled)
            switches[switch_id] = SwitchRecord(
                switch_id=switch_id,
                applicable=applicability.applicable,
                applicable_reason=applicability.reason,
                enabled=enabled,
                default_enabled=self._defaults[preset_id][switch_id],
                actual=actual,
                actual_reason=actual_reason,
                pending_alternative=pending,
            )
        return PresetOptionRecord(
            identity=self._identity_by_id[preset_id],
            switches=switches,
        )

    def records(self, preset_ids: Sequence[str] | None = None) -> tuple[PresetOptionRecord, ...]:
        """按源顺序（或调用方给定的展示顺序）返回记录；筛选不改变任何状态。"""
        if preset_ids is None:
            return tuple(self.record(preset_id) for preset_id in self._order)
        return tuple(self.record(preset_id) for preset_id in preset_ids)

    def processing_summary(self, cancelled: bool = False) -> "ProcessingSummary":
        """生成实际处理摘要（本轮 evidence=contract-baseline，不是运行记录）。"""
        return build_processing_summary(self, cancelled=cancelled)

    # ---- 内部 ----

    def _require_preset(self, preset_id: str) -> None:
        if preset_id not in self._preset_by_id:
            raise OptionConfigError([
                ConfigIssue(
                    ISSUE_UNKNOWN_ID,
                    f"未知笔刷 ID {preset_id!r}（配置与当前源文件不匹配？）",
                    preset_id,
                )
            ])


# ---------------------------------------------------------------------------
# 显式配置：格式、校验、错误、加载与导出
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigIssue:
    """一条配置错误（机器可读 code + 中文 message + JSON 路径）。"""

    code: str
    message: str
    location: str = ""

    def render(self) -> str:
        prefix = f"{self.location}: " if self.location else ""
        return f"[{self.code}] {prefix}{self.message}"


class OptionConfigError(ValueError):
    """配置文件/逐支选项不合法。issues 保留全部错误，不会只报第一条。"""

    def __init__(self, issues: Sequence[ConfigIssue]) -> None:
        self.issues = tuple(issues)
        detail = "；".join(issue.render() for issue in self.issues) or "未知错误"
        super().__init__(f"导出选项配置无效：{detail}")


@dataclass(frozen=True)
class ResolvedPresetEntry:
    """配置解析后的单支结果：目标 ID + 显式写出的开关值。"""

    preset_id: str
    switches: Mapping[str, bool]


@dataclass(frozen=True)
class ResolvedOptionConfig:
    """校验通过的配置（尚未应用，应用不读取勾选快照以外的任何状态）。"""

    source_digest: str | None
    entries: tuple[ResolvedPresetEntry, ...]


def _top_level_keys() -> set[str]:
    return {"format", "version", "source_digest", "presets"}


def _entry_keys() -> set[str]:
    return {"id", "name", "switches"}


def _collect_option_issues(
    config: Any,
    identities: Sequence[PresetIdentity],
    source_digest: str | None,
    presets_by_id: Mapping[str, BrushPreset] | None,
) -> tuple[list[ConfigIssue], ResolvedOptionConfig | None]:
    """收集全部配置错误；结构合法时同时返回解析结果。"""
    issues: list[ConfigIssue] = []

    def add(code: str, message: str, location: str = "") -> None:
        issues.append(ConfigIssue(code, message, location))

    current_source: str | None = None
    if source_digest is not None:
        current_source = normalize_source_digest(source_digest)

    if not isinstance(config, dict):
        add(ISSUE_BAD_FIELD, "配置根必须是 JSON 对象", "$")
        return issues, None

    for key in config:
        if key not in _top_level_keys():
            add(ISSUE_BAD_FIELD, f"未知顶层字段 {key!r}；只允许 {sorted(_top_level_keys())}", "$")

    fmt = config.get("format")
    if fmt is None:
        add(ISSUE_BAD_FIELD, f"缺少 format 字段（固定为 {CONFIG_FORMAT!r}）", "$.format")
    elif fmt != CONFIG_FORMAT:
        add(ISSUE_BAD_FIELD, f"format 必须为 {CONFIG_FORMAT!r}，实际 {fmt!r}", "$.format")

    version = config.get("version")
    if version is None:
        add(ISSUE_BAD_FIELD, f"缺少 version 字段（当前为 {CONFIG_VERSION}）", "$.version")
    elif type(version) is not int or version != CONFIG_VERSION:
        add(
            ISSUE_VERSION_MISMATCH,
            f"配置版本不受支持：期望 {CONFIG_VERSION}，实际 {version!r}",
            "$.version",
        )

    configured_source: str | None = None
    raw_source = config.get("source_digest")
    if raw_source is not None:
        if not isinstance(raw_source, str):
            add(ISSUE_BAD_FIELD, "source_digest 必须是字符串", "$.source_digest")
        else:
            try:
                configured_source = normalize_source_digest(raw_source)
            except ValueError as exc:
                add(ISSUE_BAD_FIELD, f"source_digest 不是合法摘要：{exc}", "$.source_digest")
            if configured_source is not None and current_source is not None:
                if configured_source != current_source:
                    add(
                        ISSUE_SOURCE_MISMATCH,
                        f"配置来自另一份源文件（配置 {configured_source}，当前 {current_source}）",
                        "$.source_digest",
                    )

    by_id: dict[str, PresetIdentity] = {}
    for identity in identities:
        if identity.preset_id in by_id:
            raise ValueError(f"identities 出现重复 ID：{identity.preset_id}")
        by_id[identity.preset_id] = identity
    by_name: dict[str, list[PresetIdentity]] = {}
    for identity in identities:
        by_name.setdefault(identity.name, []).append(identity)

    raw_presets = config.get("presets")
    entries: list[ResolvedPresetEntry] = []
    if raw_presets is None:
        add(ISSUE_BAD_FIELD, "缺少 presets 字段（可以为空数组）", "$.presets")
        raw_presets = []
    elif not isinstance(raw_presets, list):
        add(ISSUE_BAD_FIELD, "presets 必须是 JSON 数组", "$.presets")
        raw_presets = []

    seen_ids: set[str] = set()
    seen_names: set[str] = set()

    for index, raw_entry in enumerate(raw_presets):
        location = f"$.presets[{index}]"
        if not isinstance(raw_entry, dict):
            add(ISSUE_BAD_FIELD, "条目必须是 JSON 对象", location)
            continue
        for key in raw_entry:
            if key not in _entry_keys():
                add(ISSUE_BAD_FIELD, f"未知条目字段 {key!r}；只允许 id/name/switches", location)

        raw_id = raw_entry.get("id")
        raw_name = raw_entry.get("name")
        id_ok = isinstance(raw_id, str) and bool(raw_id)
        name_ok = isinstance(raw_name, str) and bool(raw_name)
        if raw_id is not None and not id_ok:
            add(ISSUE_BAD_FIELD, "id 必须是非空字符串", f"{location}.id")
        if raw_name is not None and not name_ok:
            add(ISSUE_BAD_FIELD, "name 必须是非空字符串", f"{location}.name")
        if raw_id is None and raw_name is None:
            add(ISSUE_BAD_FIELD, "条目必须至少提供 id 或 name；同名笔刷必须用 id", location)
            continue

        target: PresetIdentity | None = None
        if id_ok:
            target = by_id.get(raw_id)
            if target is None:
                add(
                    ISSUE_UNKNOWN_ID,
                    f"未知笔刷 ID {raw_id!r}（配置与当前源文件不匹配？）",
                    f"{location}.id",
                )
            elif name_ok and raw_name != target.name:
                add(
                    ISSUE_BAD_FIELD,
                    f"name {raw_name!r} 与 ID 对应笔刷的载入名 {target.name!r} 不一致",
                    f"{location}.name",
                )
        elif name_ok:
            matches = by_name.get(raw_name, [])
            if not matches:
                add(ISSUE_UNKNOWN_NAME, f"未知笔刷名称 {raw_name!r}", f"{location}.name")
            elif len(matches) > 1:
                add(
                    ISSUE_DUPLICATE_NAME,
                    f"名称 {raw_name!r} 对应 {len(matches)} 支笔刷（同名不可唯一确定），请改用稳定 ID",
                    f"{location}.name",
                )
            else:
                target = matches[0]

        if name_ok:
            if raw_name in seen_names:
                add(
                    ISSUE_DUPLICATE_NAME,
                    f"配置文件里重复出现名称 {raw_name!r}；同名笔刷请只写 id",
                    f"{location}.name",
                )
            seen_names.add(raw_name)

        if target is not None:
            if target.preset_id in seen_ids:
                add(ISSUE_DUPLICATE_ID, f"配置文件里重复出现 ID {target.preset_id!r}", f"{location}.id")
            seen_ids.add(target.preset_id)

        raw_switches = raw_entry.get("switches")
        if raw_switches is None:
            add(ISSUE_BAD_FIELD, "条目缺少 switches 字段", location)
            raw_switches = {}
        elif not isinstance(raw_switches, dict):
            add(ISSUE_BAD_FIELD, "switches 必须是 JSON 对象", f"{location}.switches")
            raw_switches = {}

        resolved_switches: dict[str, bool] = {}
        for switch_id, value in raw_switches.items():
            switch_location = f"{location}.switches.{switch_id}"
            if switch_id not in SWITCH_IDS:
                add(ISSUE_UNKNOWN_SWITCH, f"未知开关 ID {switch_id!r}", switch_location)
                continue
            if type(value) is not bool:
                add(
                    ISSUE_BAD_FIELD,
                    f"开关 {switch_id} 的值必须是 JSON 布尔 true/false，实际 {value!r}",
                    switch_location,
                )
                continue
            if value and target is not None and presets_by_id is not None:
                preset = presets_by_id.get(target.preset_id)
                if preset is not None:
                    applicability = switch_applicability(preset, switch_id)
                    if not applicability.applicable:
                        add(
                            ISSUE_INAPPLICABLE_ON,
                            f"开关 {switch_id} 对「{target.name}」不适用"
                            f"（{applicability.reason}），不能设为 true",
                            switch_location,
                        )
                        continue
            resolved_switches[switch_id] = value

        if target is not None:
            entries.append(ResolvedPresetEntry(target.preset_id, resolved_switches))

    if issues:
        return issues, None
    return [], ResolvedOptionConfig(configured_source, tuple(entries))


def validate_option_config(
    config: Any,
    identities: Sequence[PresetIdentity],
    source_digest: str | None = None,
    presets_by_id: Mapping[str, BrushPreset] | None = None,
) -> list[ConfigIssue]:
    """校验配置并返回全部错误（空列表=通过）；不抛 OptionConfigError。

    GUI/CLI 共用：GUI 可把每条 ConfigIssue.render() 直接显示；CLI 用
    code 决定退出码。传入 presets_by_id 时才会执行“不适用不可强开”
    检查（需要 BrushPreset 才能算适用性）。
    """
    issues, _ = _collect_option_issues(config, identities, source_digest, presets_by_id)
    return issues


def resolve_option_config(
    config: Any,
    identities: Sequence[PresetIdentity],
    source_digest: str | None = None,
    presets_by_id: Mapping[str, BrushPreset] | None = None,
) -> ResolvedOptionConfig:
    """校验并解析配置；有任何错误就抛 OptionConfigError（不静默用默认值）。"""
    issues, resolved = _collect_option_issues(config, identities, source_digest, presets_by_id)
    if issues or resolved is None:
        raise OptionConfigError(issues)
    return resolved


def load_option_config(
    path: str | Path,
    identities: Sequence[PresetIdentity],
    source_digest: str | None = None,
    presets_by_id: Mapping[str, BrushPreset] | None = None,
) -> ResolvedOptionConfig:
    """读取 UTF-8（可带 BOM）JSON 配置并校验。

    读不到文件、JSON 语法错、字段错都转成 OptionConfigError，由调用方显示
    原文；绝不“读失败就用默认值继续”。
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise OptionConfigError([
            ConfigIssue(ISSUE_UNREADABLE, f"无法读取配置文件 {file_path}: {exc}", str(file_path))
        ]) from exc
    try:
        config = json.loads(text)
    except json.JSONDecodeError as exc:
        location = f"{file_path}:{exc.lineno}:{exc.colno}"
        raise OptionConfigError([
            ConfigIssue(ISSUE_BAD_JSON, f"JSON 解析失败：{exc.msg}", location)
        ]) from exc
    return resolve_option_config(config, identities, source_digest, presets_by_id)


def option_config_payload(state: ExportOptionState) -> dict[str, Any]:
    """当前勾选 → 配置字典（只写适用开关；只写 ID，保证同名笔刷可重载）。"""
    presets: list[dict[str, Any]] = []
    for record in state.records():
        switches = {
            switch_id: record.switches[switch_id].enabled
            for switch_id in SWITCH_IDS
            if record.switches[switch_id].applicable
        }
        if switches:
            presets.append({"id": record.identity.preset_id, "switches": switches})
    return {
        "format": CONFIG_FORMAT,
        "version": CONFIG_VERSION,
        "source_digest": state.source_digest,
        "presets": presets,
    }


def dump_option_config(state: ExportOptionState) -> str:
    """导出配置文本：UTF-8 JSON、键序固定、无时间戳/无本机路径、确定性。"""
    payload = option_config_payload(state)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def save_option_config(path: str | Path, state: ExportOptionState) -> None:
    """把配置写到指定路径（UTF-8、LF）。父目录必须已存在，避免误建目录。"""
    Path(path).write_text(dump_option_config(state), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# 取消回调与进度事件（冻结接口；真正接入转换在任务 169/172）
# ---------------------------------------------------------------------------


class ConversionCancelled(Exception):
    """整批转换被取消。转换器必须停止且不发布半成品（写出策略归任务 169）。"""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        message = f"转换已取消：{reason}" if reason else "转换已取消"
        super().__init__(message)


#: 冻结的取消回调签名：无参、返回 True 表示“已请求取消”。
#: 转换器可在每支笔开始前调用；GUI/CLI 可传 lambda: token.cancelled。
CancelCallback = Callable[[], bool]


class CancelToken:
    """可轮询的取消令牌。

    冻结语义：

    - cancel() 幂等，保留**第一次**取消原因；
    - 转换器在每支笔开始前调用 raise_if_cancelled()，已开始的那支允许
      做完内存产物，但不据此发布文件；
    - 令牌不持有线程/进程句柄，也不强杀 Krita/PS（铁律）。
    """

    __slots__ = ("_reason",)

    def __init__(self) -> None:
        self._reason: str | None = None

    def cancel(self, reason: str = "") -> None:
        if self._reason is None:
            self._reason = reason.strip() or "用户取消"

    @property
    def cancelled(self) -> bool:
        return self._reason is not None

    @property
    def reason(self) -> str:
        return self._reason or ""

    def raise_if_cancelled(self) -> None:
        if self._reason is not None:
            raise ConversionCancelled(self._reason)


def raise_if_cancel_requested(callback: CancelCallback | None) -> None:
    """冻结的取消回调适配：回调返回 True 即抛 ConversionCancelled。

    callback=None 表示调用方没有取消能力（例如一次性 CLI 转换），不报错。
    转换器应在每支笔开始前调用本函数；它只轮询，不持有线程/进程句柄。
    """
    if callback is not None and callback():
        raise ConversionCancelled("取消回调返回 True")


@dataclass(frozen=True)
class ProgressEvent:
    """一次进度事件（GUI 线程只消费，不反写转换状态）。

    - phase 取 PROGRESS_PHASES；
    - completed/total 以“支”为单位，done 时 completed==total；
    - cancelled 只表示事件产生时令牌已被取消，不表示文件已清理。
    """

    phase: str
    total: int
    completed: int
    preset_id: str | None = None
    message: str = ""
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "total": self.total,
            "completed": self.completed,
            "preset_id": self.preset_id,
            "message": self.message,
            "cancelled": self.cancelled,
        }


#: 进度回调签名。回调必须快速返回；不得在回调里取消线程/进程。
ProgressCallback = Callable[[ProgressEvent], None]


def validate_progress_event(event: ProgressEvent) -> None:
    """校验事件自洽性；不合法是编程错误，抛 ValueError（不是静默丢弃）。"""
    if event.phase not in PROGRESS_PHASES:
        raise ValueError(f"未知进度阶段 {event.phase!r}；只允许 {PROGRESS_PHASES}")
    if isinstance(event.total, bool) or not isinstance(event.total, int) or event.total < 0:
        raise ValueError(f"total 必须是非负整数，实际 {event.total!r}")
    if isinstance(event.completed, bool) or not isinstance(event.completed, int) or event.completed < 0:
        raise ValueError(f"completed 必须是非负整数，实际 {event.completed!r}")
    if event.completed > event.total:
        raise ValueError(f"completed={event.completed} 不能大于 total={event.total}")
    if event.phase == PHASE_DONE and event.completed != event.total:
        raise ValueError("done 阶段的 completed 必须等于 total")
    if event.preset_id is not None and not isinstance(event.preset_id, str):
        raise ValueError("preset_id 必须是字符串或 None")


def notify_progress(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    """校验后把事件交给回调；callback=None 表示界面不订阅进度。"""
    validate_progress_event(event)
    if callback is not None:
        callback(event)


# ---------------------------------------------------------------------------
# 实际处理摘要（冻结接口；本轮不是运行记录）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessingSummary:
    """一次转换/契约基线的实际处理摘要。

    三层数据都在 items 的每个开关里：applicable（源事实）、
    enabled（用户勾选）、actual（真正发生/将发生什么）。三者不得合并。
    """

    source_digest: str
    cancelled: bool
    evidence: str
    items: tuple[PresetOptionRecord, ...]

    def counts(self) -> dict[str, int]:
        applicable = enabled = applied = alternative = pending = 0
        not_applicable = not_processed = 0
        for record in self.items:
            for switch_id in SWITCH_IDS:
                switch = record.switches[switch_id]
                if switch.applicable:
                    applicable += 1
                else:
                    not_applicable += 1
                if switch.enabled:
                    enabled += 1
                if switch.actual == ACTUAL_APPLIED:
                    applied += 1
                elif switch.actual == ACTUAL_ALTERNATIVE:
                    alternative += 1
                elif switch.actual == ACTUAL_PENDING_ALTERNATIVE:
                    pending += 1
                elif switch.actual == ACTUAL_NOT_PROCESSED:
                    not_processed += 1
        return {
            "total_presets": len(self.items),
            "applicable": applicable,
            "enabled": enabled,
            "applied": applied,
            "alternative": alternative,
            "pending_alternative": pending,
            "not_applicable": not_applicable,
            "not_processed": not_processed,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": SUMMARY_FORMAT,
            "version": SUMMARY_VERSION,
            "source_digest": self.source_digest,
            "cancelled": self.cancelled,
            "evidence": self.evidence,
            "counts": self.counts(),
            "presets": [record.to_dict() for record in self.items],
        }

    def format_lines(self) -> list[str]:
        """中文摘要行（GUI 状态区可直接显示；不把 pending 写成已关闭）。"""
        counts = self.counts()
        lines = [
            f"共 {counts['total_presets']} 支笔刷：开关适用 {counts['applicable']} 项，"
            f"勾选 {counts['enabled']} 项，已执行现行处理 {counts['applied']} 项，"
            f"待替代路径 {counts['pending_alternative']} 项。"
        ]
        for record in self.items:
            for switch_id in SWITCH_IDS:
                switch = record.switches[switch_id]
                if not switch.applicable:
                    continue
                lines.append(
                    f"· {record.identity.name}：{SWITCH_INFO[switch_id].title}"
                    f"（适用，勾选={'是' if switch.enabled else '否'}，"
                    f"实际={ACTUAL_LABELS[switch.actual]}）"
                )
        return lines


def build_processing_summary(
    state: ExportOptionState, *, cancelled: bool = False
) -> ProcessingSummary:
    """从当前选项状态生成摘要。

    注意：任务 167 没有把选项接进 convert.py，因此这里的 actual 是
    “现行管线会做什么”的契约基线，而非一次真实运行的观测；真实运行摘要由
    任务 168/169 在接线后以 evidence=conversion-run 生成。
    """
    return ProcessingSummary(
        source_digest=state.source_digest,
        cancelled=bool(cancelled),
        evidence=SUMMARY_EVIDENCE_CONTRACT,
        items=state.records(),
    )


# ---------------------------------------------------------------------------
# 相关既有处理登记（按使用者的“牺牲可编辑性”定义复核）
#
# 定义（使用者 2026-11-24 明确）：牺牲可编辑性 = 在 Krita 里不能再修改某个
# 参数，或者该参数的可调节范围发生变化。仅改变数值映射、写入常量、改变位图
# 几何或缺少对应模式，本身不构成编辑性开关；以下 4 项均不符合该定义，只登记
# 证据与影响，不新增开关、不改代码。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingTreatmentNote:
    """一处与编辑性开关相关的现行处理（复核后判定**不构成**编辑性损失）。

    editability_loss=False 表示按上述定义不满足“不能再修改/范围变化”，因此
    不进入三项开关名单；switch_id 恒为 None。保留说明是为了 168/187 追溯，
    不是待裁决的候选开关。
    """

    key: str
    title: str
    evidence: str
    behavior: str
    impact: str
    reason: str
    editability_loss: bool
    switch_id: str | None = None
    decision: str = "仅登记（2026-11-24 使用者裁决：不扩开关）"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "evidence": self.evidence,
            "behavior": self.behavior,
            "impact": self.impact,
            "reason": self.reason,
            "editability_loss": self.editability_loss,
            "switch_id": self.switch_id,
            "decision": self.decision,
        }


#: 与三项开关同域的既有处理。它们会改变外观或数值，但按定义都不满足
#: “Krita 参数不可修改 / 可调范围变化”；只登记证据，不新增开关。
EXISTING_TREATMENT_NOTES: tuple[ExistingTreatmentNote, ...] = (
    ExistingTreatmentNote(
        key="height_strength_gain",
        title="高度系纹理强度 ×1.2 并封顶 1.0",
        evidence="src/brush_converter/convert.py:63-90",
        behavior="高度系 Strength = min(1.0, 1.2 × PS depth/100)",
        impact="PS 深度 >83% 时多个深度值都写到 Strength=1.0。",
        reason="Krita 的 Strength 仍可修改、0–1 可调范围未变；变化的是 PS 深度到 Strength 的数值映射，属外观校准，不是字段不可调或范围变化。",
        editability_loss=False,
    ),
    ExistingTreatmentNote(
        key="hardmix_full_coverage_bias",
        title="hardMix 满覆盖时强制 Strength=1.0 并偏置亮度 ±1/255",
        evidence="src/brush_converter/convert.py:327-340",
        behavior="hardMix + 满覆盖 + 恒定深度时覆盖 Strength/Brightness 字段",
        impact="该笔的 PS 深度/亮度原值没有随预设传递，被常量替代。",
        reason="Krita 的 Strength/Brightness 字段仍可修改、范围未变；这是写入数值被覆盖、PS 原值未保留，不是参数被禁用或可调范围变化。",
        editability_loss=False,
    ),
    ExistingTreatmentNote(
        key="sampled_tip_square_padding",
        title="非方形采样笔尖补白成正方形（边框 255）",
        evidence="src/brush_converter/convert.py:43-59",
        behavior="非方形笔尖居中补白到 max(宽,高)，间距另乘短边/长边补偿",
        impact="Krita 尺寸/间距基准改为长边；补白本身按铁律禁止改动。",
        reason="Krita 的 Size/Spacing 仍可修改、范围未变；补白改变的是笔尖位图几何与间距基准，属铁律固定的既有处理，不是参数不可调。",
        editability_loss=False,
    ),
    ExistingTreatmentNote(
        key="unmappable_texture_modes",
        title="不可映射/塌缩纹理混合模式回退到已登记模式",
        evidence="src/brush_converter/mapping.py:39-95",
        behavior="无 Krita 算子或 PS 无真值的模式回退到 Multiply/减去的已实测条目",
        impact="Krita 界面显示回退模式，PS 原模式语义不可恢复。",
        reason="Krita 纹理模式控件仍可修改（仅限其 16 种枚举）；PS 模式无对应是能力缺失与映射结论，不是该控件被禁用或可调范围变化。",
        editability_loss=False,
    ),
)
