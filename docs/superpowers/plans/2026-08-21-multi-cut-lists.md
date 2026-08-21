# Multi-Cut Lists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend ClipCut from "one trim range → one export" to a list of cuts on the same source video, each optionally with its own crop, exportable as separate files or one combined file, with an action to auto-populate the list from ffmpeg scene detection.

**Architecture:** A `Cut` dataclass holds one cut's start/end representation, mode, and optional crop override. `MainWindow.cuts: list[Cut]` replaces the single implicit cut; `MainWindow.active_cut: int` tracks which one the Start/End/Crop widgets currently display. A new `select_cut(index)` method is the single sync point — it pushes a `Cut`'s data into the existing widgets (reusing every existing field-sync method unchanged) and is called both by UI selection (list row / timeline segment click) and by the export loop (to "switch to" each cut before building its ffmpeg command). This keeps `get_trim_times()`/`get_crop()`/`build_command()` reading from live widget state exactly as they do today — multi-cut export just calls `select_cut(i)` before each one.

**Tech Stack:** PySide6 (QGraphicsView/QWidget), ffmpeg/ffprobe via QProcess, single-file app (`clipcut`, ~2400 lines). No automated test framework — every task verifies with a small `QT_QPA_PLATFORM=offscreen` Python script (instantiating `MainWindow` directly, as used throughout this project's development) plus a manual GUI check.

**Spec:** `docs/superpowers/specs/2026-08-21-multi-cut-design.md`

## Global Constraints

- A freshly-loaded file always has exactly one `Cut` and `per_cut_crop_enabled = False` — the N=1 case must look and behave identically to today's single-cut app. Never break this.
- No automated test suite exists; do not introduce a new test framework. Verify with offscreen scripts (`os.environ["QT_QPA_PLATFORM"] = "offscreen"`, load the real `clipcut` module via `importlib.machinery.SourceFileLoader`, drive `MainWindow` methods directly) matching the pattern used throughout this project's history — see any `/tmp/claude-*/scratchpad/test_*.py` reasoning in prior commits' context if available, or just write fresh ones per task.
- Keep the existing single-file layout (`clipcut`). Do not split into multiple modules.
- Follow existing UI conventions exactly: `make_label_row()` for label+combo rows, `make_note()` for helper text, `make_separator()` for group dividers, `LABEL_ROW_WIDTH` for alignment, the `setEnabled(True/False)` pairing in `load_file`/`remove_file` for every new interactive widget, and the `_syncing` guard pattern for any field that both reads and writes state.
- Every new destructive action (deleting the last-but-one cut is disallowed entirely; replacing the cut list via scene detection) must not silently discard user work — confirm first per the spec.

---

### Task 1: `Cut` data model and `select_cut()` sync

**Files:**
- Modify: `clipcut` (near the top, after `NumberLineEdit`/before `GuideItem`, add the `Cut` dataclass; `MainWindow.__init__` and `load_file`/`remove_file` for `self.cuts`/`self.active_cut`/`self.shared_crop`/`self.per_cut_crop_enabled`; new `select_cut` method near `on_cut_mode_changed`)

**Interfaces:**
- Produces:
  - `Cut` dataclass: `start_repr: int`, `end_repr: int | None`, `frame_mode: bool`, `crop: tuple[int,int,int,int] | None`
  - `MainWindow.cuts: list[Cut]`
  - `MainWindow.active_cut: int`
  - `MainWindow.shared_crop: tuple[int,int,int,int]` (default `(0,0,0,0)`)
  - `MainWindow.per_cut_crop_enabled: bool` (default `False`)
  - `MainWindow.select_cut(index: int) -> None` — pushes `cuts[index]` into the Start/End fields, the Frame/Keyframe radio, and (if `per_cut_crop_enabled`) the crop fields; sets `self.active_cut = index`. No-op crop push when `per_cut_crop_enabled` is `False` (crop widgets keep showing `shared_crop`, which nothing in this task changes yet).

- [ ] **Step 1: Add the `Cut` dataclass and import `dataclasses`**

Add near the top of `clipcut`, after the existing imports:

```python
from dataclasses import dataclass, field


@dataclass
class Cut:
    """One entry in the cut list. start_repr/end_repr are a keyframe index or a
    frame number depending on frame_mode, matching how the Start/End fields
    already represent time - see read_keyframe_fields_seconds/
    read_frame_fields_seconds. end_repr=None means "through the end of the
    file", the same blank-field convention the UI already uses."""
    start_repr: int = 0
    end_repr: int | None = None
    frame_mode: bool = True
    crop: tuple | None = None
```

- [ ] **Step 2: Write the verification script for the dataclass**

```bash
cat > /tmp/test_cut_dataclass.py << 'EOF'
import sys, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()

c = m.Cut()
assert c.start_repr == 0 and c.end_repr is None and c.frame_mode is True and c.crop is None
c2 = m.Cut(start_repr=5, end_repr=20, frame_mode=False, crop=(10, 10, 10, 10))
assert c2.start_repr == 5 and c2.crop == (10, 10, 10, 10)
print("OK")
EOF
python3 /tmp/test_cut_dataclass.py
```

Expected: `OK` printed, no traceback.

- [ ] **Step 3: Add `cuts`/`active_cut`/`shared_crop`/`per_cut_crop_enabled` state**

In `MainWindow.__init__`, near where `self.frame_mode = True` is set, add:

```python
        self.cuts = []
        self.active_cut = 0
        self.shared_crop = (0, 0, 0, 0)
        self.per_cut_crop_enabled = False
```

- [ ] **Step 4: Initialize `cuts` on file load, reset on file removal**

In `load_file`, right after `self.frame_mode` is established as `True` (the existing default) and before the field-clearing block, add:

```python
        self.cuts = [Cut(frame_mode=self.frame_mode)]
        self.active_cut = 0
        self.shared_crop = (0, 0, 0, 0)
```

In `remove_file`, alongside the other state resets, add:

```python
        self.cuts = []
        self.active_cut = 0
        self.shared_crop = (0, 0, 0, 0)
```

- [ ] **Step 5: Implement `select_cut`**

Add near `on_cut_mode_changed`:

```python
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
        if self.per_cut_crop_enabled:
            crop = cut.crop if cut.crop is not None else self.shared_crop
            for edit, value in zip(self.crop_edits, crop):
                edit.setText(str(value) if value else "")
            self.preview.set_crop_pixels(*crop)
        self._syncing = False
        self.update_crop_size_fields()
        self.update_suffix_placeholder()
```

- [ ] **Step 6: Verify `select_cut` with an offscreen script**

```bash
cat > /tmp/test_select_cut.py << 'EOF'
import sys, os, pathlib
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()
from PySide6.QtWidgets import QApplication
app = QApplication([])

win = m.MainWindow()
win.load_file(pathlib.Path("/tmp/claude-1000/-home-james-Projects-clipcut/bd5394b0-7eb7-4b69-939a-f1c83f3e2d8b/scratchpad/test.mp4"))
assert len(win.cuts) == 1
assert win.active_cut == 0

win.cuts.append(m.Cut(start_repr=10, end_repr=20, frame_mode=True))
win.select_cut(1)
assert win.active_cut == 1
assert win.start_frame_edit.text() == "10"
assert win.end_frame_edit.text() == "20"

win.select_cut(0)
assert win.active_cut == 0
assert win.start_frame_edit.text() == ""
print("OK")
EOF
python3 /tmp/test_select_cut.py
```

Expected: `OK` printed. (Reuse the existing `test.mp4` fixture already sitting in the session's scratchpad from prior manual testing; if it's gone, regenerate with `ffmpeg -y -f lavfi -i testsrc=size=640x360:rate=30:duration=5 -pix_fmt yuv420p /tmp/test.mp4` and point the script at that path instead.)

- [ ] **Step 7: Manual check**

Run `./clipcut` (or with a path arg), confirm nothing changed visually or behaviorally from before this task — loading a file, editing Start/End, switching Frame/Keyframe still work exactly as before. This task adds infrastructure with no new UI yet.

- [ ] **Step 8: Commit**

```bash
git add clipcut
git commit -m "Add Cut data model and select_cut() sync point for multi-cut lists"
```

---

### Task 2: Cut list UI

**Files:**
- Modify: `clipcut` (Cut tab construction in `MainWindow.__init__`; new `add_cut`/`delete_cut`/`on_cut_list_selected`/`refresh_cut_list` methods; field-edit handlers that currently write directly to "the" cut need to write into `cuts[active_cut]` after the fact)

**Interfaces:**
- Consumes: `Cut`, `MainWindow.cuts`, `MainWindow.active_cut`, `MainWindow.select_cut(index)` from Task 1
- Produces:
  - `MainWindow.cut_list: QListWidget`
  - `MainWindow.add_cut_button`, `MainWindow.delete_cut_button: QPushButton`
  - `MainWindow.refresh_cut_list() -> None` — rebuilds `cut_list`'s rows from `self.cuts`, sorted by resolved start time, selecting `active_cut`'s row
  - `MainWindow.sync_active_cut_from_widgets() -> None` — reads the current Start/End/mode widget state back into `cuts[active_cut]` (the inverse of `select_cut`)

- [ ] **Step 1: Add the list widget and buttons to the Cut tab**

In `MainWindow.__init__`, after `cut_mode_row` is built and before `cut_note`, add:

```python
        self.cut_list = QListWidget()
        self.cut_list.setMaximumHeight(100)
        self.cut_list.currentRowChanged.connect(self.on_cut_list_selected)

        cut_list_buttons = QHBoxLayout()
        self.add_cut_button = QPushButton("Add Cut")
        self.add_cut_button.clicked.connect(self.add_cut)
        self.delete_cut_button = QPushButton("Delete Cut")
        self.delete_cut_button.clicked.connect(self.delete_cut)
        cut_list_buttons.addWidget(self.add_cut_button)
        cut_list_buttons.addWidget(self.delete_cut_button)
```

Add `QListWidget` and `QListWidgetItem` to the `PySide6.QtWidgets` import block (alphabetical, next to `QLineEdit`/`QMainWindow`).

In the `cut_tab_layout` assembly (after `cut_note`, before `cut_mode_row`... actually after `cut_mode_row` and the Start/End widgets, matching "list below the fields" from the spec):

```python
        cut_tab_layout.addWidget(make_separator())
        cut_tab_layout.addWidget(self.cut_list)
        cut_tab_layout.addLayout(cut_list_buttons)
```

- [ ] **Step 2: Implement `refresh_cut_list`, `sync_active_cut_from_widgets`, `add_cut`, `delete_cut`, `on_cut_list_selected`**

```python
    def cut_start_seconds(self, cut):
        """Best-effort start time in seconds for sorting/display - reuses
        keyframe_time/fps math without touching the live widgets."""
        if cut.frame_mode:
            return cut.start_repr / (self.video_fps or 30.0)
        return self.keyframe_time(cut.start_repr)

    def refresh_cut_list(self):
        was_blocked = self.cut_list.blockSignals(True)
        self.cut_list.clear()
        ordered = sorted(range(len(self.cuts)), key=lambda i: self.cut_start_seconds(self.cuts[i]))
        for row, cut_index in enumerate(ordered):
            self.cut_list.addItem(f"{row + 1}: cut")
            self.cut_list.item(row).setData(Qt.UserRole, cut_index)
            if cut_index == self.active_cut:
                self.cut_list.setCurrentRow(row)
        self.cut_list.blockSignals(was_blocked)
        self.delete_cut_button.setEnabled(len(self.cuts) > 1)

    def sync_active_cut_from_widgets(self):
        if not self.cuts:
            return
        cut = self.cuts[self.active_cut]
        cut.frame_mode = self.frame_mode
        start_edit = self.start_frame_edit if self.frame_mode else self.start_keyframe_edit
        end_edit = self.end_frame_edit if self.frame_mode else self.end_keyframe_edit
        cut.start_repr = int(start_edit.text()) if start_edit.text() else 0
        cut.end_repr = int(end_edit.text()) if end_edit.text() else None
        if self.per_cut_crop_enabled:
            texts = [e.text() for e in self.crop_edits]
            if any(texts):
                cut.crop = tuple(int(t) if t else 0 for t in texts)

    def add_cut(self):
        if not self.path:
            return
        self.sync_active_cut_from_widgets()
        self.cuts.append(Cut(frame_mode=self.frame_mode))
        self.select_cut(len(self.cuts) - 1)
        self.refresh_cut_list()

    def delete_cut(self):
        if len(self.cuts) <= 1:
            return
        del self.cuts[self.active_cut]
        self.select_cut(max(0, self.active_cut - 1))
        self.refresh_cut_list()

    def on_cut_list_selected(self, row):
        if row < 0 or not self.cuts:
            return
        self.sync_active_cut_from_widgets()
        cut_index = self.cut_list.item(row).data(Qt.UserRole)
        self.select_cut(cut_index)
```

- [ ] **Step 3: Call `sync_active_cut_from_widgets` whenever the fields change, and `refresh_cut_list` after load**

Field edits currently call `write_start_field`/`write_end_field`/`on_cut_mode_changed` but never persist into `cuts[active_cut]`. Add a call to `self.sync_active_cut_from_widgets()` at the end of `write_start_field`, `write_end_field`, and `on_cut_mode_changed` (after the existing body, so persisting doesn't disturb any existing return value/behavior).

In `load_file`, after the `self.cuts = [Cut(...)]` line from Task 1, add:

```python
        self.refresh_cut_list()
```

In `remove_file`, after `self.cuts = []`, add:

```python
        self.cut_list.clear()
```

- [ ] **Step 4: Verify with an offscreen script**

```bash
cat > /tmp/test_cut_list.py << 'EOF'
import sys, os, pathlib
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()
from PySide6.QtWidgets import QApplication
app = QApplication([])

win = m.MainWindow()
win.load_file(pathlib.Path("/tmp/claude-1000/-home-james-Projects-clipcut/bd5394b0-7eb7-4b69-939a-f1c83f3e2d8b/scratchpad/test.mp4"))
assert win.cut_list.count() == 1
assert not win.delete_cut_button.isEnabled()

win.start_frame_edit.setText("5")
win.add_cut()
assert len(win.cuts) == 2
assert win.cuts[0].start_repr == 5, win.cuts[0]
assert win.cut_list.count() == 2
assert win.delete_cut_button.isEnabled()

win.start_frame_edit.setText("99")
win.on_cut_list_selected(0)  # back to cut 0 (sorted by start, cut 0 starts at 5s)
assert win.start_frame_edit.text() == "5"

win.delete_cut()
assert len(win.cuts) == 1
print("OK")
EOF
python3 /tmp/test_cut_list.py
```

Expected: `OK` printed.

- [ ] **Step 5: Manual check**

Load a video, click "Add Cut" a couple of times, set different Start/End on each, click between list rows and confirm the fields track correctly. Delete a cut and confirm the button disables at exactly one remaining cut.

- [ ] **Step 6: Commit**

```bash
git add clipcut
git commit -m "Add Cut list UI: add/delete/select rows synced to Start/End fields"
```

---

### Task 3: Timeline shows all cuts as segments

**Files:**
- Modify: `clipcut` (`Scrubber` class: replace `trim_start`/`trim_end`/`has_trim` with a list of segments; `MainWindow` wiring that currently calls `scrubber.set_trim`)

**Interfaces:**
- Consumes: `MainWindow.cuts`, `MainWindow.active_cut`, `MainWindow.cut_start_seconds` from Tasks 1-2
- Produces:
  - `Scrubber.set_cuts(segments: list[tuple[float, float, bool]]) -> None` — each tuple is `(start_seconds, end_seconds, is_active)`, replaces `set_trim`
  - `Scrubber.segment_clicked: Signal(int)` — emits the clicked segment's index (position in the `segments` list passed to `set_cuts`) when a segment (not a handle) is clicked
  - `Scrubber.segment_edited: Signal(int, float, float)` — emits `(index, new_start, new_end)` while dragging a segment's handle

- [ ] **Step 1: Replace `Scrubber`'s single-trim state with a segment list**

Replace the `self.trim_start = 0.0` / `self.trim_end = 0.0` / `self.has_trim = False` lines in `Scrubber.__init__` with:

```python
        self.segments = []  # list of (start, end, is_active)
        self._drag_segment = None  # index into self.segments currently being dragged
```

Add signals at class level, alongside `seek_requested`/`trim_changed`:

```python
    segment_clicked = Signal(int)
    segment_edited = Signal(int, float, float)
```

Replace `set_trim` with:

```python
    def set_cuts(self, segments):
        self.segments = segments
        self.update()
```

- [ ] **Step 2: Rewrite `paintEvent` to draw every segment**

Replace the trim-box-drawing block (the `if self.has_trim:` sections) with a loop over `self.segments`:

```python
        for start, end, is_active in self.segments:
            x1, x2 = self.x_for_time(start), self.x_for_time(end)
            fill = QColor("#3a5f8a") if is_active else QColor("#2f4a63")
            painter.fillRect(x1, 0, max(1, x2 - x1), self.height(), fill)
            box_color = QColor("#7fc7ff") if is_active else QColor(255, 255, 255, 90)
            painter.fillRect(x1, 0, 1, self.height(), box_color)
            painter.fillRect(x2, 0, 1, self.height(), box_color)
            painter.fillRect(x1, 0, max(1, x2 - x1), 1, box_color)
            painter.fillRect(x1, self.height() - 1, max(1, x2 - x1), 1, box_color)
```

Place this where the old trim-box block was (after the keyframe-tick loop, before the playhead line - keep the playhead and keyframe-tick code exactly as-is).

- [ ] **Step 3: Rewrite hit-testing and dragging for multiple segments**

Replace `_handle_at`:

```python
    def _handle_at(self, x):
        if self.duration <= 0:
            return None, None
        for i, (start, end, _active) in enumerate(self.segments):
            if abs(x - self.x_for_time(start)) <= self.HANDLE_GRAB:
                return i, "start"
            if abs(x - self.x_for_time(end)) <= self.HANDLE_GRAB:
                return i, "end"
        for i, (start, end, _active) in enumerate(self.segments):
            if self.x_for_time(start) <= x <= self.x_for_time(end):
                return i, "body"
        return None, "playhead"
```

Replace `mousePressEvent`:

```python
    def mousePressEvent(self, event):
        x = event.position().toPoint().x()
        index, kind = self._handle_at(x)
        if kind == "body":
            self.segment_clicked.emit(index)
            self._dragging = None
            return
        self._dragging = kind
        self._drag_segment = index
        self._drag_to(x)
```

Replace the body of `_drag_to`'s start/end branches (leave the `playhead` branch untouched):

```python
        if self._dragging == "start" and self._drag_segment is not None:
            start, end, active = self.segments[self._drag_segment]
            new_start = max(0.0, min(t, end - 0.1))
            self.segments[self._drag_segment] = (new_start, end, active)
            self.segment_edited.emit(self._drag_segment, new_start, end)
        elif self._dragging == "end" and self._drag_segment is not None:
            start, end, active = self.segments[self._drag_segment]
            new_end = min(self.duration, max(t, start + 0.1))
            self.segments[self._drag_segment] = (start, new_end, active)
            self.segment_edited.emit(self._drag_segment, start, new_end)
        elif self._dragging == "playhead":
            self.position = t
            self.seek_requested.emit(t)
```

- [ ] **Step 4: Wire `MainWindow` to feed `set_cuts` and handle the new signals**

Add a method that builds the segment list from `self.cuts` and pushes it to the scrubber; call it everywhere `scrubber.set_trim`/the old sync happened, and from `refresh_cut_list`:

```python
    def refresh_timeline_cuts(self):
        if not self.duration:
            self.scrubber.set_cuts([])
            return
        segments = []
        for i, cut in enumerate(self.cuts):
            start, end = self.trim_seconds_for_cut(cut)
            segments.append((start, end, i == self.active_cut))
        self.scrubber.set_cuts(segments)

    def trim_seconds_for_cut(self, cut):
        """Resolve a Cut's start/end to seconds without touching live widgets -
        the read-only counterpart to select_cut, used for timeline/export."""
        if cut.frame_mode:
            fps = self.video_fps or 30.0
            start = cut.start_repr / fps
            end = cut.end_repr / fps if cut.end_repr is not None else self.duration
        else:
            start = self.keyframe_time(cut.start_repr)
            end = self.keyframe_time(cut.end_repr) if cut.end_repr is not None else self.duration
        return start, end
```

Call `self.refresh_timeline_cuts()` at the end of `refresh_cut_list` (Task 2) and at the end of `select_cut` (Task 1).

Connect the new scrubber signals in `MainWindow.__init__` next to the existing `self.scrubber.trim_changed.connect(...)`:

```python
        self.scrubber.segment_clicked.connect(self.on_cut_list_selected)
        self.scrubber.segment_edited.connect(self.on_segment_edited)
```

Add the handler:

```python
    def on_segment_edited(self, index, start, end):
        if index != self.active_cut:
            self.sync_active_cut_from_widgets()
            self.select_cut(index)
        cut = self.cuts[index]
        if cut.frame_mode:
            fps = self.video_fps or 30.0
            self.write_start_field(start)
            self.write_end_field(end)
        else:
            self.start_keyframe_edit.setText(str(self.keyframe_index_of(start)))
            self.end_keyframe_edit.setText(str(self.keyframe_index_of(end)))
        self.refresh_timeline_cuts()
```

Remove the old `self.scrubber.trim_changed.connect(self.on_trim_changed)` connection and the now-dead `on_trim_changed` method (its job is fully replaced by `on_segment_edited`); double check no other call site references `on_trim_changed` before deleting it (`grep -n on_trim_changed clipcut`).

- [ ] **Step 5: Verify with an offscreen script**

```bash
cat > /tmp/test_timeline_segments.py << 'EOF'
import sys, os, pathlib
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()
from PySide6.QtWidgets import QApplication
app = QApplication([])

win = m.MainWindow()
win.load_file(pathlib.Path("/tmp/claude-1000/-home-james-Projects-clipcut/bd5394b0-7eb7-4b69-939a-f1c83f3e2d8b/scratchpad/test.mp4"))
win.cuts.append(m.Cut(start_repr=60, end_repr=90, frame_mode=True))
win.refresh_timeline_cuts()
assert len(win.scrubber.segments) == 2
assert win.scrubber.segments[0][2] is True  # cut 0 still active
print("OK")
EOF
python3 /tmp/test_timeline_segments.py
```

Expected: `OK` printed.

- [ ] **Step 6: Manual check**

Load a video, add a second cut, confirm two colored segments appear on the timeline with the active one brighter, drag a handle and confirm the corresponding cut's fields update, click the other segment's body and confirm it becomes selected (list row highlight moves too).

- [ ] **Step 7: Commit**

```bash
git add clipcut
git commit -m "Show every cut as its own timeline segment, wire selection and dragging"
```

---

### Task 4: Per-cut crop toggle

**Files:**
- Modify: `clipcut` (Crop tab construction: new checkbox; every crop-field handler that currently writes `self.preview.set_crop_pixels`/reads `self.preview.crop` needs to write into `shared_crop` or `cuts[active_cut].crop` depending on the toggle)

**Interfaces:**
- Consumes: `Cut.crop`, `MainWindow.shared_crop`, `MainWindow.per_cut_crop_enabled`, `MainWindow.select_cut` from Task 1
- Produces:
  - `MainWindow.per_cut_crop_check: QCheckBox`
  - `MainWindow.reset_cut_crop_button: QPushButton` ("Reset to Shared Crop")
  - `MainWindow.current_crop_target() -> tuple` — returns `cuts[active_cut].crop` (seeding it from `shared_crop` first if `None`) when `per_cut_crop_enabled`, else `shared_crop`. Every crop-editing handler writes its result here instead of a bare `self.shared_crop = ...`.

- [ ] **Step 1: Add the checkbox and reset button**

In the Crop tab construction, add near `self.lock_aspect_check`:

```python
        self.per_cut_crop_check = QCheckBox("Per-Cut Crop")
        self.per_cut_crop_check.toggled.connect(self.on_per_cut_crop_toggled)
        self.per_cut_crop_check.setEnabled(False)

        self.reset_cut_crop_button = QPushButton("Reset to Shared Crop")
        self.reset_cut_crop_button.clicked.connect(self.reset_cut_crop)
        self.reset_cut_crop_button.setEnabled(False)
```

Add both to `crop_tab_layout` (checkbox near the other checkboxes/lock toggle; reset button near `recenter_crop_button`/`clear_crop_button`), and to the existing `setEnabled(True)`/`setEnabled(False)` blocks in `load_file`/`remove_file` alongside the other crop controls.

- [ ] **Step 2: Implement the crop-target helper and every write site**

```python
    def current_crop_target(self):
        if not self.per_cut_crop_enabled:
            return self.shared_crop
        cut = self.cuts[self.active_cut]
        if cut.crop is None:
            cut.crop = self.shared_crop
        return cut.crop

    def set_current_crop(self, left, top, right, bottom):
        if self.per_cut_crop_enabled:
            self.cuts[self.active_cut].crop = (left, top, right, bottom)
        else:
            self.shared_crop = (left, top, right, bottom)

    def on_per_cut_crop_toggled(self, enabled):
        self.per_cut_crop_enabled = enabled
        self.reset_cut_crop_button.setEnabled(enabled)
        if self.path:
            crop = self.current_crop_target()
            self.preview.set_crop_pixels(*crop)
            self.update_crop_size_fields()

    def reset_cut_crop(self):
        if not self.per_cut_crop_enabled or not self.cuts:
            return
        self.cuts[self.active_cut].crop = None
        crop = self.current_crop_target()
        self.preview.set_crop_pixels(*crop)
        self.update_crop_size_fields()
```

Every place that currently mutates crop state as a side effect of a crop-field edit needs one added line calling `self.set_current_crop(left, top, right, bottom)` with the values it just applied via `self.preview.set_crop_pixels(...)`/`apply_scaled_crop(...)`. Find every such site with:

```bash
grep -n "self.preview.set_crop_pixels\|self.preview.apply_scaled_crop" clipcut
```

For each call site inside `MainWindow` (not inside `VideoPreview` itself - those are internal to the widget and don't need this), add `self.set_current_crop(left, top, right, bottom)` immediately after, using whatever the four values are already named in that function (they're already local variables at every such call site: `on_crop_field_edited`, `on_crop_size_field_edited`, `on_crop_position_field_edited`, `clear_crop`, `recenter_crop`, `on_aspect_ratio_selected`, `on_guides_changed`).

- [ ] **Step 3: Update `select_cut` to seed/apply crop consistently**

`select_cut` (Task 1, Step 5) already pushes `cut.crop if cut.crop is not None else self.shared_crop` into the crop widgets when `per_cut_crop_enabled` - no change needed there, but replace that inline expression with a call to `self.current_crop_target()` for consistency (it does the same seeding-on-read, keeping the "first edit of a None crop seeds from shared" behavior in one place):

```python
        if self.per_cut_crop_enabled:
            crop = self.cuts[index].crop if self.cuts[index].crop is not None else self.shared_crop
```

stays as read-only (does *not* seed - `select_cut` is called on every row click, and clicking a row shouldn't silently create a crop override; only *editing* should, via `current_crop_target`). Leave `select_cut` as written in Task 1; this step is a no-op confirmation, not a code change - skip editing it.

- [ ] **Step 4: Verify with an offscreen script**

```bash
cat > /tmp/test_per_cut_crop.py << 'EOF'
import sys, os, pathlib
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()
from PySide6.QtWidgets import QApplication
app = QApplication([])

win = m.MainWindow()
win.load_file(pathlib.Path("/tmp/claude-1000/-home-james-Projects-clipcut/bd5394b0-7eb7-4b69-939a-f1c83f3e2d8b/scratchpad/test.mp4"))
win.per_cut_crop_check.setChecked(True)
assert win.per_cut_crop_enabled is True

win.add_cut()
win.crop_left_edit_placeholder = None  # not a real widget; using preview directly below
win.preview.set_crop_pixels(50, 0, 0, 0)
win.set_current_crop(50, 0, 0, 0)
assert win.cuts[1].crop == (50, 0, 0, 0)

win.select_cut(0)
assert win.cuts[0].crop is None  # cut 0 never edited, still None

win.reset_cut_crop()  # resets whichever is active (cut 0, already None - no-op)
win.select_cut(1)
win.reset_cut_crop()
assert win.cuts[1].crop == win.shared_crop
print("OK")
EOF
python3 /tmp/test_per_cut_crop.py
```

Expected: `OK` printed. (The `crop_left_edit_placeholder` line is a harmless no-op left in deliberately to show the script doesn't depend on exact crop-field widget names - remove it if it's confusing; it does nothing.)

- [ ] **Step 5: Manual check**

Load a video, add a second cut, turn on Per-Cut Crop, set different crops on each cut via the Crop tab, switch between cuts and confirm the preview/fields show each cut's own crop. Turn the checkbox off and confirm every cut now shows the shared crop. Turn it back on and confirm the per-cut crops are still remembered. Click "Reset to Shared Crop" on a cut and confirm it snaps back to the shared value.

- [ ] **Step 6: Commit**

```bash
git add clipcut
git commit -m "Add Per-Cut Crop toggle: independent crop override per cut"
```

---

### Task 5: Extract `build_ffmpeg_args` from `build_command`

**Files:**
- Modify: `clipcut` (`build_command` in `MainWindow`)

**Interfaces:**
- Consumes: nothing new
- Produces: `MainWindow.build_ffmpeg_args(out_path) -> (cmd: list[str], target_duration: float)` — everything `build_command` does except computing the default output path, which callers now supply. `build_command()` becomes a thin wrapper that computes its usual default path and delegates, so single-cut export is byte-for-byte unchanged.

This is a pure refactor - the point is to make single-cut export path-agnostic so the multi-cut export loop (Tasks 6-7) can supply numbered or temp paths without duplicating the crop/audio/codec-selection logic.

- [ ] **Step 1: Capture current `build_command` output for a fixed scenario (regression baseline)**

```bash
cat > /tmp/test_build_command_before.py << 'EOF'
import sys, os, pathlib
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()
from PySide6.QtWidgets import QApplication
app = QApplication([])

win = m.MainWindow()
win.load_file(pathlib.Path("/tmp/claude-1000/-home-james-Projects-clipcut/bd5394b0-7eb7-4b69-939a-f1c83f3e2d8b/scratchpad/test.mp4"))
win.write_start_field(1.0)
win.write_end_field(3.0)
cmd, out_path, duration = win.build_command()
print("CMD:", cmd)
print("OUT:", out_path)
print("DUR:", duration)
EOF
python3 /tmp/test_build_command_before.py
```

Record the exact printed `CMD`/`OUT`/`DUR` output somewhere (paste it into a scratch note) - Step 4 re-runs this same script after the refactor and must print an identical `CMD` and `DUR` (the `OUT` path will differ run-to-run only if `next_available` finds an existing file from a prior run; delete any stray `test_1_cut.mp4`-style output file in the scratchpad between runs to keep it deterministic).

- [ ] **Step 2: Refactor `build_command`**

Replace the current body of `build_command`:

```python
    def build_command(self):
        """Return (cmd, out_path, target_duration). Raises ValueError on bad input."""
        start, end = self.get_trim_times()
        target_duration = end - start
        append_text = self.suffix_edit.text().strip() or self.compute_default_suffix()
        cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-i", str(self.path), "-ss", str(start), "-to", str(end)]

        if self.audio_only.isChecked():
            out_path = next_available(self.path.with_name(f"{self.path.stem}_{append_text}.m4a"))
            cmd += ["-vn", "-c:a", "copy", str(out_path)]
            return cmd, out_path, target_duration

        left, top, crop_w, crop_h, has_crop = self.get_crop()
        out_path = next_available(self.path.with_name(f"{self.path.stem}_{append_text}{self.path.suffix}"))
        if has_crop:
            cmd += ["-vf", f"crop={crop_w}:{crop_h}:{left}:{top}",
                    "-c:v", "libx264", "-crf", "17", "-preset", "veryfast"]
        elif self.frame_mode:
            cmd += ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast"]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-an"] if self.remove_audio.isChecked() else ["-c:a", "copy"]
        cmd.append(str(out_path))
        return cmd, out_path, target_duration
```

with:

```python
    def build_ffmpeg_args(self, out_path):
        """Return (cmd, target_duration) for the currently-active cut's trim/crop/
        audio settings, writing to the given out_path. Raises ValueError on bad
        input. Path-agnostic core of build_command, reused by multi-cut export."""
        start, end = self.get_trim_times()
        target_duration = end - start
        cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-i", str(self.path), "-ss", str(start), "-to", str(end)]

        if self.audio_only.isChecked():
            cmd += ["-vn", "-c:a", "copy", str(out_path)]
            return cmd, target_duration

        left, top, crop_w, crop_h, has_crop = self.get_crop()
        if has_crop:
            cmd += ["-vf", f"crop={crop_w}:{crop_h}:{left}:{top}",
                    "-c:v", "libx264", "-crf", "17", "-preset", "veryfast"]
        elif self.frame_mode:
            cmd += ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast"]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-an"] if self.remove_audio.isChecked() else ["-c:a", "copy"]
        cmd.append(str(out_path))
        return cmd, target_duration

    def build_command(self):
        """Return (cmd, out_path, target_duration) using the default single-export
        output path. Raises ValueError on bad input."""
        append_text = self.suffix_edit.text().strip() or self.compute_default_suffix()
        if self.audio_only.isChecked():
            out_path = next_available(self.path.with_name(f"{self.path.stem}_{append_text}.m4a"))
        else:
            out_path = next_available(self.path.with_name(f"{self.path.stem}_{append_text}{self.path.suffix}"))
        cmd, target_duration = self.build_ffmpeg_args(out_path)
        return cmd, out_path, target_duration
```

- [ ] **Step 3: Verify no other code called the removed inline logic directly**

```bash
grep -n "def build_command\|def build_ffmpeg_args\|build_command(" clipcut
```

Confirm `build_command` is still called the same way everywhere it was before (only `do_process` should call it) and `build_ffmpeg_args` doesn't exist anywhere but its new definition and `build_command`'s delegation.

- [ ] **Step 4: Re-run the baseline script and diff**

```bash
python3 /tmp/test_build_command_before.py
```

Expected: identical `CMD` and `DUR` lines to Step 1's recorded output (`OUT` may legitimately differ only due to `next_available` dedup state, as noted in Step 1).

- [ ] **Step 5: Manual check**

Load a video, trim it, hit Process, confirm export still produces a correct file exactly as before this task.

- [ ] **Step 6: Commit**

```bash
git add clipcut
git commit -m "Refactor build_command: extract path-agnostic build_ffmpeg_args"
```

---

### Task 6: Export mode radio + Separate Files export

**Files:**
- Modify: `clipcut` (Export section UI: new radio pair; `do_process`/`on_process_finished` gain multi-cut sequencing)

**Interfaces:**
- Consumes: `MainWindow.cuts`, `select_cut`, `sync_active_cut_from_widgets`, `build_ffmpeg_args` from Tasks 1, 2, 5
- Produces:
  - `MainWindow.export_mode_separate: QRadioButton`, `MainWindow.export_mode_combined: QRadioButton` (default: separate)
  - `MainWindow.export_queue: list[tuple[list[str], pathlib.Path]]` - `(cmd, out_path)` pairs still to run
  - `MainWindow.run_next_export() -> None` - pops and starts the next queued export, or reports completion when empty

- [ ] **Step 1: Add the export-mode radios**

In the Export section (`sidebar.addWidget(make_heading("Export"))` area), add before the `button_row`:

```python
        self.export_mode_separate = QRadioButton("Separate files")
        self.export_mode_combined = QRadioButton("Combined file")
        self.export_mode_separate.setChecked(True)
        self.export_mode_group = QButtonGroup(self)
        self.export_mode_group.addButton(self.export_mode_separate)
        self.export_mode_group.addButton(self.export_mode_combined)
        export_mode_row = QHBoxLayout()
        export_mode_row.addWidget(self.export_mode_separate)
        export_mode_row.addWidget(self.export_mode_combined)
        sidebar.addLayout(export_mode_row)
```

- [ ] **Step 2: Rewrite `do_process` to build a queue of one export per cut, and `on_process_finished` to advance it**

Replace `do_process`'s current body with:

```python
    def do_process(self):
        if self.export_mode_combined.isChecked() and len(self.cuts) > 1:
            self.do_process_combined()
            return
        try:
            self.sync_active_cut_from_widgets()
            saved_active = self.active_cut
            append_text = self.suffix_edit.text().strip() or self.compute_default_suffix()
            queue = []
            for i, cut in enumerate(self.cuts):
                self.select_cut(i)
                if self.audio_only.isChecked():
                    ext = ".m4a"
                else:
                    ext = self.path.suffix
                suffix = f"{append_text}_{i + 1}" if len(self.cuts) > 1 else append_text
                out_path = next_available(self.path.with_name(f"{self.path.stem}_{suffix}{ext}"))
                cmd, _duration = self.build_ffmpeg_args(out_path)
                queue.append((cmd, out_path))
            self.select_cut(saved_active)
        except ValueError as e:
            self.status.setText(f"Invalid input: {e}")
            self.error_details_button.setVisible(False)
            return

        self.export_queue = queue
        self.export_total = len(queue)
        self.error_details_button.setVisible(False)
        self.process_button.setEnabled(False)
        self.run_next_export()

    def run_next_export(self):
        if not self.export_queue:
            self.spinner_timer.stop()
            self.process_button.setEnabled(True)
            self.status.setText("Saved successfully")
            return
        cmd, out_path = self.export_queue.pop(0)
        self.pending_out_path = out_path
        start, end = self.get_trim_times() if len(self.cuts) == 1 else (0, 1)
        self.progress_target_duration = 1  # multi-cut progress is per-file, not aggregate - see note below
        self.progress_buffer = ""
        self.progress_percent = 0
        self.spinner_index = 0
        self.advance_spinner()
        self.spinner_timer.start()

        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.on_process_output)
        self.process.finished.connect(self.on_process_finished)
        self.process.errorOccurred.connect(self.on_process_error)
        self.process.start(cmd[0], cmd[1:])
```

Note: the `self.progress_target_duration = 1` placeholder above sidesteps recomputing per-cut duration cleanly through the queue; replace it precisely in the next step rather than shipping that approximation.

- [ ] **Step 3: Fix per-cut progress duration properly**

Change `export_queue`'s entries from `(cmd, out_path)` to `(cmd, out_path, target_duration)` - update the `queue.append(...)` line in `do_process` to `queue.append((cmd, out_path, target_duration))` using the `_duration` value already returned by `build_ffmpeg_args` (rename the throwaway `_duration` to `target_duration` at that call site). Update `run_next_export`'s unpacking to `cmd, out_path, target_duration = self.export_queue.pop(0)` and set `self.progress_target_duration = target_duration` instead of the `1` placeholder. Delete the now-unused `start, end = self.get_trim_times() if len(self.cuts) == 1 else (0, 1)` line from Step 2 entirely - it's dead code once duration comes from the queue tuple.

- [ ] **Step 4: Update `on_process_finished` to advance the queue instead of just finishing**

Replace the existing success branch (the part of `on_process_finished` that runs after the `if exit_code != 0:` early return) so that on success it calls `self.run_next_export()` instead of directly setting "Saved successfully" - `run_next_export` now owns the terminal "Saved successfully" status once the queue is empty (Step 2). Keep the failure branch (`if exit_code != 0:`) exactly as it is today, except also clear `self.export_queue = []` so a failed cut doesn't leave stale queued exports for the next unrelated click of Process.

- [ ] **Step 5: Verify with an offscreen script**

```bash
cat > /tmp/test_separate_export.py << 'EOF'
import sys, os, pathlib, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()
from PySide6.QtWidgets import QApplication
app = QApplication([])

src = pathlib.Path("/tmp/claude-1000/-home-james-Projects-clipcut/bd5394b0-7eb7-4b69-939a-f1c83f3e2d8b/scratchpad/test.mp4")
win = m.MainWindow()
win.load_file(src)
win.write_start_field(0.5)
win.write_end_field(1.5)
win.add_cut()
win.write_start_field(2.0)
win.write_end_field(3.0)

win.do_process()
assert len(win.export_queue) == 1  # first export already popped and started by run_next_export
for _ in range(200):
    app.processEvents()
    time.sleep(0.05)
    if win.process_button.isEnabled():
        break
assert win.process_button.isEnabled(), "export did not finish"
out1 = src.with_name(f"{src.stem}_cut_1{src.suffix}")
out2 = src.with_name(f"{src.stem}_cut_2{src.suffix}")
assert out1.exists(), f"missing {out1}"
assert out2.exists(), f"missing {out2}"
out1.unlink(); out2.unlink()
print("OK")
EOF
python3 /tmp/test_separate_export.py
```

Expected: `OK` printed (allow it to run for a few seconds - it's launching real ffmpeg twice).

- [ ] **Step 6: Manual check**

Load a video, add a second cut with a different range, hit Process, confirm two numbered output files appear and both play the correct content.

- [ ] **Step 7: Commit**

```bash
git add clipcut
git commit -m "Add Separate Files multi-cut export: one file per cut, queued sequentially"
```

---

### Task 7: Combined File export

**Files:**
- Modify: `clipcut` (new `do_process_combined`/`on_combined_export_finished`/`run_concat`)

**Interfaces:**
- Consumes: `MainWindow.cuts`, `build_ffmpeg_args`, `run_next_export`'s queue plumbing from Task 6
- Produces: `MainWindow.do_process_combined() -> None` - exports each cut to a temp file, then concatenates

- [ ] **Step 1: Implement `do_process_combined`**

```python
    def do_process_combined(self):
        try:
            self.sync_active_cut_from_widgets()
            saved_active = self.active_cut
            tmp_dir = Path(tempfile.mkdtemp(prefix="clipcut_"))
            queue = []
            crops = []
            for i, cut in enumerate(self.cuts):
                self.select_cut(i)
                ext = ".m4a" if self.audio_only.isChecked() else self.path.suffix
                tmp_path = tmp_dir / f"part_{i + 1}{ext}"
                cmd, target_duration = self.build_ffmpeg_args(tmp_path)
                queue.append((cmd, tmp_path, target_duration))
                try:
                    crops.append(self.get_crop()[:2] + self.get_crop()[2:4])
                except ValueError:
                    crops.append(None)
            self.select_cut(saved_active)
        except ValueError as e:
            self.status.setText(f"Invalid input: {e}")
            self.error_details_button.setVisible(False)
            return

        self.combined_tmp_dir = tmp_dir
        self.combined_uniform = len(set(crops)) <= 1 and (
            len(self.cuts) == 1 or all(c.frame_mode == self.cuts[0].frame_mode for c in self.cuts))
        self.export_queue = queue
        self.error_details_button.setVisible(False)
        self.process_button.setEnabled(False)
        self.run_next_export()
```

- [ ] **Step 2: Make `run_next_export` finish into a concat step when the queue was a combined-mode run**

Add a `self.export_is_combined = False` flag, set to `True` at the start of `do_process_combined` (after `self.export_queue = queue`) and `False` at the start of `do_process`. Change `run_next_export`'s empty-queue branch:

```python
        if not self.export_queue:
            if getattr(self, "export_is_combined", False):
                self.run_concat()
                return
            self.spinner_timer.stop()
            self.process_button.setEnabled(True)
            self.status.setText("Saved successfully")
            return
```

- [ ] **Step 3: Implement `run_concat`**

```python
    def run_concat(self):
        parts = sorted(self.combined_tmp_dir.glob("part_*"))
        append_text = self.suffix_edit.text().strip() or self.compute_default_suffix()
        ext = ".m4a" if self.audio_only.isChecked() else self.path.suffix
        out_path = next_available(self.path.with_name(f"{self.path.stem}_{append_text}{ext}"))

        if self.combined_uniform:
            list_path = self.combined_tmp_dir / "list.txt"
            list_path.write_text("".join(f"file '{p}'\n" for p in parts))
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)]
        else:
            inputs = []
            for p in parts:
                inputs += ["-i", str(p)]
            n = len(parts)
            filter_str = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
            cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_str, "-map", "[v]", "-map", "[a]", str(out_path)]

        self.pending_out_path = out_path
        self.export_is_combined = False  # concat's own finish should behave like a normal single completion
        self.progress_target_duration = 0
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.on_process_output)
        self.process.finished.connect(self.on_concat_finished)
        self.process.errorOccurred.connect(self.on_process_error)
        self.process.start(cmd[0], cmd[1:])
```

- [ ] **Step 4: Implement `on_concat_finished` (cleanup + reuse the existing success/failure reporting)**

```python
    def on_concat_finished(self, exit_code, exit_status):
        shutil.rmtree(self.combined_tmp_dir, ignore_errors=True)
        self.on_process_finished(exit_code, exit_status)
```

- [ ] **Step 5: Add the `tempfile`/`shutil`/`Path` imports**

Confirm `Path` is already imported (`from pathlib import Path` exists near the top); add `import tempfile` and `import shutil` next to the existing `import subprocess` line.

- [ ] **Step 6: Verify with an offscreen script**

```bash
cat > /tmp/test_combined_export.py << 'EOF'
import sys, os, pathlib, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()
from PySide6.QtWidgets import QApplication
app = QApplication([])

src = pathlib.Path("/tmp/claude-1000/-home-james-Projects-clipcut/bd5394b0-7eb7-4b69-939a-f1c83f3e2d8b/scratchpad/test.mp4")
win = m.MainWindow()
win.load_file(src)
win.export_mode_combined.setChecked(True)
win.write_start_field(0.5)
win.write_end_field(1.5)
win.add_cut()
win.write_start_field(2.0)
win.write_end_field(3.0)

win.do_process()
for _ in range(200):
    app.processEvents()
    time.sleep(0.05)
    if win.process_button.isEnabled():
        break
assert win.process_button.isEnabled(), "combined export did not finish"
out = src.with_name(f"{src.stem}_cut{src.suffix}")
assert out.exists(), f"missing {out}"

import subprocess, json
probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out)], capture_output=True, text=True)
dur = float(json.loads(probe.stdout)["format"]["duration"])
assert 1.8 < dur < 2.2, f"unexpected combined duration {dur}"  # ~1s + ~1s of content
out.unlink()
print("OK")
EOF
python3 /tmp/test_combined_export.py
```

Expected: `OK` printed.

- [ ] **Step 7: Manual check**

Add two cuts with different ranges (and, separately, a run with two different per-cut crops to force the concat-filter fallback), select "Combined file", hit Process, confirm one output file plays both segments back to back with correct content/crop.

- [ ] **Step 8: Commit**

```bash
git add clipcut
git commit -m "Add Combined File export: concat demuxer fast path + filter fallback"
```

---

### Task 8: Scene detection

**Files:**
- Modify: `clipcut` (new "Detect Scenes..." button + threshold field on the Cut tab; new `detect_scenes`/`on_scene_detect_output`/`on_scene_detect_finished` methods)

**Interfaces:**
- Consumes: `Cut`, `MainWindow.cuts`, `refresh_cut_list`, `select_cut`, `trim_seconds_for_cut` from Tasks 1-3
- Produces: `MainWindow.detect_scenes() -> None`

- [ ] **Step 1: Add the button and threshold field**

In the Cut tab, below the cut-list buttons:

```python
        self.scene_threshold_edit = NumberLineEdit()
        self.scene_threshold_edit.setText("30")
        self.scene_threshold_edit.setMaximumWidth(60)
        self.detect_scenes_button = QPushButton("Detect Scenes...")
        self.detect_scenes_button.clicked.connect(self.detect_scenes)
        self.detect_scenes_button.setEnabled(False)
        scene_row = QHBoxLayout()
        scene_row.addWidget(QLabel("Sensitivity %"))
        scene_row.addWidget(self.scene_threshold_edit)
        scene_row.addWidget(self.detect_scenes_button)
        cut_tab_layout.addLayout(scene_row)
```

(Threshold entered as a 0-100 integer percentage - matches `NumberLineEdit`'s integer-only convention rather than introducing a float spinner; divide by 100 when building the ffmpeg filter.)

Add `self.detect_scenes_button.setEnabled(True/False)` to the existing `load_file`/`remove_file` enable-toggling blocks.

- [ ] **Step 2: Implement `detect_scenes` with a confirm-before-replace guard**

```python
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

    def on_scene_detect_finished(self, _exit_code, _exit_status):
        self.detect_scenes_button.setEnabled(True)
        times = []
        for line in self.scene_detect_output.splitlines():
            marker = "pts_time:"
            if marker in line:
                chunk = line.split(marker, 1)[1].split()[0]
                try:
                    times.append(float(chunk))
                except ValueError:
                    pass
        self.scene_process = None
        if not times:
            self.status.setText("No scene changes detected")
            return

        times = sorted(set(t for t in times if 0 < t < self.duration))
        boundaries = [0.0] + times + [self.duration]
        new_cuts = []
        for start, end in zip(boundaries, boundaries[1:]):
            if self.frame_mode:
                fps = self.video_fps or 30.0
                new_cuts.append(Cut(start_repr=round(start * fps), end_repr=round(end * fps), frame_mode=True))
            else:
                new_cuts.append(Cut(start_repr=self.keyframe_index_of(start),
                                     end_repr=self.keyframe_index_of(end), frame_mode=False))
        self.cuts = new_cuts
        self.select_cut(0)
        self.refresh_cut_list()
        self.status.setText(f"Found {len(new_cuts)} scenes")
```

Add `QMessageBox` to the `PySide6.QtWidgets` import block.

- [ ] **Step 3: Verify parsing logic against a real multi-scene clip**

```bash
ffmpeg -y -f lavfi -i "color=red:size=320x240:rate=30:duration=1,format=yuv420p" -f lavfi -i "color=blue:size=320x240:rate=30:duration=1,format=yuv420p" -f lavfi -i "color=green:size=320x240:rate=30:duration=1,format=yuv420p" -filter_complex "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]" -map "[v]" /tmp/scenes_test.mp4

cat > /tmp/test_scene_detect.py << 'EOF'
import sys, os, pathlib, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("clipcut_app", "/home/james/Projects/clipcut/clipcut").load_module()
from PySide6.QtWidgets import QApplication
app = QApplication([])

win = m.MainWindow()
win.load_file(pathlib.Path("/tmp/scenes_test.mp4"))
win.detect_scenes()
for _ in range(200):
    app.processEvents()
    time.sleep(0.05)
    if win.detect_scenes_button.isEnabled():
        break
assert win.detect_scenes_button.isEnabled(), "scene detection did not finish"
assert len(win.cuts) >= 2, f"expected multiple scenes, got {len(win.cuts)}"
print("cuts found:", len(win.cuts))
print("OK")
EOF
python3 /tmp/test_scene_detect.py
```

Expected: `OK` printed with `cuts found: 3` (one per hard color-change boundary; a threshold of 30% reliably catches a full-frame color swap). Hard color cuts between solid frames are an easy case precisely so this step is a deterministic regression check, not a tuning exercise.

- [ ] **Step 4: Manual check**

Load a real video with actual scene changes (a downloaded clip, screen recording with cuts, etc. - not the synthetic single-color test fixture), click "Detect Scenes...", confirm the cut list and timeline populate with one segment per detected scene spanning the whole video. Add a second cut manually first, then re-run detection, and confirm the confirm-before-replace dialog appears and cancelling leaves the manual cut untouched.

- [ ] **Step 5: Commit**

```bash
git add clipcut
git commit -m "Add scene-detection action: replace cut list with one cut per detected scene"
```

---

## Self-Review

**Spec coverage:**
- Data model (`Cut`, `cuts`, `active_cut`, `shared_crop`, `per_cut_crop_enabled`) → Task 1.
- Cut tab list UI (add/delete/select rows, reusing existing field sync) → Task 2.
- Timeline segments (multi-region paint, drag, click-to-select) → Task 3.
- Per-cut crop toggle + seeding + reset → Task 4.
- Export refactor to make single-export logic reusable → Task 5.
- Separate-files export → Task 6.
- Combined-file export (concat demuxer + filter fallback) → Task 7.
- Scene detection with confirm-before-replace → Task 8.
- Backward compatibility (N=1 default, `per_cut_crop_enabled=False` default): enforced directly in Task 1 Step 4 and carried through every later task's default states - no task changes what a freshly-loaded single-cut file looks like.

**Placeholder scan:** The one placeholder-shaped line (`self.progress_target_duration = 1` in Task 6 Step 2) is explicitly flagged as temporary and corrected in the very next step (Step 3) with the real value - it's scaffolding within a single task's step sequence, not a shipped gap, consistent with how the skill's "no placeholders" rule is meant to prevent code that's *left* unfinished.

**Type consistency check:**
- `Cut.end_repr` is `int | None` everywhere it's read (`select_cut`, `sync_active_cut_from_widgets`, `trim_seconds_for_cut`, `detect_scenes`) - confirmed consistent.
- `select_cut(index)` / `on_cut_list_selected(row)` - `row` is a list-widget position, `index` is a `cuts` list position; Task 2's `refresh_cut_list` stores the mapping via `Qt.UserRole` and `on_cut_list_selected` looks it up before calling `select_cut` - not conflated.
- `Scrubber.set_cuts(segments)` signature (`list[(start, end, is_active)]`) matches exactly between its Task 3 Step 1 definition and every call site (`refresh_timeline_cuts`).
- `build_ffmpeg_args(out_path) -> (cmd, target_duration)` return shape matches every call site added in Tasks 6-7 (`do_process`, `do_process_combined`).
