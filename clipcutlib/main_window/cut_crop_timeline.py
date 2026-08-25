"""PerCutCropTimelineMixin: displaying a crop for the active cut, the Per-Cut Crop toggle, and timeline/playhead <-> cut-list sync."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox


class PerCutCropTimelineMixin:
    def show_crop(self, crop):
        """Push a resolved (left, top, right, bottom) crop into the crop text
        fields, the preview overlay, and the Size/Position fields - the single
        sync point for *displaying* a crop (as opposed to editing one), shared by
        select_cut and on_per_cut_crop_toggled. get_crop() reads
        crop_edits as the source of truth, so any path that changes which crop is
        on screen must go through here or the fields silently go stale.

        Also re-syncs the Preset dropdown to whatever this crop actually is,
        rather than leaving it showing whichever preset was last picked -
        possibly for a different cut entirely once Per-Cut Crop is on."""
        was_syncing = self._syncing
        self._syncing = True
        for edit, value in zip(self.crop_edits, crop):
            edit.setText(str(value) if value else "")
        self.aspect_combo.setCurrentIndex(self.match_aspect_preset(crop))
        self._syncing = was_syncing
        # set_locked_ratio() before set_crop_pixels(): the latter checks
        # locked_ratio to decide whether all four guides render as an engaged
        # crop line, including ones landing on exactly 0.
        self.preview.set_locked_ratio(self.current_target_ratio())
        self.preview.set_crop_pixels(*crop)
        self.update_crop_size_fields()

    def current_crop_target(self):
        if not self.per_cut_crop_enabled or not self.cuts:
            return self.shared_crop
        cut = self.cuts[self.active_cut]
        if cut.crop is None:
            cut.crop = self.shared_crop
        return cut.crop

    def set_current_crop(self, left, top, right, bottom):
        if self.per_cut_crop_enabled and self.cuts:
            self.cuts[self.active_cut].crop = (left, top, right, bottom)
        else:
            self.shared_crop = (left, top, right, bottom)
        # refresh_cut_list() also calls update_combined_export_availability() -
        # needed here too so the crop icon on this cut's row appears the moment
        # a per-cut crop is actually set, not only after reselecting/toggling.
        self.refresh_cut_list()

    def on_per_cut_crop_toggled(self, enabled):
        if self._syncing:
            return
        if not enabled and any(c.crop is not None for c in self.cuts):
            reply = QMessageBox.question(
                self, "Turn Off Per-Cut Crop?",
                "Turning off Per-Cut Crop clears every cut's individual crop override "
                "(export will use the shared crop for all of them). Continue?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                self._syncing = True
                self.per_cut_crop_check.setChecked(True)
                self._syncing = False
                return
            for cut in self.cuts:
                cut.crop = None
        if enabled and self.shared_crop != (0, 0, 0, 0):
            reply = QMessageBox.question(
                self, "Turn On Per-Cut Crop?",
                "Turning on Per-Cut Crop clears the current shared crop (cuts start "
                "crop-less until you set one individually). Continue?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                self._syncing = True
                self.per_cut_crop_check.setChecked(False)
                self._syncing = False
                return
        if enabled:
            # A cut with no override of its own falls back to shared_crop for
            # display - clear it here so cuts you haven't touched yet show as
            # crop-less, not as if they'd already inherited whatever generic
            # crop was set before switching to per-cut mode.
            self.shared_crop = (0, 0, 0, 0)
        self.per_cut_crop_enabled = enabled
        if self.path:
            # Read-only resolution, same as select_cut - toggling the checkbox only
            # changes which crop is *displayed*; it must not itself seed an override
            # onto the active cut the way an actual edit (current_crop_target) would.
            if enabled:
                cut = self.cuts[self.active_cut]
                crop = cut.crop if cut.crop is not None else self.shared_crop
            else:
                crop = self.shared_crop
            self.show_crop(crop)
        # refresh_cut_list() also calls update_combined_export_availability() -
        # needed here too since toggling changes every row's crop-icon visibility
        # at once (the icon only shows while this checkbox is on).
        self.refresh_cut_list()

    def trim_seconds_for_cut(self, cut):
        """Resolve a Cut's start/end to seconds without touching any live widget -
        the read-only counterpart to select_cut, used for list sorting (Task 2)
        and timeline segments (Task 3)."""
        if cut.frame_mode:
            fps = self.video_fps or 30.0
            start = cut.start_repr / fps
            end = cut.end_repr / fps if cut.end_repr is not None else self.duration
        else:
            start = self.keyframe_time(cut.start_repr)
            end = self.keyframe_time(cut.end_repr) if cut.end_repr is not None else self.duration
        return start, end

    def refresh_timeline_cuts(self):
        if not self.duration:
            self.scrubber.set_cuts([])
            return
        segments = []
        for i, cut in enumerate(self.cuts):
            start, end = self.trim_seconds_for_cut(cut)
            segments.append((start, end, i == self.active_cut, cut.frame_mode))
        self.scrubber.set_cuts(segments)

    def sync_selection_to_playhead(self):
        """The Cut list's selection follows the playhead: if it's inside a
        cut, that cut becomes the selected one (and its crop is what the
        overlay shows, via on_cut_list_selection_changed/refresh_cut_list); if
        it's in a gap (outside every cut), the list is deselected entirely and
        the crop overlay hides - there's no "current cut" there to show one
        for. Leaves active_cut and the Start/End fields alone when
        deselecting, so they still show whatever was last being edited."""
        if not self.cuts:
            return
        pos = self.current_position()
        match = next((i for i, c in enumerate(self.cuts)
                      if self.trim_seconds_for_cut(c)[0] <= pos <= self.trim_seconds_for_cut(c)[1]), None)
        selected_rows = self.selected_cut_rows()
        if match is None:
            if selected_rows:
                self.cut_list.clearSelection()
        else:
            already_selected = (len(selected_rows) == 1
                                 and self.cut_list.item(selected_rows[0]).data(Qt.UserRole) == match)
            if not already_selected:
                self.sync_active_cut_from_widgets()
                self.select_cut(match)
                self.refresh_cut_list()

    def on_segment_clicked(self, index):
        """Handler for Scrubber.segment_clicked, which emits a raw index into
        self.cuts (creation order, via enumerate() in refresh_timeline_cuts) -
        unlike on_cut_list_selected's row argument, this needs no translation
        through cut_list's start-time-sorted rows."""
        self.sync_active_cut_from_widgets()
        self.select_cut(index)
        self.refresh_cut_list()

    def on_segment_edited(self, index, start, end):
        if index != self.active_cut:
            self.sync_active_cut_from_widgets()
            self.select_cut(index)
        cut = self.cuts[index]
        if cut.frame_mode:
            self.write_start_field(start)
            self.write_end_field(end)
        else:
            self.start_keyframe_edit.setText(str(self.keyframe_index_of(start)))
            self.end_keyframe_edit.setText(str(self.keyframe_index_of(end)))
        self.refresh_timeline_cuts()
        self.refresh_cut_list()

