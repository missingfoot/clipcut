"""ExportProcessMixin: driving the QProcess-based ffmpeg/gifsicle export pipeline."""

import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout

from clipcutlib.constants import GIFSICLE_PATH
from clipcutlib.utils import next_available, parse_out_time_seconds


class ExportProcessMixin:
    def advance_spinner(self):
        frame = self.spinner_frames[self.spinner_index]
        self.spinner_index = (self.spinner_index + 1) % len(self.spinner_frames)
        self.status.setText(f"{frame} {self.spinner_label}... {self.progress_percent}%")

    def on_process_output(self):
        if self.progress_target_duration <= 0:
            self.process.readAllStandardOutput()
            return
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self.progress_buffer += data
        *complete_lines, self.progress_buffer = self.progress_buffer.split("\n")
        for line in complete_lines:
            seconds = parse_out_time_seconds(line.strip())
            if seconds is not None:
                pct = max(0, min(100, int(seconds / self.progress_target_duration * 100)))
                self.progress_percent = pct

    def do_process(self):
        if self.export_mode_combined.isChecked() and len(self.cuts) > 1:
            self.do_process_combined()
            return
        self.export_is_combined = False
        try:
            self.sync_active_cut_from_widgets()
            saved_active = self.active_cut
            append_text = self.suffix_edit.text().strip() or self.compute_default_suffix()
            queue = []
            for row, cut_index in enumerate(self.ordered_cut_indices()):
                self.select_cut(cut_index)
                ext = self.export_ext()
                suffix = f"{append_text}_{row + 1}" if len(self.cuts) > 1 else append_text
                out_path = next_available(self.path.with_name(f"{self.path.stem}_{suffix}{ext}"))
                cmd, target_duration = self.build_ffmpeg_args(out_path)
                queue.append((cmd, out_path, target_duration))
            self.select_cut(saved_active)
        except ValueError as e:
            self.status.setText(f"Invalid input: {e}")
            self.error_details_button.setVisible(False)
            return

        self.export_queue = queue
        self.export_total = len(queue)
        self.export_current = 0
        self.export_gif_optimize = (self.format_combo.currentText() == "GIF"
                                     and self.gif_optimize_check.isChecked()
                                     and GIFSICLE_PATH is not None)
        self.error_details_button.setVisible(False)
        self.process_button.setEnabled(False)
        self.run_next_export()

    def do_process_combined(self):
        if not self.crops_are_uniform():
            # Belt-and-suspenders: the Combined radio is disabled whenever crops
            # differ (update_combined_export_availability), so this shouldn't be
            # reachable, but never silently build a nonsensical combined file.
            self.status.setText("Combined export requires every cut to use the same crop")
            self.error_details_button.setVisible(False)
            return
        try:
            self.sync_active_cut_from_widgets()
            saved_active = self.active_cut
            tmp_dir = Path(tempfile.mkdtemp(prefix="clipcut_"))
            queue = []
            for row, cut_index in enumerate(self.ordered_cut_indices()):
                self.select_cut(cut_index)
                ext = self.export_ext()
                tmp_path = tmp_dir / f"part_{row + 1}{ext}"
                cmd, target_duration = self.build_ffmpeg_args(tmp_path)
                queue.append((cmd, tmp_path, target_duration))
            self.select_cut(saved_active)
        except ValueError as e:
            self.status.setText(f"Invalid input: {e}")
            self.error_details_button.setVisible(False)
            return

        self.combined_tmp_dir = tmp_dir
        self.export_queue = queue
        self.export_total = len(queue)
        self.export_current = 0
        self.export_is_combined = True
        # The per-cut parts are temp files discarded after concat - never
        # worth optimizing. Only the final joined output (set in run_concat)
        # is a real GIF export worth running gifsicle on.
        self.export_gif_optimize = False
        self.error_details_button.setVisible(False)
        self.process_button.setEnabled(False)
        self.run_next_export()

    def run_concat(self):
        parts = sorted(self.combined_tmp_dir.glob("part_*"), key=lambda p: int(p.stem.split("_")[1]))
        append_text = self.suffix_edit.text().strip() or self.compute_default_suffix()
        ext = self.export_ext()
        out_path = next_available(self.path.with_name(f"{self.path.stem}_{append_text}{ext}"))

        list_path = self.combined_tmp_dir / "list.txt"
        list_path.write_text("".join(f"file '{p}'\n" for p in parts))
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)]

        self.pending_out_path = out_path
        self.export_is_combined = False  # concat's own finish should behave like a normal single completion
        self.export_gif_optimize = (self.format_combo.currentText() == "GIF"
                                     and self.gif_optimize_check.isChecked()
                                     and GIFSICLE_PATH is not None)
        self.progress_target_duration = 0
        self.progress_percent = 0
        self.spinner_index = 0
        self.spinner_label = "Joining"
        self.advance_spinner()
        self.spinner_timer.start()  # the preceding per-part on_process_finished stopped it
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.on_process_output)
        self.process.finished.connect(self.on_concat_finished)
        self.process.errorOccurred.connect(self.on_process_error)
        self.process.start(cmd[0], cmd[1:])

    def on_concat_finished(self, exit_code, exit_status):
        self.cleanup_combined_tmp_dir()
        self.on_process_finished(exit_code, exit_status)

    def run_next_export(self):
        if not self.export_queue:
            if getattr(self, "export_is_combined", False):
                self.run_concat()
                return
            self.spinner_timer.stop()
            self.process_button.setEnabled(True)
            self.status.setText("Saved successfully")
            return
        cmd, out_path, target_duration = self.export_queue.pop(0)
        self.pending_out_path = out_path
        self.progress_target_duration = target_duration
        self.progress_buffer = ""
        self.progress_percent = 0
        self.spinner_index = 0
        self.export_current += 1
        self.spinner_label = (f"Exporting {self.export_current} of {self.export_total}"
                               if self.export_total > 1 else "Working")
        self.advance_spinner()
        self.spinner_timer.start()

        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.on_process_output)
        self.process.finished.connect(self.on_process_finished)
        self.process.errorOccurred.connect(self.on_process_error)
        self.process.start(cmd[0], cmd[1:])

    def report_error(self, summary, details):
        self.status.setText(summary)
        self.error_details = details
        self.error_details_button.setVisible(bool(details))

    def show_error_details(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Error Details")
        dialog.resize(600, 400)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(self.error_details)
        text.setReadOnly(True)
        layout.addWidget(text)

        footer = QHBoxLayout()
        copy_button = QPushButton("Copy to Clipboard")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.error_details))
        footer.addWidget(copy_button)
        footer.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        dialog.exec()

    def cleanup_combined_tmp_dir(self):
        """Remove the combined-export scratch dir, if one is in flight, so a
        failed combined export (per-cut part failure, or ffmpeg failing to
        launch) doesn't leave a clipcut_* temp directory full of video parts
        behind permanently. Safe to call unconditionally - cleared after use so
        a later unrelated export doesn't try to clean it again."""
        tmp_dir = getattr(self, "combined_tmp_dir", None)
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.combined_tmp_dir = None

    def on_process_error(self, _error):
        self.spinner_timer.stop()
        self.process_button.setEnabled(True)
        self.report_error("Failed to launch ffmpeg", self.process.errorString())
        self.process = None
        self.export_queue = []
        self.cleanup_combined_tmp_dir()

    def on_process_finished(self, exit_code, _exit_status):
        self.spinner_timer.stop()
        stderr = bytes(self.process.readAllStandardError()).decode(errors="replace")
        self.process = None

        if exit_code != 0:
            self.process_button.setEnabled(True)
            self.report_error("ffmpeg failed", stderr)
            self.export_queue = []
            self.cleanup_combined_tmp_dir()
            return

        out_path = self.pending_out_path
        src_stat = self.path.stat()
        os.utime(out_path, (src_stat.st_atime, src_stat.st_mtime))
        self.error_details_button.setVisible(False)
        if getattr(self, "export_gif_optimize", False):
            self.run_gifsicle(out_path)
        else:
            self.run_next_export()

    def run_gifsicle(self, out_path):
        """gifsicle's -b rewrites the file in place (it reads the whole
        input before writing output, so this is safe) - -O3 is its full
        optimization level, --lossy trades a little fidelity for a lot more
        compression than ffmpeg's palette approach alone can reach."""
        self.pending_out_path = out_path
        self.progress_target_duration = 0  # gifsicle has no -progress-style output to parse
        self.progress_percent = 0
        self.spinner_index = 0
        self.spinner_label = "Optimizing"
        self.advance_spinner()
        self.spinner_timer.start()
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.on_process_output)
        self.process.finished.connect(self.on_gifsicle_finished)
        self.process.errorOccurred.connect(self.on_gifsicle_error)
        self.process.start(GIFSICLE_PATH, ["-b", "-O3", "--lossy=80", str(out_path)])

    def on_gifsicle_finished(self, _exit_code, _exit_status):
        # Best-effort: the ffmpeg-produced GIF is already valid and saved
        # either way, so a gifsicle failure just means this one export
        # missed the extra optimization pass, not that the export failed.
        self.spinner_timer.stop()
        self.process = None
        self.run_next_export()

    def on_gifsicle_error(self, _error):
        self.spinner_timer.stop()
        self.process = None
        self.run_next_export()


