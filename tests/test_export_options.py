# -*- coding: utf-8 -*-
"""任务 167：逐支选项契约的定向测试。

覆盖四组验收目标：

1. 稳定身份：同名笔刷可独立定位；改名、筛选、排序不串设置；同一源内重复
   预设也各有身份。
2. 三项开关：计算/采样、预缩放边界（含银行家舍入）、亮度越界 ±127/±128
   边界；适用性/勾选/实际处理三层分开，不适用不可强开。
3. 坏配置：重复名字、未知 ID、坏字段、版本/源不匹配都必须报明错误，且
   不静默用默认值、不部分应用。
4. 冻结接口：取消令牌、进度事件、实际处理摘要、配置版本与“只登记不扩项”
   的可编辑性登记表。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from brush_converter.export_options import (  # noqa: E402
    ACTUAL_ALTERNATIVE,
    ACTUAL_APPLIED,
    ACTUAL_NOT_APPLICABLE,
    ACTUAL_NOT_PROCESSED,
    ACTUAL_PENDING_ALTERNATIVE,
    CANCELLED_PHASE,
    CONFIG_FORMAT,
    CONFIG_VERSION,
    EXISTING_TREATMENT_NOTES,
    ISSUE_BAD_FIELD,
    ISSUE_BAD_JSON,
    ISSUE_DUPLICATE_ID,
    ISSUE_DUPLICATE_NAME,
    ISSUE_INAPPLICABLE_ON,
    ISSUE_SOURCE_MISMATCH,
    ISSUE_UNKNOWN_ID,
    ISSUE_UNKNOWN_NAME,
    ISSUE_UNKNOWN_SWITCH,
    ISSUE_UNREADABLE,
    ISSUE_VERSION_MISMATCH,
    PHASE_CONVERT,
    PHASE_DONE,
    PHASE_PREPARE,
    PROGRESS_PHASES,
    SUMMARY_EVIDENCE_CONTRACT,
    SUMMARY_FORMAT,
    SUMMARY_VERSION,
    SWITCH_BAKE_TEXTURE_BC,
    SWITCH_IDS,
    SWITCH_INFO,
    SWITCH_PRESCALE_TEXTURE,
    SWITCH_RASTERIZE_COMPUTED,
    CancelToken,
    ConfigIssue,
    ConversionCancelled,
    ExportOptionState,
    OptionConfigError,
    ProgressEvent,
    build_identities,
    build_processing_summary,
    default_switch_values,
    dump_option_config,
    load_option_config,
    normalize_source_digest,
    notify_progress,
    option_config_payload,
    preset_content_digest,
    preset_identity,
    raise_if_cancel_requested,
    resolve_option_config,
    save_option_config,
    source_digest_of_bytes,
    source_digest_of_file,
    switch_applicability,
    validate_option_config,
    validate_progress_event,
)
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402

pytestmark = [pytest.mark.verify, pytest.mark.verify_params]

SOURCE = source_digest_of_bytes(b"sample abr bytes for task 167")
SOURCE_B = source_digest_of_bytes(b"another abr file")

#: 测试样本固定落在工作区任务目录（任务铁律：不使用系统临时目录）。
#: 这里只按路径写/读，不依赖目录枚举，避免 Windows 沙箱对新建目录的枚举拒绝。
SAMPLE_DIR = REPO_ROOT / ".tmp" / "167" / "samples"


def _sample_path(name: str) -> Path:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    return SAMPLE_DIR / name


def _tip(fill: int = 0, shape: tuple[int, int] = (4, 5)) -> np.ndarray:
    return np.full(shape, fill, dtype=np.uint8)


def _texture(
    *,
    scale: float = 100.0,
    brightness: int = 0,
    contrast: int = 0,
    image: np.ndarray | None = None,
    name: str = "tex",
    uuid: str = "texture-uuid",
) -> TextureSettings:
    if image is None:
        image = np.zeros((16, 16), dtype=np.uint8)
    return TextureSettings(
        name=name,
        uuid=uuid,
        scale=scale,
        brightness=brightness,
        contrast=contrast,
        image=image,
    )


def _preset(
    name: str = "笔刷",
    *,
    computed: bool = False,
    tip: np.ndarray | None = None,
    texture: TextureSettings | None = None,
    diameter: float = 30.0,
    hardness: float = 50.0,
) -> BrushPreset:
    if not computed and tip is None:
        tip = _tip()
    return BrushPreset(
        name=name,
        is_computed=computed,
        tip_gray=None if computed else tip,
        texture=texture,
        diameter=diameter,
        hardness=hardness,
    )


def _state(*presets: BrushPreset, source: str = SOURCE) -> ExportOptionState:
    return ExportOptionState.create(list(presets), source)


def _config(entries: list[dict], **overrides) -> dict:
    data = {"format": CONFIG_FORMAT, "version": CONFIG_VERSION, "presets": entries}
    data.update(overrides)
    return data


def _entry(state: ExportOptionState, preset_id: str, **switches) -> dict:
    return {"id": preset_id, "switches": switches}


def _preset_map(state: ExportOptionState) -> dict[str, BrushPreset]:
    return {pid: state.preset(pid) for pid in state.preset_ids}


def _codes(error: OptionConfigError) -> list[str]:
    return [issue.code for issue in error.issues]


# ---------------------------------------------------------------------------
# 一、稳定身份
# ---------------------------------------------------------------------------


def test_source_digest_normalization_and_file() -> None:
    raw = b"abc"
    digest = source_digest_of_bytes(raw)
    assert digest.startswith("sha256:")
    assert normalize_source_digest(digest) == digest
    assert normalize_source_digest(digest[7:]) == digest
    sample = _sample_path("source-digest.abr")
    sample.write_bytes(raw)
    assert source_digest_of_file(sample) == digest
    with pytest.raises(ValueError):
        normalize_source_digest("not-a-digest")


def test_identity_format_and_ordinal() -> None:
    preset = _preset()
    identity = preset_identity(preset, 1, SOURCE)
    assert re.fullmatch(r"bc1-[0-9a-f]{12}-\d{4,}-[0-9a-f]{8}", identity.preset_id)
    assert identity.ordinal == 1
    assert identity.source_digest == SOURCE
    assert preset_identity(preset, 2, SOURCE).preset_id != identity.preset_id


def test_identity_is_deterministic() -> None:
    preset = _preset()
    first = preset_identity(preset, 3, SOURCE)
    second = preset_identity(_preset(), 3, SOURCE)
    assert first == second


def test_identity_ignores_display_name() -> None:
    a = _preset(name="原名")
    b = _preset(name="改名后的显示名")
    assert preset_content_digest(a) == preset_content_digest(b)
    id_a = preset_identity(a, 1, SOURCE).preset_id
    id_b = preset_identity(b, 1, SOURCE).preset_id
    assert id_a == id_b
    assert preset_identity(b, 1, SOURCE).name == "改名后的显示名"


def test_same_name_brushes_have_distinct_identities() -> None:
    first = _preset(name="同名", tip=_tip(0))
    second = _preset(name="同名", tip=_tip(200))
    identities = build_identities([first, second], SOURCE)
    assert identities[0].name == identities[1].name
    assert identities[0].preset_id != identities[1].preset_id


def test_identical_presets_distinguish_by_ordinal() -> None:
    preset = _preset()
    identities = build_identities([preset, _preset()], SOURCE)
    assert identities[0].content_digest == identities[1].content_digest
    assert identities[0].preset_id != identities[1].preset_id


def test_identity_changes_with_source_content() -> None:
    preset = _preset()
    assert preset_identity(preset, 1, SOURCE).preset_id != preset_identity(preset, 1, SOURCE_B).preset_id


def test_identity_accepts_explicit_source_ordinals() -> None:
    identities = build_identities([_preset(), _preset()], SOURCE, ordinals=[7, 9])
    assert [item.ordinal for item in identities] == [7, 9]
    assert identities[0].preset_id.split("-")[2] == "0007"
    with pytest.raises(ValueError):
        build_identities([_preset()], SOURCE, ordinals=[1, 2])
    with pytest.raises(ValueError):
        build_identities([_preset(), _preset()], SOURCE, ordinals=[1, 1])


def test_state_uses_ids_not_names_or_list_positions() -> None:
    # 同名、不同纹理（内容摘要不同）：用第二个 ID 关闭预缩放，验证不会串到第一个
    textured_a = _preset(name="同名", texture=_texture(scale=150))
    textured_b = _preset(name="同名", texture=_texture(scale=150, uuid="other"))
    state = _state(textured_a, textured_b)
    first_id, second_id = state.preset_ids
    state.set_override(second_id, SWITCH_PRESCALE_TEXTURE, False)
    assert state.enabled(second_id, SWITCH_PRESCALE_TEXTURE) is False
    assert state.enabled(first_id, SWITCH_PRESCALE_TEXTURE) is True
    # 只取显示子集（模拟筛选）不影响状态，顺序也可以任意
    subset = state.records([second_id])
    assert subset[0].switches[SWITCH_PRESCALE_TEXTURE].enabled is False
    assert state.enabled(second_id, SWITCH_PRESCALE_TEXTURE) is False


def test_config_targets_one_of_two_same_name_brushes() -> None:
    first = _preset(name="同名", texture=_texture(scale=150, uuid="a"))
    second = _preset(name="同名", texture=_texture(scale=150, uuid="b"))
    state = _state(first, second)
    first_id, second_id = state.preset_ids
    state.apply_config(_config([_entry(state, second_id, prescale_texture=False)]))
    assert state.enabled(second_id, SWITCH_PRESCALE_TEXTURE) is False
    assert state.enabled(first_id, SWITCH_PRESCALE_TEXTURE) is True


# ---------------------------------------------------------------------------
# 二、条件默认值与适用性边界
# ---------------------------------------------------------------------------


def test_switch_list_is_frozen() -> None:
    assert SWITCH_IDS == (
        SWITCH_RASTERIZE_COMPUTED,
        SWITCH_PRESCALE_TEXTURE,
        SWITCH_BAKE_TEXTURE_BC,
    )
    assert len(set(SWITCH_IDS)) == 3
    for switch_id in SWITCH_IDS:
        info = SWITCH_INFO[switch_id]
        assert info.title
        assert info.condition
        assert info.cost
        assert "168" in info.alternative_status


def test_computed_brush_defaults() -> None:
    preset = _preset(computed=True)
    values = default_switch_values(preset)
    assert values[SWITCH_RASTERIZE_COMPUTED] is True
    assert values[SWITCH_PRESCALE_TEXTURE] is False
    assert values[SWITCH_BAKE_TEXTURE_BC] is False
    applicability = switch_applicability(preset, SWITCH_PRESCALE_TEXTURE)
    assert applicability.applicable is False
    assert "无纹理" in applicability.reason


def test_sampled_brush_defaults() -> None:
    preset = _preset()
    values = default_switch_values(preset)
    assert values[SWITCH_RASTERIZE_COMPUTED] is False
    assert "采样笔尖" in switch_applicability(preset, SWITCH_RASTERIZE_COMPUTED).reason


def test_prescale_boundary_at_100_percent() -> None:
    preset = _preset(texture=_texture(scale=100.0))
    applicability = switch_applicability(preset, SWITCH_PRESCALE_TEXTURE)
    assert applicability.applicable is False
    assert "≤ 100" in applicability.reason


def test_prescale_boundary_rounding_to_same_size() -> None:
    # 16 px × 103.125% = 16.5 → 银行家舍入到 16，现行管线不预缩放
    preset = _preset(texture=_texture(scale=103.125))
    applicability = switch_applicability(preset, SWITCH_PRESCALE_TEXTURE)
    assert applicability.applicable is False
    assert "四舍五入到原尺寸" in applicability.reason


def test_prescale_boundary_just_above_rounding() -> None:
    # 16 px × 103.2% = 16.512 → 17，现行管线会预缩放
    preset = _preset(texture=_texture(scale=103.2))
    applicability = switch_applicability(preset, SWITCH_PRESCALE_TEXTURE)
    assert applicability.applicable is True
    assert "17×17" in applicability.reason


def test_prescale_without_texture_image_is_not_applicable() -> None:
    texture = TextureSettings(name="tex", uuid="u", scale=150.0, image=None)
    preset = _preset(texture=texture)
    for switch_id in (SWITCH_PRESCALE_TEXTURE, SWITCH_BAKE_TEXTURE_BC):
        applicability = switch_applicability(preset, switch_id)
        assert applicability.applicable is False
        assert "无纹理图案位图" in applicability.reason


def test_bake_boundary_plus_127_and_128() -> None:
    not_baked = _preset(texture=_texture(scale=100, brightness=127, contrast=-50))
    baked = _preset(texture=_texture(scale=100, brightness=128, contrast=-50))
    assert switch_applicability(not_baked, SWITCH_BAKE_TEXTURE_BC).applicable is False
    assert switch_applicability(baked, SWITCH_BAKE_TEXTURE_BC).applicable is True


def test_bake_boundary_minus_127_and_128() -> None:
    not_baked = _preset(texture=_texture(scale=100, brightness=-127, contrast=-50))
    baked = _preset(texture=_texture(scale=100, brightness=-128, contrast=-50))
    assert switch_applicability(not_baked, SWITCH_BAKE_TEXTURE_BC).applicable is False
    assert switch_applicability(baked, SWITCH_BAKE_TEXTURE_BC).applicable is True


def test_bake_default_off_without_out_of_range() -> None:
    preset = _preset(texture=_texture(scale=100, brightness=50, contrast=0))
    assert default_switch_values(preset)[SWITCH_BAKE_TEXTURE_BC] is False


def test_computed_brush_with_texture_has_all_applicable_conditions() -> None:
    preset = _preset(computed=True, texture=_texture(scale=150, brightness=128, contrast=-50))
    values = default_switch_values(preset)
    assert values == {
        SWITCH_RASTERIZE_COMPUTED: True,
        SWITCH_PRESCALE_TEXTURE: True,
        SWITCH_BAKE_TEXTURE_BC: True,
    }


def test_inapplicable_cannot_be_forced_on_in_state() -> None:
    state = _state(_preset())
    preset_id = state.preset_ids[0]
    with pytest.raises(OptionConfigError) as exc:
        state.set_override(preset_id, SWITCH_RASTERIZE_COMPUTED, True)
    assert ISSUE_INAPPLICABLE_ON in _codes(exc.value)
    assert "不适用" in str(exc.value)


def test_inapplicable_false_is_accepted_and_keeps_default() -> None:
    state = _state(_preset())
    preset_id = state.preset_ids[0]
    state.set_override(preset_id, SWITCH_RASTERIZE_COMPUTED, False)
    assert state.has_override(preset_id, SWITCH_RASTERIZE_COMPUTED) is False
    assert state.enabled(preset_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_switch_value_must_be_bool() -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    with pytest.raises(TypeError):
        state.set_override(preset_id, SWITCH_RASTERIZE_COMPUTED, 0)


def test_unknown_switch_and_unknown_preset_raise_explicit_errors() -> None:
    state = _state(_preset())
    with pytest.raises(ValueError):
        state.enabled(state.preset_ids[0], "resize_tip")
    with pytest.raises(OptionConfigError) as exc:
        state.enabled("bc1-000000000000-0001-00000000", SWITCH_RASTERIZE_COMPUTED)
    assert ISSUE_UNKNOWN_ID in _codes(exc.value)


# ---------------------------------------------------------------------------
# 三、三层记录与生命周期
# ---------------------------------------------------------------------------


def test_three_layers_are_separate_and_honest() -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    state.set_override(preset_id, SWITCH_RASTERIZE_COMPUTED, False)
    record = state.record(preset_id)
    switch = record.switches[SWITCH_RASTERIZE_COMPUTED]
    assert switch.applicable is True
    assert switch.default_enabled is True
    assert switch.enabled is False
    assert switch.actual == ACTUAL_PENDING_ALTERNATIVE
    assert switch.pending_alternative is True
    assert "任务 168" in switch.actual_reason
    # 不适用项三层也不合并：适用=False、勾选=False、实际=不适用
    other = record.switches[SWITCH_PRESCALE_TEXTURE]
    assert other.applicable is False
    assert other.enabled is False
    assert other.actual == ACTUAL_NOT_APPLICABLE
    assert other.pending_alternative is False


def test_default_applicable_switch_is_applied() -> None:
    state = _state(_preset(computed=True))
    switch = state.record(state.preset_ids[0]).switches[SWITCH_RASTERIZE_COMPUTED]
    assert switch.actual == ACTUAL_APPLIED
    assert switch.pending_alternative is False


def test_reset_clears_manual_overrides() -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    state.set_override(preset_id, SWITCH_RASTERIZE_COMPUTED, False)
    assert state.enabled(preset_id, SWITCH_RASTERIZE_COMPUTED) is False
    state.reset()
    assert state.enabled(preset_id, SWITCH_RASTERIZE_COMPUTED) is True
    assert state.has_override(preset_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_retarget_new_file_clears_overrides_and_recomputes_defaults() -> None:
    state = _state(_preset(computed=True))
    old_id = state.preset_ids[0]
    state.set_override(old_id, SWITCH_RASTERIZE_COMPUTED, False)
    state.retarget([_preset(name="新文件笔刷", computed=True)], SOURCE_B)
    assert state.source_digest == SOURCE_B
    new_id = state.preset_ids[0]
    assert new_id != old_id
    assert state.enabled(new_id, SWITCH_RASTERIZE_COMPUTED) is True
    assert state.has_override(new_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_reload_same_source_with_retarget_also_clears() -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    state.set_override(preset_id, SWITCH_RASTERIZE_COMPUTED, False)
    state.retarget([_preset(computed=True)], SOURCE)
    assert state.enabled(preset_id, SWITCH_RASTERIZE_COMPUTED) is True


# ---------------------------------------------------------------------------
# 四、配置格式、校验与错误
# ---------------------------------------------------------------------------


def test_valid_config_applies_and_dump_roundtrips() -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    state.set_override(preset_id, SWITCH_RASTERIZE_COMPUTED, False)
    text = dump_option_config(state)
    payload = json.loads(text)
    assert payload["format"] == CONFIG_FORMAT
    assert payload["version"] == CONFIG_VERSION
    assert payload["source_digest"] == SOURCE
    assert len(payload["presets"]) == 1
    assert "name" not in payload["presets"][0]
    assert payload["presets"][0]["switches"] == {SWITCH_RASTERIZE_COMPUTED: False}
    fresh = _state(_preset(computed=True))
    loaded = json.loads(text)
    assert fresh.apply_config(loaded) == 1
    assert fresh.enabled(preset_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_dump_is_deterministic_and_has_no_local_path() -> None:
    first = _state(_preset(computed=True, texture=_texture(scale=150)))
    second = _state(_preset(computed=True, texture=_texture(scale=150)))
    first_text = dump_option_config(first)
    assert first_text == dump_option_config(first)
    assert first_text == dump_option_config(second)
    assert str(REPO_ROOT) not in first_text
    assert "timestamp" not in first_text.lower()


def test_save_and_load_config_file() -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    state.set_override(preset_id, SWITCH_RASTERIZE_COMPUTED, False)
    path = _sample_path("options.json")
    save_option_config(path, state)
    raw = path.read_bytes()
    assert raw.startswith(b"{")
    assert b"\r\n" not in raw
    fresh = _state(_preset(computed=True))
    assert fresh.load_config(path) == 1
    assert fresh.enabled(preset_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_load_config_accepts_utf8_bom() -> None:
    state = _state(_preset(computed=True))
    path = _sample_path("bom.json")
    path.write_bytes(b"\xef\xbb\xbf" + dump_option_config(state).encode("utf-8"))
    resolved = load_option_config(path, state.identities, SOURCE, _preset_map(state))
    assert resolved.source_digest == SOURCE


def test_config_version_is_frozen() -> None:
    assert CONFIG_VERSION == 1
    state = _state(_preset(computed=True))
    bad = _config([], version=2)
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(bad, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_VERSION_MISMATCH in _codes(exc.value)


def test_config_missing_format_and_version_are_bad_fields() -> None:
    state = _state(_preset())
    config = {"presets": []}
    issues = validate_option_config(config, state.identities, SOURCE, _preset_map(state))
    codes = [issue.code for issue in issues]
    assert codes.count(ISSUE_BAD_FIELD) >= 2
    with pytest.raises(OptionConfigError):
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))


def test_config_unknown_top_level_field_is_rejected() -> None:
    state = _state(_preset())
    issues = validate_option_config(
        _config([], extra=True), state.identities, SOURCE, _preset_map(state))
    assert ISSUE_BAD_FIELD in [issue.code for issue in issues]


def test_config_unknown_id_is_explicit() -> None:
    state = _state(_preset())
    config = _config([{"id": "bc1-000000000000-0001-00000000", "switches": {}}])
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_UNKNOWN_ID in _codes(exc.value)
    assert "未知笔刷 ID" in str(exc.value)


def test_config_duplicate_names_in_source_are_explicit() -> None:
    state = _state(
        _preset(name="同名", tip=_tip(0)),
        _preset(name="同名", tip=_tip(200)),
    )
    config = _config([{"name": "同名", "switches": {SWITCH_RASTERIZE_COMPUTED: False}}])
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_DUPLICATE_NAME in _codes(exc.value)
    assert "同名不可唯一确定" in str(exc.value)


def test_config_duplicate_name_entries_are_explicit() -> None:
    state = _state(
        _preset(name="甲", tip=_tip(0)),
        _preset(name="乙", tip=_tip(200)),
    )
    first_id, second_id = state.preset_ids
    config = _config([
        {"id": first_id, "name": "甲", "switches": {SWITCH_RASTERIZE_COMPUTED: False}},
        {"id": second_id, "name": "甲", "switches": {SWITCH_RASTERIZE_COMPUTED: False}},
    ])
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_DUPLICATE_NAME in _codes(exc.value)
    assert ISSUE_BAD_FIELD in _codes(exc.value)  # 第二个条目的 name 与 ID 不一致


def test_config_unique_name_only_entry_works() -> None:
    state = _state(_preset(name="唯一", computed=True))
    preset_id = state.preset_ids[0]
    config = _config([{"name": "唯一", "switches": {SWITCH_RASTERIZE_COMPUTED: False}}])
    assert state.apply_config(config) == 1
    assert state.enabled(preset_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_config_unknown_name_is_explicit() -> None:
    state = _state(_preset(name="唯一"))
    config = _config([{"name": "不存在", "switches": {}}])
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_UNKNOWN_NAME in _codes(exc.value)


def test_config_id_name_mismatch_is_bad_field() -> None:
    state = _state(_preset(name="甲"), _preset(name="乙", tip=_tip(200)))
    first_id = state.preset_ids[0]
    config = _config([{"id": first_id, "name": "乙", "switches": {}}])
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_BAD_FIELD in _codes(exc.value)


def test_config_duplicate_id_is_explicit() -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    config = _config([_entry(state, preset_id), _entry(state, preset_id)])
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_DUPLICATE_ID in _codes(exc.value)


def test_config_unknown_switch_is_explicit() -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    config = _config([{"id": preset_id, "switches": {"resize_tip": False}}])
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_UNKNOWN_SWITCH in _codes(exc.value)


@pytest.mark.parametrize("value", [1, 0, "true", None, [True]])
def test_config_switch_value_must_be_bool(value) -> None:
    state = _state(_preset(computed=True))
    preset_id = state.preset_ids[0]
    config = _config([{"id": preset_id, "switches": {SWITCH_RASTERIZE_COMPUTED: value}}])
    issues = validate_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_BAD_FIELD in [issue.code for issue in issues]


def test_config_bad_containers() -> None:
    state = _state(_preset())
    cases = [
        [],
        {"format": CONFIG_FORMAT, "version": 1, "presets": {}},
        _config(["not-an-object"]),
        _config([{"id": state.preset_ids[0], "switches": []}]),
        _config([{"id": state.preset_ids[0]}]),
        _config([{"switches": {}}]),
    ]
    for config in cases:
        issues = validate_option_config(config, state.identities, SOURCE, _preset_map(state))
        assert any(issue.code == ISSUE_BAD_FIELD for issue in issues), config


def test_config_source_mismatch_is_explicit() -> None:
    state = _state(_preset(computed=True))
    config = _config([], source_digest=SOURCE_B)
    with pytest.raises(OptionConfigError) as exc:
        resolve_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_SOURCE_MISMATCH in _codes(exc.value)


def test_config_invalid_source_digest_is_bad_field() -> None:
    state = _state(_preset())
    config = _config([], source_digest="not-a-sha256")
    issues = validate_option_config(config, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_BAD_FIELD in [issue.code for issue in issues]


def test_config_inapplicable_on_is_explicit_and_not_applied() -> None:
    state = _state(_preset())
    preset_id = state.preset_ids[0]
    config = _config([{"id": preset_id, "switches": {SWITCH_RASTERIZE_COMPUTED: True}}])
    with pytest.raises(OptionConfigError) as exc:
        state.apply_config(config)
    assert ISSUE_INAPPLICABLE_ON in _codes(exc.value)
    assert "不能设为 true" in str(exc.value)
    assert state.has_override(preset_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_config_inapplicable_false_is_allowed() -> None:
    state = _state(_preset())
    preset_id = state.preset_ids[0]
    config = _config([{"id": preset_id, "switches": {SWITCH_RASTERIZE_COMPUTED: False}}])
    assert state.apply_config(config) == 1
    assert state.enabled(preset_id, SWITCH_RASTERIZE_COMPUTED) is False
    assert state.has_override(preset_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_failed_apply_does_not_partially_change_state() -> None:
    state = _state(_preset(computed=True, texture=_texture(scale=150)), _preset())
    first_id, _ = state.preset_ids
    state.set_override(first_id, SWITCH_RASTERIZE_COMPUTED, False)
    bad = _config([
        {"id": first_id, "switches": {SWITCH_RASTERIZE_COMPUTED: True}},
        {"id": "bc1-000000000000-0002-00000000", "switches": {}},
    ])
    with pytest.raises(OptionConfigError):
        state.apply_config(bad)
    assert state.enabled(first_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_apply_config_replaces_previous_overrides() -> None:
    state = _state(_preset(computed=True), _preset(computed=True, name="第二支"))
    first_id, second_id = state.preset_ids
    state.set_override(first_id, SWITCH_RASTERIZE_COMPUTED, False)
    state.apply_config(_config([_entry(state, second_id, rasterize_computed=False)]))
    assert state.enabled(first_id, SWITCH_RASTERIZE_COMPUTED) is True
    assert state.enabled(second_id, SWITCH_RASTERIZE_COMPUTED) is False


def test_load_config_errors_are_all_explicit() -> None:
    state = _state(_preset(computed=True))
    missing = _sample_path("missing.json")
    with pytest.raises(OptionConfigError) as exc:
        load_option_config(missing, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_UNREADABLE in _codes(exc.value)
    broken = _sample_path("broken.json")
    broken.write_text("{bad json", encoding="utf-8")
    with pytest.raises(OptionConfigError) as exc:
        load_option_config(broken, state.identities, SOURCE, _preset_map(state))
    assert ISSUE_BAD_JSON in _codes(exc.value)
    assert "JSON 解析失败" in str(exc.value)


def test_validate_returns_issues_without_raising() -> None:
    state = _state(_preset())
    issues = validate_option_config({"format": CONFIG_FORMAT}, state.identities, SOURCE)
    assert issues
    assert all(isinstance(issue, ConfigIssue) for issue in issues)
    assert all(issue.render().startswith("[") for issue in issues)


def test_option_payload_only_lists_applicable_switches() -> None:
    state = _state(_preset(computed=True))
    payload = option_config_payload(state)
    switches = payload["presets"][0]["switches"]
    assert switches == {SWITCH_RASTERIZE_COMPUTED: True}
    assert SWITCH_PRESCALE_TEXTURE not in switches


# ---------------------------------------------------------------------------
# 五、取消、进度与摘要接口
# ---------------------------------------------------------------------------


def test_cancel_token_is_idempotent_and_keeps_first_reason() -> None:
    token = CancelToken()
    assert token.cancelled is False
    token.raise_if_cancelled()
    token.cancel("用户点了取消")
    assert token.cancelled is True
    assert token.reason == "用户点了取消"
    token.cancel("第二次取消")
    assert token.reason == "用户点了取消"
    with pytest.raises(ConversionCancelled) as exc:
        token.raise_if_cancelled()
    assert exc.value.reason == "用户点了取消"
    assert "用户点了取消" in str(exc.value)


def test_cancel_without_reason_uses_default() -> None:
    token = CancelToken()
    token.cancel()
    assert token.reason == "用户取消"


def test_cancel_callback_contract() -> None:
    token = CancelToken()
    raise_if_cancel_requested(lambda: token.cancelled)
    raise_if_cancel_requested(None)
    token.cancel("回调取消")
    with pytest.raises(ConversionCancelled):
        raise_if_cancel_requested(lambda: token.cancelled)


def test_progress_event_validation_and_callback() -> None:
    captured: list[ProgressEvent] = []
    event = ProgressEvent(phase=PHASE_CONVERT, total=3, completed=1, preset_id="bc1-x")
    notify_progress(captured.append, event)
    assert captured == [event]
    assert event.to_dict()["completed"] == 1
    assert PHASE_DONE in PROGRESS_PHASES
    assert CANCELLED_PHASE in PROGRESS_PHASES
    with pytest.raises(ValueError):
        validate_progress_event(ProgressEvent(phase="unknown", total=1, completed=0))
    with pytest.raises(ValueError):
        validate_progress_event(ProgressEvent(phase=PHASE_CONVERT, total=1, completed=2))
    with pytest.raises(ValueError):
        validate_progress_event(ProgressEvent(phase=PHASE_DONE, total=2, completed=1))


def test_progress_callback_can_be_none() -> None:
    notify_progress(None, ProgressEvent(phase=PHASE_PREPARE, total=0, completed=0))


def test_processing_summary_counts_and_layers() -> None:
    computed = _preset(name="计算", computed=True)
    prescaled = _preset(name="缩放", texture=_texture(scale=150))
    baked = _preset(
        name="烘焙",
        texture=_texture(scale=100, brightness=128, contrast=-50),
    )
    state = _state(computed, prescaled, baked)
    computed_id, prescaled_id, _ = state.preset_ids
    state.set_override(computed_id, SWITCH_RASTERIZE_COMPUTED, False)
    summary = state.processing_summary()
    assert summary.source_digest == SOURCE
    assert summary.cancelled is False
    assert summary.evidence == SUMMARY_EVIDENCE_CONTRACT
    counts = summary.counts()
    assert counts["total_presets"] == 3
    assert counts["applicable"] == 3
    assert counts["enabled"] == 2
    assert counts["applied"] == 2
    assert counts["pending_alternative"] == 1
    assert counts["not_applicable"] == 6
    assert counts["alternative"] == 0
    assert counts["not_processed"] == 0
    record = summary.items[0]
    assert record.switches[SWITCH_RASTERIZE_COMPUTED].actual == ACTUAL_PENDING_ALTERNATIVE
    assert summary.items[1].switches[SWITCH_PRESCALE_TEXTURE].actual == ACTUAL_APPLIED
    assert summary.items[2].switches[SWITCH_BAKE_TEXTURE_BC].actual == ACTUAL_APPLIED
    assert summary.items[1].switches[SWITCH_RASTERIZE_COMPUTED].actual == ACTUAL_NOT_APPLICABLE
    assert state.enabled(prescaled_id, SWITCH_PRESCALE_TEXTURE) is True


def test_processing_summary_to_dict_and_text() -> None:
    state = _state(_preset(computed=True), _preset(texture=_texture(scale=150)))
    state.set_override(state.preset_ids[0], SWITCH_RASTERIZE_COMPUTED, False)
    summary = build_processing_summary(state, cancelled=True)
    payload = summary.to_dict()
    assert payload["format"] == SUMMARY_FORMAT
    assert payload["version"] == SUMMARY_VERSION
    assert payload["cancelled"] is True
    assert payload["evidence"] == SUMMARY_EVIDENCE_CONTRACT
    assert json.loads(json.dumps(payload, ensure_ascii=False))["counts"]["pending_alternative"] == 1
    lines = summary.format_lines()
    assert any("待替代路径 1 项" in line for line in lines)
    assert all("已关闭" not in line for line in lines)


def test_summary_actual_constants_are_frozen() -> None:
    assert ACTUAL_APPLIED == "applied"
    assert ACTUAL_NOT_APPLICABLE == "not_applicable"
    assert ACTUAL_PENDING_ALTERNATIVE == "pending_alternative"
    assert ACTUAL_ALTERNATIVE == "alternative"
    assert ACTUAL_NOT_PROCESSED == "not_processed"


# ---------------------------------------------------------------------------
# 六、登记表：按定义复核，只登记不扩开关
# ---------------------------------------------------------------------------


def test_registry_does_not_add_switches() -> None:
    assert EXISTING_TREATMENT_NOTES
    keys = [item.key for item in EXISTING_TREATMENT_NOTES]
    assert len(keys) == len(set(keys))
    for item in EXISTING_TREATMENT_NOTES:
        assert item.editability_loss is False
        assert item.switch_id is None
        assert "不扩开关" in item.decision
        assert item.evidence
        assert item.impact
        assert item.reason
    assert all(item.switch_id not in SWITCH_IDS for item in EXISTING_TREATMENT_NOTES)


def test_editability_notes_apply_user_definition() -> None:
    # 使用者定义：Krita 里不能再修改某参数，或可调范围发生变化，才算牺牲可编辑性。
    by_key = {item.key: item for item in EXISTING_TREATMENT_NOTES}
    assert len(by_key) == 4
    for item in by_key.values():
        assert item.editability_loss is False
        assert "仍可修改" in item.reason
        assert "范围" in item.reason or "能力缺失" in item.reason


def test_registered_tradeoffs_are_not_switches_but_documented() -> None:
    titles = "".join(item.title for item in EXISTING_TREATMENT_NOTES)
    assert "补白" in titles
    assert "高度系" in titles
    assert "hardMix" in titles
    assert "纹理混合模式" in titles
    assert len(SWITCH_IDS) == 3
