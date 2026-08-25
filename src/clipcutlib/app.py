"""Application bootstrap: constructs QApplication and the main window."""

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from clipcutlib.main_window import MainWindow


def main(argv=None, base_dir=None):
    app = QApplication(argv if argv is not None else sys.argv)
    # Organization/application name give QSettings() a config file to write
    # to (~/.config/clipcut/clipcut.conf) - without these it has nowhere
    # sensible to store window_geometry between runs.
    app.setOrganizationName("clipcut")
    app.setApplicationName("clipcut")
    # The installed package ships this as a themed icon (see PKGBUILD/
    # clipcut.desktop); fromTheme finds it there. Running straight from a
    # source checkout (no install step) falls back to the .png sitting next
    # to the launcher script instead (base_dir, passed in by it), so the
    # window/taskbar icon isn't blank in dev.
    icon = QIcon.fromTheme("clipcut")
    if icon.isNull() and base_dir is not None:
        local_icon = Path(base_dir) / "clipcut.png"
        if local_icon.exists():
            icon = QIcon(str(local_icon))
    if not icon.isNull():
        app.setWindowIcon(icon)
    args = argv if argv is not None else sys.argv
    initial = Path(args[1]).resolve() if len(args) > 1 else None
    win = MainWindow(initial)
    win.show()
    sys.exit(app.exec())
