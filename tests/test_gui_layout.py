"""Synthetic, offscreen regressions for width-aware brush cards and compact GUI layout."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSettings  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from brush_converter.abr import AbrFile  # noqa: E402
from brush_converter.export_options import source_digest_of_bytes  # noqa: E402
from brush_converter.mapping import BrushPreset, TextureSettings  # noqa: E402
from gui import i18n, main_window  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    assert application.platformName() == "offscreen", "Never show windows on the user desktop"
    original = QFont(application.font())
    font = QFont(original)
    font.setPointSize(9)
    # The Windows offscreen backend does not enumerate native font fallbacks.
    # Use an installed UI typeface so bilingual layout is measured, not tofu boxes.
    font_ids = []
    if sys.platform == "win32":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc"):
            font_id = QFontDatabase.addApplicationFont(str(fonts / name))
            if font_id >= 0:
                font_ids.append(font_id)
        if font_ids:
            font.setFamily(QFontDatabase.applicationFontFamilies(font_ids[0])[0])
    application.setFont(font)
    yield application
    application.setFont(original)
    for font_id in font_ids:
        QFontDatabase.removeApplicationFont(font_id)


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    settings.setValue("language", "zh")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    asynchronous_errors = []
    monkeypatch.setattr(sys, "excepthook",
                        lambda *_args: asynchronous_errors.append(_args[1]))
    win = MainWindow()
    yield win
    assert not win._active_workers
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert not asynchronous_errors, asynchronous_errors
    i18n.set_language("zh")


def load(window, *, count=4):
    """In-memory source data only: no ABR parsing, no conversion and no user settings."""
    texture = TextureSettings("pattern", "synthetic", scale=200, brightness=150,
                              contrast=-50, image=np.arange(64, dtype=np.uint8).reshape(8, 8))
    presets = [
        BrushPreset("水彩铺色 Watercolour wash", diameter=120, hardness=50,
                    is_computed=True, texture=texture),
        BrushPreset("LongUnbrokenBrushName" * 6 + "无空格长名称" * 6, diameter=36,
                    is_computed=True, warnings=["A long synthetic unmapped-parameter warning " * 3]),
        BrushPreset("采样笔刷 Sampled tip", diameter=64, is_computed=False,
                    tip_gray=np.full((8, 12), 160, dtype=np.uint8)),
        BrushPreset("缺失笔尖 Missing tip", diameter=80, is_computed=False),
    ][:count]
    window._file_path = "Synthetic collection.abr"
    window._on_parsed(AbrFile(6, 2, name="Synthetic collection"), presets,
                      source_digest=source_digest_of_bytes(b"gui-layout-regression"),
                      show_dialog=False)
    return window._selection


def settle(app, window):
    """Process queued layout work, then prove the timer and sizes have converged."""
    for _ in range(16):
        app.processEvents()
    view = window.brush_list
    before = [view.item(index).sizeHint() for index in range(view.count())]
    for _ in range(8):
        app.processEvents()
    assert before == [view.item(index).sizeHint() for index in range(view.count())]
    assert not view._layout_timer.isActive(), "Layout must not keep scheduling itself"


def assert_cards_fit(window):
    view = window.brush_list
    checked = 0
    for item, card in window._items.values():
        if item.isHidden():
            continue
        checked += 1
        assert view.visualItemRect(item).width() <= view.viewport().width()
        assert card.width() <= view.viewport().width()
        assert card.width() >= card.minimumSizeHint().width()
        assert card.height() >= card.heightForWidth(card.width())
        for label in card.findChildren(QLabel):
            if not label.wordWrap() or not label.text() or label.isHidden():
                continue
            assert label.width() > 0
            assert label.height() >= label.heightForWidth(label.width()), label.text()
            origin = label.mapTo(card, QPoint(0, 0))
            assert origin.x() >= 0 and origin.y() >= 0
            assert origin.x() + label.width() <= card.width()
            assert origin.y() + label.height() <= card.height()
        for control in (card.checkbox, card.edit_btn):
            origin = control.mapTo(card, QPoint(0, 0))
            assert origin.x() >= 0 and origin.y() >= 0
            assert origin.x() + control.width() <= card.width()
            assert origin.y() + control.height() <= card.height()
    assert checked
    assert view.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("size", [(1120, 820), (820, 620), (720, 540)])
def test_cards_fit_window_sizes(window, app, language, size):
    model = load(window)
    window.lang_combo.setCurrentIndex(window.lang_combo.findData(language))
    window.resize(*size)
    window.show()
    state = model.options.dump_config()
    selected = model.selected_ids
    window.splitter.setSizes([size[0] // 2, size[0] // 2])
    settle(app, window)
    assert (window.width(), window.height()) == size
    language_width = window.lang_combo.fontMetrics().horizontalAdvance(
        window.lang_combo.currentText()
    )
    assert window.lang_combo.width() >= language_width + 32
    assert_cards_fit(window)
    assert model.options.dump_config() == state
    assert model.selected_ids == selected
    assert not window._active_workers


def test_splitter_narrow_then_wide_remeasures_rows(window, app):
    load(window)
    window.resize(1200, 840)
    window.show()
    window.splitter.setSizes([650, 500])
    settle(app, window)
    wide = [item.sizeHint().height() for item, _ in window._items.values()]
    window.splitter.setSizes([290, 860])
    settle(app, window)
    assert window.brush_list.viewport().width() < 300
    assert_cards_fit(window)
    narrow = [item.sizeHint().height() for item, _ in window._items.values()]
    assert all(a >= b for a, b in zip(narrow, wide))
    assert any(a > b for a, b in zip(narrow, wide))
    window.splitter.setSizes([650, 500])
    settle(app, window)
    assert_cards_fit(window)
    assert [item.sizeHint().height() for item, _ in window._items.values()] == wide


def test_content_changes_at_same_width_remeasure_without_losing_state(window, app):
    model = load(window)
    first, second, *_ = model.selected_ids
    model.set_selected(second, False)
    window._apply_view()
    window.resize(920, 680)
    window.show()
    settle(app, window)
    original = window._items[first][0].sizeHint().height()
    state = model.options.dump_config()
    selected = model.selected_ids
    window.tag_editor.common_edit.setPlainText("\n".join(
        f"Long bilingual tag for wrapping 测试标签 {index}" for index in range(8)
    ))
    settle(app, window)
    assert_cards_fit(window)
    assert window._items[first][0].sizeHint().height() > original
    assert model.options.dump_config() == state
    assert model.selected_ids == selected
    tags = window._tags.freeze(model.options.preset_ids)
    window.radio_kpp.setChecked(True)
    settle(app, window)
    assert_cards_fit(window)
    assert window._items[first][1].tags.text() == i18n.tr("metadata_kpp_scope")
    window.radio_bundle.setChecked(True)
    settle(app, window)
    assert_cards_fit(window)
    assert window._tags.freeze(model.options.preset_ids) == tags


@pytest.mark.parametrize("extra_points", [4, 9])
def test_font_changes_at_same_width_remeasure(window, app, extra_points):
    load(window)
    window.show()
    settle(app, window)
    before = [item.sizeHint().height() for item, _ in window._items.values()]
    width = window.brush_list.viewport().width()
    window.list_group.setFixedWidth(window.list_group.width())
    original = QFont(app.font())
    font = QFont(original)
    font.setPointSize(max(9, font.pointSize()) + extra_points)
    try:
        app.setFont(font)
        settle(app, window)
        assert window.brush_list.viewport().width() == width
        assert_cards_fit(window)
        assert all(card.name_label.font().bold() for _item, card in window._items.values())
        assert any(item.sizeHint().height() > height
                   for (item, _), height in zip(window._items.values(), before))
    finally:
        app.setFont(original)
        settle(app, window)


def test_style_border_and_padding_are_included_in_item_height(window, app):
    load(window)
    window.show()
    settle(app, window)
    window.brush_list.setStyleSheet("""
        QListWidget#brushList::item {
            border: 3px solid palette(highlight);
            padding: 7px 5px;
        }
    """)
    settle(app, window)
    assert_cards_fit(window)
    for item, card in window._items.values():
        assert item.sizeHint().height() >= card.heightForWidth(card.width()) + 20


def test_empty_states_filtering_and_hidden_rows_reflow(window, app):
    window.show()
    settle(app, window)
    assert window.brush_list.placeholder.isVisible()
    assert window.brush_list.placeholder.text() == i18n.tr("list_empty")
    model = load(window)
    settle(app, window)
    assert not window.brush_list.placeholder.isVisible()
    selected = model.selected_ids
    window.search_edit.setText("no matching synthetic brush")
    settle(app, window)
    assert window.brush_list.placeholder.isVisible()
    assert window.brush_list.placeholder.text() == i18n.tr("list_no_matches")
    assert model.counts["hidden"] == len(selected)
    window.resize(780, 580)
    window.tag_editor.common_edit.setPlainText("A long tag while all brushes are hidden " * 2)
    window.search_edit.clear()
    settle(app, window)
    assert_cards_fit(window)
    assert not window.brush_list.placeholder.isVisible()
    assert model.selected_ids == selected
    window.lang_combo.setCurrentIndex(window.lang_combo.findData("en"))
    window.search_edit.setText("no matching synthetic brush")
    settle(app, window)
    assert window.brush_list.placeholder.text() == i18n.tr("list_no_matches")


def test_language_and_sort_preserve_selection_tags_and_options(window, app):
    model = load(window)
    first, second, *_ = model.selected_ids
    window._on_checked(second, False)
    window.brush_list.setCurrentItem(window._items[first][0])
    window._on_switch_changed("rasterize_computed", False)
    window.tag_editor.common_edit.setPlainText("Synthetic common tag\n合成公共标签")
    window.metadata_fields["author"].setText("Synthetic author")
    window.show()
    settle(app, window)
    state = model.options.dump_config()
    selected = model.selected_ids
    tags = window._tags.freeze(model.options.preset_ids)
    for language in ("en", "zh"):
        window.lang_combo.setCurrentIndex(window.lang_combo.findData(language))
        window.sort_combo.setCurrentIndex(window.sort_combo.findData("name_desc"))
        settle(app, window)
        assert_cards_fit(window)
        assert model.options.dump_config() == state
        assert model.selected_ids == selected
        assert model.focused_id == first
        assert window._tags.freeze(model.options.preset_ids) == tags
        assert window.metadata_fields["author"].text() == "Synthetic author"


def test_tall_card_scrolls_to_its_end_in_pixels(window, app):
    load(window, count=1)
    window.resize(800, 600)
    window.show()
    card = next(iter(window._items.values()))[1]
    card.summary.setText("\n".join(f"Synthetic detail line {i}" for i in range(45)))
    window.brush_list.request_item_layout()
    settle(app, window)
    assert_cards_fit(window)
    view = window.brush_list
    assert view.verticalScrollMode() == view.ScrollMode.ScrollPerPixel
    assert card.height() > view.viewport().height()
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
    settle(app, window)
    bottom = card.edit_btn.mapTo(view.viewport(), QPoint(0, card.edit_btn.height()))
    assert 0 < bottom.y() <= view.viewport().height()


def test_scrollbar_appearance_and_disappearance_converge(window, app):
    load(window, count=1)
    window.resize(1200, 1500)
    window.show()
    settle(app, window)
    assert not window.brush_list.verticalScrollBar().isVisible()
    width_without_scrollbar = window.brush_list.viewport().width()
    window.resize(1200, 560)
    settle(app, window)
    assert window.brush_list.verticalScrollBar().isVisible()
    assert window.brush_list.viewport().width() < width_without_scrollbar
    assert_cards_fit(window)
    window.resize(1200, 1500)
    settle(app, window)
    assert not window.brush_list.verticalScrollBar().isVisible()
    assert_cards_fit(window)


def test_queued_layout_survives_clear_rebuild_and_window_deletion(window, app):
    load(window)
    window.show()
    window.brush_list.request_item_layout()
    window.brush_list.clear()
    window._items.clear()
    settle(app, window)
    window._populate()
    window.lang_combo.setCurrentIndex(window.lang_combo.findData("en"))
    settle(app, window)
    assert_cards_fit(window)
    window.brush_list.request_item_layout()
    window.close()
    # Fixture teardown deletes the window and dispatches DeferredDelete events.


def test_inspect_button_scrolls_to_current_brush(window, app):
    model = load(window)
    window.resize(820, 620)
    window.show()
    settle(app, window)
    second = model.options.preset_ids[1]
    window._items[second][1].edit_btn.click()
    settle(app, window)
    assert model.focused_id == second
    position = window.current_label.mapTo(window.detail_scroll.viewport(), QPoint(0, 0))
    assert position.y() < window.detail_scroll.viewport().height()
    assert position.y() + window.current_label.height() > 0
    assert not window._active_workers


@pytest.mark.parametrize("dark", [False, True])
def test_palette_and_focus_changes_keep_cards_readable(window, app, dark):
    load(window)
    window.show()
    settle(app, window)
    original = QPalette(app.palette())
    palette = QPalette(original)
    palette.setColor(QPalette.ColorRole.Window, QColor("#22262c" if dark else "#f2f4f7"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#171b20" if dark else "#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#303741" if dark else "#edf2f8"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f0f2f4" if dark else "#17212c"))
    palette.setColor(QPalette.ColorRole.WindowText, palette.color(QPalette.ColorRole.Text))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#aeb8c4" if dark else "#596879"))
    try:
        app.setPalette(palette)
        window.brush_list.setCurrentRow(1)
        window.brush_list.setFocus()
        settle(app, window)
        assert_cards_fit(window)
        for _item, card in window._items.values():
            label = card.identity_label
            assert label.foregroundRole() == QPalette.ColorRole.PlaceholderText
            assert label.palette().color(label.foregroundRole()) == palette.color(
                QPalette.ColorRole.PlaceholderText
            )
    finally:
        app.setPalette(original)
        settle(app, window)
