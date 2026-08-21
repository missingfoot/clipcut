# Multi-cut lists with per-cut crop and scene detection

Status: approved, pending implementation plan
Date: 2026-08-21

## Goal

ClipCut currently trims exactly one range and exports exactly one file. This
extends it to a *list* of cuts (start/end pairs) on the same source video,
each optionally carrying its own crop, exported either as separate files or
stitched into one combined file. A scene-detection action can auto-populate
the list from ffmpeg's scene-change filter.

## Out of scope (this iteration)

- Per-cut audio settings (remove-audio/audio-only stay global, matching today).
- Manual drag-reorder of cuts - list stays sorted by start time.
- Cross-fades or any transition between cuts in combined mode - it's a hard cut.
- Editing crop guides directly on a *timeline thumbnail* per cut - crop editing
  still happens through the existing preview + Crop tab, just retargeted.

## Data model

`MainWindow` currently tracks one implicit cut via the Start/End fields and
`self.frame_mode`. This becomes a list:

```python
@dataclass
class Cut:
    start_repr: int  # keyframe index or frame number, per frame_mode at creation time
    end_repr: int | None  # None = blank = "through the end", matching today's convention
    frame_mode: bool  # each cut remembers which mode it was authored in
    crop: tuple[int, int, int, int] | None  # None = use MainWindow.shared_crop
```

`MainWindow` gains:
- `self.cuts: list[Cut]` - always has at least one entry once a file is loaded
  (mirrors today's single implicit cut).
- `self.active_cut: int` - index into `self.cuts` that the Start/End fields
  and (if per-cut crop is on) the Crop tab currently edit.
- `self.shared_crop` - the crop tuple used when a cut's `crop` is `None`.
  Replaces the current single `self.preview.crop` as the "default" source.
- `self.per_cut_crop_enabled: bool` - the new Crop-tab checkbox.

`VideoPreview` stays crop-tuple-agnostic (it doesn't know about cuts): it
keeps drawing/editing whatever crop tuple `MainWindow` currently hands it
(`shared_crop` or `cuts[active_cut].crop`). No change to its internals beyond
`MainWindow` swapping which tuple it feeds in.

## Cut tab UI

- A `QListWidget` (or a simple `QTreeWidget` for a two-column start/end look)
  below the existing radio/fields, listing each cut ("1: 0:00-0:12", "2:
  0:12-0:41", ...), sorted by start time.
- "Add Cut" button: appends a new entry (defaults: start = current playhead,
  end = blank/"through the end", same frame_mode as the currently active
  cut) and selects it.
- "Delete Cut" button: removes the selected entry (disabled when only one
  cut remains - there must always be at least one).
- Selecting a row sets `active_cut` and repopulates the existing Start/End
  fields (and the Frame/Keyframe radio, since a cut remembers its own mode)
  from that cut - reusing `read_keyframe_fields_seconds`/
  `read_frame_fields_seconds`/`write_start_field`/`write_end_field` as-is,
  just writing into `cuts[active_cut]` instead of a single implicit target.
- Editing Start/End/mode always edits `cuts[active_cut]`.

## Timeline (Scrubber)

Today's single `trim_start`/`trim_end`/`has_trim` becomes a list of
segments the widget paints and lets you drag:

- `set_cuts(list[(start_seconds, end_seconds, is_active)])` replaces
  `set_trim`. Each segment paints in the existing blue; the active one gets
  the brighter `#7fc7ff` boundary treatment that "has_trim" cuts get today,
  inactive ones a dimmer boundary so they're still visible but clearly not
  the one you're editing.
- Dragging a segment's start/end handle edits that cut directly (emits
  which cut index changed); dragging inside a segment could later support
  moving the whole cut, but that's not required for v1 - the corner/edge
  handles are enough, matching today's single-cut interaction shape.
- Clicking a segment (not on a handle) sets it as the active cut, mirroring
  list-row selection - both drive the same `active_cut` state.

## Crop tab: per-cut crop

- New checkbox "Per-Cut Crop" under the existing crop controls, off by
  default.
- Off: Crop tab behaves exactly as today - reads/writes `shared_crop`.
- On: Crop tab reads/writes `cuts[active_cut].crop`. If that cut's `crop` is
  still `None` (never customized), editing anything seeds it by copying
  `shared_crop` as the starting point, then diverges independently from
  then on. A "Reset to Shared Crop" affordance (small button or right-click,
  TBD in the plan) clears a cut's override back to `None`.
- Turning the checkbox off does not delete per-cut crops (they're just
  stored, not shown or edited) - it also governs export: off, every cut
  exports with `shared_crop` regardless of any stored per-cut override; on,
  each cut exports with its own crop (falling back to `shared_crop` for any
  cut still at `None`). One switch for both editing and export avoids a
  confusing "I set a per-cut crop but it's not being used" state.

## Scene detection

- "Detect Scenes..." button on the Cut tab, opening a small inline control
  (or dialog) for the sensitivity threshold, defaulting to `0.3` - the same
  default mini-converter uses.
- Runs `ffmpeg -i <path> -vf "select='gt(scene,threshold)',showinfo" -f null -`
  as a background `QProcess` (same async pattern `do_process` already uses
  for export), parsing `pts_time:` values out of the `showinfo` stderr lines
  for scene-change timestamps.
- On completion, replaces `self.cuts` entirely with one consecutive cut per
  detected boundary, covering `[0, t1], [t1, t2], ..., [tN, duration]`, all
  in the currently-active Frame/Keyframe mode, all crop-less (`None`,
  falling back to whatever `shared_crop`/per-cut state already existed is
  discarded along with the old list - this is a destructive replace, as
  agreed). Confirm-before-replace if `self.cuts` has more than one entry or
  any per-cut crops set, to avoid silently discarding manual work.

## Export

New radio choice on the Export section (or Cut tab - TBD in plan): "Separate
files" / "Combined file". Default: separate files (matches today's
single-file behavior most closely).

**Separate files**: loop `self.cuts`, run today's `build_command`/`do_process`
pipeline once per cut with that cut's start/end/crop (falling back to
`shared_crop`/global audio settings), producing
`{stem}_{suffix}_1{ext}`, `_2{ext}`, etc. Fully reuses existing per-export
logic; the only change is wrapping it in a loop and sequencing the
already-async `QProcess` runs one after another (matching how a single
export already reports progress/status - just N of them back to back).

**Combined file**: export each cut to a temp file exactly as in separate
mode, then join them:
- If every cut used the identical crop (all `None`/shared, or per-cut but
  numerically equal) and identical mode/codec path (all keyframe-mode
  stream-copy, or all re-encoded), use ffmpeg's concat *demuxer* (fast,
  no re-encode) - list the temp files in a concat script and run
  `ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4`.
- Otherwise (crops differ between cuts, or codecs/resolutions would
  mismatch) fall back to the concat *filter* (`concat=n=N:v=1:a=1`),
  which re-encodes but handles heterogeneous inputs correctly.
- Clean up temp files after the combined output is written (success or
  failure).

## Backward compatibility

A freshly-loaded file starts with exactly one `Cut` (matching today's
behavior 1:1) and `per_cut_crop_enabled = False` - so a user who never
touches the new controls sees no change at all. This keeps the migration
low-risk: the single-cut path is just the N=1 case of the new one, not a
separate code path to maintain.

## Testing

No automated test suite exists in this project; verification stays manual,
matching the established pattern for prior features in this codebase:
- Add/delete cuts, confirm Start/End fields and timeline segments track
  the active cut correctly in both Frame and Keyframe mode.
- Toggle Per-Cut Crop, confirm the Crop tab/preview switch context on cut
  selection, confirm a cut's override survives switching away and back.
- Run scene detection on a clip with several real scene changes, confirm
  the list replaces correctly and the confirm-before-replace fires when
  expected.
- Export in both Separate and Combined modes; for Combined, verify both
  the concat-demuxer fast path (uniform crop/mode) and the concat-filter
  fallback (differing per-cut crops) produce a correctly joined output.
