"""Small QGraphicsRectItem-based interactive elements used by the crop overlay."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainterPath
from PySide6.QtWidgets import QGraphicsRectItem


class GuideItem(QGraphicsRectItem):
    """A draggable crop guide line. Calls back with its new scene-coordinate
    position (y for horizontal guides, x for vertical) while dragged."""

    INACTIVE_COLOR = QColor(255, 255, 255, 60)
    HIT_PADDING = 5  # grabbable area extends this far past the 1px visual line on each side

    def __init__(self, orientation, on_press, on_drag, on_double_click):
        super().__init__()
        self.orientation = orientation  # 'h' (drags vertically) or 'v' (drags horizontally)
        self.on_press = on_press
        self.on_drag = on_drag
        self.on_double_click = on_double_click
        self.active_color = QColor(255, 0, 255)
        self.setCursor(Qt.SizeVerCursor if orientation == "h" else Qt.SizeHorCursor)
        self.setPen(Qt.NoPen)
        self.setBrush(Qt.NoBrush)
        self.setZValue(10)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self._dragging = False
        self._active = False

    def set_active(self, active):
        if active != self._active:
            self._active = active
            self.update()

    def set_active_color(self, color):
        self.active_color = color
        self.update()

    def paint(self, painter, _option, _widget=None):
        painter.fillRect(self.rect(), self.active_color if self._active else self.INACTIVE_COLOR)

    def boundingRect(self):
        # Wider than the painted line so the guide isn't a finnicky 1px target to grab.
        pad = self.HIT_PADDING
        return self.rect().adjusted(0, -pad, 0, pad) if self.orientation == "h" \
            else self.rect().adjusted(-pad, 0, pad, 0)

    def shape(self):
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def mousePressEvent(self, event):
        self._dragging = True
        self.on_press()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        pos = event.scenePos()
        self.on_drag(pos.y() if self.orientation == "h" else pos.x())

    def mouseReleaseEvent(self, _event):
        self._dragging = False

    def mouseDoubleClickEvent(self, event):
        self._dragging = False
        self.on_double_click()
        event.accept()


class CropMoveItem(QGraphicsRectItem):
    """Invisible drag surface over the crop interior. Pans whichever axes currently
    have both bounding guides pulled in (left+right, top+bottom, or both)."""

    def __init__(self, on_press, on_drag):
        super().__init__()
        self.on_press = on_press
        self.on_drag = on_drag
        self.setPen(Qt.NoPen)
        self.setBrush(Qt.NoBrush)
        self.setZValue(8)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self._dragging = False
        self._start_pos = None

    def set_axes(self, h_active, v_active):
        # A distinct grab cursor (vs. the guides' resize cursors) so pan vs.
        # single-edge resize is obvious at a glance.
        self.setCursor(Qt.ClosedHandCursor if self._dragging else Qt.OpenHandCursor)
        self.setVisible(h_active or v_active)

    def mousePressEvent(self, event):
        self._dragging = True
        self._start_pos = event.scenePos()
        self.setCursor(Qt.ClosedHandCursor)
        self.on_press()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        pos = event.scenePos()
        # Total delta from drag start (not incremental) so sub-pixel motion
        # accumulates instead of being truncated away on every single move event.
        self.on_drag(pos.x() - self._start_pos.x(), pos.y() - self._start_pos.y())

    def mouseReleaseEvent(self, _event):
        self._dragging = False
        self._start_pos = None
        self.setCursor(Qt.OpenHandCursor)


class CornerResizeItem(QGraphicsRectItem):
    """Small diagonal-resize hotspot at one crop-box corner. Drags the two adjacent
    edges (e.g. left+top) independently, like a traditional crop tool's corner handle."""

    SIZE = 14

    def __init__(self, h_edge, v_edge, cursor, on_press, on_drag):
        super().__init__()
        self.h_edge = h_edge  # "left" or "right"
        self.v_edge = v_edge  # "top" or "bottom"
        self.on_press = on_press
        self.on_drag = on_drag
        self.setPen(Qt.NoPen)
        self.setBrush(Qt.NoBrush)
        self.setZValue(12)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setCursor(cursor)
        self._dragging = False

    def mousePressEvent(self, event):
        self._dragging = True
        self.on_press()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        self.on_drag(self.h_edge, self.v_edge, event.scenePos())

    def mouseReleaseEvent(self, _event):
        self._dragging = False
