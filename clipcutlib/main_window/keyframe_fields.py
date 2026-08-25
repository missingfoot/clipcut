"""KeyframeFieldsMixin: Start/End field <-> scrubber sync for both trim modes."""

from PySide6.QtWidgets import QMessageBox

from clipcutlib.small_widgets import Cut
from clipcutlib.utils import format_time


class KeyframeFieldsMixin:
    def on_keyframe_fields_changed(self):
        self.start_keyframe_ref.setText(f"({format_time(self.keyframe_time(int(self.start_keyframe_edit.text() or 0)))})")
        end_time = self.keyframe_time(int(self.end_keyframe_edit.text())) if self.end_keyframe_edit.text() else (self.duration or 0)
        self.end_keyframe_ref.setText(f"({format_time(end_time)})")
        self.update_suffix_placeholder()
        if self._syncing or not self.path:
            return
        try:
            start, end = self.get_trim_times()
        except ValueError:
            return
        self._syncing = True
        self.sync_active_cut_from_widgets()
        self.refresh_timeline_cuts()
        self._syncing = False
        self.refresh_cut_list()

    def on_frame_fields_changed(self):
        fps = self.video_fps or 30.0
        self.start_frame_ref.setText(f"({format_time(int(self.start_frame_edit.text() or 0) / fps)})")
        end_time = int(self.end_frame_edit.text()) / fps if self.end_frame_edit.text() else (self.duration or 0)
        self.end_frame_ref.setText(f"({format_time(end_time)})")
        self.update_suffix_placeholder()
        if self._syncing or not self.path:
            return
        try:
            start, end = self.get_trim_times()
        except ValueError:
            return
        self._syncing = True
        self.sync_active_cut_from_widgets()
        self.refresh_timeline_cuts()
        self._syncing = False
        self.refresh_cut_list()

    def write_start_field(self, seconds):
        """Write a Start value, but leave the field blank instead of a literal "0" when
        it's at the very beginning - blank already means that, and a real "0" reads as
        if something deliberate were set."""
        if self.frame_mode:
            fps = self.video_fps or 30.0
            frame = round(seconds * fps)
            self.start_frame_edit.setText("" if frame <= 0 else str(frame))
        else:
            idx = self.keyframe_index_of(seconds)
            self.start_keyframe_edit.setText("" if idx <= 0 else str(idx))
        self.sync_active_cut_from_widgets()
        self.refresh_timeline_cuts()
        self.refresh_cut_list()

    def write_end_field(self, seconds):
        """Write an End value, but leave the field blank instead of a concrete number
        when it's at (or past) the file's actual end - see write_start_field."""
        at_end = seconds >= self.duration - 0.01
        if self.frame_mode:
            fps = self.video_fps or 30.0
            self.end_frame_edit.setText("" if at_end else str(round(seconds * fps)))
        else:
            self.end_keyframe_edit.setText("" if at_end else str(self.keyframe_index_of(seconds)))
        self.sync_active_cut_from_widgets()
        self.refresh_timeline_cuts()
        self.refresh_cut_list()

    def on_cut_mode_changed(self, is_frame):
        if self._syncing:
            return
        if self.path:
            # Cuts can't mix Frame/Keyframe modes (merge relies on every cut sharing
            # one representation) - switching modes always starts over with a single
            # cut, so confirm first if there's real work to lose.
            has_work = len(self.cuts) > 1 or any(c.crop is not None for c in self.cuts)
            if has_work:
                reply = QMessageBox.question(
                    self, "Switch Mode?",
                    "Frame and Keyframe cuts can't be mixed in the same list - switching "
                    "modes discards every cut and starts over with a single cut in the "
                    "new mode. Continue?",
                    QMessageBox.Yes | QMessageBox.No)
                if reply != QMessageBox.Yes:
                    self._syncing = True
                    self.cut_mode_frame.setChecked(not is_frame)
                    self.cut_mode_keyframe.setChecked(is_frame)
                    self._syncing = False
                    return
        self.frame_mode = is_frame
        self.scrubber.set_keyframes([] if is_frame else self.keyframe_times)
        self.frame_start_widget.setVisible(is_frame)
        self.frame_end_widget.setVisible(is_frame)
        self.keyframe_start_widget.setVisible(not is_frame)
        self.keyframe_end_widget.setVisible(not is_frame)
        if self.path:
            try:
                # Read from the mode we're leaving (self.frame_mode already reflects the
                # new mode by the time this handler fires, so get_trim_times() would
                # read the wrong, not-yet-populated field set).
                start, end = self.read_keyframe_fields_seconds() if is_frame else self.read_frame_fields_seconds()
            except ValueError:
                start, end = 0.0, self.duration
            self.cuts = [Cut(frame_mode=is_frame)]
            self.active_cut = 0
            # self.frame_mode already reflects the mode just switched to, so these
            # target the right fields; blank stays blank rather than becoming a literal
            # "0" or a concrete End number that (for Keyframe mode) could cap it to a
            # keyframe well before the file's actual end.
            self._syncing = True
            self.write_start_field(start)
            self.write_end_field(end)
            self._syncing = False
            self.select_cut(0)
        self.update_suffix_placeholder()

