"""主窗口：打开 ABR、预览笔尖、勾选转换、产物格式、未映射参数提醒、中英文切换。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from brush_converter.abr import AbrFile
from brush_converter.convert import ConvertResult
from brush_converter.kpp.kpp_writer import render_tip_preview
from brush_converter.mapping import BrushPreset

from . import i18n
from .i18n import get_language, set_language, tr, tr_warning
from .workers import ConvertWorker, ParseWorker


def _gray_to_pixmap(gray: np.ndarray | None, size: int = 64) -> QPixmap:
    """灰度蒙版（255=墨）→ 白色在透明底上的缩略图。"""
    if gray is None:
        # 计算笔刷：画一个简单圆点占位
        gray = np.zeros((64, 64), dtype=np.uint8)
        yy, xx = np.mgrid[0:64, 0:64].astype(np.float64)
        d = np.sqrt((xx - 31.5) ** 2 + (yy - 31.5) ** 2)
        gray = np.clip(1.0 - d / 28.0, 0, 1) * 255.0
        gray = gray.astype(np.uint8)
    rgba = render_tip_preview(gray, size)
    h, w = rgba.shape[:2]
    img = QImage(rgba.tobytes(), w, h, w * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(img.copy())


class BrushItemWidget(QWidget):
    """单个笔刷卡片：勾选框 + 缩略图 + 名称 + 未映射警告。"""

    def __init__(self, preset: BrushPreset, parent=None):
        super().__init__(parent)
        self.preset = preset
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        layout.addWidget(self.checkbox)

        thumb = QLabel()
        thumb.setPixmap(_gray_to_pixmap(preset.tip_gray))
        thumb.setFixedSize(64, 64)
        thumb.setStyleSheet("background:#3a3a3a; border-radius:6px;")
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(thumb)

        name_col = QVBoxLayout()
        name_label = QLabel(preset.name)
        name_label.setStyleSheet("font-size:13px;")
        name_col.addWidget(name_label)

        kind = tr("computed_brush") if preset.is_computed else tr("sampled_brush")
        info = f"{kind} · {tr('diameter', d=preset.diameter or '—')}"
        info_label = QLabel(info)
        info_label.setStyleSheet("color:#888888; font-size:11px;")
        name_col.addWidget(info_label)

        if preset.warnings:
            warn_text = tr("warn_prefix") + tr("list_sep").join(
                tr_warning(w) for w in preset.warnings)
            warn = QLabel(warn_text)
            warn.setStyleSheet("color:#c98a1b; font-size:11px;")
            name_col.addWidget(warn)

        layout.addLayout(name_col)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    """主窗口：打开 ABR → 预览/勾选笔刷 → 选择产物格式 → 转换。

    解析与转换都在后台 QThread 执行（见 workers.py），避免阻塞界面。
    界面文本支持中文/English 切换（右上角下拉框），选择会持久化。
    """

    def __init__(self):
        super().__init__()
        self._settings = QSettings("brushConverter", "brushConverter")
        saved = self._settings.value("language", "zh")
        set_language(saved if saved in ("zh", "en") else "zh")

        self._abr: AbrFile | None = None
        self._presets: list[BrushPreset] = []
        self._file_path: str | None = None
        self._status: tuple[str, dict] | None = None
        self._parse_worker: ParseWorker | None = None
        self._convert_worker: ConvertWorker | None = None

        self._build_ui()
        self._retranslate()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        """搭建界面：顶栏（打开文件 + 语言切换）+ 笔刷列表 + 输出设置 + 转换按钮。"""
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 顶栏：打开文件 + 右上角语言切换
        top = QHBoxLayout()
        self.open_btn = QPushButton(tr("open_abr"))
        self.open_btn.clicked.connect(self._on_open)
        top.addWidget(self.open_btn)
        self.file_label = QLabel(tr("no_file"))
        self.file_label.setStyleSheet("color:#888888;")
        top.addWidget(self.file_label, 1)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setCurrentIndex(0 if get_language() == "zh" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self.lang_combo)
        root.addLayout(top)

        # 笔刷列表
        self.list_group = QGroupBox(tr("brush_list"))
        list_layout = QVBoxLayout(self.list_group)
        self.brush_list = QListWidget()
        self.brush_list.setSpacing(2)
        list_layout.addWidget(self.brush_list)

        sel_row = QHBoxLayout()
        self.sel_all_btn = QPushButton(tr("select_all"))
        self.sel_all_btn.clicked.connect(lambda: self._set_all(True))
        self.sel_none_btn = QPushButton(tr("select_none"))
        self.sel_none_btn.clicked.connect(lambda: self._set_all(False))
        self.count_label = QLabel("")
        sel_row.addWidget(self.sel_all_btn)
        sel_row.addWidget(self.sel_none_btn)
        sel_row.addWidget(self.count_label)
        sel_row.addStretch(1)
        list_layout.addLayout(sel_row)
        root.addWidget(self.list_group, 1)

        # 输出设置
        self.out_group = QGroupBox(tr("output"))
        out_layout = QVBoxLayout(self.out_group)

        fmt_row = QHBoxLayout()
        self.fmt_label = QLabel(tr("format"))
        fmt_row.addWidget(self.fmt_label)
        self.fmt_group = QButtonGroup(self)
        self.radio_kpp = QRadioButton(tr("kpp"))
        self.radio_bundle = QRadioButton(tr("bundle"))
        self.radio_bundle.setChecked(True)
        self.fmt_group.addButton(self.radio_kpp)
        self.fmt_group.addButton(self.radio_bundle)
        fmt_row.addWidget(self.radio_kpp)
        fmt_row.addWidget(self.radio_bundle)
        fmt_row.addStretch(1)
        out_layout.addLayout(fmt_row)

        dir_row = QHBoxLayout()
        self.out_dir_label = QLabel(tr("out_dir"))
        dir_row.addWidget(self.out_dir_label)
        self.out_dir_edit = QLineEdit(str(Path.cwd() / "converted"))
        dir_row.addWidget(self.out_dir_edit, 1)
        self.browse_btn = QPushButton(tr("browse"))
        self.browse_btn.clicked.connect(self._on_browse)
        dir_row.addWidget(self.browse_btn)
        out_layout.addLayout(dir_row)
        root.addWidget(self.out_group)

        # 转换按钮
        self.convert_btn = QPushButton(tr("convert"))
        self.convert_btn.setStyleSheet("padding:8px; font-size:14px;")
        self.convert_btn.clicked.connect(self._on_convert)
        self.convert_btn.setEnabled(False)
        root.addWidget(self.convert_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#666666;")
        root.addWidget(self.status_label)

    def _retranslate(self) -> None:
        """按当前语言重设静态文本；已加载笔刷时重建列表卡片。"""
        self.setWindowTitle(tr("window_title"))
        self.open_btn.setText(tr("open_abr"))
        self.file_label.setText(self._file_path or tr("no_file"))
        self.list_group.setTitle(tr("brush_list"))
        self.sel_all_btn.setText(tr("select_all"))
        self.sel_none_btn.setText(tr("select_none"))
        self.out_group.setTitle(tr("output"))
        self.fmt_label.setText(tr("format"))
        self.radio_kpp.setText(tr("kpp"))
        self.radio_bundle.setText(tr("bundle"))
        self.out_dir_label.setText(tr("out_dir"))
        self.browse_btn.setText(tr("browse"))
        self.convert_btn.setText(tr("convert"))
        if self._presets:
            self._populate(self._presets)
        self._refresh_status()

    def _set_status(self, key: str | None = None, **kwargs) -> None:
        """记录并显示状态文本（key 为 i18n 键，None 表示清空）。"""
        if key is None:
            self._status = None
            self.status_label.setText("")
        else:
            self._status = (key, kwargs)
            self.status_label.setText(tr(key, **kwargs))

    def _refresh_status(self) -> None:
        if self._status is None:
            self.status_label.setText("")
        else:
            key, kwargs = self._status
            self.status_label.setText(tr(key, **kwargs))

    # ---------- 事件 ----------
    def _on_lang_changed(self, index: int) -> None:
        """右上角语言切换：更新模块语言、持久化并重绘界面。"""
        lang = self.lang_combo.itemData(index)
        if lang == get_language():
            return
        set_language(lang)
        self._settings.setValue("language", lang)
        self._retranslate()

    def _on_open(self) -> None:
        """选择并异步解析 ABR 文件（ParseWorker 后台线程）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("select_abr"), "", tr("abr_filter"))
        if not path:
            return
        self._file_path = path
        self.file_label.setText(path)
        self._set_status("parsing")
        self.open_btn.setEnabled(False)
        self.convert_btn.setEnabled(False)
        self._parse_worker = ParseWorker(path)
        self._parse_worker.done.connect(self._on_parsed)
        self._parse_worker.start()

    def _on_parsed(self, abr, presets) -> None:
        """解析完成的回调：填充列表，失败则弹窗。"""
        self.open_btn.setEnabled(True)
        if abr is None:
            self._set_status(None)
            QMessageBox.critical(self, tr("parse_failed"), str(presets))
            return
        self._abr = abr
        self._presets = presets
        self._populate(presets)
        self._set_status("loaded", n=len(presets))
        self.convert_btn.setEnabled(True)

    def _populate(self, presets: list[BrushPreset]) -> None:
        """把预设填充为列表项（每项一个 BrushItemWidget 卡片）。"""
        self.brush_list.clear()
        for bp in presets:
            item = QListWidgetItem()
            widget = BrushItemWidget(bp)
            item.setSizeHint(widget.sizeHint())
            widget.checkbox.toggled.connect(self._update_count)
            self.brush_list.addItem(item)
            self.brush_list.setItemWidget(item, widget)
        self._update_count()

    def _selected_presets(self) -> list[BrushPreset]:
        """返回当前勾选的预设列表（按列表顺序）。"""
        selected = []
        for i in range(self.brush_list.count()):
            item = self.brush_list.item(i)
            widget = self.brush_list.itemWidget(item)
            if widget is not None and widget.checkbox.isChecked():
                selected.append(widget.preset)
        return selected

    def _set_all(self, checked: bool) -> None:
        """全选 / 全不选。"""
        for i in range(self.brush_list.count()):
            widget = self.brush_list.itemWidget(self.brush_list.item(i))
            if widget is not None:
                widget.checkbox.setChecked(checked)

    def _update_count(self) -> None:
        """刷新「共 N 支 · 已选 M 支」计数标签。"""
        total = self.brush_list.count()
        sel = len(self._selected_presets())
        self.count_label.setText(tr("count", total=total, sel=sel))

    def _on_browse(self) -> None:
        """弹出目录选择框，更新输出目录输入框。"""
        d = QFileDialog.getExistingDirectory(
            self, tr("select_out_dir"), self.out_dir_edit.text())
        if d:
            self.out_dir_edit.setText(d)

    def _on_convert(self) -> None:
        """校验勾选 → 未映射参数提醒 → 纹理亮度/对比度提示 → 后台转换。"""
        if self._abr is None:
            return
        selected = self._selected_presets()
        if not selected:
            QMessageBox.information(self, tr("hint"), tr("need_select"))
            return

        # 未映射参数提醒
        warned: dict[str, list[str]] = {}
        for bp in selected:
            if bp.warnings:
                warned[bp.name] = bp.warnings
        if warned:
            sep = tr("list_sep")
            lines = [
                f"· {name}{tr('colon')}{sep.join(tr_warning(w) for w in ws)}"
                for name, ws in warned.items()
            ]
            msg = tr("unmapped_body", lines="\n".join(lines))
            ret = QMessageBox.warning(
                self, tr("unmapped_title"), msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return

        # 纹理亮度/对比度为经验映射，提示用户转换后手动微调
        textured = [bp for bp in selected
                    if bp.texture is not None and bp.texture.image is not None]
        if textured:
            msg = tr("texture_hint_body", n=len(textured))
            ret = QMessageBox.warning(
                self, tr("texture_hint_title"), msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if ret != QMessageBox.StandardButton.Yes:
                return

        out_dir = self.out_dir_edit.text().strip() or "converted"
        make_bundle = self.radio_bundle.isChecked()
        make_standalone = self.radio_kpp.isChecked()

        self.convert_btn.setEnabled(False)
        self._set_status("converting")
        self._convert_worker = ConvertWorker(self._abr, selected, out_dir,
                                             make_bundle, make_standalone)
        self._convert_worker.done.connect(self._on_converted)
        self._convert_worker.start()

    def _on_converted(self, result) -> None:
        """转换完成的回调：显示输出位置，失败则弹错误框。"""
        self.convert_btn.setEnabled(True)
        if isinstance(result, str):  # 错误信息
            self._set_status(None)
            QMessageBox.critical(self, tr("convert_failed"), result)
            return
        r: ConvertResult = result
        paths = []
        if r.bundle_path:
            paths.append(r.bundle_path)
        if r.kpp_files and not r.bundle_path:
            paths.append(str(Path(r.out_dir) / "kpp"))
        self._set_status("done_status", n=len(r.kpp_files))
        QMessageBox.information(
            self, tr("done_title"),
            tr("done_body", n=len(r.kpp_files), paths="\n".join(paths)))


def main() -> int:
    """启动 GUI 主循环，返回应用退出码。"""
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
