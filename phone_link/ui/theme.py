"""Colors and application-wide styles."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication

BG = "#12171e"
BG_DEEP = "#0e1217"
SURFACE = "#1a2028"
SURFACE_ALT = "#202833"
SURFACE_HOVER = "#273140"
BORDER = "#2d3743"
BORDER_STRONG = "#3b4757"
TEXT = "#eef3f9"
TEXT_SOFT = "#c3ccd8"
MUTED = "#95a2b2"
DIM = "#697687"
ACCENT = "#3daee9"
ACCENT_HOVER = "#57bdf4"
ACCENT_PRESSED = "#2b9bd6"
ACCENT_SOFT = "#17384c"
SUCCESS = "#56d68a"
WARNING = "#f1c45a"
DANGER = "#ff7383"
CHARGING = "#56d68a"

APP_QSS = f"""
QWidget {{
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #07141d;
}}
QMainWindow, QWidget#Root {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {BG}, stop:1 {BG_DEEP});
}}
QLabel {{
    background: transparent;
}}
QLabel#Brand {{
    color: {MUTED};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#PhoneName {{
    color: {TEXT};
    font-size: 23px;
    font-weight: 600;
}}
QLabel#Subtitle {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#SectionLabel {{
    color: {MUTED};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.5px;
}}
QLabel#PanelTitle {{
    color: {TEXT};
    font-size: 21px;
    font-weight: 600;
}}
QLabel#PanelSubtitle {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#Badge {{
    color: {ACCENT};
    background: {ACCENT_SOFT};
    border: 1px solid #24536e;
    border-radius: 10px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#CardApp {{
    color: {MUTED};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#CardTime {{
    color: {DIM};
    font-size: 10px;
}}
QLabel#CardTitle {{
    color: {TEXT};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#CardBody {{
    color: {TEXT_SOFT};
    font-size: 12px;
}}
QLabel#CardError {{
    color: {DANGER};
    font-size: 11px;
}}
QLabel#Muted {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#Dim {{
    color: {DIM};
    font-size: 11px;
}}
QLabel#ShortcutKey {{
    color: {TEXT};
    background: #0f151c;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 9px;
    font-family: "JetBrains Mono";
    font-size: 11px;
    font-weight: 600;
}}
QLabel#OverlayTitle {{
    color: {TEXT};
    font-size: 16px;
    font-weight: 600;
}}
QLabel#OverlayBody {{
    color: {MUTED};
    font-size: 11px;
}}
QLabel#BatteryText {{
    color: {TEXT_SOFT};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#PillText {{
    color: {TEXT_SOFT};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#StatusDot {{
    color: {SUCCESS};
    font-size: 13px;
}}
QLabel#MediaTitle {{
    color: {TEXT};
    font-size: 13px;
    font-weight: 600;
}}
QFrame#MediaBar {{
    background: rgba(26, 32, 40, 235);
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QSlider#MediaSeek {{
    background: transparent;
    min-height: 14px;
}}
QSlider#MediaSeek::groove:horizontal {{
    height: 4px;
    background: #2b3542;
    border-radius: 2px;
}}
QSlider#MediaSeek::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider#MediaSeek::add-page:horizontal {{
    background: transparent;
}}
QSlider#MediaSeek::handle:horizontal {{
    width: 11px;
    height: 11px;
    margin: -4px 0;
    border-radius: 5px;
    background: {TEXT};
}}
QSlider#MediaSeek::handle:horizontal:hover {{
    background: {ACCENT_HOVER};
}}
QSlider#MediaSeek::handle:horizontal:disabled {{
    background: {BORDER_STRONG};
}}
QSlider#PhoneVolume {{
    background: transparent;
    min-height: 18px;
}}
QSlider#PhoneVolume::groove:horizontal {{
    height: 6px;
    background: #2b3542;
    border-radius: 3px;
}}
QSlider#PhoneVolume::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}
QSlider#PhoneVolume::add-page:horizontal {{
    background: transparent;
}}
QSlider#PhoneVolume::handle:horizontal {{
    width: 15px;
    height: 15px;
    margin: -5px 0;
    border-radius: 7px;
    background: {TEXT};
}}
QSlider#PhoneVolume::handle:horizontal:hover {{
    background: {ACCENT_HOVER};
}}
QSlider#PhoneVolume::handle:horizontal:disabled {{
    background: {BORDER_STRONG};
}}
QMenu {{
    background: #1c242e;
    border: 1px solid {BORDER_STRONG};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    color: {TEXT_SOFT};
    padding: 7px 20px 7px 14px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}
QMenu::item:disabled {{
    color: {DIM};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 5px 8px;
}}
QCheckBox {{
    color: {TEXT_SOFT};
    font-size: 12px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: #0f151c;
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QFrame#Header {{
    background: rgba(26, 32, 40, 210);
    border: 1px solid {BORDER};
    border-radius: 18px;
}}
QFrame#Panel {{
    background: rgba(26, 32, 40, 235);
    border: 1px solid {BORDER};
    border-radius: 18px;
}}
QFrame#PhoneFrame {{
    background: #080b0f;
    border: 1px solid #303b48;
    border-radius: 26px;
}}
QFrame#PhoneScreen {{
    background: #000000;
    border-radius: 19px;
}}
QFrame#NavBar {{
    background: rgba(26, 32, 40, 225);
    border: 1px solid {BORDER};
    border-radius: 18px;
}}
QPushButton#NavButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    color: {TEXT_SOFT};
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 500;
}}
QPushButton#NavButton:hover {{
    background: {SURFACE_HOVER};
    border-color: {BORDER};
    color: {TEXT};
}}
QPushButton#NavButton:pressed {{
    background: #18202a;
}}
QPushButton#NavButton:disabled {{
    background: transparent;
    color: {DIM};
}}
QWidget#MirrorPlaceholder {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #0d131a, stop:1 #070a0e);
}}
QWidget#NativeScreen {{
    background: #000000;
}}
QFrame#NotificationCard {{
    background: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#NotificationCard:hover {{
    background: {SURFACE_HOVER};
    border-color: {BORDER_STRONG};
}}
QFrame#NotificationCard[recent="true"] {{
    background: #0f151c;
    border-color: #1a222c;
}}
QFrame#NotificationCard[recent="true"]:hover {{
    background: #121922;
    border-color: #232d39;
}}
QFrame#NotificationCard[recent="true"] QLabel#CardTitle {{
    color: {MUTED};
}}
QFrame#NotificationCard[recent="true"] QLabel#CardBody {{
    color: {MUTED};
}}
QFrame#DropZone {{
    background: rgba(15, 21, 28, 160);
    border: 1px dashed #3a4553;
    border-radius: 12px;
}}
QFrame#DropZone[connected="false"] {{
    background: transparent;
    border-color: #242d37;
}}
QFrame#DropZone[active="true"] {{
    background: {ACCENT_SOFT};
    border-color: {ACCENT};
}}
QLabel#DropZoneLabel {{
    color: {MUTED};
    font-size: 11px;
}}
QFrame#DropZone[connected="false"] QLabel#DropZoneLabel {{
    color: {DIM};
}}
QFrame#DropZone[active="true"] QLabel#DropZoneLabel {{
    color: {TEXT};
}}
QFrame#Separator {{
    background: {BORDER};
    max-height: 1px;
    min-height: 1px;
}}
QPushButton {{
    background: {SURFACE_ALT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 7px 14px;
    font-size: 12px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {SURFACE_HOVER};
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed {{
    background: #1a222d;
}}
QPushButton:disabled {{
    color: {DIM};
    background: #1b212a;
    border-color: #242d37;
}}
QLineEdit {{
    background: #0f151c;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 9px;
    font-size: 12px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QPlainTextEdit {{
    background: #0f151c;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 9px;
    font-size: 12px;
    selection-background-color: {ACCENT};
}}
QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}
QPushButton#PrimaryButton {{
    background: {ACCENT};
    color: #07141d;
    border: 1px solid {ACCENT};
    font-weight: 700;
}}
QPushButton#PrimaryButton:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#PrimaryButton:pressed {{
    background: {ACCENT_PRESSED};
}}
QPushButton#GhostButton {{
    background: transparent;
    border: 1px solid transparent;
    color: {MUTED};
    padding: 6px 10px;
}}
QPushButton#GhostButton:hover {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}
QPushButton#DismissButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 2px;
}}
QPushButton#DismissButton:hover {{
    background: #3a2930;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 6px;
}}
QToolButton:hover {{
    background: {SURFACE_HOVER};
    border-color: {BORDER};
}}
QToolButton:pressed {{
    background: #18202a;
}}
QToolButton:checked {{
    background: {ACCENT_SOFT};
    border-color: #24536e;
}}
QToolButton:checked:hover {{
    background: #1b4259;
    border-color: {ACCENT};
}}
QToolButton:disabled {{
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 3px 2px 3px;
}}
QScrollBar::handle:vertical {{
    background: #3a4553;
    min-height: 36px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: #4a5768;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    height: 0;
}}
QToolTip {{
    color: {TEXT};
    background: #252e39;
    border: 1px solid {BORDER_STRONG};
    padding: 5px 8px;
    border-radius: 5px;
}}
QDialog {{
    background: {BG};
}}
QStatusBar {{
    background: transparent;
    color: {MUTED};
    font-size: 11px;
    border: none;
}}
QStatusBar::item {{
    border: none;
}}
QTextEdit#CodeBlock, QPlainTextEdit#CodeBlock {{
    background: #0d1218;
    color: #cfe8f7;
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 10px;
    selection-background-color: {ACCENT};
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont("Noto Sans")
    font.setPointSizeF(10.5)
    app.setFont(font)
    app.setStyleSheet(APP_QSS)


def muted_color() -> QColor:
    return QColor(MUTED)
