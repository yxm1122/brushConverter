"""逐支选择状态机：稳定身份、可见性和导出快照，不依赖 Qt 或持久配置。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass

from brush_converter.convert import ConversionSwitches, PresetPlan, plan_preset
from brush_converter.export_options import (
    SWITCH_IDS,
    ExportOptionState,
    PresetIdentity,
    option_config_payload,
)
from brush_converter.mapping import BrushPreset

FILTER_MODES = ("all", "selected", "computed", "sampled", "affected")
SORT_MODES = ("source", "name_asc", "name_desc")


def is_renderable(preset: BrushPreset) -> bool:
    """与169的跳过条件一致；缺笔尖不能冒充预计会处理。"""
    return preset.is_computed or preset.tip_gray is not None


def state_plan(state: ExportOptionState, preset_id: str) -> PresetPlan:
    """复用168生产计划，不把167尚未接线的契约摘要当作实际计划。"""
    return plan_preset(state.preset(preset_id), ConversionSwitches(**{
        switch_id: state.enabled(preset_id, switch_id) for switch_id in SWITCH_IDS
    }))


@dataclass(frozen=True)
class ExportSnapshot:
    """与界面分离的配置和数据副本；ID沿用源序号，不重新按子集编号。"""

    presets: tuple[BrushPreset, ...]
    identities: tuple[PresetIdentity, ...]
    source_digest: str
    config_json: str
    hidden_selected: int

    def option_state(self) -> ExportOptionState:
        """只用167共用校验重建；每次取全新副本，后台不能改动预确认数据。"""
        presets = [deepcopy(preset) for preset in self.presets]
        state = ExportOptionState(presets, self.identities, self.source_digest)
        state.apply_config(json.loads(self.config_json))
        return state


class BrushSelection:
    """源顺序只读，展示顺序独立；选择和开关只认 preset_id。"""

    def __init__(self, presets: list[BrushPreset], source_digest: str):
        self.options = ExportOptionState.create(presets, source_digest)
        self._selected = set(self.options.preset_ids)
        self.query = ""
        self.filter_mode = "all"
        self.sort_mode = "source"
        self.focused_id = next(iter(self.options.preset_ids), None)

    @property
    def selected_ids(self) -> tuple[str, ...]:
        """导出始终按源顺序，完全相同的同名预设也不会互换。"""
        return tuple(key for key in self.options.preset_ids if key in self._selected)

    def is_selected(self, preset_id: str) -> bool:
        self.options.identity(preset_id)
        return preset_id in self._selected

    def set_selected(self, preset_id: str, selected: bool) -> None:
        self.options.identity(preset_id)
        if type(selected) is not bool:
            raise TypeError("选择状态必须是布尔值")
        if selected:
            self._selected.add(preset_id)
        else:
            self._selected.discard(preset_id)

    def set_view(self, *, query: str | None = None, filter_mode: str | None = None,
                 sort_mode: str | None = None) -> None:
        """只改变可见性；非法筛选/排序不默默回退。"""
        if filter_mode is not None and filter_mode not in FILTER_MODES:
            raise ValueError(f"未知筛选 {filter_mode!r}")
        if sort_mode is not None and sort_mode not in SORT_MODES:
            raise ValueError(f"未知排序 {sort_mode!r}")
        if query is not None:
            self.query = query
        if filter_mode is not None:
            self.filter_mode = filter_mode
        if sort_mode is not None:
            self.sort_mode = sort_mode

    @property
    def display_ids(self) -> tuple[str, ...]:
        """全部源ID的展示排序，不随搜索删除条目。"""
        keys = list(self.options.preset_ids)
        if self.sort_mode != "source":
            keys.sort(key=lambda key: self.options.preset(key).name.casefold(),
                      reverse=self.sort_mode == "name_desc")
        return tuple(keys)

    @property
    def visible_ids(self) -> tuple[str, ...]:
        query = self.query.strip().casefold()
        visible = []
        for key in self.options.preset_ids:
            preset = self.options.preset(key)
            identity = self.options.identity(key)
            if query and query not in f"{preset.name} {key} {identity.ordinal}".casefold():
                continue
            if self.filter_mode == "selected" and key not in self._selected:
                continue
            if self.filter_mode == "computed" and not preset.is_computed:
                continue
            if self.filter_mode == "sampled" and preset.is_computed:
                continue
            if self.filter_mode == "affected" and not any(
                self.options.applicability(key, switch).applicable for switch in SWITCH_IDS
            ):
                continue
            visible.append(key)
        if self.sort_mode != "source":
            visible.sort(key=lambda key: self.options.preset(key).name.casefold(),
                         reverse=self.sort_mode == "name_desc")
        return tuple(visible)

    @property
    def counts(self) -> dict[str, int]:
        visible = set(self.visible_ids)
        return {"total": len(self.options.preset_ids), "visible": len(visible),
                "sel": len(self._selected), "hidden": len(self._selected - visible)}

    def set_visible_selected(self, selected: bool) -> None:
        """先固定可见集合，再批量操作，避免“仅已选”筛选边改边漏。"""
        for key in self.visible_ids:
            self.set_selected(key, selected)

    def snapshot(self) -> ExportSnapshot:
        """包含隐藏已选，排除未选；整批复制后交给后台，不共享可变位图。"""
        keys = self.selected_ids
        payload = option_config_payload(self.options)
        payload["presets"] = [row for row in payload["presets"] if row["id"] in keys]
        return ExportSnapshot(
            presets=tuple(deepcopy(self.options.preset(key)) for key in keys),
            identities=tuple(self.options.identity(key) for key in keys),
            source_digest=self.options.source_digest,
            config_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            hidden_selected=self.counts["hidden"],
        )
