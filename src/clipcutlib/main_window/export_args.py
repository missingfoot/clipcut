"""ExportArgsMixin: reading trim/crop/export-option fields and building the ffmpeg filter/arg list."""

from clipcutlib.constants import (
    AUDIO_CODEC_EXT,
    AUDIO_FORMAT_PRESETS,
    FORMAT_PRESETS,
    GIF_DITHER_MODES,
    IMAGE_FORMAT_EXTS,
    WEBP_PRESETS,
)
from clipcutlib.utils import probe_audio_codec


class ExportArgsMixin:
    def read_frame_fields_seconds(self):
        if self.duration is None:
            raise ValueError("no file loaded")
        fps = self.video_fps or 30.0
        start_frame = int(self.start_frame_edit.text() or 0)
        if start_frame < 0:
            raise ValueError("frame numbers must be >= 0")
        start = start_frame / fps
        # Blank End means "through the end of the file", matching blank Start meaning
        # "from the beginning" - not frame 0, which would make every blank-End cut empty.
        if self.end_frame_edit.text():
            end_frame = int(self.end_frame_edit.text())
            if end_frame < 0:
                raise ValueError("frame numbers must be >= 0")
            end = end_frame / fps
        else:
            end = self.duration
        if not (0 <= start < end <= self.duration + 0.01):
            raise ValueError("frame range leaves nothing to cut")
        return start, end

    def read_keyframe_fields_seconds(self):
        if not self.keyframe_times:
            raise ValueError("no keyframe data")
        start_idx = int(self.start_keyframe_edit.text()) if self.start_keyframe_edit.text() else 0
        if start_idx < 0:
            raise ValueError("keyframe numbers must be >= 0")
        start = self.keyframe_time(start_idx)
        # Blank End means "through the end of the file". Unlike Start, the end of a
        # stream-copy cut doesn't need to land on a keyframe, so it isn't capped to the
        # last keyframe's time - which can sit well before the file's actual end (e.g. a
        # clip with only one keyframe would otherwise default End to time 0).
        if self.end_keyframe_edit.text():
            end_idx = int(self.end_keyframe_edit.text())
            if end_idx < 0:
                raise ValueError("keyframe numbers must be >= 0")
            end = self.keyframe_time(end_idx)
        else:
            end = self.duration
        if not (0 <= start < end <= self.duration + 0.01):
            raise ValueError("keyframe range leaves nothing to cut")
        return start, end

    def get_trim_times(self):
        if self.frame_mode:
            return self.read_frame_fields_seconds()
        return self.read_keyframe_fields_seconds()

    def get_crop(self):
        left, top, right, bottom = (int(edit.text() or 0) for edit in self.crop_edits)
        for name, v in (("left", left), ("top", top), ("right", right), ("bottom", bottom)):
            if v < 0:
                raise ValueError(f"crop {name} must be >= 0")
        crop_w = self.video_width - left - right
        crop_h = self.video_height - top - bottom
        has_crop = bool(left or top or right or bottom)
        if has_crop and (crop_w <= 0 or crop_h <= 0):
            raise ValueError("crop margins exceed frame size")
        return left, top, crop_w, crop_h, has_crop

    def build_scale_filter(self, width, height):
        """Return a scale=... filter string for the given (post-crop) output
        dimensions, or None if the Scale field is blank (original size).
        Raises ValueError on bad input."""
        text = self.scale_value_edit.text().strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            raise ValueError("scale value must be a number")
        if value <= 0:
            raise ValueError("scale value must be > 0")
        if self.scale_mode_combo.currentText() == "Percentage":
            factor = value / 100.0
            # trunc(.../2)*2 keeps both dimensions even, which most codecs
            # (yuv420p chroma subsampling) require.
            return f"scale=trunc(iw*{factor}/2)*2:trunc(ih*{factor}/2)*2"
        target = int(round(value))
        if target % 2:
            target += 1
        return f"scale={target}:-2" if width >= height else f"scale=-2:{target}"

    def build_fps_filter(self):
        """GIF/WebP-only: return a fps=... filter, or None if left blank
        (original frame rate). The main lever for shrinking an animated
        export beyond what quality/compression alone can do."""
        text = self.image_fps_edit.text().strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            raise ValueError("frame rate must be a number")
        if value <= 0:
            raise ValueError("frame rate must be > 0")
        return f"fps={value}"

    def build_eq_filter(self):
        """GIF-only: return an eq=saturation=0 filter when Black and white
        is checked, else None."""
        return "eq=saturation=0" if self.gif_grayscale_check.isChecked() else None

    def gif_max_colors(self):
        text = self.gif_colors_edit.text().strip() or "256"
        try:
            value = int(text)
        except ValueError:
            raise ValueError("max colors must be a whole number")
        if not 2 <= value <= 256:
            raise ValueError("max colors must be between 2 and 256")
        return value

    def webp_quality(self):
        text = self.webp_quality_edit.text().strip() or "80"
        try:
            value = int(text)
        except ValueError:
            raise ValueError("quality must be a whole number")
        if not 0 <= value <= 100:
            raise ValueError("quality must be between 0 and 100")
        return value

    def compute_default_suffix(self):
        """Best-effort suffix reflecting current options, for the placeholder text."""
        if self.audio_only.isChecked():
            return "audio_only"
        try:
            start, end = self.get_trim_times()
            trimmed = start > 0 or end < (self.duration or 0)
        except ValueError:
            trimmed = False
        try:
            _, _, _, _, has_crop = self.get_crop() if self.path else (0, 0, 0, 0, False)
        except ValueError:
            has_crop = False

        parts = []
        if has_crop:
            parts.append("cropped")
        if self.remove_audio.isChecked():
            parts.append("no_audio")
        if trimmed or not parts:
            parts.append("cut")
        if self.frame_mode:
            parts.append("exact")
        return "_".join(parts)

    def update_suffix_placeholder(self):
        self.suffix_edit.setPlaceholderText(self.compute_default_suffix())

    def export_ext(self):
        if self.audio_only.isChecked():
            preset = AUDIO_FORMAT_PRESETS[self.format_combo.currentText()]
            if preset is None:
                codec = probe_audio_codec(self.path)
                return AUDIO_CODEC_EXT.get(codec, ".m4a")
            return preset[0]
        format_name = self.format_combo.currentText()
        if format_name in IMAGE_FORMAT_EXTS:
            return IMAGE_FORMAT_EXTS[format_name]
        preset = FORMAT_PRESETS[format_name]
        return preset[0] if preset else self.path.suffix

    def build_ffmpeg_args(self, out_path):
        """Return (cmd, target_duration) for the currently-active cut's trim/crop/
        audio/format settings, writing to the given out_path. Raises ValueError on
        bad input. Path-agnostic, reused by every export path (single, per-cut,
        combined)."""
        start, end = self.get_trim_times()
        target_duration = end - start
        cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-i", str(self.path), "-ss", str(start), "-to", str(end)]

        if self.audio_only.isChecked():
            preset = AUDIO_FORMAT_PRESETS[self.format_combo.currentText()]
            if preset is not None:
                cmd += ["-vn", *preset[1], str(out_path)]
                return cmd, target_duration
            codec = probe_audio_codec(self.path)
            if codec in AUDIO_CODEC_EXT:
                cmd += ["-vn", "-c:a", "copy", str(out_path)]
            else:
                cmd += ["-vn", "-c:a", "aac", "-b:a", "192k", str(out_path)]
            return cmd, target_duration

        format_name = self.format_combo.currentText()
        left, top, crop_w, crop_h, has_crop = self.get_crop()
        crop_filter = f"crop={crop_w}:{crop_h}:{left}:{top}" if has_crop else None
        effective_w = crop_w if has_crop else self.video_width
        effective_h = crop_h if has_crop else self.video_height
        scale_filter = self.build_scale_filter(effective_w, effective_h)
        vf_parts = [f for f in (crop_filter, scale_filter) if f]

        if format_name == "GIF":
            # A generated palette (rather than the plain gif codec's fixed
            # web-safe one) is what actually gives GIF output good color -
            # fps/crop/scale, if any, have to happen upstream of that same
            # filter chain, not as a separate -vf.
            eq_filter = self.build_eq_filter()
            fps_filter = self.build_fps_filter()
            parts = ([eq_filter] if eq_filter else []) + ([fps_filter] if fps_filter else []) + vf_parts
            prefix = ",".join(parts) + "," if parts else ""
            max_colors = self.gif_max_colors()
            dither = GIF_DITHER_MODES[self.gif_dither_combo.currentText()]
            chain = (f"{prefix}split[s0][s1];[s0]palettegen=stats_mode=diff:"
                     f"max_colors={max_colors}[p];[s1][p]paletteuse=dither={dither}")
            cmd += ["-vf", chain, "-an", str(out_path)]
            return cmd, target_duration

        if format_name == "WebP (Animated)":
            fps_filter = self.build_fps_filter()
            parts = ([fps_filter] if fps_filter else []) + vf_parts
            if parts:
                cmd += ["-vf", ",".join(parts)]
            quality = self.webp_quality()
            preset = WEBP_PRESETS[self.webp_preset_combo.currentText()]
            cmd += ["-c:v", "libwebp_anim", "-lossless", "0", "-quality", str(quality),
                    "-preset", str(preset), "-loop", "0", "-an", str(out_path)]
            return cmd, target_duration

        preset = FORMAT_PRESETS[format_name]
        if vf_parts:
            # Scaling (like cropping) can't stream-copy - it always needs a
            # real re-encode, even in Original/keyframe mode.
            cmd += ["-vf", ",".join(vf_parts)]
            cmd += preset[1] if preset else ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast"]
        elif preset:
            cmd += preset[1]
        elif self.frame_mode:
            cmd += ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast"]
        else:
            cmd += ["-c:v", "copy"]

        if self.remove_audio.isChecked():
            cmd += ["-an"]
        elif preset:
            cmd += preset[2]
        else:
            cmd += ["-c:a", "copy"]
        cmd.append(str(out_path))
        return cmd, target_duration

