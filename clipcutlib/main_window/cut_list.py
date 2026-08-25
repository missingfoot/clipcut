"""CutListMixin: the cut list widget - add/delete/keep/merge, scene detection, selection."""

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidgetItem, QMessageBox, QWidget

from clipcutlib.small_widgets import Cut
from clipcutlib.utils import format_time


class CutListMixin:
    def cut_start_seconds(self, cut):
        """Best-effort start time in seconds for sorting/display - reuses
        keyframe_time/fps math without touching the live widgets."""
        if cut.frame_mode:
            return cut.start_repr / (self.video_fps or 30.0)
        return self.keyframe_time(cut.start_repr)

    def ordered_cut_indices(self):
        """Indices into self.cuts in start-time (chronological) order - the order
        the cut list UI shows, used here and by both export builders so there is
        one definition of "cut order," not two (creation order vs. chronological)
        that can silently disagree."""
        return sorted(range(len(self.cuts)), key=lambda i: self.cut_start_seconds(self.cuts[i]))

    def make_cut_row_widget(self, number, start, end, has_crop):
        """A per-row widget (icon, a fixed-width left-aligned number, the
        start-end range, and - if this cut carries its own crop - a crop icon
        on the far right) so the number "column" actually lines up regardless
        of digit count (1 vs 10 vs 100) - in the normal UI font, not a
        monospace one. A plain text item can't do this: proportional fonts
        don't give equal-width digits, so padding with spaces doesn't
        reliably align."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)
        icon_label = QLabel()
        icon_label.setPixmap(self.cut_row_icon.pixmap(16, 16))
        layout.addWidget(icon_label)
        number_label = QLabel(str(number))
        number_label.setMinimumWidth(QFontMetrics(number_label.font()).horizontalAdvance("999") + 4)
        layout.addWidget(number_label)
        layout.addWidget(QLabel(f"{format_time(start)}-{format_time(end)}"))
        layout.addStretch()
        if has_crop:
            crop_icon_label = QLabel()
            crop_icon_label.setPixmap(self.cut_row_crop_icon.pixmap(16, 16))
            crop_icon_label.setToolTip("This cut has its own crop")
            layout.addWidget(crop_icon_label)
        return widget

    def refresh_cut_list(self):
        was_blocked = self.cut_list.blockSignals(True)
        self.cut_list.clear()
        ordered = self.ordered_cut_indices()
        for row, cut_index in enumerate(ordered):
            cut = self.cuts[cut_index]
            start, end = self.trim_seconds_for_cut(cut)
            has_crop = self.per_cut_crop_enabled and cut.crop is not None
            item = QListWidgetItem()
            self.cut_list.addItem(item)
            self.cut_list.setItemWidget(item, self.make_cut_row_widget(row + 1, start, end, has_crop))
            item.setData(Qt.UserRole, cut_index)
            if cut_index == self.active_cut:
                self.cut_list.setCurrentRow(row)
                item.setSelected(True)
        self.cut_list.blockSignals(was_blocked)
        self.update_cut_selection_buttons()
        self.preview.set_crop_overlay_visible(len(self.selected_cut_rows()) == 1)
        self.refresh_timeline_cuts()
        self.update_combined_export_availability()

    def crops_are_uniform(self):
        """Whether every cut would export with the same crop and codec path.
        Combined-file export only supports the fast stream-copy concat demuxer,
        which requires every part to be pixel-identical - so this gates whether
        Combined is even offered, rather than falling back to a re-encoding
        concat filter for mismatched crops."""
        if len(self.cuts) <= 1:
            return True
        resolved = set()
        frame_modes = set()
        for cut in self.cuts:
            if self.per_cut_crop_enabled and cut.crop is not None:
                left, top, right, bottom = cut.crop
            else:
                left, top, right, bottom = self.shared_crop
            crop_w = (self.video_width or 0) - left - right
            crop_h = (self.video_height or 0) - top - bottom
            resolved.add((left, top, crop_w, crop_h))
            frame_modes.add(cut.frame_mode)
        return len(resolved) <= 1 and len(frame_modes) <= 1

    def update_combined_export_availability(self):
        enabled = bool(self.path) and self.crops_are_uniform()
        self.export_mode_combined.setEnabled(enabled)
        self.export_mode_combined.setToolTip(
            "" if enabled else "Combined file export requires every cut to use the same crop.")
        if not enabled and self.export_mode_combined.isChecked():
            self.export_mode_separate.setChecked(True)

    def sync_active_cut_from_widgets(self):
        """Save the Start/End/mode fields into the active cut. Deliberately does
        NOT touch cut.crop: this is called on nearly every interaction (editing
        Start/End, adding a cut, switching selection, even just clicking
        Process), including ones that never touched the crop fields at all - if
        it captured crop_edits' current text too, every one of those unrelated
        actions would silently stamp whatever crop happened to be on screen
        (often just the shared_crop fallback display) onto the active cut as if
        it were a deliberate per-cut override. Every real crop edit already
        persists through set_current_crop() directly (on_crop_field_edited,
        on_guides_changed, on_aspect_ratio_selected, clear_crop, etc.)."""
        if not self.cuts:
            return
        cut = self.cuts[self.active_cut]
        cut.frame_mode = self.frame_mode
        start_edit = self.start_frame_edit if self.frame_mode else self.start_keyframe_edit
        end_edit = self.end_frame_edit if self.frame_mode else self.end_keyframe_edit
        cut.start_repr = int(start_edit.text()) if start_edit.text() else 0
        cut.end_repr = int(end_edit.text()) if end_edit.text() else None

    def add_cut(self):
        if not self.path:
            return
        self.sync_active_cut_from_widgets()
        pos = self.current_position()
        if self.frame_mode:
            fps = self.video_fps or 30.0
            start_repr = round(pos * fps)
        else:
            start_repr = self.keyframe_index_of(pos)
        self.cuts.append(Cut(start_repr=start_repr, frame_mode=self.frame_mode))
        self.select_cut(len(self.cuts) - 1)
        self.refresh_cut_list()

    def selected_cut_rows(self):
        """Currently selected cut_list rows, sorted ascending - row numbers are
        chronological (ordered_cut_indices' order), so ascending rows == earliest
        to latest, regardless of which order they were clicked/selected in."""
        return sorted(self.cut_list.row(item) for item in self.cut_list.selectedItems())

    def update_cut_selection_buttons(self, rows=None):
        if rows is None:
            rows = self.selected_cut_rows()
        cut_indices = {self.cut_list.item(r).data(Qt.UserRole) for r in rows}
        # Delete/Keep are disabled (not just a no-op click) when the selection is
        # empty or covers every cut - there'd be nothing left outside it either
        # way, matching the old Delete Cut button's behavior of disabling once
        # only one cut remained.
        has_partial_selection = bool(rows) and len(cut_indices) < len(self.cuts)
        self.delete_selected_button.setEnabled(has_partial_selection)
        self.keep_selected_button.setEnabled(has_partial_selection)
        contiguous = len(rows) >= 2 and all(rows[i + 1] - rows[i] == 1 for i in range(len(rows) - 1))
        self.merge_selected_button.setEnabled(contiguous)

    def delete_selected_cuts(self):
        rows = self.selected_cut_rows()
        if not rows:
            return
        cut_indices = {self.cut_list.item(r).data(Qt.UserRole) for r in rows}
        if len(cut_indices) >= len(self.cuts):
            return  # would delete every cut - there must always be at least one
        self.cuts = [c for i, c in enumerate(self.cuts) if i not in cut_indices]
        # Select whatever now sits where the deleted block started - the cut right
        # after it, shifted up to fill the gap - so repeated Delete clicks walk
        # down the list instead of jumping back to row 0 every time. Falls back to
        # the new last row if the deleted block ran to the end of the list.
        new_ordered = self.ordered_cut_indices()
        next_row = min(rows[0], len(new_ordered) - 1)
        self.select_cut(new_ordered[next_row])
        self.refresh_cut_list()

    def keep_selected_cuts(self):
        rows = self.selected_cut_rows()
        if not rows:
            return
        cut_indices = {self.cut_list.item(r).data(Qt.UserRole) for r in rows}
        if len(cut_indices) >= len(self.cuts):
            return  # nothing outside the selection to discard
        self.cuts = [c for i, c in enumerate(self.cuts) if i in cut_indices]
        self.select_cut(0)
        self.refresh_cut_list()

    def merge_selected_cuts(self):
        rows = self.selected_cut_rows()
        if len(rows) < 2 or any(rows[i + 1] - rows[i] != 1 for i in range(len(rows) - 1)):
            return  # merge button is only enabled for a contiguous selection anyway
        cut_indices = [self.cut_list.item(r).data(Qt.UserRole) for r in rows]
        first_cut = self.cuts[cut_indices[0]]
        last_cut = self.cuts[cut_indices[-1]]
        # All cuts share one frame_mode (on_cut_mode_changed enforces this list-wide),
        # so first/last's start_repr/end_repr are already in the same representation -
        # no seconds round-trip needed. end_repr carries over None (through-the-end)
        # unchanged if the merge includes the chronologically last cut in the list.
        merged = Cut(start_repr=first_cut.start_repr, end_repr=last_cut.end_repr,
                     frame_mode=first_cut.frame_mode, crop=first_cut.crop)
        remove = set(cut_indices)
        new_cuts = [c for i, c in enumerate(self.cuts) if i not in remove]
        merged_index = len(new_cuts)
        new_cuts.append(merged)
        self.cuts = new_cuts
        self.select_cut(merged_index)
        self.refresh_cut_list()

    def detect_scenes(self):
        if not self.path:
            return
        has_work = len(self.cuts) > 1 or any(c.crop is not None for c in self.cuts)
        if has_work:
            reply = QMessageBox.question(
                self, "Replace Cuts?",
                "Scene detection replaces the entire cut list. Continue and discard the current cuts?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        threshold = int(self.scene_threshold_edit.text() or 30) / 100.0
        self.scene_detect_output = ""
        self.detect_scenes_button.setEnabled(False)
        self.status.setText("Detecting scenes...")
        self.scene_process = QProcess(self)
        self.scene_process.setProcessChannelMode(QProcess.MergedChannels)
        self.scene_process.readyReadStandardOutput.connect(self.on_scene_detect_output)
        self.scene_process.finished.connect(self.on_scene_detect_finished)
        self.scene_process.start("ffmpeg", [
            "-i", str(self.path),
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-"])

    def on_scene_detect_output(self):
        data = bytes(self.scene_process.readAllStandardOutput()).decode(errors="replace")
        self.scene_detect_output += data

    def on_scene_detect_finished(self, exit_code, _exit_status):
        output = self.scene_detect_output
        self.scene_process = None
        # File may have been removed (remove_file already disabled the button and
        # cleared self.duration) while detection was running - nothing sensible to
        # do with a result for a file that's no longer loaded, and self.duration
        # being None would blow up the `t < self.duration` comparison below.
        if not self.path or not self.duration:
            self.detect_scenes_button.setEnabled(bool(self.path))
            return
        self.detect_scenes_button.setEnabled(True)
        if exit_code != 0:
            self.report_error("Scene detection failed", output)
            return

        times = []
        for line in output.splitlines():
            marker = "pts_time:"
            if marker in line:
                chunk = line.split(marker, 1)[1].split()[0]
                try:
                    times.append(float(chunk))
                except ValueError:
                    pass
        if not times:
            self.status.setText("No scene changes detected")
            return

        times = sorted(set(t for t in times if 0 < t < self.duration))
        boundaries = [0.0] + times + [self.duration]
        segments = list(zip(boundaries, boundaries[1:]))
        new_cuts = []
        for i, (start, end) in enumerate(segments):
            is_last = i == len(segments) - 1
            if self.frame_mode:
                fps = self.video_fps or 30.0
                end_repr = None if is_last else round(end * fps)
                new_cuts.append(Cut(start_repr=round(start * fps), end_repr=end_repr, frame_mode=True))
            else:
                end_repr = None if is_last else self.keyframe_index_of(end)
                new_cuts.append(Cut(start_repr=self.keyframe_index_of(start),
                                     end_repr=end_repr, frame_mode=False))
        self.cuts = new_cuts
        self.select_cut(0)
        self.refresh_cut_list()
        self.status.setText(f"Found {len(new_cuts)} scenes")

    def on_cut_list_selection_changed(self):
        if not self.cuts:
            return
        rows = self.selected_cut_rows()
        self.update_cut_selection_buttons(rows)
        # No selection, or more than one row, means there's no single "current
        # cut" to show a crop for.
        self.preview.set_crop_overlay_visible(len(rows) == 1)
        # A single selected row drives the Start/End/crop fields exactly like
        # before (multi-select has no one cut to edit, so the fields just keep
        # showing whatever was last active).
        if len(rows) == 1:
            self.sync_active_cut_from_widgets()
            cut_index = self.cut_list.item(rows[0]).data(Qt.UserRole)
            self.select_cut(cut_index)

    def select_cut(self, index):
        """Push cuts[index]'s data into the Start/End/mode widgets (and crop
        widgets, if per-cut crop is on) - the single sync point used by both
        UI selection and the export loop. Mirrors write_start_field/
        write_end_field's blank-means-default convention: end_repr=None
        writes a blank End field."""
        cut = self.cuts[index]
        self.active_cut = index
        self._syncing = True
        self.frame_mode = cut.frame_mode
        self.cut_mode_frame.setChecked(cut.frame_mode)
        self.cut_mode_keyframe.setChecked(not cut.frame_mode)
        self.frame_start_widget.setVisible(cut.frame_mode)
        self.frame_end_widget.setVisible(cut.frame_mode)
        self.keyframe_start_widget.setVisible(not cut.frame_mode)
        self.keyframe_end_widget.setVisible(not cut.frame_mode)
        start_edit = self.start_frame_edit if cut.frame_mode else self.start_keyframe_edit
        end_edit = self.end_frame_edit if cut.frame_mode else self.end_keyframe_edit
        start_edit.setText(str(cut.start_repr) if cut.start_repr else "")
        end_edit.setText(str(cut.end_repr) if cut.end_repr is not None else "")
        self._syncing = False
        if self.per_cut_crop_enabled:
            crop = cut.crop if cut.crop is not None else self.shared_crop
            self.show_crop(crop)
        else:
            self.update_crop_size_fields()
        self.update_suffix_placeholder()
        self.refresh_timeline_cuts()

