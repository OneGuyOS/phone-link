"""Pick an installed Android app to run on its own scrcpy display."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..apps import AppListLoader
from .icons import glyph_pixmap
from .theme import ACCENT, ACCENT_SOFT, DANGER, DIM, MUTED, TEXT


class AppStreamDialog(QDialog):
    """A searchable list of the phone's launchable apps."""

    def __init__(
        self,
        adb_path: str | None,
        serial: str,
        parent: QWidget | None = None,
        current: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.adb_path = adb_path
        self.serial = serial
        self._current = (current or "").strip()
        self._packages: list[str] = []
        self._loading = False
        self._failed = False
        self._loader = AppListLoader(adb_path, serial, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)

        self.setWindowTitle("Phone Link — stream an app")
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMaximumWidth(620)
        self.resize(560, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(13)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(13)
        mark = QLabel(self)
        mark.setFixedSize(46, 46)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(glyph_pixmap("apps", ACCENT, 26))
        mark.setStyleSheet(f"background: {ACCENT_SOFT}; border-radius: 11px;")
        header.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)

        heading = QVBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(2)
        title = QLabel("Stream an Android app", self)
        title.setStyleSheet(f"color: {TEXT}; font-size: 21px; font-weight: 600;")
        heading.addWidget(title)
        subtitle = QLabel(
            "The app opens in the mirror frame on its own virtual display. "
            "Your phone screen stays available for everything else.",
            self,
        )
        subtitle.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        subtitle.setTextFormat(Qt.TextFormat.PlainText)
        subtitle.setWordWrap(True)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)
        root.addLayout(header)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filter by package name, for example youtube or claude")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)

        self.app_list = QListWidget(self)
        self.app_list.setObjectName("AppList")
        self.app_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.app_list.setUniformItemSizes(True)
        self.app_list.setAlternatingRowColors(True)
        self.app_list.setMinimumHeight(300)
        self.app_list.currentItemChanged.connect(self._on_selection)
        self.app_list.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        root.addWidget(self.app_list, 1)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self.status = QLabel("Loading installed apps…", self)
        self.status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        self.retry = QPushButton("Try again", self)
        self.retry.clicked.connect(self._load)
        self.retry.hide()
        status_row.addWidget(self.retry)
        root.addLayout(status_row)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
        footer.setSpacing(9)
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.open_button = QPushButton("Open app", self)
        self.open_button.setObjectName("PrimaryButton")
        self.open_button.setDefault(True)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._accept_selection)
        footer.addWidget(self.open_button)
        root.addLayout(footer)

        self.search.setFocus()
        self._load()

    def selected_package(self) -> str | None:
        item = self.app_list.currentItem()
        if item is None:
            return None
        package = item.data(Qt.ItemDataRole.UserRole)
        return package if isinstance(package, str) and package else None

    def _load(self) -> None:
        self.retry.hide()
        self._loading = True
        self._failed = False
        self.status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self.status.setText("Loading installed apps…")
        self.app_list.clear()
        self.open_button.setEnabled(False)
        self._packages = []
        self._loader.start()

    def _on_loaded(self, packages: object) -> None:
        if not isinstance(packages, list):
            return
        self._loading = False
        self._packages = [str(package) for package in packages]
        self.status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self.status.setText(
            f"{len(self._packages)} installed apps. Type to filter, then press Open app."
        )
        self._filter()

    def _on_failed(self, message: str) -> None:
        self._loading = False
        self._failed = True
        self.status.setStyleSheet(f"color: {DANGER}; font-size: 11px;")
        self.status.setText(message)
        self.retry.show()
        self.open_button.setEnabled(False)

    def _filter(self) -> None:
        if self._loading or self._failed:
            return
        needle = self.search.text().strip().casefold()
        matches = [
            package
            for package in self._packages
            if not needle or needle in package.casefold()
        ]
        self.app_list.blockSignals(True)
        self.app_list.clear()
        for package in matches:
            item = QListWidgetItem(package, self.app_list)
            item.setData(Qt.ItemDataRole.UserRole, package)
            if package == self._current:
                self.app_list.setCurrentItem(item)
        self.app_list.blockSignals(False)
        if matches and self.app_list.currentItem() is None:
            self.app_list.setCurrentRow(0)

        if self._packages:
            if matches:
                self.status.setText(
                    f"{len(matches)} of {len(self._packages)} apps match."
                    if needle
                    else f"{len(self._packages)} installed apps. "
                    "Type to filter, then press Open app."
                )
                self.status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
            else:
                self.status.setText(f"No app matches “{self.search.text().strip()}”.")
                self.status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        else:
            self.status.setText("No launchable apps were found on this phone.")
            self.status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._on_selection(self.app_list.currentItem())

    def _on_selection(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None = None,
    ) -> None:
        del previous
        self.open_button.setEnabled(current is not None)

    def _accept_selection(self) -> None:
        if self.selected_package() is not None:
            self.accept()

    def done(self, result: int) -> None:
        self._loader.stop()
        super().done(result)
