"""Application entry point.  Qt must be imported after the platform is chosen."""

from __future__ import annotations

import signal
import sys

from .bootstrap import configure_qt_platform

configure_qt_platform()

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from . import __version__  # noqa: E402
from .dependencies import check_dependencies  # noqa: E402
from .ui.dialogs import DependencyDialog  # noqa: E402
from .ui.icons import app_icon  # noqa: E402
from .ui.main_window import MainWindow  # noqa: E402
from .ui.theme import apply_theme  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    if any(flag in {"--version", "-V"} for flag in arguments[1:]):
        print(f"Phone Link {__version__}")
        return 0
    app = QApplication(arguments)
    app.setApplicationName("Phone Link")
    app.setApplicationDisplayName("Phone Link")
    app.setOrganizationName("Phone Link")
    app.setDesktopFileName("phone-link")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(True)
    apply_theme(app)

    # Let Ctrl+C in the launching terminal close the Qt event loop cleanly.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    report = check_dependencies(app.platformName())
    if report.has_problems:
        dialog = DependencyDialog(report)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return 1
        report = dialog.report

    window = MainWindow(report)
    window.show()
    return app.exec()
