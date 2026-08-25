"""Pure helpers with no Qt/UI dependency: ffprobe/ffmpeg shells and small formatters."""

import json
import subprocess


def probe(path):
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    num, _, den = stream["avg_frame_rate"].partition("/")
    fps = int(num) / int(den) if int(den or 0) else 30.0
    return int(stream["width"]), int(stream["height"]), duration, fps


def probe_audio_codec(path):
    """Source audio codec name (e.g. 'opus', 'aac'), or None if no audio stream / probe failure."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name",
            "-of", "json",
            str(path),
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    streams = json.loads(out.stdout).get("streams")
    return streams[0]["codec_name"] if streams else None


def get_keyframe_times(path):
    """Sorted list of keyframe timestamps - where a stream-copy cut's start actually lands."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    times = []
    for line in out.stdout.splitlines():
        pts_time, _, flags = line.partition(",")
        if flags.startswith("K"):
            try:
                times.append(float(pts_time))
            except ValueError:
                pass
    times.sort()
    return times


def grab_thumbnail(path):
    """Grab the first frame as a PNG (bytes), for players that fail to decode a preview."""
    out = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    if out.returncode != 0 or not out.stdout:
        return None
    return out.stdout


def next_available(path):
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def parse_out_time_seconds(line):
    """Parse an ffmpeg -progress 'out_time=HH:MM:SS.ffffff' line into seconds, or None."""
    if not line.startswith("out_time="):
        return None
    h, m, s = line[len("out_time="):].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def seconds_to_hms(total_seconds):
    total_seconds = max(0, int(round(total_seconds)))
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return h, m, s


def format_time(seconds):
    h, m, s = seconds_to_hms(seconds)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
