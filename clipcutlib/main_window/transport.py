"""TransportMixin: playback/keyframe-stepping transport controls and the zoom buttons' enabled state."""

import bisect

from PySide6.QtGui import QIcon
from PySide6.QtMultimedia import QMediaPlayer


class TransportMixin:
    def keyframe_time(self, idx):
        if not self.keyframe_times:
            return 0.0
        idx = max(0, min(len(self.keyframe_times) - 1, idx))
        return self.keyframe_times[idx]

    def keyframe_index_of(self, t):
        """Index of the nearest keyframe at-or-before t - where a stream-copy start
        (or, for symmetry, an end picked from this same list) actually lands."""
        if not self.keyframe_times:
            return 0
        idx = bisect.bisect_right(self.keyframe_times, t) - 1
        return max(0, idx)

    # --- playback controls ---

    def toggle_playback(self):
        if self.preview.player.playbackState() == QMediaPlayer.PlayingState:
            self.preview.player.pause()
        else:
            self.preview.player.play()

    def frame_duration(self):
        return 1.0 / self.video_fps if self.video_fps else 1.0

    # QMediaPlayer.setPosition() only takes whole milliseconds, so a seek to a keyframe
    # lands up to ~1ms short of its true (sub-ms-precision) timestamp; current_position()
    # then reads back that slightly-earlier value. Without this tolerance, the next
    # prev/next press treats "slightly before keyframe X" as still short of X and finds
    # the same one again - permanently stuck, especially with closely-spaced keyframes.
    KEYFRAME_SEEK_EPS = 0.002

    def prev_keyframe_time(self, t):
        if not self.keyframe_times:
            return 0.0
        idx = bisect.bisect_left(self.keyframe_times, t - self.KEYFRAME_SEEK_EPS) - 1
        return self.keyframe_times[max(0, idx)]

    def next_keyframe_time(self, t):
        if not self.keyframe_times:
            return t
        idx = bisect.bisect_right(self.keyframe_times, t + self.KEYFRAME_SEEK_EPS)
        return self.keyframe_times[min(len(self.keyframe_times) - 1, idx)]

    def current_position(self):
        """The player's actual current position, in seconds. Reads the player directly
        rather than self.scrubber.position, which only updates when positionChanged
        arrives asynchronously - clicking a step/set button again before that signal
        lands would otherwise compute from a stale, pre-seek position."""
        return self.preview.player.position() / 1000.0

    def on_scrubber_stepped(self, direction):
        """Wheel-scroll over the timeline steps the playhead one frame (Frame mode)
        or one keyframe (Keyframe mode) at a time, matching Step Back/Forward."""
        if direction > 0:
            self.step_forward()
        else:
            self.step_back()

    def step_back(self):
        pos = self.current_position()
        if self.frame_mode:
            new_pos = max(0.0, pos - self.frame_duration())
        else:
            new_pos = self.prev_keyframe_time(pos)
        self.preview.player.setPosition(int(new_pos * 1000))

    def step_forward(self):
        pos = self.current_position()
        if self.frame_mode:
            new_pos = min(self.duration or 0.0, pos + self.frame_duration())
        else:
            new_pos = self.next_keyframe_time(pos)
        self.preview.player.setPosition(int(new_pos * 1000))

    def toggle_mute(self):
        muted = not self.preview.audio_output.isMuted()
        self.preview.audio_output.setMuted(muted)
        icon = "player-volume-muted-symbolic" if muted else "player-volume-symbolic"
        self.mute_button.setIcon(QIcon.fromTheme(icon))
        self.mute_button.setToolTip("Unmute" if muted else "Mute")

    def update_zoom_buttons(self):
        """Keep the Zoom In/Out/Fit buttons' enabled state in sync with the preview's
        current zoom_factor - connected to VideoPreview.zoom_changed, so this fires
        no matter which of zoom_in/out/reset/wheel triggered it, plus load and
        remove_file (see VideoPreview.min_zoom/max_zoom/fit_scale for the bounds)."""
        if not self.path:
            return
        zoom = self.preview.effective_zoom()
        self.zoom_out_button.setEnabled(zoom > self.preview.min_zoom() + 0.005)
        self.zoom_in_button.setEnabled(zoom < self.preview.max_zoom() - 0.005)
        self.zoom_fit_button.setEnabled(abs(zoom - self.preview.fit_scale()) > 0.005)

