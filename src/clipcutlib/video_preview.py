"""VideoPreview: the video surface widget - playback, zoom/pan, and the
draggable crop-guide overlay."""

from PySide6.QtCore import QRect, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from clipcutlib.crop_items import CornerResizeItem, CropMoveItem, GuideItem
from clipcutlib.utils import grab_thumbnail


class VideoPreview(QGraphicsView):
    """Video surface with playback, drop-to-load, and draggable crop guides overlaid on the frame."""

    # left, top, right, bottom (real video pixels), preserves_ratio (whether this change
    # is guaranteed to keep any active aspect-ratio lock intact - true for pan/locked
    # corner-scale, false for a single-guide drag/reset that can only break it)
    guides_changed = Signal(int, int, int, int, bool)
    # Fires whenever zoom_factor/_zoom_pinned changes (zoom_in/out, reset_zoom, mouse
    # wheel) - MainWindow listens to keep the % readout and button enabled-state in
    # sync no matter which of those triggered the change.
    zoom_changed = Signal()

    THICKNESS = 1
    PADDING = 24
    # Round-number stops Zoom In/Out step through, rather than a flat multiplicative
    # step - see zoom_steps(). MAX_ZOOM is the upper bound on zoom_factor, generous
    # enough to inspect fine detail on a small source, independent of fit_scale().
    # The lower bound isn't a constant: see min_zoom().
    ZOOM_LEVELS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
    MAX_ZOOM = 8.0

    def __init__(self, on_drop, parent=None):
        super().__init__(parent)
        self.on_drop = on_drop
        # Deliberately does NOT accept drops itself: Qt only bubbles unclaimed drag
        # events up to an accepting ancestor, so this widget must stay out of the way
        # for the window-level phantom drop overlay to receive them.
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background-color: #000000; border: none;")
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)

        self.gscene = QGraphicsScene(self)
        self.setScene(self.gscene)

        self.video_width = None
        self.video_height = None
        self.crop = (0, 0, 0, 0)  # left, top, right, bottom, in real video pixels
        # Sticky per-guide "is this edge cropping at all" flag. Deliberately independent
        # of crop == 0: panning the crop box can push an edge to 0 in transit without
        # dropping it - only an explicit single-guide drag to 0 or a double-click reset
        # actually removes a guide.
        self.guide_active = {"left": False, "top": False, "right": False, "bottom": False}
        # Snapshot of guide_active taken at the start of the current drag gesture - see
        # _effective_ratio's `active` param for why _guide_dragged needs this instead of
        # the live dict.
        self._guide_active_at_drag_start = dict(self.guide_active)
        # Width/height of the selected aspect-ratio preset, or None for "None" - the
        # ratio a locked edit targets when one is set; otherwise a locked edit preserves
        # whatever ratio the box already has. See _effective_ratio and lock_aspect below.
        self.locked_ratio = None
        # Master switch (the "Lock Aspect Ratio" checkbox): when on, every interactive
        # edit - guide drag, field edit, corner drag, double-click reset - is routed
        # through _resize_locked to keep the box's ratio fixed; when off, every one of
        # them behaves as an independent free edit, exactly as if this feature didn't exist.
        self.lock_aspect = True
        # Pinned display scale, in native-pixel multiples (1.0 == 100%) - only
        # meaningful once _zoom_pinned is True (see zoom_in/out/set_zoom). Until
        # then, effective_zoom() tracks fit_scale() live instead of this, so "Fit"
        # keeps recomputing itself as the window resizes (and, importantly, once
        # the initial layout finishes settling right after startup - fit_scale()
        # right at construction, before the window is shown, would otherwise get
        # pinned to a bogus size forever). reset_zoom() drops back to this
        # tracking mode; any explicit zoom_in/out/set_zoom pins it.
        self.zoom_factor = 1.0
        self._zoom_pinned = False
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._panning = False

        self.video_item = QGraphicsVideoItem()
        self.gscene.addItem(self.video_item)

        self.player = QMediaPlayer(self)
        self.player.setLoops(QMediaPlayer.Infinite)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setMuted(True)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_item)
        self.player.errorOccurred.connect(self.on_player_error)

        self.fallback_item = QGraphicsPixmapItem()
        self.fallback_item.setTransformationMode(Qt.SmoothTransformation)
        self.gscene.addItem(self.fallback_item)
        self.fallback_item.hide()

        self.hint_item = self.gscene.addSimpleText("Drop video here")
        self.hint_item.setBrush(QColor("#999999"))

        self.guide_top = GuideItem("h", self._crop_move_start, lambda y: self._guide_dragged("top", y),
                                    lambda: self._guide_reset("top"))
        self.guide_bottom = GuideItem("h", self._crop_move_start, lambda y: self._guide_dragged("bottom", y),
                                       lambda: self._guide_reset("bottom"))
        self.guide_left = GuideItem("v", self._crop_move_start, lambda x: self._guide_dragged("left", x),
                                     lambda: self._guide_reset("left"))
        self.guide_right = GuideItem("v", self._crop_move_start, lambda x: self._guide_dragged("right", x),
                                      lambda: self._guide_reset("right"))
        for g in (self.guide_top, self.guide_bottom, self.guide_left, self.guide_right):
            self.gscene.addItem(g)
            g.hide()

        self.crop_move = CropMoveItem(self._crop_move_start, self._crop_moved)
        self.gscene.addItem(self.crop_move)
        self.crop_move.hide()

        self.corner_tl = CornerResizeItem("left", "top", Qt.SizeFDiagCursor, self._crop_move_start, self._corner_dragged)
        self.corner_br = CornerResizeItem("right", "bottom", Qt.SizeFDiagCursor, self._crop_move_start, self._corner_dragged)
        self.corner_tr = CornerResizeItem("right", "top", Qt.SizeBDiagCursor, self._crop_move_start, self._corner_dragged)
        self.corner_bl = CornerResizeItem("left", "bottom", Qt.SizeBDiagCursor, self._crop_move_start, self._corner_dragged)
        self.corners = (self.corner_tl, self.corner_tr, self.corner_bl, self.corner_br)
        for c in self.corners:
            self.gscene.addItem(c)
            c.hide()

        self.black_preview = False
        self.crop_overlay_visible = True  # whether exactly one cut is selected
        self.bg_color = QColor(0, 0, 0)
        self.shade_top = QGraphicsRectItem()
        self.shade_bottom = QGraphicsRectItem()
        self.shade_left = QGraphicsRectItem()
        self.shade_right = QGraphicsRectItem()
        for s in (self.shade_top, self.shade_bottom, self.shade_left, self.shade_right):
            s.setPen(Qt.NoPen)
            s.setZValue(5)
            self.gscene.addItem(s)
            s.hide()

        self.show_center = False
        self.center_h = QGraphicsRectItem()
        self.center_v = QGraphicsRectItem()
        for c in (self.center_h, self.center_v):
            c.setBrush(QColor(255, 255, 255, 90))
            c.setPen(Qt.NoPen)
            c.setZValue(6)
            c.setAcceptedMouseButtons(Qt.NoButton)
            self.gscene.addItem(c)
            c.hide()

        self.show_thirds = False
        self.thirds_v1 = QGraphicsRectItem()
        self.thirds_v2 = QGraphicsRectItem()
        self.thirds_h1 = QGraphicsRectItem()
        self.thirds_h2 = QGraphicsRectItem()
        self.thirds_lines = (self.thirds_v1, self.thirds_v2, self.thirds_h1, self.thirds_h2)
        for t in self.thirds_lines:
            t.setBrush(QColor(255, 255, 255, 90))
            t.setPen(Qt.NoPen)
            t.setZValue(6)
            t.setAcceptedMouseButtons(Qt.NoButton)
            self.gscene.addItem(t)
            t.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.viewport().rect()
        self.gscene.setSceneRect(0, 0, rect.width(), rect.height())
        text_rect = self.hint_item.boundingRect()
        self.hint_item.setPos((rect.width() - text_rect.width()) / 2,
                               (rect.height() - text_rect.height()) / 2)
        self._clamp_pan()
        self._update_pan_cursor()
        self.refresh_overlay()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton and self._content_overflows():
            self._panning = True
            self._pan_last_pos = event.position().toPoint()
            # Cursor lives on the viewport widget, not the QGraphicsView itself.
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            pos = event.position().toPoint()
            self.pan_x += pos.x() - self._pan_last_pos.x()
            self.pan_y += pos.y() - self._pan_last_pos.y()
            self._pan_last_pos = pos
            self._clamp_pan()
            self.refresh_overlay()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _content_overflows(self):
        """Whether the current zoom makes the displayed frame bigger than the viewport
        on at least one axis - the condition for panning to mean anything at all."""
        if not self.video_width or not self.video_height:
            return False
        rect = self.viewport().rect()
        disp_w = self.video_width * self.effective_zoom()
        disp_h = self.video_height * self.effective_zoom()
        return disp_w > rect.width() or disp_h > rect.height()

    def _clamp_pan(self):
        """Keep pan_x/pan_y within range so the video always fully covers the viewport
        (or stays centered, if it's smaller than the viewport on that axis) - without this,
        dragging past an edge would let the offset run away and require an equal drag back
        before the view visibly moves again."""
        if not self.video_width or not self.video_height:
            return
        rect = self.viewport().rect()
        disp_w = self.video_width * self.effective_zoom()
        disp_h = self.video_height * self.effective_zoom()
        base_x = (rect.width() - disp_w) / 2
        base_y = (rect.height() - disp_h) / 2
        if disp_w > rect.width():
            lo, hi = (rect.width() - disp_w) - base_x, -base_x
            self.pan_x = max(lo, min(hi, self.pan_x))
        else:
            self.pan_x = 0.0
        if disp_h > rect.height():
            lo, hi = (rect.height() - disp_h) - base_y, -base_y
            self.pan_y = max(lo, min(hi, self.pan_y))
        else:
            self.pan_y = 0.0

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() == Qt.MiddleButton:
            self._panning = False
            self._update_pan_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _update_pan_cursor(self):
        """Reset to the default cursor - the closed-hand cursor is only shown while
        actually panning (middle button held), not as an always-on hover hint."""
        self.viewport().unsetCursor()

    def wheelEvent(self, event):
        if self.video_width and self.video_height:
            (self.zoom_in if event.angleDelta().y() > 0 else self.zoom_out)(anchor=event.position())
        event.accept()

    def load(self, path, video_width, video_height):
        self.video_width = video_width
        self.video_height = video_height
        self.crop = (0, 0, 0, 0)
        self.guide_active = {"left": False, "top": False, "right": False, "bottom": False}
        self.locked_ratio = None
        self.zoom_factor = 1.0
        self._zoom_pinned = False
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.hint_item.hide()
        self.fallback_item.hide()
        self.video_item.show()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        # Not autoplaying (starts paused), but briefly play+pause so the first frame
        # actually renders instead of leaving the preview black until Play is pressed.
        self.player.play()
        self.player.pause()
        for g in (self.guide_top, self.guide_bottom, self.guide_left, self.guide_right):
            g.show()
        for s in (self.shade_top, self.shade_bottom, self.shade_left, self.shade_right):
            s.show()
        self.refresh_overlay()

    def unload(self):
        self.crop_move.hide()
        for c in self.corners:
            c.hide()
        self.center_h.hide()
        self.center_v.hide()
        for th in self.thirds_lines:
            th.hide()
        self.video_width = None
        self.video_height = None
        self.crop = (0, 0, 0, 0)
        self.guide_active = {"left": False, "top": False, "right": False, "bottom": False}
        self.locked_ratio = None
        self.zoom_factor = 1.0
        self._zoom_pinned = False
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.player.stop()
        self.player.setSource(QUrl())
        self.video_item.hide()
        self.fallback_item.hide()
        self.fallback_item.setPixmap(QPixmap())
        for g in (self.guide_top, self.guide_bottom, self.guide_left, self.guide_right):
            g.hide()
        for s in (self.shade_top, self.shade_bottom, self.shade_left, self.shade_right):
            s.hide()
        self.hint_item.show()

    def on_player_error(self, _error, _error_string):
        if not self.video_width:
            return
        png_bytes = grab_thumbnail(self.player.source().toLocalFile())
        if not png_bytes:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(png_bytes, "PNG")
        self.fallback_item.setPixmap(pixmap)
        self.video_item.hide()
        self.fallback_item.show()
        self.refresh_overlay()

    def fit_scale(self):
        """The zoom_factor that fits the whole frame within the padded viewport - the
        scale "Fit" means. Independent of the current zoom_factor, so it stays valid to
        compare against (min_zoom, the % readout's "Fit" label) no matter how zoomed in
        or out the view actually is right now."""
        if not self.video_width or not self.video_height:
            return 1.0
        rect = self.viewport().rect()
        w = max(1, rect.width() - 2 * self.PADDING)
        h = max(1, rect.height() - 2 * self.PADDING)
        return min(w / self.video_width, h / self.video_height)

    def min_zoom(self):
        """Zoom-out floor: whichever of Fit or 100% is *less* zoomed-in. A large source
        floors at Fit (no point shrinking past seeing the whole frame); a source already
        smaller than the viewport floors at 100% (Fit would be upscaling it, and zooming
        out from there would only shrink it further into the corner - never useful)."""
        return min(self.fit_scale(), 1.0)

    def max_zoom(self):
        return max(self.MAX_ZOOM, self.fit_scale())

    def effective_zoom(self):
        """The scale actually in effect right now: fit_scale(), tracked live, until an
        explicit zoom pins a specific number (see _zoom_pinned)."""
        return self.zoom_factor if self._zoom_pinned else self.fit_scale()

    def zoom_steps(self):
        """The stops Zoom In/Out click through: ZOOM_LEVELS' round percentages that
        fall within [min_zoom, max_zoom], plus the exact floor and ceiling themselves
        (which usually aren't round numbers - e.g. a source whose Fit is 137%) so
        zooming never overshoots past them into an out-of-range value."""
        lo, hi = self.min_zoom(), self.max_zoom()
        levels = {lo, hi} | {lvl for lvl in self.ZOOM_LEVELS if lo <= lvl <= hi}
        return sorted(levels)

    def _next_zoom_level(self, up):
        """The next zoom_steps() stop above (up=True) or below the current effective
        zoom, or None if already at the end of the ladder in that direction."""
        current = self.effective_zoom()
        steps = self.zoom_steps() if up else reversed(self.zoom_steps())
        for level in steps:
            if (level > current + 0.005) if up else (level < current - 0.005):
                return level
        return None

    def zoom_in(self, anchor=None):
        level = self._next_zoom_level(up=True)
        if level is not None:
            self.set_zoom(level, anchor)

    def zoom_out(self, anchor=None):
        level = self._next_zoom_level(up=False)
        if level is not None:
            self.set_zoom(level, anchor)

    def reset_zoom(self):
        """Drop back to Fit, tracked live - not just a one-time snap to today's
        fit_scale(), which would go stale the next time the window resizes."""
        self._zoom_pinned = False
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._update_pan_cursor()
        self.refresh_overlay()
        self.zoom_changed.emit()

    def set_zoom(self, factor, anchor=None):
        """Change zoom_factor to `factor`, clamped to [min_zoom, max_zoom]. With no
        anchor (button clicks), pan resets to center like today. With an anchor - a
        QPointF in viewport coordinates, e.g. the cursor position on a wheel-zoom -
        pan is instead solved so whatever video-content point was under the anchor
        stays under it, rather than the view recentering under the cursor."""
        old_rect = self.content_rect()
        frac = None
        if anchor is not None and old_rect.width() > 0 and old_rect.height() > 0:
            frac = ((anchor.x() - old_rect.x()) / old_rect.width(),
                    (anchor.y() - old_rect.y()) / old_rect.height())

        self.zoom_factor = max(self.min_zoom(), min(self.max_zoom(), factor))
        self._zoom_pinned = True

        if frac is not None:
            rect = self.viewport().rect()
            disp_w = max(1, round(self.video_width * self.zoom_factor))
            disp_h = max(1, round(self.video_height * self.zoom_factor))
            base_x = (rect.width() - disp_w) / 2
            base_y = (rect.height() - disp_h) / 2
            self.pan_x = (anchor.x() - frac[0] * disp_w) - base_x
            self.pan_y = (anchor.y() - frac[1] * disp_h) - base_y
            self._clamp_pan()
        else:
            self.pan_x = 0.0
            self.pan_y = 0.0

        self._update_pan_cursor()
        self.refresh_overlay()
        self.zoom_changed.emit()

    def content_rect(self):
        """Where the video frame is actually drawn within the view (letterbox/pillarbox-aware),
        inset by PADDING so crop=0 guides land clear of the video/window edge and stay grabbable."""
        rect = self.viewport().rect()
        if not self.video_width or not self.video_height:
            return QRect(0, 0, rect.width(), rect.height())
        zoom = self.effective_zoom()
        disp_w = max(1, round(self.video_width * zoom))
        disp_h = max(1, round(self.video_height * zoom))
        base_x = (rect.width() - disp_w) / 2
        base_y = (rect.height() - disp_h) / 2
        if disp_w > rect.width():
            lo, hi = rect.width() - disp_w, 0
            x = max(lo, min(hi, base_x + self.pan_x))
        else:
            x = base_x
        if disp_h > rect.height():
            lo, hi = rect.height() - disp_h, 0
            y = max(lo, min(hi, base_y + self.pan_y))
        else:
            y = base_y
        return QRect(int(x), int(y), disp_w, disp_h)

    def set_crop_pixels(self, left, top, right, bottom):
        self.crop = (left, top, right, bottom)
        if self.locked_ratio is not None:
            # A preset ratio is engaged - all four guides stay shown as an engaged
            # crop line together, even ones that land on exactly 0 (e.g. the ratio
            # only crops one axis), and even after redisplaying this crop later
            # (switching cuts, toggling per-cut crop) - not just right after the
            # preset was first applied.
            self.guide_active = {"left": True, "top": True, "right": True, "bottom": True}
        else:
            self.guide_active = {"left": left > 0, "top": top > 0, "right": right > 0, "bottom": bottom > 0}
        self.refresh_overlay()

    def set_locked_ratio(self, ratio):
        self.locked_ratio = ratio

    def set_lock_aspect(self, enabled):
        self.lock_aspect = enabled

    def set_show_center(self, show):
        self.show_center = show
        self.refresh_overlay()

    def set_show_thirds(self, show):
        self.show_thirds = show
        self.refresh_overlay()

    def set_black_preview(self, enabled):
        """"Black" crop style: solid opaque mask outside the crop with no guides, corner
        handles, or center cross drawn over it - a clean look at the actual result instead
        of the editable "Guides" overlay. The hit areas hide along with their visuals, so
        editing is effectively paused while this is on."""
        self.black_preview = enabled
        self.refresh_overlay()

    def set_crop_overlay_visible(self, visible):
        """Whether exactly one cut is currently selected. With none selected
        (the playhead is in a gap between cuts, or every row was deselected) or
        several selected at once, there's no single "current cut" to show a
        crop for, so the whole overlay hides instead of showing stale/
        ambiguous guides."""
        if visible != self.crop_overlay_visible:
            self.crop_overlay_visible = visible
            self.refresh_overlay()

    def set_bg_color(self, color):
        self.bg_color = color
        self.refresh_overlay()

    def set_line_color(self, color):
        for g in (self.guide_left, self.guide_right, self.guide_top, self.guide_bottom):
            g.set_active_color(color)

    def refresh_overlay(self):
        rect = self.content_rect()
        full = self.viewport().rect()
        self.video_item.setPos(rect.x(), rect.y())
        self.video_item.setSize(rect.size().toSizeF())
        self.fallback_item.setPos(rect.x(), rect.y())
        if not self.fallback_item.pixmap().isNull():
            scale = rect.width() / max(1, self.fallback_item.pixmap().width())
            self.fallback_item.setScale(scale)

        if not self.video_width or not self.video_height:
            return
        # Whether to show any crop-overlay element at all: black_preview already
        # hides guides/corners/center-thirds for its own "clean look" reason;
        # crop_overlay_visible hides everything (including the shading) when
        # there's no single selected cut to show a crop for.
        overlay_visible = not self.black_preview and self.crop_overlay_visible
        left, top, right, bottom = self.crop
        sx = rect.width() / self.video_width
        sy = rect.height() / self.video_height
        t = self.THICKNESS

        gx_left = rect.left() + int(left * sx)
        gx_right = rect.right() - int(right * sx)
        gy_top = rect.top() + int(top * sy)
        gy_bottom = rect.bottom() - int(bottom * sy)

        # Placed at whole-pixel coordinates (not centered with a t/2 half-pixel offset)
        # so a 1px line lands crisply on one pixel instead of blurring across two under
        # antialiasing.
        self.guide_left.setRect(gx_left, 0, t, full.height())
        self.guide_right.setRect(gx_right, 0, t, full.height())
        self.guide_top.setRect(0, gy_top, full.width(), t)
        self.guide_bottom.setRect(0, gy_bottom, full.width(), t)
        self.guide_left.set_active(self.guide_active["left"])
        self.guide_right.set_active(self.guide_active["right"])
        self.guide_top.set_active(self.guide_active["top"])
        self.guide_bottom.set_active(self.guide_active["bottom"])
        for g in (self.guide_left, self.guide_right, self.guide_top, self.guide_bottom):
            g.setVisible(overlay_visible)

        h_active = self.guide_active["left"] and self.guide_active["right"]
        v_active = self.guide_active["top"] and self.guide_active["bottom"]
        self.crop_move.setRect(gx_left + t, gy_top + t,
                                max(0, gx_right - gx_left - 2 * t), max(0, gy_bottom - gy_top - 2 * t))
        self.crop_move.set_axes(h_active, v_active)
        if not overlay_visible:
            self.crop_move.hide()

        all_active = h_active and v_active
        s = CornerResizeItem.SIZE
        corner_pos = {
            self.corner_tl: (gx_left, gy_top),
            self.corner_tr: (gx_right, gy_top),
            self.corner_bl: (gx_left, gy_bottom),
            self.corner_br: (gx_right, gy_bottom),
        }
        for corner, (cx, cy) in corner_pos.items():
            corner.setRect(cx - s / 2, cy - s / 2, s, s)
            corner.setVisible(all_active and overlay_visible)

        shade_alpha = 255 if self.black_preview else 140
        shade_color = QColor(self.bg_color)
        shade_color.setAlpha(shade_alpha)
        for s in (self.shade_top, self.shade_bottom, self.shade_left, self.shade_right):
            s.setBrush(shade_color)
            s.setVisible(self.crop_overlay_visible)
        self.shade_left.setRect(0, 0, max(0, gx_left), full.height())
        self.shade_right.setRect(gx_right, 0, max(0, full.width() - gx_right), full.height())
        self.shade_top.setRect(0, 0, full.width(), max(0, gy_top))
        self.shade_bottom.setRect(0, gy_bottom, full.width(), max(0, full.height() - gy_bottom))

        # Whole-pixel midpoint, same reasoning as the guides above: a fractional position
        # (plus the old -t/2 centering) lands the 1px line on a half-pixel and blurs it
        # across two under antialiasing instead of landing crisply on one.
        mid_x = int(round((gx_left + gx_right) / 2))
        mid_y = int(round((gy_top + gy_bottom) / 2))
        self.center_v.setRect(mid_x, gy_top, t, max(0, gy_bottom - gy_top))
        self.center_h.setRect(gx_left, mid_y, max(0, gx_right - gx_left), t)
        self.center_h.setVisible(self.show_center and overlay_visible)
        self.center_v.setVisible(self.show_center and overlay_visible)

        third_x1 = int(round(gx_left + (gx_right - gx_left) / 3))
        third_x2 = int(round(gx_left + (gx_right - gx_left) * 2 / 3))
        third_y1 = int(round(gy_top + (gy_bottom - gy_top) / 3))
        third_y2 = int(round(gy_top + (gy_bottom - gy_top) * 2 / 3))
        self.thirds_v1.setRect(third_x1, gy_top, t, max(0, gy_bottom - gy_top))
        self.thirds_v2.setRect(third_x2, gy_top, t, max(0, gy_bottom - gy_top))
        self.thirds_h1.setRect(gx_left, third_y1, max(0, gx_right - gx_left), t)
        self.thirds_h2.setRect(gx_left, third_y2, max(0, gx_right - gx_left), t)
        for th in self.thirds_lines:
            th.setVisible(self.show_thirds and overlay_visible)

    def effective_ratio(self):
        """The ratio the Size fields should target, for the current crop. Unlike
        _effective_ratio, this skips the "all four guides engaged" gate: a Size edit
        always sets both dimensions at once (directly, or via ratio-derivation), so it
        can't suffer the single-guide ambiguity that gate exists for - it's a scale
        operation like a corner drag, not a one-edge drag."""
        if not self.lock_aspect:
            return None
        if self.locked_ratio:
            return self.locked_ratio
        left, top, right, bottom = self.crop
        box_h = self.video_height - top - bottom
        return (self.video_width - left - right) / box_h if box_h > 0 else None

    def _effective_ratio(self, box_w0, box_h0, active=None):
        """The ratio a locked edit should target, or None if edits should be free right
        now: off when the Lock Aspect Ratio checkbox is off, when fewer than all four
        guides are engaged yet (nothing meaningful to lock until a real box exists - a
        single guide is just one edge, not a shape), a preset's ratio when one is
        selected, otherwise whatever ratio the box already has (or None if it's empty).
        `active` lets a caller pin the "all four engaged" check to a snapshot from
        before the current gesture (see _guide_dragged) instead of the live, sticky
        self.guide_active - otherwise dragging the 4th guide in would activate it (and
        satisfy the all-four check) partway through its own drag, snapping the rest of
        that same drag into a lock the user never asked for yet."""
        active = self.guide_active if active is None else active
        if not self.lock_aspect or not all(active.values()):
            return None
        if self.locked_ratio:
            return self.locked_ratio
        return box_w0 / box_h0 if box_h0 > 0 else None

    def _resize_locked(self, which, new_value, left0, top0, right0, bottom0, ratio):
        """Set edge `which` to `new_value` while preserving `ratio`: the opposite edge on
        that axis stays fixed, and the perpendicular pair resizes around its previous
        center. Used by guide drags, field edits, and double-click resets alike."""
        if which in ("left", "right"):
            fixed = right0 if which == "left" else left0
            box_w = max(1, self.video_width - new_value - fixed)
            box_h = box_w / ratio
            center = (top0 + (self.video_height - bottom0)) / 2
            max_w = self.video_width - fixed
            max_h = 2 * min(center, self.video_height - center)
        else:
            fixed = bottom0 if which == "top" else top0
            box_h = max(1, self.video_height - new_value - fixed)
            box_w = box_h * ratio
            center = (left0 + (self.video_width - right0)) / 2
            max_h = self.video_height - fixed
            max_w = 2 * min(center, self.video_width - center)

        over = max(box_w / max_w if max_w > 0 else 1, box_h / max_h if max_h > 0 else 1)
        if over > 1:
            box_w /= over
            box_h /= over

        if which in ("left", "right"):
            top = max(0, min(self.video_height - 1, int(round(center - box_h / 2))))
            bottom = max(0, min(self.video_height - 1, int(round(self.video_height - center - box_h / 2))))
            driven = max(0, min(self.video_width - 1, int(round(self.video_width - fixed - box_w))))
            return (driven, top, fixed, bottom) if which == "left" else (fixed, top, driven, bottom)
        else:
            left = max(0, min(self.video_width - 1, int(round(center - box_w / 2))))
            right = max(0, min(self.video_width - 1, int(round(self.video_width - center - box_w / 2))))
            driven = max(0, min(self.video_height - 1, int(round(self.video_height - fixed - box_h))))
            return (left, driven, right, fixed) if which == "top" else (left, fixed, right, driven)

    def _mark_active_sticky(self, left, top, right, bottom):
        """Mark any newly-nonzero edge active without ever deactivating one just because
        this particular edit pushed it to 0 - shared by every ratio-locked/scaled edit
        (guide drags, field edits, double-click resets, corner and Size-field scaling)."""
        for name, val in zip(("left", "top", "right", "bottom"), (left, top, right, bottom)):
            self.guide_active[name] = self.guide_active[name] or (val > 0)

    def apply_scaled_crop(self, left, top, right, bottom):
        """Commit a crop from a ratio-preserving scale (e.g. the Size fields acting as a
        field version of the corner-drag scaler) with sticky guide activation."""
        self._mark_active_sticky(left, top, right, bottom)
        self.crop = (left, top, right, bottom)
        self.refresh_overlay()

    def set_edge(self, which, new_value):
        """Set one crop edge from a typed field value, honoring the aspect-ratio lock the
        same way dragging that edge's guide would. Returns (left, top, right, bottom, locked)."""
        dim = self.video_width if which in ("left", "right") else self.video_height
        new_value = max(0, min(dim - 1, new_value))
        left0, top0, right0, bottom0 = self.crop
        box_w0 = self.video_width - left0 - right0
        box_h0 = self.video_height - top0 - bottom0
        ratio = self._effective_ratio(box_w0, box_h0)
        if ratio:
            left, top, right, bottom = self._resize_locked(which, new_value, left0, top0, right0, bottom0, ratio)
            self._mark_active_sticky(left, top, right, bottom)
        else:
            left, top, right, bottom = left0, top0, right0, bottom0
            if which == "left":
                left = new_value
            elif which == "right":
                right = new_value
            elif which == "top":
                top = new_value
            else:
                bottom = new_value
            self.guide_active[which] = new_value > 0
        self.crop = (left, top, right, bottom)
        self.refresh_overlay()
        return left, top, right, bottom, bool(ratio)

    def _guide_dragged(self, which, pos):
        rect = self.content_rect()
        sx = rect.width() / self.video_width
        sy = rect.height() / self.video_height
        if which == "left":
            new_value = max(0, min(self.video_width - 1, int((pos - rect.left()) / sx)))
        elif which == "right":
            new_value = max(0, min(self.video_width - 1, int((rect.right() - pos) / sx)))
        elif which == "top":
            new_value = max(0, min(self.video_height - 1, int((pos - rect.top()) / sy)))
        else:
            new_value = max(0, min(self.video_height - 1, int((rect.bottom() - pos) / sy)))

        left0, top0, right0, bottom0 = self._crop_at_drag_start
        box_w0 = self.video_width - left0 - right0
        box_h0 = self.video_height - top0 - bottom0
        ratio = self._effective_ratio(box_w0, box_h0, active=self._guide_active_at_drag_start)
        if ratio:
            left, top, right, bottom = self._resize_locked(which, new_value, left0, top0, right0, bottom0, ratio)
            self._mark_active_sticky(left, top, right, bottom)
        else:
            left, top, right, bottom = self.crop
            if which == "left":
                left = new_value
            elif which == "right":
                right = new_value
            elif which == "top":
                top = new_value
            else:
                bottom = new_value
            self.guide_active[which] = new_value > 0

        self.crop = (left, top, right, bottom)
        self.refresh_overlay()
        self.guides_changed.emit(left, top, right, bottom, bool(ratio))

    def _crop_move_start(self):
        self._crop_at_drag_start = self.crop
        self._guide_active_at_drag_start = dict(self.guide_active)

    def _crop_moved(self, dx, dy):
        rect = self.content_rect()
        sx = rect.width() / self.video_width
        sy = rect.height() / self.video_height
        left0, top0, right0, bottom0 = self._crop_at_drag_start
        left, top, right, bottom = left0, top0, right0, bottom0

        if self.guide_active["left"] and self.guide_active["right"]:
            d = max(-left0, min(right0, round(dx / sx)))
            left, right = left0 + d, right0 - d
        if self.guide_active["top"] and self.guide_active["bottom"]:
            d = max(-top0, min(bottom0, round(dy / sy)))
            top, bottom = top0 + d, bottom0 - d

        self.crop = (left, top, right, bottom)
        self.refresh_overlay()
        self.guides_changed.emit(left, top, right, bottom, True)

    def _corner_dragged(self, h_edge, v_edge, scene_pos):
        # With the lock on, corners scale proportionally (opposite edges fixed) rather
        # than resizing the two edges independently - the preset's ratio if one is
        # selected, else whatever ratio the box already had when this drag started.
        rect = self.content_rect()
        sx = rect.width() / self.video_width
        sy = rect.height() / self.video_height
        left0, top0, right0, bottom0 = self._crop_at_drag_start
        box_w0 = self.video_width - left0 - right0
        box_h0 = self.video_height - top0 - bottom0
        ratio = self._effective_ratio(box_w0, box_h0)

        if ratio:
            fixed_h, anchor_x = (right0, rect.right() - int(right0 * sx)) if h_edge == "left" \
                else (left0, rect.left() + int(left0 * sx))
            fixed_v, anchor_y = (bottom0, rect.bottom() - int(bottom0 * sy)) if v_edge == "top" \
                else (top0, rect.top() + int(top0 * sy))

            w_from_dx = abs(scene_pos.x() - anchor_x)
            h_from_dy = abs(scene_pos.y() - anchor_y)
            # Two candidate boxes, one driven by the horizontal delta and one by the
            # vertical delta; take whichever is larger so the box tracks the cursor's
            # dominant drag direction instead of always following one fixed axis.
            candidate_by_x = (w_from_dx, w_from_dx / ratio)
            candidate_by_y = (h_from_dy * ratio, h_from_dy)
            box_w_px, box_h_px = max(candidate_by_x, candidate_by_y, key=lambda wh: wh[0] * wh[1])

            box_w_vid = box_w_px / sx
            box_h_vid = box_h_px / sy
            # Cap both dimensions together (not independently) so hitting the video's edge
            # on one axis scales the whole box back instead of letting the other axis keep
            # growing alone and breaking the ratio.
            max_w = self.video_width - fixed_h
            max_h = self.video_height - fixed_v
            over = max(box_w_vid / max_w if max_w > 0 else 1, box_h_vid / max_h if max_h > 0 else 1)
            if over > 1:
                box_w_vid /= over
                box_h_vid /= over
            new_h = max(0, min(self.video_width - 1, int(self.video_width - fixed_h - box_w_vid)))
            new_v = max(0, min(self.video_height - 1, int(self.video_height - fixed_v - box_h_vid)))
            left, right = (new_h, fixed_h) if h_edge == "left" else (fixed_h, new_h)
            top, bottom = (new_v, fixed_v) if v_edge == "top" else (fixed_v, new_v)
        else:
            # Degenerate case: the box had zero height/width at drag start, so there's no
            # ratio to preserve - fall back to resizing the two edges independently.
            left, top, right, bottom = self.crop
            if h_edge == "left":
                left = max(0, min(self.video_width - 1, int((scene_pos.x() - rect.left()) / sx)))
            else:
                right = max(0, min(self.video_width - 1, int((rect.right() - scene_pos.x()) / sx)))
            if v_edge == "top":
                top = max(0, min(self.video_height - 1, int((scene_pos.y() - rect.top()) / sy)))
            else:
                bottom = max(0, min(self.video_height - 1, int((rect.bottom() - scene_pos.y()) / sy)))

        self.crop = (left, top, right, bottom)
        self.refresh_overlay()
        self.guides_changed.emit(left, top, right, bottom, bool(ratio))

    def _guide_reset(self, which):
        left0, top0, right0, bottom0 = self.crop
        box_w0 = self.video_width - left0 - right0
        box_h0 = self.video_height - top0 - bottom0
        ratio = self._effective_ratio(box_w0, box_h0)
        if ratio:
            # Extend this edge to the frame boundary while preserving the ratio, instead
            # of the unlocked meaning of "remove this edge" (which would break it).
            left, top, right, bottom = self._resize_locked(which, 0, left0, top0, right0, bottom0, ratio)
            self._mark_active_sticky(left, top, right, bottom)
        else:
            left, top, right, bottom = left0, top0, right0, bottom0
            if which == "left":
                left = 0
            elif which == "right":
                right = 0
            elif which == "top":
                top = 0
            else:
                bottom = 0
            self.guide_active[which] = False
        self.crop = (left, top, right, bottom)
        self.refresh_overlay()
        self.guides_changed.emit(left, top, right, bottom, bool(ratio))
