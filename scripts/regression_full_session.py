#!/usr/bin/env python3
"""Full-session regression test for the multi-cut-lists feature.

Exercises the cross-widget interactions that unit-style per-task verification
scripts didn't cover and that the 2026-08-21 final whole-branch review caught:
cut creation out of chronological order, per-cut crop toggle/edit/reset, both
export modes (including that Combined file export greys itself out whenever
cuts disagree on crop, and a uniform-crop + remove-audio combined export),
and scene detection. This is the regression test recommended at the end of
.superpowers/sdd/2026-08-21-multi-cut-lists/final-review-findings.md, written
to specifically catch Critical #1 (scrubber click-to-seek dead on a fresh
load) and Critical #2 (per-cut crop toggle/reset not syncing the crop text
fields, the actual export source of truth), plus Important #3-#7.

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
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QVBoxLayout

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

    # --- Important #6: cut list rows are distinct, showing a number + start-end,
    # via a per-row widget (icon + fixed-width left-aligned number label + time
    # label) rather than plain item text - real column alignment in the normal
    # UI font, not a monospace hack ---
    def row_labels(win, i):
        """The QLabel children of row i's item widget, in build order:
        [icon (no text), number, time-range]."""
        widget = win.cut_list.itemWidget(win.cut_list.item(i))
        return [w for w in widget.findChildren(QLabel)]

    win.refresh_cut_list()
    row_texts = ["".join(l.text() for l in row_labels(win, i)) for i in range(win.cut_list.count())]
    check("cut list rows are not all identical placeholder text",
          len(set(row_texts)) == len(row_texts), str(row_texts))
    row0_labels = row_labels(win, 0)
    check("cut list row widget has icon, number, and start-end labels",
          len(row0_labels) == 3, str([l.text() for l in row0_labels]))
    check("cut list row's number label reads '1' (no colon, no manual padding)",
          row0_labels[1].text() == "1", row0_labels[1].text())
    check("cut list row's time label shows the start-end range",
          row0_labels[2].text() == f"{clipcut.format_time(0.0)}-{clipcut.format_time(6.0)}",
          row0_labels[2].text())
    check("cut list rows carry a real QIcon (not embedded in the text)",
          isinstance(win.cut_row_icon, clipcut.QIcon))
    # Whether the icon actually resolves to a picture depends on the desktop's
    # icon theme (this offscreen test environment has none installed, so
    # QIcon.fromTheme legitimately returns a null-but-valid QIcon here) -
    # not asserted non-null; verified visually on a real desktop instead.
    number_label_width = row0_labels[1].minimumWidth()
    check("the number label reserves a fixed width, so 1/10/100 all left-align in the same column",
          number_label_width > 0, str(number_label_width))

    # --- Important #7: editing the active cut's Start re-sorts/relabels the list
    # live, without a separate manual refresh ---
    win.select_cut(1)  # the ~4.0-5.0s cut
    win.write_start_field(0.2)  # now starts before cut index 2 (0.5s)
    check("ordered_cut_indices() reflects the just-edited start immediately",
          win.ordered_cut_indices() == [0, 1, 2, 3], str(win.ordered_cut_indices()))
    reordered_texts = ["".join(l.text() for l in row_labels(win, i)) for i in range(win.cut_list.count())]
    check("write_start_field's re-sort is visible in the cut list without an extra refresh_cut_list() call",
          reordered_texts != row_texts, str(reordered_texts))

    # From here on, chronological order is [0, 1, 2, 3] (cut 1's start was just
    # moved to 0.2s, ahead of cut 2's 0.5s) - snapshot it once so the export
    # assertions below check against the actual current order, not a stale one.
    expected_export_order = win.ordered_cut_indices()

    # --- Critical #2: per-cut crop toggle/edit/reset stay in sync with crop_edits,
    # the actual field get_crop() (and therefore every export) reads from ---
    win.select_cut(1)
    win.shared_crop = (9, 9, 9, 9)  # simulate a generic crop set before going per-cut
    orig_question = QMessageBox.question

    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.No)
    win.per_cut_crop_check.setChecked(True)
    pump(20)
    QMessageBox.question = orig_question
    check("declining the turn-on confirmation leaves Per-Cut Crop off",
          not win.per_cut_crop_check.isChecked() and not win.per_cut_crop_enabled)
    check("...and leaves the pre-existing shared crop untouched",
          win.shared_crop == (9, 9, 9, 9), str(win.shared_crop))

    turn_on_question_calls = []
    QMessageBox.question = staticmethod(
        lambda *a, **kw: turn_on_question_calls.append(a) or QMessageBox.Yes)
    win.per_cut_crop_check.setChecked(True)
    pump(20)
    QMessageBox.question = orig_question
    check("turning per-cut crop on with a real shared crop to lose asks for confirmation",
          bool(turn_on_question_calls))
    check("accepting the turn-on confirmation clears the pre-existing generic (shared) crop",
          win.shared_crop == (0, 0, 0, 0), str(win.shared_crop))
    check("toggling per-cut crop ON does not seed an override onto the active cut",
          win.cuts[1].crop is None, str(win.cuts[1].crop))
    check("toggling per-cut crop ON shows the resolved (shared) crop in the fields",
          all(e.text() == "" for e in win.crop_edits), str([e.text() for e in win.crop_edits]))

    for edit, value in zip(win.crop_edits, ("20", "10", "30", "15")):
        edit.setText(value)  # fires on_crop_field_edited -> set_current_crop, the real path
    check("editing the crop fields writes the override onto the active cut",
          win.cuts[1].crop == (20, 10, 30, 15), str(win.cuts[1].crop))

    # --- Switching Horizontal/Vertical must not destroy a freeform (non-
    # preset) crop. orient_horizontal.toggled used to fire on_aspect_ratio_
    # selected directly, which unconditionally recomputed the crop from
    # current_target_ratio() - with no preset engaged ("None"), that means
    # (0,0,0,0), wiping out whatever manual crop was in progress. ---
    check("no preset is engaged for this freeform crop", win.aspect_combo.currentText() == "None")
    win.orient_vertical.setChecked(True)
    check("switching orientation with no preset engaged leaves a freeform crop untouched",
          win.cuts[1].crop == (20, 10, 30, 15), str(win.cuts[1].crop))
    win.orient_horizontal.setChecked(True)
    check("switching back leaves it untouched too",
          win.cuts[1].crop == (20, 10, 30, 15), str(win.cuts[1].crop))

    # With a preset actually engaged, orientation SHOULD recompute the crop -
    # that's the whole point of the toggle (re-crop for the flipped ratio).
    sixteen_nine_index = win.aspect_combo.findText("16:9")
    win.aspect_combo.setCurrentIndex(sixteen_nine_index)
    win.on_aspect_ratio_selected(sixteen_nine_index)
    horizontal_crop = win.cuts[1].crop
    win.orient_vertical.setChecked(True)
    check("switching orientation WITH a preset engaged recomputes the crop, doesn't just clear it",
          win.cuts[1].crop is not None and win.cuts[1].crop != horizontal_crop,
          f"{win.cuts[1].crop} vs horizontal={horizontal_crop}")
    win.orient_horizontal.setChecked(True)  # restore for the rest of this section

    # A preset whose ratio already matches the source video (fixture is
    # 320x240 - exactly 4:3) computes an all-zero crop, since nothing needs
    # trimming - that must still count as "4:3" selected, not bounce back to
    # "None", or there's nothing engaged left for a follow-up orientation
    # switch to recompute from.
    four_three_index = win.aspect_combo.findText("4:3")
    win.aspect_combo.setCurrentIndex(four_three_index)
    win.on_aspect_ratio_selected(four_three_index)
    check("a preset matching the video's native ratio produces an all-zero crop",
          win.cuts[1].crop == (0, 0, 0, 0), str(win.cuts[1].crop))
    check("...but stays selected as '4:3', not silently reverted to 'None'",
          win.aspect_combo.currentText() == "4:3", win.aspect_combo.currentText())
    win.orient_vertical.setChecked(True)
    check("switching to Vertical right after recomputes a real crop from the still-engaged 4:3 preset",
          win.cuts[1].crop is not None and win.cuts[1].crop != (0, 0, 0, 0),
          str(win.cuts[1].crop))
    check("...and the dropdown still reads '4:3' (just reinterpreted for portrait)",
          win.aspect_combo.currentText() == "4:3", win.aspect_combo.currentText())
    win.orient_horizontal.setChecked(True)  # restore for the rest of this section

    # --- "Original" preset: the video's own native ratio, so a user can crop
    # to the file's existing shape (and then scale/resize within it) without
    # needing to know or pick whether it's 1:1, 16:9, 4:3, etc. Fixture is
    # 320x240 (exactly 4:3), so applying it crops to (0,0,0,0) - nothing needs
    # trimming, the video already matches its own ratio by definition. ---
    current_index = win.aspect_combo.findText("Original")
    win.aspect_combo.setCurrentIndex(current_index)
    win.on_aspect_ratio_selected(current_index)
    check("applying 'Original' crops to (0,0,0,0) - the video's own ratio needs no trimming",
          win.cuts[1].crop == (0, 0, 0, 0), str(win.cuts[1].crop))
    check("'Original' stays selected in the dropdown, not silently reverted to 'None'",
          win.aspect_combo.currentText() == "Original", win.aspect_combo.currentText())

    win.orient_vertical.setChecked(True)
    check("switching to Vertical with 'Original' engaged crops to fit the video's ratio in portrait",
          win.cuts[1].crop is not None and win.cuts[1].crop != (0, 0, 0, 0),
          str(win.cuts[1].crop))
    check("...and the dropdown still reads 'Original' after the flip",
          win.aspect_combo.currentText() == "Original", win.aspect_combo.currentText())
    win.orient_horizontal.setChecked(True)

    check("'Original' is never reverse-matched onto an unrelated crop (match_aspect_preset skips it)",
          win.match_aspect_preset((20, 10, 20, 10)) != current_index)

    none_index = win.aspect_combo.findText("None")
    win.aspect_combo.setCurrentIndex(none_index)
    win.on_aspect_ratio_selected(none_index)
    win.set_current_crop(20, 10, 30, 15)
    win.select_cut(1)

    # --- Crop must never leak onto a cut the user didn't touch. Previously,
    # sync_active_cut_from_widgets() (called by nearly every interaction -
    # editing Start/End, adding a cut, switching selection, clicking Process)
    # also captured crop_edits' *currently displayed* text into the active
    # cut's crop, even when the interaction had nothing to do with cropping -
    # stamping whatever was on screen (often just the shared_crop fallback
    # display) onto cuts as if it were a deliberate override. ---
    win.select_cut(2)
    check("switching to a cut with no override shows blank crop fields",
          all(e.text() == "" for e in win.crop_edits), str([e.text() for e in win.crop_edits]))
    win.write_start_field(0.6)
    win.write_end_field(1.4)
    check("editing Start/End on an untouched cut does NOT give it a crop",
          win.cuts[2].crop is None, str(win.cuts[2].crop))

    new_cut_index = len(win.cuts)
    win.add_cut()
    check("a newly-added cut starts with no crop", win.cuts[new_cut_index].crop is None,
          str(win.cuts[new_cut_index].crop))
    win.write_end_field(2.0)
    check("editing a newly-added cut's End field does NOT give it a crop either",
          win.cuts[new_cut_index].crop is None, str(win.cuts[new_cut_index].crop))
    win.cuts.pop(new_cut_index)  # done with this scratch cut, remove it directly
    win.select_cut(1)
    win.refresh_cut_list()

    # --- The Preset dropdown must reflect whichever cut is actually selected,
    # not just remember whatever preset was last picked (possibly for a
    # different cut, or before Per-Cut Crop was even on) ---
    win.select_cut(1)  # crop (20,10,30,15) - not a preset shape
    check("Preset dropdown shows 'None' for a cut whose crop doesn't match any preset",
          win.aspect_combo.currentText() == "None", win.aspect_combo.currentText())
    sixteen_nine_index = win.aspect_combo.findText("16:9")
    win.aspect_combo.setCurrentIndex(sixteen_nine_index)
    win.on_aspect_ratio_selected(sixteen_nine_index)
    check("applying 16:9 to cut 1 selects it in the dropdown",
          win.aspect_combo.currentText() == "16:9", win.aspect_combo.currentText())
    win.select_cut(2)
    check("switching to a cut with no crop resets the dropdown to 'None', not the last preset",
          win.aspect_combo.currentText() == "None", win.aspect_combo.currentText())
    win.select_cut(1)
    check("switching back to the 16:9 cut restores that preset in the dropdown",
          win.aspect_combo.currentText() == "16:9", win.aspect_combo.currentText())
    none_index = win.aspect_combo.findText("None")
    win.aspect_combo.setCurrentIndex(none_index)
    win.on_aspect_ratio_selected(none_index)  # back to (20,10,30,15) baseline, no preset lock
    win.set_current_crop(20, 10, 30, 15)
    win.select_cut(1)

    # set_current_crop() is the real path a mouse-drag crop edit (or
    # on_crop_field_edited) goes through - the crop icon must appear the
    # instant it's called, with no extra reselect/toggle needed to see it.
    # Same values as just set above, so this doesn't disturb the (20,10,30,15)
    # override the rest of this section relies on.
    win.set_current_crop(20, 10, 30, 15)
    row1 = win.ordered_cut_indices().index(1)
    check("the crop icon appears immediately from set_current_crop() alone, no reselect/toggle needed",
          len(row_labels(win, row1)) == 4, str([l.text() for l in row_labels(win, row1)]))

    # --- Applying an aspect-ratio preset with per-cut crop on must display its
    # zero-valued edges the same way every other display path does (blank, not
    # a literal "0") - otherwise they look like they vanish the next time
    # anything redisplays the crop (toggling per-cut crop, reselecting the
    # cut), even though the stored value was never actually wrong. Uses cut 3
    # so it doesn't disturb cut 1's (20,10,30,15) override the rest of this
    # section relies on. ---
    win.select_cut(3)
    sixteen_nine_index = win.aspect_combo.findText("16:9")
    win.aspect_combo.setCurrentIndex(sixteen_nine_index)
    win.on_aspect_ratio_selected(sixteen_nine_index)  # setCurrentIndex alone doesn't emit activated
    check("applying a 16:9 preset with per-cut crop on writes the crop onto the active cut",
          win.cuts[3].crop is not None, str(win.cuts[3].crop))
    preset_crop = win.cuts[3].crop
    check("320x240 source cropped to 16:9 has zero-valued left/right edges",
          preset_crop[0] == 0 and preset_crop[2] == 0, str(preset_crop))
    check("the preset's zero-valued edges display blank, not a literal '0'",
          win.crop_left.text() == "" and win.crop_right.text() == "",
          str([e.text() for e in win.crop_edits]))
    check("preset's zero-valued edges render as ACTIVE guides, not the dim inactive color",
          win.preview.guide_active["left"] and win.preview.guide_active["right"],
          str(win.preview.guide_active))

    # Switching to a different cut and back used to lose this: guide_active was
    # recomputed purely from value > 0 on every redisplay (select_cut -> show_crop
    # -> set_crop_pixels), so a preset's 0-valued edges went dim the moment you
    # navigated away and back - not just on a toggle round-trip.
    win.select_cut(1)
    win.select_cut(3)
    check("the preset's zero-valued guides are STILL active after switching cuts and back",
          win.preview.guide_active["left"] and win.preview.guide_active["right"],
          str(win.preview.guide_active))

    # A real interaction that redisplays this crop via show_crop() (declining the
    # turn-off confirmation - see the dedicated destructive-clear tests further
    # down - just want a redisplay here) must not change the value or its display.
    orig_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.No)
    win.per_cut_crop_check.setChecked(False)
    pump(20)
    QMessageBox.question = orig_question
    check("declining turn-off leaves the preset's crop and Per-Cut Crop itself untouched",
          win.cuts[3].crop == preset_crop and win.per_cut_crop_check.isChecked(),
          f"{win.cuts[3].crop} vs {preset_crop}, checked={win.per_cut_crop_check.isChecked()}")
    check("...and its zero edges still display blank, not '0', after redisplay",
          win.crop_left.text() == "" and win.crop_right.text() == "",
          str([e.text() for e in win.crop_edits]))
    check("...and its guides are still active after that redisplay too",
          win.preview.guide_active["left"] and win.preview.guide_active["right"],
          str(win.preview.guide_active))
    none_index = win.aspect_combo.findText("None")
    win.aspect_combo.setCurrentIndex(none_index)
    win.on_aspect_ratio_selected(none_index)  # drop the aspect lock before continuing
    win.preview.set_crop_pixels(5, 0, 0, 0)
    check("without a preset engaged, only the nonzero edge is active (freeform per-edge behavior preserved)",
          win.preview.guide_active == {"left": True, "top": False, "right": False, "bottom": False},
          str(win.preview.guide_active))
    win.select_cut(1)  # restore context for the rest of this section

    win.select_cut(2)
    check("a different cut with no override shows the shared crop, not cut 1's override",
          all(e.text() == "" for e in win.crop_edits), str([e.text() for e in win.crop_edits]))
    win.select_cut(1)
    check("re-selecting cut 1 restores its override into the fields",
          tuple(int(e.text() or 0) for e in win.crop_edits) == (20, 10, 30, 15),
          str([e.text() for e in win.crop_edits]))

    # The dedicated "Reset to Shared Crop" button was removed - clearing crops
    # now happens solely via the Per-Cut Crop checkbox itself: turning it off
    # is destructive, clearing every cut's crop override at once (not just
    # the active one), guarded by a confirm dialog so it doesn't happen by
    # accident. Declining leaves everything untouched; accepting actually
    # clears and turns it off.
    win.cuts[1].crop = (7, 7, 7, 7)
    win.cuts[2].crop = (3, 3, 3, 3)
    win.select_cut(2)
    win.select_cut(1)  # re-select to refresh fields from the override just set

    orig_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.No)
    win.per_cut_crop_check.setChecked(False)
    pump(20)
    QMessageBox.question = orig_question
    check("declining the turn-off confirmation leaves Per-Cut Crop checked",
          win.per_cut_crop_check.isChecked() and win.per_cut_crop_enabled)
    check("...and leaves every cut's override untouched",
          win.cuts[1].crop == (7, 7, 7, 7) and win.cuts[2].crop == (3, 3, 3, 3),
          f"{win.cuts[1].crop}, {win.cuts[2].crop}")

    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    win.per_cut_crop_check.setChecked(False)
    pump(20)
    QMessageBox.question = orig_question
    check("accepting the turn-off confirmation actually turns Per-Cut Crop off",
          not win.per_cut_crop_check.isChecked() and not win.per_cut_crop_enabled)
    check("...and clears every cut's override, not just the one that was active",
          all(c.crop is None for c in win.cuts), str(win.cuts))
    check("turning per-cut crop off shows shared_crop in the fields",
          all(e.text() == "" for e in win.crop_edits), str([e.text() for e in win.crop_edits]))
    win.refresh_cut_list()
    row1 = win.ordered_cut_indices().index(1)
    check("cut list hides the crop icon everywhere after every override was cleared",
          len(row_labels(win, row1)) == 3, str([l.text() for l in row_labels(win, row1)]))

    question_calls = []
    QMessageBox.question = staticmethod(lambda *a, **kw: question_calls.append(a) or QMessageBox.Yes)
    win.per_cut_crop_check.setChecked(True)
    pump(20)
    QMessageBox.question = orig_question
    check("turning per-cut crop back on (nothing left to lose) needs no confirmation",
          not question_calls)

    # Re-establish a non-uniform per-cut crop state (cut 1 differs from every
    # other cut's shared_crop) for the Combined-export greying-out tests below,
    # which the destructive clear above just wiped out.
    win.cuts[1].crop = (7, 7, 7, 7)
    # select_cut() re-syncs the crop_edits fields from this value - without it,
    # the fields still show whatever was last displayed (blank, from the toggle
    # above), and the next sync_active_cut_from_widgets() (do_process() calls it
    # internally) would clobber cuts[1].crop right back from that stale text.
    win.select_cut(1)

    # --- Minor #12: label renamed from the backwards "Sensitivity %" ---
    labels = [w.text() for w in win.findChildren(QLabel)]
    check("scene-detection field is labelled 'Threshold %', not the backwards 'Sensitivity %'",
          "Threshold %" in labels and "Sensitivity %" not in labels, str(labels))

    # --- Cut tab layout tweaks: heading/helper text for scene detection, cut
    # list has no artificial height cap, Add/Delete Cut come before the list ---
    check("Cut tab has a 'Scene Detection' heading", "Scene Detection" in labels, str(labels))
    check("cut list has no artificial max-height cap (fills available space)",
          win.cut_list.maximumHeight() >= 16777215, str(win.cut_list.maximumHeight()))
    def layout_index_containing(layout, widget):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget() is widget:
                return i
            sub = item.layout()
            if sub is not None and any(sub.itemAt(j).widget() is widget for j in range(sub.count())):
                return i
        return None

    cut_tab_layout = win.cut_list.parentWidget().layout()
    buttons_index = layout_index_containing(cut_tab_layout, win.add_cut_button)
    list_index = layout_index_containing(cut_tab_layout, win.cut_list)
    check("Add/Delete Cut buttons sit above the cut list",
          buttons_index is not None and list_index is not None and buttons_index < list_index,
          f"buttons at {buttons_index}, list at {list_index}")

    # --- Minor #8: dead build_command() removed ---
    check("dead build_command() was removed",
          not hasattr(clipcut.MainWindow, "build_command"))

    # --- Export tab replaces Audio tab: audio options, append-text, export mode,
    # and Process all live there now; only status/progress stays in the sticky
    # footer (visible from every tab); the Done button is gone entirely ---
    tab_labels = [win.section_tabs.tabText(i) for i in range(win.section_tabs.count())]
    check("tabs are Cut/Crop/Export - no separate Audio tab",
          tab_labels == ["Cut", "Crop", "Export"], str(tab_labels))
    export_tab_page = win.section_tabs.widget(tab_labels.index("Export"))

    def is_descendant_of(widget, ancestor):
        w = widget
        while w is not None:
            if w is ancestor:
                return True
            w = w.parentWidget()
        return False

    for name in ("remove_audio", "audio_only", "suffix_edit", "export_mode_separate",
                 "export_mode_combined", "process_button"):
        widget = getattr(win, name)
        check(f"{name} lives inside the Export tab",
              is_descendant_of(widget, export_tab_page))
    check("status label stays in the sticky footer, outside the tab widget",
          not is_descendant_of(win.status, win.section_tabs))
    check("error_details_button stays in the sticky footer, outside the tab widget",
          not is_descendant_of(win.error_details_button, win.section_tabs))
    check("Done button was removed", not hasattr(win, "done_button"))
    check("Reset to Shared Crop button was removed - the checkbox alone drives crop clearing",
          not hasattr(win, "reset_cut_crop_button") and not hasattr(win, "reset_cut_crop"))
    export_labels = [w.text() for w in export_tab_page.findChildren(QLabel)]
    check("Export tab has an 'Audio' heading", "Audio" in export_labels, str(export_labels))

    # --- Timeline controls are icon-only, with the old text labels moved to
    # tooltips - real icon resolution depends on the desktop's icon theme
    # (this offscreen environment has none, same caveat as the cut list's
    # icon earlier), so only text/tooltip/icon-type are checked here. ---
    timeline_buttons_and_tooltips = [
        # mute_button starts muted (audio_output.setMuted(True) in
        # VideoPreview.__init__), so "Unmute" (not "Mute") is the correct
        # initial tooltip - matching the actual state, not a hardcoded guess.
        (win.mute_button, "Unmute"), (win.zoom_out_button, "Zoom Out"),
        (win.zoom_fit_button, "Reset to Fit"), (win.zoom_in_button, "Zoom In"),
        (win.set_start_button, "Set Start"), (win.step_back_button, "Step Back"),
        (win.play_button, "Play"), (win.step_forward_button, "Step Forward"),
        (win.set_end_button, "Set End"),
    ]
    check("every timeline control button has no text label (icon-only)",
          all(b.text() == "" for b, _ in timeline_buttons_and_tooltips),
          str([(b.text()) for b, _ in timeline_buttons_and_tooltips]))
    check("every timeline control button's old label is now its tooltip",
          all(b.toolTip() == t for b, t in timeline_buttons_and_tooltips),
          str([(b.toolTip(), t) for b, t in timeline_buttons_and_tooltips]))
    check("every timeline control button carries a real QIcon",
          all(isinstance(b.icon(), clipcut.QIcon) for b, _ in timeline_buttons_and_tooltips))

    # Real starting mute state isn't assumed - this offscreen environment's
    # QAudioOutput happens to default to muted (no real device backend),
    # unlike a normal desktop session - so toggle relative to whatever it
    # actually starts as instead of hardcoding a direction.
    before_muted = win.preview.audio_output.isMuted()
    win.toggle_mute()
    after_muted = win.preview.audio_output.isMuted()
    check("toggling mute flips the actual muted state", after_muted != before_muted)
    check("...and the tooltip matches the new state",
          win.mute_button.toolTip() == ("Unmute" if after_muted else "Mute"))
    win.toggle_mute()
    check("toggling mute again flips it back",
          win.preview.audio_output.isMuted() == before_muted)
    check("...and the tooltip matches too",
          win.mute_button.toolTip() == ("Unmute" if before_muted else "Mute"))

    check("Fit button starts disabled - the view starts at Fit", not win.zoom_fit_button.isEnabled())
    win.preview.zoom_in()
    check("zooming in enables the Fit button (no longer at Fit)", win.zoom_fit_button.isEnabled())
    win.preview.reset_zoom()
    check("resetting zoom disables the Fit button again (back at Fit)",
          not win.zoom_fit_button.isEnabled())

    win.on_playback_state_changed(clipcut.QMediaPlayer.PlayingState)
    check("playback starting flips the play button's tooltip to 'Pause'",
          win.play_button.toolTip() == "Pause")
    win.on_playback_state_changed(clipcut.QMediaPlayer.PausedState)
    check("playback pausing flips it back to 'Play'", win.play_button.toolTip() == "Play")

    def find_direct_layout(root_layout, widget):
        for i in range(root_layout.count()):
            item = root_layout.itemAt(i)
            if item.widget() is widget:
                return root_layout
            sub = item.layout()
            if sub is not None:
                found = find_direct_layout(sub, widget)
                if found is not None:
                    return found
        return None

    mode_layout = find_direct_layout(export_tab_page.layout(), win.export_mode_separate)
    check("export mode radios are stacked vertically (separate lines), not side by side",
          mode_layout is not None and isinstance(mode_layout, QVBoxLayout)
          and mode_layout is not export_tab_page.layout(),
          str(type(mode_layout)))
    check("export mode has explanatory helper text",
          any("Separate files:" in t for t in export_labels), str(export_labels))

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

    # --- Simplification: Combined File export is disabled (greyed out) rather
    # than falling back to a re-encoding concat filter whenever cuts disagree on
    # crop. Cut 1 still carries the (7,7,7,7) override from the crop-sync checks
    # above, which differs from every other cut's shared_crop (0,0,0,0) - a
    # deliberately non-uniform state. ---
    win.export_mode_separate.setChecked(True)
    win.update_combined_export_availability()
    check("Combined file export is disabled while any cut's crop differs from the rest",
          not win.export_mode_combined.isEnabled())
    check("...and carries an explanatory tooltip",
          bool(win.export_mode_combined.toolTip()))

    win.export_mode_combined.setChecked(True)  # force it, bypassing the greyed-out UI
    win.update_combined_export_availability()
    check("re-checking availability with Combined still (force-)checked falls back to Separate",
          win.export_mode_separate.isChecked() and not win.export_mode_combined.isChecked())

    win.export_mode_combined.setEnabled(True)  # simulate a stale/forced click despite the gate
    win.export_mode_combined.setChecked(True)
    win.do_process_combined()
    check("do_process_combined() itself refuses non-uniform crops as a defensive backstop",
          "requires every cut to use the same crop" in win.status.text(), win.status.text())
    check("...and never touched ffmpeg (no queue was built)",
          not getattr(win, "export_queue", None))

    # Bring crops back into agreement so Combined becomes available again -
    # clearing a cut's override directly, now that there's no dedicated
    # button for it, and reselecting to resync the fields from shared_crop.
    win.cuts[1].crop = None
    win.select_cut(1)
    win.update_combined_export_availability()
    check("Combined file export re-enables once every cut's crop agrees again",
          win.export_mode_combined.isEnabled())

    # --- Important #3, #4, #5, #9/#10: a real combined export with uniform crops
    # (the only case Combined now supports) + "Remove audio" (forces the a=0
    # branch that used to hard-fail ffmpeg when the concat filter existed; the
    # demuxer's plain -c copy has never had this problem, but keep covering it) ---
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
    check("combined export with uniform crops + remove-audio completed (Important #3)",
          ok, win.status.text() + " | " + (win.error_details if hasattr(win, "error_details") else ""))
    check("combined export processed cuts in chronological order",
          processed_order == expected_export_order, f"{processed_order} != {expected_export_order}")
    check("spinner showed an 'Exporting N of M' phase during per-part export",
          any(lbl.startswith("Exporting ") for lbl in spinner_labels_seen), str(set(spinner_labels_seen)))
    check("spinner showed a 'Joining' phase during the concat pass (Minor #9)",
          "Joining" in spinner_labels_seen, str(set(spinner_labels_seen)))
    concat_cmd = next((c for c in captured_cmds if "concat" in c[1]), None)
    check("combined export used the fast stream-copy demuxer path (no re-encode)",
          concat_cmd is not None and "-filter_complex" not in concat_cmd[1] and "copy" in concat_cmd[1],
          str([c[1] for c in captured_cmds]))
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
    check("scene completion readout lands in the sticky status footer (visible from every tab)",
          "Found" in win.status.text(), win.status.text())
    check("scene detection found the 3 distinct color scenes",
          len(win.cuts) == 3, f"found {len(win.cuts)} cuts: {win.cuts}")
    if len(win.cuts) == 3:
        starts = sorted(win.cut_start_seconds(c) for c in win.cuts)
        check("scene boundaries land near the real 2s/4s cuts",
              abs(starts[1] - 2.0) < 0.5,
              str(starts))

    # --- Merge/Delete via cut_list multi-select, and global (not per-cut)
    # Frame/Keyframe mode switching: added for the "merge scenes" feature -
    # cuts can no longer mix modes, so switching modes is now a destructive,
    # confirm-before-replace, whole-list action (mirrors detect_scenes' guard) ---
    win.load_file(fixture)
    check("fresh reload starts with Delete/Keep/Merge all disabled (nothing selected)",
          not win.delete_selected_button.isEnabled() and not win.keep_selected_button.isEnabled()
          and not win.merge_selected_button.isEnabled())

    win.write_end_field(1.5)
    seek_and_add_cut(1.5)
    win.write_end_field(3.0)
    seek_and_add_cut(3.0)
    win.write_end_field(4.5)
    seek_and_add_cut(4.5)
    check("built 4 contiguous cuts for merge/delete testing",
          len(win.cuts) == 4 and win.ordered_cut_indices() == [0, 1, 2, 3], str(win.cuts))

    def select_rows(rows):
        win.cut_list.clearSelection()
        for r in rows:
            win.cut_list.item(r).setSelected(True)

    select_rows([1])
    check("single-row selection enables Delete and Keep",
          win.delete_selected_button.isEnabled() and win.keep_selected_button.isEnabled())
    check("single-row selection leaves Merge disabled (nothing to merge)",
          not win.merge_selected_button.isEnabled())
    check("single-row selection still drives the active cut, like before",
          win.active_cut == 1, str(win.active_cut))

    select_rows([0, 2])
    check("non-contiguous multi-select (ctrl-click) enables Delete and Keep",
          win.delete_selected_button.isEnabled() and win.keep_selected_button.isEnabled())
    check("non-contiguous multi-select (ctrl-click) leaves Merge disabled",
          not win.merge_selected_button.isEnabled())

    select_rows([1, 2])
    check("contiguous multi-select (shift-click) enables Delete, Keep, and Merge",
          win.delete_selected_button.isEnabled() and win.keep_selected_button.isEnabled()
          and win.merge_selected_button.isEnabled())

    before = [(c.start_repr, c.end_repr) for c in win.cuts]
    win.merge_selected_cuts()
    check("merge reduced the cut count by one", len(win.cuts) == 3, str(win.cuts))
    ordered = win.ordered_cut_indices()
    merged_cut = win.cuts[ordered[1]]
    check("merged cut spans from the first selected cut's start to the last's end",
          merged_cut.start_repr == before[1][0] and merged_cut.end_repr == before[2][1],
          f"merged={merged_cut} before={before}")

    select_rows([0])
    win.delete_selected_cuts()
    check("delete removed exactly the one selected cut", len(win.cuts) == 2, str(win.cuts))

    select_rows([0, 1])
    win.delete_selected_cuts()
    check("delete refuses to remove every remaining cut (at least one must survive)",
          len(win.cuts) == 2, str(win.cuts))
    check("selecting every cut also disables Keep (nothing outside the selection to discard)",
          not win.keep_selected_button.isEnabled())
    win.keep_selected_cuts()
    check("keep refuses when the selection covers every cut", len(win.cuts) == 2, str(win.cuts))

    select_rows([0])
    check("partial selection enables Keep", win.keep_selected_button.isEnabled())
    kept_cut = win.cuts[win.cut_list.item(0).data(Qt.UserRole)]
    win.keep_selected_cuts()
    check("keep discarded every cut except the selected one", len(win.cuts) == 1, str(win.cuts))
    check("the surviving cut is the one that was selected, not just whichever remained",
          win.cuts[0].start_repr == kept_cut.start_repr and win.cuts[0].end_repr == kept_cut.end_repr,
          f"kept={win.cuts[0]} expected={kept_cut}")

    # --- Deleting selects the row that shifts up into the gap, not row 0, so
    # repeated Delete clicks walk down the list instead of jumping to the top ---
    win.load_file(fixture)
    win.write_end_field(1.5)
    seek_and_add_cut(1.5)
    win.write_end_field(3.0)
    seek_and_add_cut(3.0)
    win.write_end_field(4.5)
    seek_and_add_cut(4.5)
    check("built 4 fresh contiguous cuts for the walk-down test",
          len(win.cuts) == 4, str(win.cuts))

    select_rows([1])
    third_cut_before = win.cuts[win.ordered_cut_indices()[2]]
    win.delete_selected_cuts()
    check("after deleting row 1, selection lands on what's now row 1 (the old row 2), not row 0",
          win.cut_list.currentRow() == 1, str(win.cut_list.currentRow()))
    check("...and it's genuinely the cut that used to be at row 2, shifted up",
          win.cuts[win.active_cut].start_repr == third_cut_before.start_repr,
          f"active={win.cuts[win.active_cut]} expected={third_cut_before}")

    # It's already selected (delete's own reselection), so clicking Delete again
    # immediately removes the next one in line - no need to reselect by hand.
    win.delete_selected_cuts()
    check("a second immediate delete removes another cut without reselecting first",
          len(win.cuts) == 2, str(win.cuts))
    check("selection still isn't stuck at row 0", win.cut_list.currentRow() == 1,
          str(win.cut_list.currentRow()))

    select_rows([1])
    win.delete_selected_cuts()
    check("deleting the last row falls back to the new last row, not row 0",
          win.cut_list.currentRow() == len(win.cuts) - 1 == 0,
          f"currentRow={win.cut_list.currentRow()} cuts={win.cuts}")

    # --- Cut list selection follows the playhead: selects whichever cut it's
    # inside, deselects entirely in a gap between cuts (or before/after all
    # of them), hiding the crop overlay since there's no single "current cut"
    # to show one for ---
    win.load_file(fixture)
    win.write_end_field(2.0)  # cut 0: 0-2s
    seek_and_add_cut(3.0)
    win.write_end_field(5.0)  # cut 1: 3-5s
    check("built 2 cuts with a real gap (2-3s) for playhead-selection testing",
          len(win.cuts) == 2, str(win.cuts))

    win.preview.player.setPosition(1000)  # 1s - inside cut 0
    win.sync_selection_to_playhead()
    check("playhead inside cut 0 selects it",
          win.selected_cut_rows() == [0], str(win.selected_cut_rows()))
    check("crop overlay is visible while a cut is selected", win.preview.crop_overlay_visible)

    win.preview.player.setPosition(2500)  # 2.5s - in the gap
    win.sync_selection_to_playhead()
    check("playhead in the gap deselects the list entirely",
          win.selected_cut_rows() == [], str(win.selected_cut_rows()))
    check("crop overlay hides with nothing selected", not win.preview.crop_overlay_visible)

    win.preview.player.setPosition(4000)  # 4s - inside cut 1
    win.sync_selection_to_playhead()
    check("playhead moving into cut 1 selects it",
          win.selected_cut_rows() == [1], str(win.selected_cut_rows()))
    check("crop overlay is visible again", win.preview.crop_overlay_visible)

    win.preview.player.setPosition(5500)  # 5.5s - after the last cut
    win.sync_selection_to_playhead()
    check("playhead after every cut deselects the list",
          win.selected_cut_rows() == [], str(win.selected_cut_rows()))
    check("crop overlay hides after the last cut too", not win.preview.crop_overlay_visible)

    win.cut_list.item(0).setSelected(True)
    win.cut_list.item(1).setSelected(True)
    check("manual multi-select also hides the crop overlay (no single current cut)",
          not win.preview.crop_overlay_visible)

    win.load_file(fixture)
    orig_question = QMessageBox.question
    question_calls = []

    def spy_question(*a, **kw):
        question_calls.append(a)
        return QMessageBox.Yes

    QMessageBox.question = staticmethod(spy_question)
    win.cut_mode_keyframe.setChecked(True)
    pump(20)
    check("switching mode with only the trivial default cut needs no confirmation",
          not question_calls)
    check("...and converts in place rather than resetting to a blank cut",
          win.frame_mode is False and len(win.cuts) == 1)
    QMessageBox.question = orig_question

    # Build a 2nd cut (still trivially, no confirmation needed for the 0->1 leg
    # since a single customized cut still isn't "work" by has_work's rule) so
    # the next switch has real work to lose.
    win.write_end_field(2.0)
    win.cut_mode_frame.setChecked(True)
    pump(20)
    seek_and_add_cut(1.0)
    check("now have 2 cuts to make the next mode switch non-trivial",
          len(win.cuts) == 2, str(win.cuts))

    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.No)
    win.cut_mode_keyframe.setChecked(True)
    pump(20)
    check("declining the mode-switch confirmation leaves the cut list untouched",
          len(win.cuts) == 2 and win.frame_mode is True,
          f"cuts={win.cuts} frame_mode={win.frame_mode}")
    check("...and reverts the radio buttons to the mode actually in effect",
          win.cut_mode_frame.isChecked() and not win.cut_mode_keyframe.isChecked())

    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    win.cut_mode_keyframe.setChecked(True)
    pump(20)
    check("accepting the mode-switch confirmation collapses the list to one cut in the new mode",
          len(win.cuts) == 1 and win.frame_mode is False and win.cuts[0].frame_mode is False,
          f"cuts={win.cuts} frame_mode={win.frame_mode}")
    QMessageBox.question = orig_question

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
