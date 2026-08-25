"""Read-only lookup tables consumed by MainWindow's export/UI-option logic."""

import shutil

from PySide6.QtGui import QColor

AUDIO_CODEC_EXT = {
    "opus": ".opus",
    "vorbis": ".ogg",
    "aac": ".m4a",
    "alac": ".m4a",
    "mp3": ".mp3",
    "flac": ".flac",
}

# Export format presets - "Original" (None) keeps today's behavior exactly
# (stream-copy when the cut allows it, libx264 re-encode otherwise, same
# container as the source). Every other entry is (ext, video codec args,
# audio codec args) and always re-encodes - picking a specific format is a
# deliberate conversion, not something worth stream-copy-optimizing.
FORMAT_PRESETS = {
    "Original": None,
    "MP4 (H.264)": (".mp4", ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast"],
                     ["-c:a", "aac", "-b:a", "192k"]),
    "MP4 (H.265)": (".mp4", ["-c:v", "libx265", "-crf", "20", "-preset", "veryfast", "-tag:v", "hvc1"],
                     ["-c:a", "aac", "-b:a", "192k"]),
    "WebM (VP9)": (".webm", ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0"],
                    ["-c:a", "libopus", "-b:a", "128k"]),
    "MKV (H.264)": (".mkv", ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast"],
                     ["-c:a", "aac", "-b:a", "192k"]),
    "MP4 (AV1)": (".mp4", ["-c:v", "libsvtav1", "-crf", "30", "-preset", "8"],
                   ["-c:a", "aac", "-b:a", "192k"]),
}

# Audio-only export formats - shown in format_combo instead of FORMAT_PRESETS
# while "Audio only" is checked. "Original" keeps today's behavior (stream-
# copy into a container matching the source codec, see AUDIO_CODEC_EXT/
# probe_audio_codec; falls back to an AAC re-encode for an unrecognized
# codec). Every other entry is (ext, audio codec args) and always re-encodes,
# same rationale as FORMAT_PRESETS.
AUDIO_FORMAT_PRESETS = {
    "Original": None,
    "M4A (AAC)": (".m4a", ["-c:a", "aac", "-b:a", "192k"]),
    "MP3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
    "Opus": (".opus", ["-c:a", "libopus", "-b:a", "128k"]),
    "FLAC": (".flac", ["-c:a", "flac"]),
    "WAV": (".wav", ["-c:a", "pcm_s16le"]),
}

# Animated image formats - these don't fit FORMAT_PRESETS' plain codec-args
# shape (no audio track at all, and GIF needs a generated-palette filter
# chain rather than a fixed codec, for quality reasons - straight -c:v gif
# looks banded). Handled as their own branch in build_ffmpeg_args.
IMAGE_FORMAT_EXTS = {
    "GIF": ".gif",
    "WebP (Animated)": ".webp",
}

# libwebp's content-type presets - the real "reduce blockiness" lever, since
# ffmpeg's libwebp/libwebp_anim wrapper only exposes lossless/preset/quality
# (no compression-effort/method option to tune, unlike the standalone cwebp
# tool). Trimmed to the ones relevant to video-derived clips - "Indoor"
# (libwebp's "picture") and "Outdoor" (libwebp's "photo") are relabeled for
# clarity since that's the actual distinction between them; None/Icon/Text
# were dropped as irrelevant to video content. "Default" is the default -
# users can switch to Outdoor/Indoor/Drawing if blockiness shows up.
WEBP_PRESETS = {
    "Default": 0,
    "Indoor": 1,
    "Outdoor": 2,
    "Drawing": 3,
}

# GIF-only: paletteuse's dithering mode. This is the single biggest lever
# for GIF file size beyond fps/colors/scale - error-diffusion dithering
# (Smooth) scatters different noise onto every frame, which is great for
# gradients but defeats GIF's frame-to-frame LZW compression; ordered
# dithering (Compact) is deterministic per-pixel-position so repeated/static
# regions compress far better, and None skips dithering entirely (visible
# color banding, but the smallest and most compressible output of the three).
GIF_DITHER_MODES = {
    "Smooth": "sierra2_4a",
    "Compact": "bayer",
    "None": "none",
}

# ffmpeg's gif encoder doesn't do real frame-to-frame delta compression at
# all (verified: paletteuse's diff_mode=rectangle changes nothing about
# output size, only its own internal processing speed) - every frame is
# independently LZW-compressed. gifsicle's -O3 does the actual delta-frame
# optimization Photoshop's "Save for Web" used to, which is what actually
# closes the size gap; --lossy trades a little visual fidelity for a lot
# more of it. Checked once at import time since it never changes mid-run.
GIFSICLE_PATH = shutil.which("gifsicle")


BG_COLOR_CHOICES = (
    ("Black", QColor(0, 0, 0)),
    ("White", QColor(255, 255, 255)),
    ("Mid Grey", QColor(128, 128, 128)),
)

LINE_COLOR_CHOICES = (
    ("Magenta", QColor(255, 0, 255)),
    ("White", QColor(255, 255, 255)),
    ("Black", QColor(0, 0, 0)),
    ("Lime Green", QColor(0, 255, 0)),
    ("Red", QColor(255, 0, 0)),
    ("Blue", QColor(0, 0, 255)),
    ("Cyan", QColor(0, 255, 255)),
    ("Orange", QColor(255, 165, 0)),
    ("Yellow", QColor(255, 255, 0)),
)

LABEL_ROW_WIDTH = 80  # fixed label width so a column of label+combo rows all line up
