"""FileMixin: loading and clearing the active source file."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics

from clipcutlib.small_widgets import Cut
from clipcutlib.utils import format_time, get_keyframe_times, probe


class FileMixin:
    def set_filename_label(self, name):
        width = self.filename_label.width() or 250
        elided = QFontMetrics(self.filename_label.font()).elidedText(name, Qt.ElideMiddle, width)
        self.filename_label.setText(elided)
        self.filename_label.setToolTip(name)

    def set_file_dependent_widgets_enabled(self, enabled):
        for widget in self.file_dependent_widgets:
            widget.setEnabled(enabled)

    def load_file(self, path):
        try:
            w, h, duration, fps = probe(path)
        except Exception as e:
            self.status.setText(f"Failed to read file: {e}")
            return
        self.path = path
        self.video_width, self.video_height = w, h
        self.duration = duration
        self.video_fps = fps
        # Pre-filled with the source's real frame rate as an editable value
        # (not just a placeholder) so nudging it up/down starts from the
        # actual number instead of an invisible implicit default.
        self.image_fps_edit.setText(f"{fps:.3f}".rstrip("0").rstrip("."))
        self.update_scale_default()
        self.keyframe_times = get_keyframe_times(path)
        self.set_filename_label(path.name)
        self.browse_button.setText("Replace File...")
        self.cuts = [Cut(frame_mode=self.frame_mode)]
        self.active_cut = 0
        self.shared_crop = (0, 0, 0, 0)
        self.per_cut_crop_enabled = False
        self.per_cut_crop_check.setChecked(False)
        self.refresh_cut_list()
        self.start_keyframe_edit.clear()
        self.end_keyframe_edit.clear()
        for field in self.frame_edits:
            field.clear()
        for field in self.crop_edits:
            field.clear()
        self.crop_width_edit.clear()
        self.crop_height_edit.clear()
        self.crop_x_edit.clear()
        self.crop_y_edit.clear()
        self.aspect_combo.setCurrentIndex(0)
        self.orient_horizontal.setChecked(True)
        self.crop_width_edit.setPlaceholderText(str(w))
        self.crop_height_edit.setPlaceholderText(str(h))
        self.set_file_dependent_widgets_enabled(True)
        self.status.setText("")
        self.update_suffix_placeholder()

        self.preview.load(path, w, h)
        self.update_zoom_buttons()
        self.scrubber.set_duration(duration)
        self.scrubber.set_keyframes([] if self.frame_mode else self.keyframe_times)
        self.time_label.setText(f"0:00 / {format_time(duration)}")

    def remove_file(self):
        self.path = None
        self.video_width = None
        self.video_height = None
        self.duration = None
        self.video_fps = None
        self.image_fps_edit.clear()
        self.image_fps_edit.setPlaceholderText("Original")
        self.update_scale_default()
        self.keyframe_times = []
        self.cuts = []
        self.cut_list.clear()
        self.delete_selected_button.setEnabled(False)
        self.keep_selected_button.setEnabled(False)
        self.merge_selected_button.setEnabled(False)
        self.active_cut = 0
        self.shared_crop = (0, 0, 0, 0)
        self.per_cut_crop_enabled = False
        self.per_cut_crop_check.setChecked(False)
        self.filename_label.setText("Drag and drop or choose a file")
        self.filename_label.setToolTip("")
        self.browse_button.setText("Browse...")
        for field in self.keyframe_edits:
            field.clear()
        self.start_keyframe_ref.setText("(0:00)")
        self.end_keyframe_ref.setText("(0:00)")
        for field in self.frame_edits:
            field.clear()
        for field in self.crop_edits:
            field.clear()
        self.crop_width_edit.clear()
        self.crop_height_edit.clear()
        self.crop_x_edit.clear()
        self.crop_y_edit.clear()
        self.crop_width_edit.setPlaceholderText("")
        self.crop_height_edit.setPlaceholderText("")
        self.aspect_combo.setCurrentIndex(0)
        self.orient_horizontal.setChecked(True)
        self.set_file_dependent_widgets_enabled(False)
        self.status.setText("")
        self.update_suffix_placeholder()
        self.update_combined_export_availability()

        self.preview.unload()
        self.update_zoom_buttons()
        self.scrubber.set_duration(0)
        self.refresh_timeline_cuts()
        self.scrubber.set_keyframes([])
        self.time_label.setText("0:00 / 0:00")

    # --- video preview <-> crop field sync ---

