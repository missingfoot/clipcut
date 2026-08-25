"""Scrubber: the timeline widget with a playhead and draggable cut segments."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QWidget


class Scrubber(QWidget):
    """Seek bar with a playhead plus draggable start/end trim handles."""

    seek_requested = Signal(float)
    segment_clicked = Signal(int)
    segment_edited = Signal(int, float, float)
    stepped = Signal(int)  # +1/-1 per wheel notch - forward/back one frame or keyframe

    HANDLE_GRAB = 6
    SNAP_GRAB = 10  # pixels - how close a dragged edge must land to another cut's edge to snap

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.duration = 0.0
        self.position = 0.0
        self.segments = []  # list of (start, end, is_active)
        self._drag_segment = None  # index into self.segments currently being dragged
        self.keyframe_times = []
        self._dragging = None
        self.edges_locked = False
        self.snap_enabled = False
        # Needed to receive mouseMoveEvent while no button is held, so hover
        # can show a resize cursor over a draggable edge handle.
        self.setMouseTracking(True)

    def set_edges_locked(self, locked):
        self.edges_locked = locked
        self.unsetCursor()

    def set_snap_enabled(self, enabled):
        self.snap_enabled = enabled

    def _snap_time(self, t, exclude_index):
        """Snap t to the nearest OTHER segment's start/end within SNAP_GRAB
        pixels, when snapping is on; otherwise (or with nothing close
        enough) return t unchanged."""
        if not self.snap_enabled or self.duration <= 0:
            return t
        x = self.x_for_time(t)
        best_t, best_dist = t, self.SNAP_GRAB
        for i, (start, end, _active, _fm) in enumerate(self.segments):
            if i == exclude_index:
                continue
            for edge in (start, end):
                dist = abs(x - self.x_for_time(edge))
                if dist < best_dist:
                    best_dist = dist
                    best_t = edge
        return best_t

    def set_keyframes(self, times):
        self.keyframe_times = times
        self.update()

    def set_duration(self, duration):
        self.duration = duration
        self.update()

    def set_position(self, seconds):
        self.position = seconds
        self.update()

    def set_cuts(self, segments):
        self.segments = segments
        self.update()

    def x_for_time(self, t):
        if self.duration <= 0:
            return 0
        return int(t / self.duration * self.width())

    def time_for_x(self, x):
        if self.width() <= 0 or self.duration <= 0:
            return 0.0
        return max(0.0, min(self.duration, x / self.width() * self.duration))

    def paintEvent(self, _event):
        painter = QPainter(self)
        # Same background role a QLineEdit uses, so the timeline reads as a text-entry
        # surface rather than an arbitrarily-picked dark gray.
        painter.fillRect(self.rect(), self.palette().color(QPalette.Base))
        if self.duration > 0:
            for kf in self.keyframe_times:
                kx = self.x_for_time(kf)
                painter.fillRect(kx, 0, 1, self.height(), QColor(255, 255, 255, 45))
            for start, end, is_active, _frame_mode in self.segments:
                x1, x2 = self.x_for_time(start), self.x_for_time(end)
                fill = QColor("#3a5f8a") if is_active else QColor("#2f4a63")
                painter.fillRect(x1, 0, max(1, x2 - x1), self.height(), fill)
                box_color = QColor("#7fc7ff") if is_active else QColor(255, 255, 255, 90)
                painter.fillRect(x1, 0, 1, self.height(), box_color)
                painter.fillRect(x2, 0, 1, self.height(), box_color)
                painter.fillRect(x1, 0, max(1, x2 - x1), 1, box_color)
                painter.fillRect(x1, self.height() - 1, max(1, x2 - x1), 1, box_color)
            px = self.x_for_time(self.position)
            painter.fillRect(px, 0, 1, self.height(), QColor("#ffffff"))
        # A single stroked rect, not four overlapping fillRects - overlapping semi-
        # transparent fills would double up at the corners and look brighter there.
        painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        painter.end()

    def _handle_at(self, x):
        if self.duration <= 0:
            return None, None
        for i, (start, end, _active, _frame_mode) in enumerate(self.segments):
            if abs(x - self.x_for_time(start)) <= self.HANDLE_GRAB:
                return i, "start"
            if abs(x - self.x_for_time(end)) <= self.HANDLE_GRAB:
                return i, "end"
        for i, (start, end, _active, _frame_mode) in enumerate(self.segments):
            if self.x_for_time(start) <= x <= self.x_for_time(end):
                return i, "body"
        return None, "playhead"

    def mousePressEvent(self, event):
        x = event.position().toPoint().x()
        index, kind = self._handle_at(x)
        if event.button() == Qt.MiddleButton:
            # Whole-cut move, mirroring the video preview's middle-drag pan -
            # only in Frame mode, since Keyframe-mode cuts are anchored to
            # detected keyframes rather than a freely-movable position.
            if kind == "body" and index is not None and self.segments[index][3]:
                self._dragging = "move"
                self._drag_segment = index
                self._move_anchor_x = x
                self._move_orig = self.segments[index][:2]
                self.setCursor(Qt.ClosedHandCursor)
            return
        if kind in ("start", "end") and self.edges_locked:
            # Edges are locked against accidental drags (packing many cuts
            # tightly together made a plain click too easy to mistake for a
            # resize) - treat a hit on the handle as a body click instead.
            kind = "body"
        if kind == "body":
            self.segment_clicked.emit(index)
            # Selection and seeking are not in conflict, and there's no other way
            # to scrub within this widget - fall through to a playhead drag.
            kind = "playhead"
            index = None
        self._dragging = kind
        self._drag_segment = index
        self._drag_to(x)

    def mouseMoveEvent(self, event):
        x = event.position().toPoint().x()
        if self._dragging == "move":
            self._move_to(x)
        elif self._dragging:
            self._drag_to(x)
        else:
            _index, kind = self._handle_at(x)
            if kind in ("start", "end") and not self.edges_locked:
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.unsetCursor()

    def mouseReleaseEvent(self, _event):
        if self._dragging == "move":
            self.unsetCursor()
        self._dragging = None

    def wheelEvent(self, event):
        if self.duration <= 0:
            return
        delta = event.angleDelta().y()
        if delta != 0:
            self.stepped.emit(1 if delta > 0 else -1)
        event.accept()

    def _drag_to(self, x):
        t = self.time_for_x(x)
        if self._dragging == "start" and self._drag_segment is not None:
            start, end, active, frame_mode = self.segments[self._drag_segment]
            new_start = max(0.0, min(self._snap_time(t, self._drag_segment), end - 0.1))
            self.segments[self._drag_segment] = (new_start, end, active, frame_mode)
            self.segment_edited.emit(self._drag_segment, new_start, end)
        elif self._dragging == "end" and self._drag_segment is not None:
            start, end, active, frame_mode = self.segments[self._drag_segment]
            new_end = min(self.duration, max(self._snap_time(t, self._drag_segment), start + 0.1))
            self.segments[self._drag_segment] = (start, new_end, active, frame_mode)
            self.segment_edited.emit(self._drag_segment, start, new_end)
        elif self._dragging == "playhead":
            self.position = t
            self.seek_requested.emit(t)
        self.update()

    def _move_to(self, x):
        delta = self.time_for_x(x) - self.time_for_x(self._move_anchor_x)
        orig_start, orig_end = self._move_orig
        length = orig_end - orig_start
        new_start = max(0.0, min(orig_start + delta, self.duration - length))
        new_end = new_start + length
        if self.snap_enabled:
            # Snap whichever edge (leading or trailing) lands closer to
            # another cut's edge, shifting the whole cut by that amount so
            # the two cuts end up touching with no gap or overlap.
            shift_start = self._snap_time(new_start, self._drag_segment) - new_start
            shift_end = self._snap_time(new_end, self._drag_segment) - new_end
            shift = 0.0
            if shift_start and (not shift_end or abs(shift_start) <= abs(shift_end)):
                shift = shift_start
            elif shift_end:
                shift = shift_end
            if shift:
                new_start = max(0.0, min(new_start + shift, self.duration - length))
                new_end = new_start + length
        _start, _end, active, frame_mode = self.segments[self._drag_segment]
        self.segments[self._drag_segment] = (new_start, new_end, active, frame_mode)
        self.segment_edited.emit(self._drag_segment, new_start, new_end)
        # Attach the playhead to the cut's new start so the preview shows
        # exactly where it's being dragged to as it moves.
        self.position = new_start
        self.seek_requested.emit(new_start)
        self.update()
