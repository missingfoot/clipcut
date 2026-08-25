"""ExportOptionsMixin: export-tab option reactivity (format/scale UI, playback-state icons, timeline toggles)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtMultimedia import QMediaPlayer

from clipcutlib.utils import format_time


class ExportOptionsMixin:
    def on_format_changed(self, format_name):
        is_gif = format_name == "GIF"
        is_webp = format_name == "WebP (Animated)"
        is_image = is_gif or is_webp

        def set_row_visible(row, visible):
            for widget in row:
                widget.setVisible(visible)

        self.audio_section_widget.setVisible(not is_image)
        self.image_options_widget.setVisible(is_image)
        set_row_visible(self.image_fps_row, is_image)          # shared by both
        set_row_visible(self.webp_quality_row, is_webp)
        set_row_visible(self.webp_preset_row, is_webp)
        set_row_visible(self.gif_colors_row, is_gif)
        set_row_visible(self.gif_dither_row, is_gif)
        set_row_visible(self.gif_grayscale_row, is_gif)
        set_row_visible(self.gif_optimize_row, is_gif)

        if self.audio_only.isChecked():
            self.format_note.setText(
                "Original keeps the source audio untouched. Anything else re-encodes.")
        else:
            self.format_note.setText(
                "Original keeps the source format. Anything else re-encodes.")

    def update_scale_default(self):
        """Keeps the Scale field's unit label, scroll-step range, and
        pre-filled value in sync with the current mode - percent defaults to
        100 (a no-op scale), Longest side defaults to the *effective* (post-
        crop, if any) longest dimension, both editable in place rather than
        hidden behind a generic "Original" placeholder. Called on mode
        change, on every load/remove_file, and on every crop change (via
        update_crop_size_fields) - the sensible default differs per source
        and shrinks with the crop, so it needs to track both."""
        is_percentage = self.scale_mode_combo.currentText() == "Percentage"
        self.scale_value_edit.minimum = 1 if is_percentage else 2
        self.scale_value_edit.maximum = 1000 if is_percentage else 7680
        self.scale_value_edit.step = 5 if is_percentage else 10
        self.scale_unit_label.setText("%" if is_percentage else "px")
        # 100% is a sensible default with no file loaded too, unlike Longest
        # side's default, which needs the source's actual dimensions.
        if is_percentage:
            self.scale_value_edit.setText("100")
            return
        if not self.path:
            self.scale_value_edit.clear()
            self.scale_value_edit.setPlaceholderText("Original")
            return
        try:
            _left, _top, crop_w, crop_h, has_crop = self.get_crop()
        except ValueError:
            has_crop = False
        width, height = (crop_w, crop_h) if has_crop else (self.video_width, self.video_height)
        self.scale_value_edit.setText(str(max(width, height)))

    def on_lock_edges_toggled(self, locked):
        self.scrubber.set_edges_locked(locked)
        icon = "changes-prevent-symbolic" if locked else "changes-allow-symbolic"
        self.lock_edges_button.setIcon(QIcon.fromTheme(icon))
        self.lock_edges_button.setToolTip("Unlock Cut Edges" if locked else "Lock Cut Edges")

    def on_snap_toggled(self, enabled):
        self.scrubber.set_snap_enabled(enabled)
        self.snap_button.setToolTip("Stop Snapping" if enabled else "Snap to Cuts")

    def on_loop_toggled(self, looping):
        self.loop_button.setToolTip("Stop Looping" if looping else "Loop Cut")
        if looping:
            rows = self.selected_cut_rows()
            if len(rows) == 1:
                cut_index = self.cut_list.item(rows[0]).data(Qt.UserRole)
                start, _end = self.trim_seconds_for_cut(self.cuts[cut_index])
                self.preview.player.setPosition(int(start * 1000))

    def enforce_loop(self, seconds):
        """When Loop is on and exactly one cut is selected, jump back to that
        cut's start once playback reaches its end, instead of running past it.
        Returns True if it seeked - the caller must skip
        sync_selection_to_playhead for this update in that case, or seeing
        the momentary past-the-end position would deselect the cut being
        looped before the seek-triggered positionChanged arrives to fix it."""
        if not self.loop_button.isChecked():
            return False
        rows = self.selected_cut_rows()
        if len(rows) != 1:
            return False
        cut_index = self.cut_list.item(rows[0]).data(Qt.UserRole)
        start, end = self.trim_seconds_for_cut(self.cuts[cut_index])
        if seconds >= end - 0.001:
            self.preview.player.setPosition(int(start * 1000))
            return True
        return False

    def set_start_from_playhead(self):
        self.write_start_field(self.current_position())

    def set_end_from_playhead(self):
        self.write_end_field(self.current_position())

    def on_playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlayingState
        icon = "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        self.play_button.setIcon(QIcon.fromTheme(icon))
        self.play_button.setToolTip("Pause" if playing else "Play")

    def on_player_position_changed(self, position_ms):
        seconds = position_ms / 1000
        self.scrubber.set_position(seconds)
        self.time_label.setText(f"{format_time(seconds)} / {format_time(self.duration or 0)}")
        if not self.enforce_loop(seconds):
            # While looping, selection is locked to whichever cut is being
            # looped rather than following the playhead - a mid-seek position
            # can transiently land outside the cut's range (e.g. passing
            # through 0) as the player jumps back to its start, which would
            # otherwise flash the row's selection off and back on every lap.
            if not self.loop_button.isChecked():
                self.sync_selection_to_playhead()

    def on_seek_requested(self, seconds):
        self.preview.player.setPosition(int(seconds * 1000))

    def on_remove_audio_toggled(self, checked):
        if checked:
            self.audio_only.setChecked(False)

    def on_audio_only_toggled(self, checked):
        if checked:
            self.remove_audio.setChecked(False)
        for widget in self.crop_edits:
            widget.setEnabled(not checked)
        self.scale_section_widget.setEnabled(not checked)
        # Swap Format's choices to the audio container list rather than
        # disabling it - audio-only exports still have a real format
        # decision (Original/M4A/MP3/Opus/FLAC/WAV), just not a video one.
        items = self.audio_format_items if checked else self.video_format_items
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        self.format_combo.addItems(items)
        self.format_combo.blockSignals(False)
        self.on_format_changed(self.format_combo.currentText())

