"""WindowEventsMixin: window-level Qt event overrides (close, key events, drag/drop) and the Browse... file picker."""

from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLineEdit,
    QPushButton,
    QRadioButton,
)


class WindowEventsMixin:
    def closeEvent(self, event):
        QSettings().setValue("window_geometry", self.saveGeometry())
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space
                and not event.isAutoRepeat() and self.play_button.isEnabled()
                and not isinstance(obj, (QLineEdit, QPushButton, QCheckBox, QRadioButton,
                                          QComboBox, QAbstractItemView))):
            self.toggle_playback()
            return True
        if obj is self.preview.viewport():
            if event.type() in (QEvent.DragEnter, QEvent.DragMove):
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Drop:
                urls = event.mimeData().urls()
                if urls:
                    self.load_file(Path(urls[0].toLocalFile()))
                return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.load_file(Path(urls[0].toLocalFile()))

    def browse_for_file(self):
        filename, _filter = QFileDialog.getOpenFileName(self, "Open video", str(Path.home()), "Videos (*.mp4 *.mkv *.mov *.webm *.avi);;All files (*)")
        if filename:
            self.load_file(Path(filename))

