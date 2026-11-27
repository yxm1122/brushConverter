"""Small, native-palette-based visual refinements; no forced light/dark theme."""

WINDOW_STYLE = """
QGroupBox {
    border: 1px solid palette(midlight);
    border-radius: 8px;
    margin-top: 12px;
    padding: 2px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}
QPushButton {
    padding: 5px 10px;
    min-height: 20px;
}
QPushButton#primaryAction {
    background: palette(highlight);
    color: palette(highlighted-text);
    border: 1px solid palette(highlight);
    border-radius: 6px;
    font-weight: bold;
}
QPushButton#primaryAction:disabled {
    background: palette(midlight);
    color: palette(mid);
    border-color: palette(midlight);
}
QPushButton#primaryAction:focus {
    border: 1px dashed palette(text);
}
QLineEdit, QComboBox {
    padding: 4px 6px;
    min-height: 20px;
}
QListWidget#brushList {
    border: none;
    background: palette(window);
    outline: none;
}
QListWidget#brushList::item {
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 8px;
}
QListWidget#brushList::item:hover {
    border-color: palette(highlight);
}
QListWidget#brushList::item:selected {
    background: palette(alternate-base);
    border: 1px solid palette(highlight);
    color: palette(text);
}
QListWidget#brushList::item:focus {
    border: 1px dashed palette(highlight);
}
QLabel#brushName, QLabel#currentBrush {
    font-weight: bold;
}
QLabel#brushThumbnail {
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 6px;
}
QLabel#selectionCount {
    padding: 6px;
    border-top: 1px solid palette(midlight);
}
QSplitter::handle {
    background: palette(midlight);
    margin: 10px 2px;
    border-radius: 2px;
}
"""
