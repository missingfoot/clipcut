"""Small standalone widgets/data types with no cross-class dependencies."""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit


class NumberLineEdit(QLineEdit):
    """A QLineEdit for a non-negative integer that scrolling increments/decrements:
    1 per wheel notch, 10 per notch while Shift is held."""

    def wheelEvent(self, event):
        step = 10 if event.modifiers() & Qt.ShiftModifier else 1
        direction = 1 if event.angleDelta().y() > 0 else -1
        current = int(self.text() or self.placeholderText() or 0)
        self.setText(str(max(0, current + step * direction)))
        event.accept()


@dataclass
class Cut:
    """One entry in the cut list. start_repr/end_repr are a keyframe index or a
    frame number depending on frame_mode, matching how the Start/End fields
    already represent time - see read_keyframe_fields_seconds/
    read_frame_fields_seconds. end_repr=None means "through the end of the
    file", the same blank-field convention the UI already uses."""
    start_repr: int = 0
    end_repr: int | None = None
    frame_mode: bool = True
    crop: tuple[int, int, int, int] | None = None


class ScrollAdjustLineEdit(QLineEdit):
    """A QLineEdit whose numeric value nudges up/down by `step` per mouse-
    wheel notch, clamped to [minimum, maximum] - lets the GIF/WebP quality
    knobs be tuned without needing a full spin box. If allow_blank, scrolling
    below minimum clears the field back to blank ("Original") instead of
    clamping there, so Frame rate can scroll all the way back off."""

    def __init__(self, text="", minimum=0, maximum=100, step=1, allow_blank=False, parent=None):
        super().__init__(text, parent)
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.allow_blank = allow_blank

    def wheelEvent(self, event):
        delta = self.step if event.angleDelta().y() > 0 else -self.step
        text = self.text().strip()
        if not text:
            if delta < 0:
                event.accept()
                return
            value = self.minimum
        else:
            try:
                value = float(text) + delta
            except ValueError:
                event.ignore()
                return
        if self.allow_blank and value < self.minimum:
            self.setText("")
        else:
            # Rounding first kills float noise from repeated fractional
            # steps (e.g. 0.1 + 0.1 + 0.1 landing on 0.30000000000000004).
            value = round(max(self.minimum, min(self.maximum, value)), 4)
            self.setText(str(int(value)) if value == int(value) else str(value))
        event.accept()
