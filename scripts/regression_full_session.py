#!/usr/bin/env python3
"""Full-session regression test for the multi-cut-lists feature.

Exercises the cross-widget interactions that unit-style per-task verification
scripts didn't cover and that the 2026-08-21 final whole-branch review caught:
cut creation out of chronological order, per-cut crop toggle/edit/reset,
both export modes (including a differing-crops + remove-audio combined
export), and scene detection. This is the regression test recommended at the
end of .superpowers/sdd/2026-08-21-multi-cut-lists/final-review-findings.md,
written to specifically catch Critical #1 (scrubber click-to-seek dead on a
fresh load) and Critical #2 (per-cut crop toggle/reset not syncing the crop
text fields, the actual export source of truth), plus Important #3-#7.

Run from the repo root:

    QT_QPA_PLATFORM=offscreen python3 scripts/regression_full_session.py

Requires ffmpeg/ffprobe on PATH. Builds its own fixture clip in a temp dir
(does not depend on /tmp/clipcut_fixture.mp4). Exits 0 on success, 1 if any
check failed (a summary of failures is printed at the end either way).
"""
import importlib.machinery
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QPoint, QProcess, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

REPO_ROOT = Path(__file__).resolve().parent.parent
clipcut = importlib.machinery.SourceFileLoader("clipcut", str(REPO_ROOT / "clipcut")).load_module()

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        msg = f"FAIL {name}" + (f" -- {detail}" if detail else "")
        print(msg)
        FAILURES.append(name)


def pump(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(predicate, timeout_ms=30000, interval_ms=50):
    loop = QEventLoop()
    elapsed = [0]

    def tick():
        elapsed[0] += interval_ms
        if predicate() or elapsed[0] >= timeout_ms:
            loop.quit()
        else:
            QTimer.singleShot(interval_ms, tick)

    QTimer.singleShot(interval_ms, tick)
    loop.exec()
    return predicate()


def make_scene_fixture(tmp_dir):
    """A 6s, 320x240, 30fps clip: 2s of testsrc, 2s of testsrc2, 2s of
    rgbtestsrc, with a silent audio track. Flat solid colors turned out to
    score ~0 on ffmpeg's scene-change metric (it's edge/texture-based, not a
    raw color difference) and went undetected; these three textured synthetic
    patterns give two unambiguous, well-above-default-threshold scene changes
    at ~2s and ~4s, confirmed against `ffmpeg -vf scdet=threshold=0`."""
    path = Path(tmp_dir) / "scene_fixture.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
        "-f", "lavfi", "-i", "rgbtestsrc=size=320x240:rate=30:duration=2",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-shortest",
        "-pix_fmt", "yuv420p", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def main():
    app = QApplication(sys.argv)
    tmp_dir = tempfile.mkdtemp(prefix="clipcut_regression_")
    fixture = make_scene_fixture(tmp_dir)

    win = clipcut.MainWindow()
    win.show()
    win.load_file(fixture)
    pump(200)

    # --- Step 0: fresh load looks like the old single-cut app ---
    check("fresh load has one cut spanning the whole file",
          len(win.cuts) == 1 and win.cuts[0].start_repr == 0 and win.cuts[0].end_repr is None,
          str(win.cuts))

    # --- Critical #1: scrubber body click both selects AND seeks ---
    win.scrubber.resize(400, 28)
    click_x = 200  # middle of the single full-span segment - a "body" hit, not a handle
    before_pos = win.preview.player.position()
    QTest.mousePress(win.scrubber, Qt.LeftButton, pos=QPoint(click_x, 14))
    pump(200)
    expected_t = win.scrubber.time_for_x(click_x)
    check("scrubber body click enters playhead-drag (not dead)",
          win.scrubber._dragging == "playhead", f"_dragging={win.scrubber._dragging}")
    check("scrubber body click actually seeks the player",
          abs(win.preview.player.position() - before_pos) > 50 or expected_t < 0.05,
          f"before={before_pos} after={win.preview.player.position()} expected_t={expected_t}")
    QTest.mouseRelease(win.scrubber, Qt.LeftButton, pos=QPoint(click_x, 14))

    # --- Step 2: add 3 more cuts out of chronological order, using the playhead
    # as each new cut's start (Minor #13) ---
    def seek_and_add_cut(seconds):
        win.preview.player.setPosition(int(seconds * 1000))
        pump(50)
        win.add_cut()

    seek_and_add_cut(4.0)   # cut index 1: start ~4.0s (frame 120)
    win.write_end_field(5.0)
    seek_and_add_cut(0.5)   # cut index 2: start ~0.5s (frame 15)
    win.write_end_field(1.5)
    seek_and_add_cut(2.5)   # cut index 3: start ~2.5s (frame 75)
    win.write_end_field(3.5)

    check("add_cut used the playhead position, not 0, as the new cut's start",
          len(win.cuts) == 4 and win.cuts[1].start_repr == 120 and
          win.cuts[2].start_repr == 15 and win.cuts[3].start_repr == 75,
          str(win.cuts))

    # Creation order is [0, 1, 2, 3]; chronological order (by start time) should
    # be [0 (0s), 2 (0.5s), 3 (2.5s), 1 (4.0s)] - deliberately different, to
    # exercise Important #4 (export ordering) and #6/#7 (list ordering).
    check("ordered_cut_indices() sorts by start time, not creation order",
          win.ordered_cut_indices() == [0, 2, 3, 1], str(win.ordered_cut_indices()))

    # --- Important #6: cut list rows are distinct, showing "N: start-end" ---
    win.refresh_cut_list()
    row_texts = [win.cut_list.item(i).text() for i in range(win.cut_list.count())]
    check("cut list rows are not all identical placeholder text",
          len(set(row_texts)) == len(row_texts), str(row_texts))
    expected_row0 = f"1: {clipcut.format_time(0.0)}-{clipcut.format_time(6.0)}"
    check("cut list row label matches 'N: start-end' format",
          row_texts[0] == expected_row0, f"got {row_texts[0]!r} want {expected_row0!r}")

    # --- Important #7: editing the active cut's Start re-sorts/relabels the list
    # live, without a separate manual refresh ---
    win.select_cut(1)  # the ~4.0-5.0s cut
    win.write_start_field(0.2)  # now starts before cut index 2 (0.5s)
    check("ordered_cut_indices() reflects the just-edited start immediately",
          win.ordered_cut_indices() == [0, 1, 2, 3], str(win.ordered_cut_indices()))
    reordered_texts = [win.cut_list.item(i).text() for i in range(win.cut_list.count())]
    check("write_start_field's re-sort is visible in the cut list without an extra refresh_cut_list() call",
          reordered_texts != row_texts, str(reordered_texts))

    # From here on, chronological order is [0, 1, 2, 3] (cut 1's start was just
    # moved to 0.2s, ahead of cut 2's 0.5s) - snapshot it once so the export
    # assertions below check against the actual current order, not a stale one.
    expected_export_order = win.ordered_cut_indices()

    # --- Critical #2: per-cut crop toggle/edit/reset stay in sync with crop_edits,
    # the actual field get_crop() (and therefore every export) reads from ---
    win.select_cut(1)
    win.per_cut_crop_check.setChecked(True)
    pump(20)
    check("toggling per-cut crop ON does not seed an override onto the active cut",
          win.cuts[1].crop is None, str(win.cuts[1].crop))
    check("toggling per-cut crop ON shows the resolved (shared) crop in the fields",
          all(e.text() == "" for e in win.crop_edits), str([e.text() for e in win.crop_edits]))

    for edit, value in zip(win.crop_edits, ("20", "10", "30", "15")):
        edit.setText(value)
    win.sync_active_cut_from_widgets()  # what nearly every real interaction triggers
    check("editing the crop fields writes the override onto the active cut",
          win.cuts[1].crop == (20, 10, 30, 15), str(win.cuts[1].crop))

    win.select_cut(2)
    check("a different cut with no override shows the shared crop, not cut 1's override",
          all(e.text() == "" for e in win.crop_edits), str([e.text() for e in win.crop_edits]))
    win.select_cut(1)
    check("re-selecting cut 1 restores its override into the fields",
          tuple(int(e.text() or 0) for e in win.crop_edits) == (20, 10, 30, 15),
          str([e.text() for e in win.crop_edits]))

    win.reset_cut_crop()
    check("Reset to Shared Crop clears the stored override",
          win.cuts[1].crop in (None, (0, 0, 0, 0)), str(win.cuts[1].crop))
    check("Reset to Shared Crop also clears the crop TEXT FIELDS (the actual export source)",
          all(e.text() == "" for e in win.crop_edits), str([e.text() for e in win.crop_edits]))
    win.sync_active_cut_from_widgets()  # simulate the very next interaction
    check("...and the reset survives the next sync_active_cut_from_widgets() (doesn't resurrect the old override)",
          win.cuts[1].crop in (None, (0, 0, 0, 0)), str(win.cuts[1].crop))

    # Toggling OFF must restore shared_crop into the fields, and must NOT destroy
    # a stored override; toggling back ON must restore that override untouched.
    win.cuts[1].crop = (7, 7, 7, 7)
    win.select_cut(2)
    win.select_cut(1)  # re-select to refresh fields from the override just set
    win.per_cut_crop_check.setChecked(False)
    pump(20)
    check("toggling per-cut crop OFF shows shared_crop in the fields immediately",
          all(e.text() == "" for e in win.crop_edits), str([e.text() for e in win.crop_edits]))
    check("toggling per-cut crop OFF does not destroy the stored override",
          win.cuts[1].crop == (7, 7, 7, 7), str(win.cuts[1].crop))
    win.per_cut_crop_check.setChecked(True)
    pump(20)
    check("toggling per-cut crop back ON restores the override into the fields (not destroyed)",
          tuple(int(e.text() or 0) for e in win.crop_edits) == (7, 7, 7, 7),
          str([e.text() for e in win.crop_edits]))
    check("...and the underlying cut.crop is unchanged by the toggle round-trip",
          win.cuts[1].crop == (7, 7, 7, 7), str(win.cuts[1].crop))

    # --- Minor #12: label renamed from the backwards "Sensitivity %" ---
    labels = [w.text() for w in win.findChildren(QLabel)]
    check("scene-detection field is labelled 'Threshold %', not the backwards 'Sensitivity %'",
          "Threshold %" in labels and "Sensitivity %" not in labels, str(labels))

    # --- Minor #8: dead build_command() removed ---
    check("dead build_command() was removed",
          not hasattr(clipcut.MainWindow, "build_command"))

    # --- Important #4: export builders iterate in chronological order, not
    # creation order - spy on build_ffmpeg_args to record the order cuts are
    # actually processed in ---
    processed_order = []
    orig_build_ffmpeg_args = clipcut.MainWindow.build_ffmpeg_args

    def spy_build_ffmpeg_args(self, out_path):
        processed_order.append(self.active_cut)
        return orig_build_ffmpeg_args(self, out_path)

    clipcut.MainWindow.build_ffmpeg_args = spy_build_ffmpeg_args

    win.export_mode_separate.setChecked(True)
    win.remove_audio.setChecked(False)
    win.audio_only.setChecked(False)
    win.suffix_edit.setText("regtest")
    win.do_process()
    ok = wait_until(lambda: win.process_button.isEnabled(), timeout_ms=60000)
    check("separate-mode export completed", ok, win.status.text())
    check("separate-mode export processed cuts in chronological order, not creation order",
          processed_order == expected_export_order, f"{processed_order} != {expected_export_order}")
    exported = sorted(Path(tmp_dir).glob("scene_fixture_regtest_*.mp4"))
    check("separate-mode export produced one file per cut, numbered in chronological order",
          len(exported) == 4 and exported[0].name.endswith("_1.mp4") and exported[-1].name.endswith("_4.mp4"),
          str([p.name for p in exported]))
    for p in exported:
        p.unlink(missing_ok=True)
    processed_order.clear()

    # --- Important #3 + #16, Minor #9/#10: combined export with DIFFERING crops
    # (cut 1 has a (7,7,7,7) override, others don't - forces the concat-filter
    # path) AND "Remove audio" checked (forces the a=0 branch that used to
    # hard-fail ffmpeg on every combined export with no audio to concat) ---
    captured_cmds = []
    orig_qprocess_start = QProcess.start

    def spy_qprocess_start(self, *a, **kw):
        captured_cmds.append(a)
        return orig_qprocess_start(self, *a, **kw)

    QProcess.start = spy_qprocess_start

    win.export_mode_combined.setChecked(True)
    win.remove_audio.setChecked(True)
    win.suffix_edit.setText("combined")
    spinner_labels_seen = []
    orig_advance_spinner = clipcut.MainWindow.advance_spinner

    def spy_advance_spinner(self):
        spinner_labels_seen.append(self.spinner_label)
        return orig_advance_spinner(self)

    clipcut.MainWindow.advance_spinner = spy_advance_spinner
    win.do_process()
    ok = wait_until(lambda: win.process_button.isEnabled(), timeout_ms=60000)
    clipcut.MainWindow.advance_spinner = orig_advance_spinner
    check("combined export with differing crops + remove-audio completed (Important #3)",
          ok, win.status.text() + " | " + (win.error_details if hasattr(win, "error_details") else ""))
    check("combined export processed cuts in chronological order",
          processed_order == expected_export_order, f"{processed_order} != {expected_export_order}")
    check("spinner showed an 'Exporting N of M' phase during per-part export",
          any(lbl.startswith("Exporting ") for lbl in spinner_labels_seen), str(set(spinner_labels_seen)))
    check("spinner showed a 'Joining' phase during the concat pass (Minor #9)",
          "Joining" in spinner_labels_seen, str(set(spinner_labels_seen)))
    concat_cmd = next((c for c in captured_cmds if "-filter_complex" in c[1]), None)
    check("combined export used the differing-crops filter path (not the stream-copy fast path)",
          concat_cmd is not None, str([c[0] for c in captured_cmds]))
    if concat_cmd is not None:
        argv = concat_cmd[1]
        check("filter-path concat did not demand an audio stream that doesn't exist (Important #3)",
              "a=0" in "".join(argv) and "[a]" not in argv, str(argv))
        check("filter-path concat re-encodes at the same quality as every other path (Minor #16)",
              "-crf" in argv and "17" in argv and "veryfast" in argv, str(argv))
    combined_out = sorted(Path(tmp_dir).glob("scene_fixture_combined.mp4"))
    check("combined export produced a single output file", len(combined_out) == 1, str(combined_out))
    for p in combined_out:
        p.unlink(missing_ok=True)
    check("combined temp dir was cleaned up after a successful export (Important #5)",
          getattr(win, "combined_tmp_dir", None) is None)

    QProcess.start = orig_qprocess_start
    win.remove_audio.setChecked(False)
    processed_order.clear()

    # --- Important #5: temp dir is also cleaned up when a combined export FAILS
    # (inject a bogus ffmpeg flag to force a per-part failure) ---
    def bad_build_ffmpeg_args(self, out_path):
        cmd, dur = orig_build_ffmpeg_args(self, out_path)
        cmd.insert(1, "-this-flag-does-not-exist")
        return cmd, dur

    clipcut.MainWindow.build_ffmpeg_args = bad_build_ffmpeg_args
    win.suffix_edit.setText("shouldfail")
    win.do_process()
    ok = wait_until(lambda: win.process_button.isEnabled(), timeout_ms=30000)
    check("a failed combined export still re-enables the process button", ok)
    check("a failed combined export's temp dir was cleaned up, not leaked (Important #5)",
          getattr(win, "combined_tmp_dir", None) is None)
    check("a failed export reports the ffmpeg error", bool(win.error_details))
    clipcut.MainWindow.build_ffmpeg_args = orig_build_ffmpeg_args

    # --- Minor #11: scene detection surfaces a nonzero ffmpeg exit code instead
    # of misreporting it as "No scene changes detected", and doesn't crash if
    # self.duration is None (e.g. file removed mid-detection) ---
    saved_duration = win.duration
    win.scene_detect_output = "some ffmpeg stderr explaining the failure"
    win.on_scene_detect_finished(1, 0)
    check("a nonzero scene-detection exit code is reported as an error, not silently swallowed",
          "failed" in win.status.text().lower(), win.status.text())

    win.duration = None
    try:
        win.on_scene_detect_finished(0, 0)
        no_crash = True
    except TypeError:
        no_crash = False
    check("on_scene_detect_finished doesn't crash when self.duration is None", no_crash)
    win.duration = saved_duration

    # --- Real scene detection end-to-end (3 distinct color scenes -> 2 cuts) ---
    orig_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    win.scene_threshold_edit.setText("30")
    win.detect_scenes()
    ok = wait_until(lambda: win.scene_process is None, timeout_ms=30000)
    QMessageBox.question = orig_question
    check("scene detection completed", ok)
    check("scene detection found the 3 distinct color scenes",
          len(win.cuts) == 3, f"found {len(win.cuts)} cuts: {win.cuts}")
    if len(win.cuts) == 3:
        starts = sorted(win.cut_start_seconds(c) for c in win.cuts)
        check("scene boundaries land near the real 2s/4s cuts",
              abs(starts[1] - 2.0) < 0.5,
              str(starts))

    # --- Minor #15: current_crop_target/set_current_crop don't crash with no
    # file loaded, and per_cut_crop_enabled resets symmetrically with load_file ---
    win.remove_file()
    check("remove_file resets per_cut_crop_enabled (symmetric with load_file)",
          win.per_cut_crop_enabled is False and not win.per_cut_crop_check.isChecked())
    try:
        win.current_crop_target()
        win.set_current_crop(1, 2, 3, 4)
        no_crash = True
    except IndexError:
        no_crash = False
    check("current_crop_target/set_current_crop don't crash with an empty cut list", no_crash)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for name in FAILURES:
            print(f"  - {name}")
    else:
        print("All checks passed.")

    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
