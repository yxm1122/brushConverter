"""主窗口：打开 ABR、预览笔尖、勾选转换、产物格式、未映射参数提醒。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QRadioButton,
    QVBoxLayout, QWidget, QButtonGroup, QLineEdit, QGroupBox,
)

from brush_converter.abr import AbrFile
from brush_converter.convert import ConvertResult
from brush_converter.kpp.kpp_writer import render_tip_preview
from brush_converter.mapping import BrushPreset

from .workers import ParseWorker, ConvertWorker


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

        info = f"{'计算笔刷' if preset.is_computed else '采样笔刷'} · 直径 {preset.diameter or '—'}px"
        info_label = QLabel(info)
        info_label.setStyleSheet("color:#888888; font-size:11px;")
        name_col.addWidget(info_label)

        if preset.warnings:
            warn = QLabel("⚠ 未映射：" + "、".join(preset.warnings))
            warn.setStyleSheet("color:#c98a1b; font-size:11px;")
            name_col.addWidget(warn)

        layout.addLayout(name_col)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    """主窗口：打开 ABR → 预览/勾选笔刷 → 选择产物格式 → 转换。

    解析与转换都在后台 QThread 执行（见 workers.py），避免阻塞界面。
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("brushConverter — ABR → Krita 笔刷转换")
        self.resize(760, 640)

        self._abr: AbrFile | None = None
        self._presets: list[BrushPreset] = []
        self._parse_worker: ParseWorker | None = None
        self._convert_worker: ConvertWorker | None = None

        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        """搭建界面：顶栏（打开文件）+ 笔刷列表 + 输出设置 + 转换按钮。"""
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 顶栏：打开文件
        top = QHBoxLayout()
        self.open_btn = QPushButton("打开 ABR 文件…")
        self.open_btn.clicked.connect(self._on_open)
        top.addWidget(self.open_btn)
        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet("color:#888888;")
        top.addWidget(self.file_label, 1)
        root.addLayout(top)

        # 笔刷列表
        list_group = QGroupBox("笔刷预览（勾选要转换的笔刷）")
        list_layout = QVBoxLayout(list_group)
        self.brush_list = QListWidget()
        self.brush_list.setSpacing(2)
        list_layout.addWidget(self.brush_list)

        sel_row = QHBoxLayout()
        self.sel_all_btn = QPushButton("全选")
        self.sel_all_btn.clicked.connect(lambda: self._set_all(True))
        self.sel_none_btn = QPushButton("全不选")
        self.sel_none_btn.clicked.connect(lambda: self._set_all(False))
        self.count_label = QLabel("")
        sel_row.addWidget(self.sel_all_btn)
        sel_row.addWidget(self.sel_none_btn)
        sel_row.addWidget(self.count_label)
        sel_row.addStretch(1)
        list_layout.addLayout(sel_row)
        root.addWidget(list_group, 1)

        # 输出设置
        out_group = QGroupBox("输出")
        out_layout = QVBoxLayout(out_group)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("产物格式："))
        self.fmt_group = QButtonGroup(self)
        self.radio_kpp = QRadioButton(".kpp（每支一个文件）")
        self.radio_bundle = QRadioButton(".bundle（Krita 资源包）")
        self.radio_bundle.setChecked(True)
        self.fmt_group.addButton(self.radio_kpp)
        self.fmt_group.addButton(self.radio_bundle)
        fmt_row.addWidget(self.radio_kpp)
        fmt_row.addWidget(self.radio_bundle)
        fmt_row.addStretch(1)
        out_layout.addLayout(fmt_row)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("输出目录："))
        self.out_dir_edit = QLineEdit(str(Path.cwd() / "converted"))
        dir_row.addWidget(self.out_dir_edit, 1)
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._on_browse)
        dir_row.addWidget(browse_btn)
        out_layout.addLayout(dir_row)
        root.addWidget(out_group)

        # 转换按钮
        self.convert_btn = QPushButton("开始转换")
        self.convert_btn.setStyleSheet("padding:8px; font-size:14px;")
        self.convert_btn.clicked.connect(self._on_convert)
        self.convert_btn.setEnabled(False)
        root.addWidget(self.convert_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#666666;")
        root.addWidget(self.status_label)

    # ---------- 事件 ----------
    def _on_open(self) -> None:
        """选择并异步解析 ABR 文件（ParseWorker 后台线程）。"""
        path, _ = QFileDialog.getOpenFileName(self, "选择 ABR 文件", "", "Photoshop 笔刷 (*.abr);;所有文件 (*)")
        if not path:
            return
        self.file_label.setText(path)
        self.status_label.setText("正在解析…")
        self.open_btn.setEnabled(False)
        self.convert_btn.setEnabled(False)
        self._parse_worker = ParseWorker(path)
        self._parse_worker.done.connect(self._on_parsed)
        self._parse_worker.start()

    def _on_parsed(self, abr, presets) -> None:
        """解析完成的回调：填充列表，失败则弹窗。"""
        self.open_btn.setEnabled(True)
        if abr is None:
            self.status_label.setText("")
            QMessageBox.critical(self, "解析失败", str(presets))
            return
        self._abr = abr
        self._presets = presets
        self._populate(presets)
        self.status_label.setText(f"已加载 {len(presets)} 支笔刷")
        self.convert_btn.setEnabled(True)

    def _populate(self, presets: list[BrushPreset]) -> None:
        """把预设填充为列表项（每项一个 BrushItemWidget 卡片）。"""
        self.brush_list.clear()
        for bp in presets:
            item = QListWidgetItem()
            item.setSizeHint(BrushItemWidget(bp).sizeHint())
            widget = BrushItemWidget(bp)
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
        self.count_label.setText(f"共 {total} 支 · 已选 {sel} 支")

    def _on_browse(self) -> None:
        """弹出目录选择框，更新输出目录输入框。"""
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.out_dir_edit.text())
        if d:
            self.out_dir_edit.setText(d)

    def _on_convert(self) -> None:
        """校验勾选 → 未映射参数提醒 → 后台转换。"""
        if self._abr is None:
            return
        selected = self._selected_presets()
        if not selected:
            QMessageBox.information(self, "提示", "请至少勾选一支笔刷。")
            return

        # 未映射参数提醒
        warned: dict[str, list[str]] = {}
        for bp in selected:
            if bp.warnings:
                warned[bp.name] = bp.warnings
        if warned:
            lines = [f"· {name}：{'、'.join(ws)}" for name, ws in warned.items()]
            msg = "以下笔刷包含无法映射的参数，转换后将丢失这些效果：\n\n" + "\n".join(lines) + "\n\n是否继续？"
            ret = QMessageBox.warning(self, "无法映射的参数", msg,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return

        out_dir = self.out_dir_edit.text().strip() or "converted"
        make_bundle = self.radio_bundle.isChecked()
        make_standalone = self.radio_kpp.isChecked()

        self.convert_btn.setEnabled(False)
        self.status_label.setText("正在转换…")
        self._convert_worker = ConvertWorker(self._abr, selected, out_dir,
                                             make_bundle, make_standalone)
        self._convert_worker.done.connect(self._on_converted)
        self._convert_worker.start()

    def _on_converted(self, result) -> None:
        """转换完成的回调：显示输出位置，失败则弹错误框。"""
        self.convert_btn.setEnabled(True)
        if isinstance(result, str):  # 错误信息
            self.status_label.setText("")
            QMessageBox.critical(self, "转换失败", result)
            return
        r: ConvertResult = result
        paths = []
        if r.bundle_path:
            paths.append(r.bundle_path)
        if r.kpp_files and not r.bundle_path:
            paths.append(str(Path(r.out_dir) / "kpp"))
        self.status_label.setText(f"完成：{len(r.kpp_files)} 支笔刷")
        QMessageBox.information(self, "转换完成",
                                f"成功转换 {len(r.kpp_files)} 支笔刷。\n\n输出位置：\n" + "\n".join(paths))


def main() -> int:
    """启动 GUI 主循环，返回应用退出码。"""
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
