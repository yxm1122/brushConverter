"""原生Qt分区窗口：文件、稳定笔刷列表、逐支处理后果和安全导出。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSettings, QSignalBlocker, Qt, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from brush_converter.abr import AbrFile
from brush_converter.convert import ConversionSwitches, ConvertResult, plan_preset
from brush_converter.export_options import SWITCH_IDS
from brush_converter.kpp.kpp_writer import render_tip_preview
from brush_converter.mapping import BrushPreset

from .brush_selection import (
    FILTER_MODES,
    SORT_MODES,
    BrushSelection,
    ExportSnapshot,
    is_renderable,
    state_plan,
)
from .i18n import (
    get_language,
    outcome_label,
    processing_reason,
    set_language,
    tr,
    tr_applicability,
    tr_warning,
)
from .workers import ConvertFailure, ConvertWorker, ParseWorker


def _gray_to_pixmap(gray: np.ndarray | None, size: int = 64) -> QPixmap:
    """缩略图仅作来源预览，不冒充开关改变后的笔触或Krita真实渲染。"""
    if gray is None:
        yy, xx = np.mgrid[0:64, 0:64].astype(np.float64)
        distance = np.sqrt((xx - 31.5) ** 2 + (yy - 31.5) ** 2)
        gray = (np.clip(1.0 - distance / 28.0, 0, 1) * 255.0).astype(np.uint8)
    rgba = render_tip_preview(gray, size)
    height, width = rgba.shape[:2]
    image = QImage(rgba.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(image.copy())


def export_preview_text(snapshot: ExportSnapshot) -> str:
    """按同一快照列出最终全体选择和生产计划，隐藏已选不能漏掉。"""
    state = snapshot.option_state()
    counts = {"applied": 0, "alternative": 0, "limited": 0, "skipped": 0}
    lines = []
    for identity in state.identities:
        preset = state.preset(identity.preset_id)
        lines.append(tr("forecast_brush", ordinal=identity.ordinal, name=preset.name,
                        id=identity.preset_id))
        if not is_renderable(preset):
            counts["skipped"] += 1
            lines.append(tr("skipped_tip"))
        else:
            for outcome in state_plan(state, identity.preset_id).outcomes:
                if outcome.actual in ("applied", "alternative"):
                    counts[outcome.actual] += 1
                counts["limited"] += int(outcome.limited)
                lines.append(tr("forecast_switch", title=tr(f"switch_{outcome.switch_id}"),
                                state=outcome_label(outcome),
                                reason=processing_reason(preset, outcome)))
        for warning in preset.warnings:
            lines.append(tr("warn_prefix") + tr_warning(warning))
        lines.append("")
    return "\n".join([
        tr("export_intro", n=len(state.preset_ids), hidden=snapshot.hidden_selected),
        tr("export_counts", **counts), tr("export_caution"), "", *lines,
    ])


class ExportConfirmDialog(QDialog):
    """完整可滚动的确认清单，默认回到调整，不截断同名笔刷或长清单。"""

    def __init__(self, snapshot: ExportSnapshot, directory: str, output_format: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("export_title"))
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        target = QLabel(tr("export_target", format=output_format, directory=directory))
        target.setWordWrap(True)
        layout.addWidget(target)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlainText(export_preview_text(snapshot))
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("confirm_export"))
        back = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        back.setText(tr("cancel_export"))
        back.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class BrushItemWidget(QWidget):
    """来源预览卡片：勾选只管导出，稳定ID和处理状态始终可查。"""

    def __init__(self, preset: BrushPreset, identity, checked: bool, parent=None):
        super().__init__(parent)
        self.preset = preset
        layout = QHBoxLayout(self)
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(checked)
        self.checkbox.setAccessibleName(tr("forecast_brush", ordinal=identity.ordinal,
                                          name=preset.name, id=identity.preset_id))
        layout.addWidget(self.checkbox)
        thumb = QLabel()
        thumb.setPixmap(_gray_to_pixmap(preset.tip_gray))
        layout.addWidget(thumb)
        column = QVBoxLayout()
        for text in (preset.name, tr("brush_identity", ordinal=identity.ordinal,
                                     id=identity.preset_id)):
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            column.addWidget(label)
        kind = tr("computed_brush" if preset.is_computed else "sampled_brush")
        diameter = preset.diameter if preset.diameter is not None else "—"
        self.source_info = QLabel(f"{kind} · " + tr("diameter", d=diameter))
        self.source_info.setWordWrap(True)
        column.addWidget(self.source_info)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)
        if preset.warnings:
            warning = QLabel(tr("warn_prefix") + tr("list_sep").join(
                tr_warning(value) for value in preset.warnings))
            warning.setTextFormat(Qt.TextFormat.PlainText)
            warning.setWordWrap(True)
            column.addWidget(warning)
        layout.addLayout(column, 1)
        self.edit_btn = QPushButton(tr("edit_brush"))
        layout.addWidget(self.edit_btn)


class MainWindow(QMainWindow):
    """状态机独立于控件；只把语言偏好保存到QSettings。"""

    def __init__(self):
        super().__init__()
        self._settings = QSettings("brushConverter", "brushConverter")
        saved = self._settings.value("language", "zh")
        set_language(saved if saved in ("zh", "en") else "zh")
        self._abr: AbrFile | None = None
        self._presets: list[BrushPreset] = []
        self._selection: BrushSelection | None = None
        self._file_path: str | None = None
        self._status: tuple[str, dict] | None = None
        self._busy = False
        self._parse_worker: ParseWorker | None = None
        self._convert_worker: ConvertWorker | None = None
        self._active_workers = set()
        self._export_snapshot: ExportSnapshot | None = None
        self._items: dict[str, tuple[QListWidgetItem, BrushItemWidget]] = {}
        self._build_ui()
        self._retranslate()
        self.resize(1120, 820)

    def _build_ui(self) -> None:
        """只用既有Qt原生控件，处理面板可滚动，不需要额外美术框架。"""
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        self.file_group = QGroupBox()
        top = QHBoxLayout(self.file_group)
        self.open_btn = QPushButton()
        self.open_btn.clicked.connect(self._on_open)
        self.reload_btn = QPushButton()
        self.reload_btn.clicked.connect(self._on_reload)
        self.file_label = QLabel()
        self.file_label.setTextFormat(Qt.TextFormat.PlainText)
        self.file_label.setWordWrap(True)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setCurrentIndex(0 if get_language() == "zh" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self.open_btn)
        top.addWidget(self.reload_btn)
        top.addWidget(self.file_label, 1)
        top.addWidget(self.lang_combo)
        root.addWidget(self.file_group)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list_group = QGroupBox()
        listing = QVBoxLayout(self.list_group)
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._on_view_changed)
        listing.addWidget(self.search_edit)
        filters = QHBoxLayout()
        self.filter_label = QLabel()
        self.filter_combo = QComboBox()
        for mode in FILTER_MODES:
            self.filter_combo.addItem("", mode)
        self.filter_combo.currentIndexChanged.connect(self._on_view_changed)
        self.sort_label = QLabel()
        self.sort_combo = QComboBox()
        for mode in SORT_MODES:
            self.sort_combo.addItem("", mode)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        for widget in (self.filter_label, self.filter_combo, self.sort_label, self.sort_combo):
            filters.addWidget(widget)
        listing.addLayout(filters)
        self.brush_list = QListWidget()
        self.brush_list.currentItemChanged.connect(self._on_current_changed)
        listing.addWidget(self.brush_list, 1)
        selection_row = QHBoxLayout()
        self.sel_all_btn = QPushButton()
        self.sel_all_btn.clicked.connect(lambda: self._set_all(True))
        self.sel_none_btn = QPushButton()
        self.sel_none_btn.clicked.connect(lambda: self._set_all(False))
        selection_row.addWidget(self.sel_all_btn)
        selection_row.addWidget(self.sel_none_btn)
        listing.addLayout(selection_row)
        self.count_label = QLabel()
        self.count_label.setWordWrap(True)
        listing.addWidget(self.count_label)
        splitter.addWidget(self.list_group)

        self.process_group = QGroupBox()
        processing = QVBoxLayout(self.process_group)
        self.process_help = QLabel()
        self.process_help.setWordWrap(True)
        processing.addWidget(self.process_help)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        detail = QWidget()
        details = QVBoxLayout(detail)
        self.current_label = QLabel()
        self.current_label.setTextFormat(Qt.TextFormat.PlainText)
        self.current_label.setWordWrap(True)
        details.addWidget(self.current_label)
        self.switch_groups = {}
        self.switch_checks = {}
        self.switch_status = {}
        self.switch_reasons = {}
        for switch in SWITCH_IDS:
            group = QGroupBox()
            row = QVBoxLayout(group)
            check = QCheckBox()
            check.toggled.connect(lambda value, key=switch: self._on_switch_changed(key, value))
            status = QLabel()
            reason = QLabel()
            for label in (status, reason):
                label.setWordWrap(True)
                label.setTextFormat(Qt.TextFormat.PlainText)
            row.addWidget(check)
            row.addWidget(status)
            row.addWidget(reason)
            details.addWidget(group)
            self.switch_groups[switch] = group
            self.switch_checks[switch] = check
            self.switch_status[switch] = status
            self.switch_reasons[switch] = reason
        details.addStretch(1)
        scroll.setWidget(detail)
        processing.addWidget(scroll, 1)
        self.reset_label = QLabel()
        self.reset_label.setWordWrap(True)
        processing.addWidget(self.reset_label)
        splitter.addWidget(self.process_group)
        splitter.setSizes([560, 520])
        root.addWidget(splitter, 1)

        self.out_group = QGroupBox()
        output = QVBoxLayout(self.out_group)
        formats = QHBoxLayout()
        self.fmt_label = QLabel()
        self.fmt_group = QButtonGroup(self)
        self.radio_kpp = QRadioButton()
        self.radio_bundle = QRadioButton()
        self.radio_bundle.setChecked(True)
        self.fmt_group.addButton(self.radio_kpp)
        self.fmt_group.addButton(self.radio_bundle)
        for widget in (self.fmt_label, self.radio_kpp, self.radio_bundle):
            formats.addWidget(widget)
        formats.addStretch(1)
        output.addLayout(formats)
        directory = QHBoxLayout()
        self.out_dir_label = QLabel()
        self.out_dir_edit = QLineEdit(str(Path.cwd() / "converted"))
        self.browse_btn = QPushButton()
        self.browse_btn.clicked.connect(self._on_browse)
        directory.addWidget(self.out_dir_label)
        directory.addWidget(self.out_dir_edit, 1)
        directory.addWidget(self.browse_btn)
        output.addLayout(directory)
        root.addWidget(self.out_group)
        self.convert_btn = QPushButton()
        self.convert_btn.clicked.connect(self._on_convert)
        root.addWidget(self.convert_btn)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    def _retranslate(self) -> None:
        """重建展示不重建模型；稳定ID上的选择/开关/焦点不被语言污染。"""
        self.setWindowTitle(tr("window_title"))
        for widget, key in ((self.file_group, "file_section"), (self.list_group, "brush_list"),
                            (self.process_group, "processing"), (self.out_group, "output")):
            widget.setTitle(tr(key))
        for widget, key in ((self.open_btn, "open_abr"), (self.reload_btn, "reload"),
                            (self.sel_all_btn, "select_all"), (self.sel_none_btn, "select_none"),
                            (self.fmt_label, "format"), (self.radio_kpp, "kpp"),
                            (self.radio_bundle, "bundle"), (self.out_dir_label, "out_dir"),
                            (self.browse_btn, "browse"), (self.convert_btn, "convert"),
                            (self.filter_label, "filter_label"), (self.sort_label, "sort_label"),
                            (self.process_help, "process_help"), (self.reset_label, "reset_notice")):
            widget.setText(tr(key))
        self.file_label.setText(self._file_path or tr("no_file"))
        self.search_edit.setPlaceholderText(tr("search"))
        for combo, modes, prefix in ((self.filter_combo, FILTER_MODES, "filter"),
                                     (self.sort_combo, SORT_MODES, "sort")):
            with QSignalBlocker(combo):
                for index, mode in enumerate(modes):
                    combo.setItemText(index, tr(f"{prefix}_{mode}"))
        for switch in SWITCH_IDS:
            self.switch_groups[switch].setTitle(tr(f"switch_{switch}"))
            self.switch_checks[switch].setText(tr("use_processing"))
        self._populate()
        self._refresh_status()

    def _set_status(self, key: str | None = None, **kwargs) -> None:
        self._status = (key, kwargs) if key else None
        self._refresh_status()

    def _refresh_status(self) -> None:
        self.status_label.setText(tr(self._status[0], **self._status[1]) if self._status else "")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for group in (self.file_group, self.list_group, self.process_group, self.out_group):
            group.setEnabled(not busy)
        self._update_count()

    def _on_lang_changed(self, index: int) -> None:
        lang = self.lang_combo.itemData(index)
        if lang != get_language():
            set_language(lang)
            self._settings.setValue("language", lang)
            self._retranslate()

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("select_abr"), "", tr("abr_filter"))
        if path:
            self._load_file(path)

    def _on_reload(self) -> None:
        if self._file_path and not self._busy:
            self._load_file(self._file_path)

    def _load_file(self, path: str) -> None:
        """开始载入就清旧状态；失败不允许用旧文件的配置继续导出。"""
        if self._busy:
            return
        self._file_path = path
        self.file_label.setText(path)
        self._abr = None
        self._presets = []
        self._selection = None
        self._export_snapshot = None
        with QSignalBlocker(self.search_edit), QSignalBlocker(self.filter_combo), QSignalBlocker(self.sort_combo):
            self.search_edit.clear()
            self.filter_combo.setCurrentIndex(0)
            self.sort_combo.setCurrentIndex(0)
        self._populate()
        self._set_busy(True)
        self._set_status("parsing")
        self._parse_worker = ParseWorker(path, self)
        self._parse_worker.done.connect(self._on_parsed)
        self._track_worker(self._parse_worker)
        self._parse_worker.start()

    def _on_parsed(self, abr, presets) -> None:
        if abr is not None:
            digest = self._parse_worker.source_digest if self._parse_worker else None
            if not digest:
                abr, presets = None, tr("missing_digest")
            else:
                self._abr = abr
                self._presets = presets
                self._selection = BrushSelection(presets, digest)
        if abr is None:
            self._abr = None
            self._presets = []
            self._selection = None
        self._populate()
        self._set_busy(False)
        if abr is None:
            self._set_status()
            QMessageBox.critical(self, tr("parse_failed"), str(presets))
        elif not presets:
            self._set_status()
            detail = "\n".join(getattr(abr, "diagnostics", []) or [])
            QMessageBox.warning(self, tr("no_brushes_title"), tr("no_brushes_body",
                version=f"{abr.version}.{abr.subversion}", detail=detail or tr("no_brushes_no_detail")))
        else:
            self._set_status("loaded", n=len(presets))

    def _populate(self) -> None:
        """仅重建控件，状态始终从模型按ID读取，不使用筛选后的行号。"""
        with QSignalBlocker(self.brush_list):
            self.brush_list.clear()
            self._items.clear()
            if self._selection:
                for key in self._selection.display_ids:
                    state = self._selection.options
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, key)
                    card = BrushItemWidget(state.preset(key), state.identity(key),
                                           self._selection.is_selected(key))
                    card.checkbox.toggled.connect(lambda value, preset_id=key: self._on_checked(preset_id, value))
                    card.edit_btn.clicked.connect(lambda _checked=False, preset_id=key:
                                                  self.brush_list.setCurrentItem(self._items[preset_id][0]))
                    self.brush_list.addItem(item)
                    self.brush_list.setItemWidget(item, card)
                    self._items[key] = (item, card)
                    self._refresh_card(key)
        self._apply_view()

    def _refresh_card(self, key: str) -> None:
        item, card = self._items[key]
        preset = self._selection.options.preset(key)
        if is_renderable(preset):
            card.summary.setText(" · ".join(
                tr(f"switch_{outcome.switch_id}") + ": " +
                tr("switch_on" if outcome.enabled else "switch_off")
                if outcome.applicable else tr(f"switch_{outcome.switch_id}") + ": " + tr("not_applicable")
                for outcome in state_plan(self._selection.options, key).outcomes))
        else:
            card.summary.setText(tr("skipped_tip"))
        item.setSizeHint(card.sizeHint())

    def _on_view_changed(self, *_args) -> None:
        if self._selection:
            self._selection.set_view(query=self.search_edit.text(),
                                     filter_mode=self.filter_combo.currentData())
        self._apply_view()

    def _on_sort_changed(self, *_args) -> None:
        if self._selection:
            self._selection.set_view(sort_mode=self.sort_combo.currentData())
        self._populate()

    def _apply_view(self) -> None:
        visible = set(self._selection.visible_ids) if self._selection else set()
        with QSignalBlocker(self.brush_list):
            for key, (item, _card) in self._items.items():
                item.setHidden(key not in visible)
            key = self._selection.focused_id if self._selection else None
            self.brush_list.setCurrentItem(self._items[key][0] if key in visible else None)
        self._update_count()
        self._refresh_detail()

    def _on_current_changed(self, current, _previous) -> None:
        if current is not None and self._selection:
            self._selection.focused_id = current.data(Qt.ItemDataRole.UserRole)
        self._refresh_detail()

    def _on_checked(self, key: str, value: bool) -> None:
        self._selection.set_selected(key, value)
        self._apply_view()

    def _set_all(self, checked: bool) -> None:
        if not self._selection:
            return
        self._selection.set_visible_selected(checked)
        for key, (_item, card) in self._items.items():
            with QSignalBlocker(card.checkbox):
                card.checkbox.setChecked(self._selection.is_selected(key))
        self._apply_view()

    def _update_count(self) -> None:
        counts = self._selection.counts if self._selection else dict(total=0, visible=0, sel=0, hidden=0)
        self.count_label.setText(tr("count", **counts))
        self.count_label.setStyleSheet("font-weight:bold;" if counts["hidden"] else "")
        self.convert_btn.setEnabled(not self._busy and self._abr is not None and counts["sel"] > 0)
        self.reload_btn.setEnabled(not self._busy and self._file_path is not None)

    def _refresh_detail(self) -> None:
        key = self._selection.focused_id if self._selection else None
        visible = self._selection.visible_ids if self._selection else ()
        valid = key in visible
        for group in self.switch_groups.values():
            group.setVisible(valid)
        if not valid:
            self.current_label.setText(tr("no_current"))
            return
        state = self._selection.options
        preset = state.preset(key)
        identity = state.identity(key)
        selected = tr("current_selected" if self._selection.is_selected(key) else "current_unselected")
        self.current_label.setText(f"{preset.name}\n" + tr("brush_identity", ordinal=identity.ordinal, id=key) + f"\n{selected}")
        plan = state_plan(state, key)
        for switch in SWITCH_IDS:
            check = self.switch_checks[switch]
            applicability = state.applicability(key, switch)
            outcome = plan.outcome(switch)
            with QSignalBlocker(check):
                check.setChecked(state.enabled(key, switch))
            check.setEnabled(applicability.applicable and is_renderable(preset))
            reason = tr_applicability(applicability.reason)
            check.setToolTip(reason)
            self.switch_status[switch].setText(tr("switch_status",
                applicability=tr("applicable" if applicability.applicable else "not_applicable"),
                enabled=tr("switch_on" if outcome.enabled else "switch_off"),
                default=tr("switch_on" if state.default_enabled(key, switch) else "switch_off")) +
                "\n" + tr("expected_state", state=outcome_label(outcome))
                if is_renderable(preset) else tr("skipped_tip"))
            if applicability.applicable and is_renderable(preset):
                values = {name: state.enabled(key, name) for name in SWITCH_IDS}
                on = plan_preset(preset, ConversionSwitches(**(values | {switch: True}))).outcome(switch)
                off = plan_preset(preset, ConversionSwitches(**(values | {switch: False}))).outcome(switch)
                reason += "\n\n" + tr("choice_consequences", on=processing_reason(preset, on),
                                      off=processing_reason(preset, off))
            self.switch_reasons[switch].setText(reason)

    def _on_switch_changed(self, switch: str, enabled: bool) -> None:
        key = self._selection.focused_id
        self._selection.options.set_override(key, switch, enabled)
        self._refresh_card(key)
        self._refresh_detail()

    def _selected_presets(self) -> list[BrushPreset]:
        if not self._selection:
            return []
        return [self._selection.options.preset(key) for key in self._selection.selected_ids]

    def _on_browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, tr("select_out_dir"), self.out_dir_edit.text())
        if directory:
            self.out_dir_edit.setText(directory)

    def _on_convert(self) -> None:
        if self._abr is None or self._selection is None or self._busy:
            return
        if not self._selection.selected_ids:
            QMessageBox.information(self, tr("hint"), tr("need_select"))
            return
        snapshot = self._selection.snapshot()
        out_dir = self.out_dir_edit.text().strip() or "converted"
        make_bundle = self.radio_bundle.isChecked()
        dialog = ExportConfirmDialog(snapshot, out_dir, ".bundle" if make_bundle else ".kpp", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._export_snapshot = snapshot
        self._set_busy(True)
        self._set_status("converting")
        self._convert_worker = ConvertWorker(self._abr, list(snapshot.presets), out_dir,
                                            make_bundle, not make_bundle, self, snapshot=snapshot)
        self._convert_worker.done.connect(self._on_converted)
        self._track_worker(self._convert_worker)
        self._convert_worker.start()

    def _track_worker(self, worker) -> None:
        """finished才表示线程真正结束；保留活动集合防止短暂重叠时漏掉关窗检查。"""
        self._active_workers.add(worker)
        worker.finished.connect(self._on_worker_finished)

    @Slot()
    def _on_worker_finished(self) -> None:
        """在主线程按对象身份清引用，再延迟释放，避免悬空Qt句柄和大数据泄漏。"""
        worker = self.sender()
        if self._parse_worker is worker:
            self._parse_worker = None
        if self._convert_worker is worker:
            self._convert_worker = None
        self._active_workers.discard(worker)
        worker.deleteLater()

    def _show_failure(self, failure: ConvertFailure) -> None:
        """结构化失败不是成功：保留清单直接可见，全部原始字段仍可展开。"""
        self._set_status("convert_failed")
        brief = [tr("failure_phase", phase=failure.phase or "—")]
        details = [failure.message]
        summary = failure.summary
        if summary is not None:
            key = f"transaction_{summary.status}"
            label = tr(key) if summary.status in ("failed", "rolled_back", "partial_blocked", "cancelled") else summary.status
            brief.append(tr("failure_status", status=label))
            if summary.retained:
                brief.append(tr("retained_files") + "\n" + "\n".join(summary.retained))
            if summary.blocked:
                brief.append(tr("blocked_files") + "\n" + json.dumps(summary.blocked, ensure_ascii=False, indent=2))
            if summary.rolled_back:
                brief.append(tr("rolled_back_files") + "\n" + "\n".join(summary.rolled_back))
            details.append(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Critical)
        message.setWindowTitle(tr("convert_failed"))
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setText(tr("convert_failed"))
        message.setInformativeText("\n\n".join(brief))
        message.setDetailedText(tr("raw_details") + "\n" + "\n\n".join(details))
        message.exec()

    def _on_converted(self, result) -> None:
        """169状态先于摘要，取消/全跳过/未写出不冒充转换成功。"""
        self._set_busy(False)
        if isinstance(result, (ConvertFailure, str)):
            self._show_failure(ConvertFailure(result) if isinstance(result, str) else result)
            return
        r: ConvertResult = result
        key = f"result_{r.status}"
        if r.status not in ("committed", "reused_only", "empty", "all_skipped", "cancelled"):
            key = "result_unknown"
        self._set_status(key)
        lines = [tr(key), tr("done_summary", n=len(r.kpp_files), tex=r.texture_count),
                 tr("result_detail", skipped=len(r.skipped), not_processed=len(r.not_processed))]
        paths = ([r.bundle_path] if r.bundle_path else []) + list(r.standalone_paths.values())
        if paths:
            lines.append(tr("done_body", paths="\n".join(paths)))
        if r.bundle_path:
            lines.append(tr("done_bundle", name=Path(r.bundle_path).name, hash=r.content_hash8 or "—"))
        if r.verify is not None and not r.verify.ok:
            lines.append(tr("verify_failed", detail="; ".join(r.verify.errors)))
        raw = [*r.warnings, *r.skipped, *r.not_processed]
        if r.cancel_reason:
            raw.append(r.cancel_reason)
        if raw:
            lines.append(tr("raw_details") + "\n" + "\n".join(raw))
        if r.transaction:
            for item in r.transaction.items:
                if item.state == "reused":
                    lines.append(tr("reused_notice", path=item.final_path))
                elif item.renamed_from and item.state in ("committed", "reused"):
                    lines.append(tr("renamed_notice", path=item.final_path))
        if self._export_snapshot:
            state = self._export_snapshot.option_state()
            lines.append(tr("run_heading"))
            for identity in state.identities:
                lines.append(tr("forecast_brush", ordinal=identity.ordinal, name=identity.name, id=identity.preset_id))
                plan = r.option_plans.get(identity.preset_id)
                if plan is None:
                    lines.append(tr("run_missing"))
                    continue
                for outcome in plan.outcomes:
                    lines.append(tr("forecast_switch", title=tr(f"switch_{outcome.switch_id}"),
                                    state=outcome_label(outcome),
                                    reason=processing_reason(state.preset(identity.preset_id), outcome)))
        message = QMessageBox(self)
        message.setWindowTitle(tr("done_title") if r.status in ("committed", "reused_only") else tr("hint"))
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setText(tr(key))
        # 输出位置直接可见；逐支长记录放在可展开详情，不靠用户猜文件在哪里。
        brief = [tr("done_summary", n=len(r.kpp_files), tex=r.texture_count),
                 tr("result_detail", skipped=len(r.skipped), not_processed=len(r.not_processed))]
        if paths:
            brief.append(tr("done_body", paths="\n".join(paths)))
        message.setInformativeText("\n\n".join(brief))
        message.setDetailedText("\n\n".join(lines))
        message.exec()

    def closeEvent(self, event) -> None:
        """不强杀后台；172接取消前，任务未结束时仅阻止关窗。"""
        if any(worker.isRunning() for worker in self._active_workers):
            self._set_status("busy_close")
            event.ignore()
            return
        super().closeEvent(event)


def main() -> int:
    """启动GUI主循环；本任务机器测试不调用show或真实主循环。"""
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
