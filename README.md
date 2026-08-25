# ClipCut

A quick drag-and-drop video trim/crop tool. Drop a video in, cut out the
parts you want, crop the frame if you need to, and export — with the
output preserving the source file's original modified/accessed timestamps.

## Features

**Cutting**
- Multiple cuts from one source video, managed as a list (select, delete,
  keep-only-selected, or merge a contiguous selection).
- Automatic scene detection with an adjustable threshold — replaces the
  cut list with one cut per detected scene change.
- Two trim modes: Frame (frame-accurate, re-encodes) and Keyframe
  (lossless stream-copy, snaps to the nearest keyframe).
- Timeline lock/snap/loop, and mouse-wheel step-seeking (frame or
  keyframe per notch, 10x with Shift).

**Cropping**
- Draggable edge guides and corner handles for freeform crops.
- Aspect-ratio lock with presets (Original, 16:9, 4:3, 1:1, 21:9, 4:5) and
  a horizontal/vertical orientation flip.
- One shared crop for every cut, or an independent crop per cut.
- Exact Size/Position fields, rule-of-thirds and center overlays, and
  configurable crop mask style/colors.

**Preview**
- Play/pause (incl. global Space bar), step one frame/keyframe at a time,
  mute.
- Zoom in/out through fixed steps, Fit-to-window, and cursor-anchored
  mouse-wheel zoom, with pan when zoomed in.

**Export**
- Video presets: Original (stream-copies where possible), MP4 (H.264/
  H.265), WebM (VP9), MKV (H.264), MP4 (AV1).
- Animated GIF/WebP export with frame rate, quality, palette/dither, and
  gifsicle optimization controls.
- Audio-only export (Original/M4A/MP3/Opus/FLAC/WAV) or Remove Audio.
- Scale by percentage or longest side.
- Export each cut as a separate file, or concatenate them into one
  combined file.

## Requirements

- `pyside6`
- `ffmpeg`
- `gifsicle`

## Running it

**Arch Linux (packaged):**

```sh
makepkg -si
```

This builds and installs `clipcut` as a system app (binary at
`/usr/bin/clipcut`, with a desktop launcher entry and icon).

**From source, anywhere with the dependencies installed:**

```sh
python3 clipcut [optional/path/to/video]
```

It's a single self-contained script — no build step required.

## License

All rights reserved — see [LICENSE](LICENSE). Runtime dependencies
(ffmpeg, gifsicle, PySide6) remain under their own licenses.
