"""Startup dependency report and other small dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..dependencies import DependencyReport, check_dependencies, installation_text
from .icons import app_icon_pixmap, glyph_pixmap
from .theme import (
    BORDER,
    DANGER,
    DIM,
    MUTED,
    SUCCESS,
    SURFACE_ALT,
    TEXT,
    TEXT_SOFT,
    WARNING,
)


class DependencyRow(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DepRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)
        self.icon = QLabel(self)
        self.icon.setFixedSize(20, 20)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.title = QLabel(self)
        self.title.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 600;")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        text.addWidget(self.title)
        self.detail = QLabel(self)
        self.detail.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)

        self.badge = QLabel(self)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)

    def set_item(self, label: str, ok: bool, detail: str, severity: str) -> None:
        self.title.setText(label)
        self.detail.setText(detail)
        if ok:
            self.icon.setPixmap(glyph_pixmap("check", SUCCESS, 18))
            border = BORDER
        elif severity == "required":
            self.icon.setPixmap(glyph_pixmap("close", DANGER, 18))
            border = "#59323b"
        else:
            self.icon.setPixmap(glyph_pixmap("warning", WARNING, 18))
            border = "#4d432c"
        self.setStyleSheet(
            f"QFrame#DepRow {{ background: {SURFACE_ALT}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        if ok:
            self.badge.setText("Ready")
            self.badge.setStyleSheet(f"color: {SUCCESS}; font-size: 10px; font-weight: 700;")
        elif severity == "required":
            self.badge.setText("Required")
            self.badge.setStyleSheet(f"color: {DANGER}; font-size: 10px; font-weight: 700;")
        else:
            self.badge.setText("Optional")
            self.badge.setStyleSheet(f"color: {WARNING}; font-size: 10px; font-weight: 700;")


class ShortcutsDialog(QDialog):
    """A small reference for the window-level keyboard shortcuts."""

    SHORTCUTS = (
        ("F5 / Ctrl+R", "Refresh the phone and notification state"),
        ("Ctrl+Shift+R", "Rotate the mirrored screen between portrait and landscape"),
        ("Ctrl+Shift+D", "Switch the mirror between the phone screen and Samsung DeX"),
        ("Ctrl+Shift+P", "Pop the mirror or DeX out into its own window, or dock it"),
        ("Ctrl+Shift+S", "Save a screenshot of the phone screen"),
        ("Ctrl+Shift+V", "Start or stop recording the phone screen to a video"),
        ("Ctrl+F", "Focus the notification search box"),
        ("Ctrl+Shift+M", "Open the SMS messages tab"),
        ("Ctrl+Shift+A", "Switch the right panel between notifications and the agent"),
        ("Esc", "Clear the search while it has focus"),
        ("F11", "Toggle full screen"),
        ("F1", "Show this shortcut list"),
        ("Ctrl+Q", "Quit Phone Link"),
        ("Ctrl+W", "Hide Phone Link to the system tray"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Phone Link — keyboard shortcuts")
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        self.setModal(True)
        self.setMinimumWidth(470)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        title = QLabel("Keyboard shortcuts", self)
        title.setStyleSheet(f"color: {TEXT}; font-size: 20px; font-weight: 600;")
        root.addWidget(title)
        intro = QLabel("These work while the Phone Link window is active.", self)
        intro.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        root.addWidget(intro)

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(9)
        for row, (keys, description) in enumerate(self.SHORTCUTS):
            key_label = QLabel(keys, self)
            key_label.setObjectName("ShortcutKey")
            key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(key_label, row, 0)
            text = QLabel(description, self)
            text.setStyleSheet(f"color: {TEXT_SOFT}; font-size: 12px;")
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setWordWrap(True)
            grid.addWidget(text, row, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.setObjectName("PrimaryButton")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)


class DependencyDialog(QDialog):
    """Shows what is missing, how to install it, and lets the user re-check."""

    def __init__(self, report: DependencyReport, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("Phone Link — setup")
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        self.setModal(True)
        self.setMinimumWidth(720)
        self.setMaximumWidth(820)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(14)
        mark = QLabel(self)
        mark.setFixedSize(54, 54)
        mark.setPixmap(app_icon_pixmap(54))
        header.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        heading = QVBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(2)
        self.title = QLabel(self)
        self.title.setStyleSheet(f"color: {TEXT}; font-size: 22px; font-weight: 600;")
        heading.addWidget(self.title)
        self.intro = QLabel(self)
        self.intro.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.intro.setTextFormat(Qt.TextFormat.PlainText)
        self.intro.setWordWrap(True)
        heading.addWidget(self.intro)
        header.addLayout(heading, 1)
        root.addLayout(header)

        self.rows_host = QWidget(self)
        self.rows = QVBoxLayout(self.rows_host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(7)
        root.addWidget(self.rows_host)

        self.commands_title = QLabel("Install the missing pieces", self)
        self.commands_title.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 600;")
        root.addWidget(self.commands_title)
        self.commands = QPlainTextEdit(self)
        self.commands.setObjectName("CodeBlock")
        self.commands.setReadOnly(True)
        self.commands.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.commands.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.commands.setFixedHeight(180)
        font = self.commands.font()
        font.setFamily("JetBrains Mono")
        font.setStyleHint(font.StyleHint.Monospace)
        font.setPixelSize(11)
        self.commands.setFont(font)
        root.addWidget(self.commands)

        self.hint = QLabel(
            "After installing, press Re-check. Phone Link will keep working with the parts that are available.",
            self,
        )
        self.hint.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 2, 0, 0)
        buttons.setSpacing(8)
        self.copy_button = QPushButton("Copy commands", self)
        self.copy_button.setObjectName("GhostButton")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy_commands)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        self.quit_button = QPushButton("Quit", self)
        self.quit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quit_button.clicked.connect(self.reject)
        buttons.addWidget(self.quit_button)
        self.recheck_button = QPushButton("Re-check", self)
        self.recheck_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recheck_button.clicked.connect(self.recheck)
        buttons.addWidget(self.recheck_button)
        self.continue_button = QPushButton("Continue anyway", self)
        self.continue_button.setObjectName("PrimaryButton")
        self.continue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continue_button.clicked.connect(self.accept)
        buttons.addWidget(self.continue_button)
        root.addLayout(buttons)

        self._populate()

    def _populate(self) -> None:
        report = self.report
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for check in report.items:
            row = DependencyRow(self.rows_host)
            row.set_item(check.label, check.ok, check.detail, check.severity)
            self.rows.addWidget(row)

        required = report.missing_required
        problems = report.problems
        if not problems:
            self.title.setText("Everything is ready")
            self.intro.setText("adb, scrcpy and KDE Connect were found. Starting Phone Link…")
            self.continue_button.setText("Open Phone Link")
            self.hint.hide()
        elif required:
            missing = {item.key for item in required}
            mirrors = missing & {"adb", "scrcpy"}
            needs_kde = "kdeconnect" in missing
            self.title.setText("Phone Link needs a few tools")
            if mirrors and needs_kde:
                affected = "Screen mirroring and notifications will stay offline"
            elif mirrors:
                affected = "Screen mirroring will stay offline"
            else:
                affected = "The notification panel will stay offline"
            self.intro.setText(
                f"{len(required)} required piece{'s' if len(required) != 1 else ''} "
                f"missing. You can still open the app, but {affected[0].lower() + affected[1:]} "
                "until the missing tools are installed."
            )
            self.continue_button.setText("Continue anyway")
        else:
            self.title.setText("Phone Link is almost ready")
            self.intro.setText(
                "The required tools are installed. Some optional pieces are not ready, "
                "so parts of the window will stay offline until they are."
            )
            self.continue_button.setText("Open Phone Link")

        commands = installation_text(report)
        self.commands.setPlainText(commands)
        has_commands = bool(commands.strip())
        self.commands.setVisible(has_commands)
        self.commands_title.setVisible(has_commands)
        self.copy_button.setVisible(has_commands)

        if not problems:
            QTimer.singleShot(900, self.accept)

    def recheck(self) -> None:
        self.report = check_dependencies(self.report.qt_platform)
        self._populate()

    def _copy_commands(self) -> None:
        QApplication.clipboard().setText(self.commands.toPlainText())
        self.copy_button.setText("Copied")
        QTimer.singleShot(1500, lambda: self.copy_button.setText("Copy commands"))
