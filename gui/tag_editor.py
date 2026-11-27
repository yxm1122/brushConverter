"""177：按稳定 ID 保存标签编辑，并冻结包信息；不读取系统身份或持久配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from brush_converter.export_metadata import BundleMetadata, PresetTagPlan, normalize_tags

from .brush_selection import ExportSnapshot
from .i18n import tr


@dataclass(frozen=True)
class MetadataExportSnapshot(ExportSnapshot):
    """确认清单与后台共用的快照；路径仅用于源字节核对，不作为包内字段。"""

    metadata: BundleMetadata | None = None
    tag_plans: tuple[tuple[str, PresetTagPlan], ...] = ()
    source_path: str | None = None
    make_bundle: bool = True

    def preset_tags(self) -> dict[str, tuple[str, ...]]:
        """每次给后台全新映射；最终集合只调用175的生产规则。"""
        return {key: plan.final() for key, plan in self.tag_plans}


class TagState:
    """保留原始输入文字，切筛选/语言/同名笔刷不丢编辑；重载才新建状态。"""

    def __init__(self, preset_ids: tuple[str, ...], source_filename: str):
        self.common_text = ""
        self.source_text = Path(source_filename).stem
        self.edits = {key: ("", "") for key in preset_ids}

    def plan(self, preset_id: str) -> PresetTagPlan:
        added, removed = self.edits[preset_id]
        return PresetTagPlan(
            common=normalize_tags(self.common_text.splitlines()),
            source=normalize_tags(self.source_text.splitlines()),
            added=normalize_tags(added.splitlines()),
            removed=normalize_tags(removed.splitlines()),
        )

    def freeze(self, preset_ids: tuple[str, ...]) -> tuple[tuple[str, PresetTagPlan], ...]:
        """只取本次已选稳定ID，包含隐藏已选，不把未选条目混进导出。"""
        return tuple((key, self.plan(key)) for key in preset_ids)


def tag_plan_text(plan: PresetTagPlan) -> str:
    """展示最终集合及来源关系，移除优先；不把被删来源再自动加回。"""
    final = plan.final()
    lines = [tr("tags_final", tags=tr("list_sep").join(final) or tr("tags_empty"))]
    sources = [("tags_from_common", plan.common), ("tags_from_source", plan.source),
               ("tags_from_added", plan.added)]
    for tag in final:
        origins = [tr(label) for label, tags in sources
                   if tag.casefold() in {value.casefold() for value in normalize_tags(tags)}]
        lines.append(tr("tag_relation", tag=tag, origins=" + ".join(origins)))
    if plan.removed:
        lines.append(tr("tags_removed", tags=tr("list_sep").join(plan.removed)))
    return "\n".join(lines)


class TagEditor(QWidget):
    """原生控件：每行一个标签；公共与单支输入即时保存在内存模型。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model: TagState | None = None
        self._key: str | None = None
        self._renderable = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.base_group = QGroupBox()
        base = QFormLayout(self.base_group)
        base.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.common_label = QLabel()
        self.source_label = QLabel()
        self.common_edit = QPlainTextEdit()
        self.source_edit = QPlainTextEdit()
        for label, edit in ((self.common_label, self.common_edit),
                            (self.source_label, self.source_edit)):
            edit.setMaximumHeight(66)
            base.addRow(label, edit)
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        base.addRow(self.help_label)
        layout.addWidget(self.base_group)
        self.preset_group = QGroupBox()
        single = QFormLayout(self.preset_group)
        single.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.added_label = QLabel()
        self.removed_label = QLabel()
        self.added_edit = QPlainTextEdit()
        self.removed_edit = QPlainTextEdit()
        for label, edit in ((self.added_label, self.added_edit),
                            (self.removed_label, self.removed_edit)):
            edit.setMaximumHeight(66)
            single.addRow(label, edit)
        self.final_edit = QPlainTextEdit()
        self.final_edit.setReadOnly(True)
        self.final_edit.setMinimumHeight(95)
        single.addRow(self.final_edit)
        layout.addWidget(self.preset_group)
        self.common_edit.textChanged.connect(self._base_changed)
        self.source_edit.textChanged.connect(self._base_changed)
        self.added_edit.textChanged.connect(self._preset_changed)
        self.removed_edit.textChanged.connect(self._preset_changed)
        self.retranslate()

    def bind_model(self, model: TagState | None) -> None:
        """换文件/重载显式重置，普通重绘不调用此入口。"""
        self.model = model
        self._key = None
        for edit, text in ((self.common_edit, model.common_text if model else ""),
                           (self.source_edit, model.source_text if model else "")):
            with QSignalBlocker(edit):
                edit.setPlainText(text)
        self.set_current(None)

    def set_current(self, key: str | None, *, renderable: bool = True) -> None:
        self._renderable = renderable
        self._key = key if self.model and key in self.model.edits else None
        values = self.model.edits[self._key] if self._key else ("", "")
        for edit, value in zip((self.added_edit, self.removed_edit), values):
            if edit.toPlainText() != value:
                with QSignalBlocker(edit):
                    edit.setPlainText(value)
        self.preset_group.setEnabled(self._key is not None)
        self._render_final()

    def _base_changed(self) -> None:
        if self.model is None:
            return
        self.model.common_text = self.common_edit.toPlainText()
        self.model.source_text = self.source_edit.toPlainText()
        self._render_final()
        self.changed.emit()

    def _preset_changed(self) -> None:
        if self.model is None or self._key is None:
            return
        self.model.edits[self._key] = (self.added_edit.toPlainText(), self.removed_edit.toPlainText())
        self._render_final()
        self.changed.emit()

    def _render_final(self) -> None:
        if self.model is None or self._key is None:
            self.final_edit.setPlainText(tr("tags_no_current"))
            return
        try:
            text = tag_plan_text(self.model.plan(self._key))
            if not self._renderable:
                text += "\n" + tr("tags_not_written")
        except (ValueError, TypeError) as exc:
            # 坏输入保留原文并阻止快照，不回退成上一次的合法标签。
            text = tr("metadata_invalid", detail=str(exc))
        self.final_edit.setPlainText(text)

    def retranslate(self) -> None:
        self.base_group.setTitle(tr("tags_base"))
        self.preset_group.setTitle(tr("tags_per_preset"))
        for label, key in ((self.common_label, "tags_common"), (self.source_label, "tags_source"),
                           (self.added_label, "tags_added"), (self.removed_label, "tags_remove"),
                           (self.help_label, "tags_help")):
            label.setText(tr(key))
        for edit, key in ((self.common_edit, "tags_common"), (self.source_edit, "tags_source"),
                          (self.added_edit, "tags_added"), (self.removed_edit, "tags_remove")):
            edit.setAccessibleName(tr(key))
            edit.setPlaceholderText(tr("tags_one_per_line"))
        self.final_edit.setAccessibleName(tr("tags_final_heading"))
        self._render_final()
