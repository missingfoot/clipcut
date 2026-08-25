"""UIBuildMixin: builds every widget in the main window - preview panel, transport controls, sidebar, and the Cut/Crop/Export tabs. Deliberately kept as one __init__ rather than split into per-tab _build_* methods (the originally planned finer split) - the risk/effort of threading dozens of local widget variables across method boundaries for a construction-only method outweighed the readability gain; this file being ~700 lines on its own, isolated from every other concern, already gets most of the benefit."""

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from clipcutlib.constants import (
    AUDIO_FORMAT_PRESETS,
    BG_COLOR_CHOICES,
    FORMAT_PRESETS,
    GIF_DITHER_MODES,
    IMAGE_FORMAT_EXTS,
    LABEL_ROW_WIDTH,
    LINE_COLOR_CHOICES,
    WEBP_PRESETS,
)
from clipcutlib.scrubber import Scrubber
from clipcutlib.small_widgets import NumberLineEdit, ScrollAdjustLineEdit
from clipcutlib.ui_helpers import (
    icon_button,
    image_option_row,
    make_crop_group,
    make_frame_row,
    make_heading,
    make_label_row,
    make_note,
    make_separator,
    make_vseparator,
    new_sidebar_section,
    sidebar_hr,
)
from clipcutlib.video_preview import VideoPreview


class UIBuildMixin:
    def __init__(self, initial_path=None):
        super().__init__()
        self.setWindowTitle("ClipCut")
        # Restore last session's size/position/maximized state if there is one
        # (see closeEvent) - saveGeometry()/restoreGeometry() round-trip all of
        # that together, not just width/height. Only a first-ever launch (or a
        # cleared config) falls through to the hardcoded default.
        geometry = QSettings().value("window_geometry")
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1280, 800)

        self.path = None
        self.video_width = None
        self.video_height = None
        self.duration = None
        self.video_fps = None
        self.keyframe_times = []
        self.process = None
        self.progress_target_duration = 0
        self.progress_buffer = ""
        self.spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self.spinner_index = 0
        self.spinner_timer = QTimer(self)
        self.spinner_timer.setInterval(80)
        self.spinner_timer.timeout.connect(self.advance_spinner)
        self._syncing = False

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- left column: video preview + playback controls ---
        left = QVBoxLayout()
        left.setContentsMargins(0, 1, 0, 0)
        left.setSpacing(0)
        self.preview = VideoPreview(self.load_file)
        self.preview.guides_changed.connect(self.on_guides_changed)
        self.preview.zoom_changed.connect(self.update_zoom_buttons)
        left.addWidget(self.preview, stretch=1)

        left.addWidget(make_separator())

        below_preview = QVBoxLayout()
        below_preview.setContentsMargins(12, 8, 12, 12)
        below_preview.setSpacing(8)

        self.scrubber = Scrubber()
        self.scrubber.seek_requested.connect(self.on_seek_requested)
        self.scrubber.segment_clicked.connect(self.on_segment_clicked)
        self.scrubber.segment_edited.connect(self.on_segment_edited)
        self.scrubber.stepped.connect(self.on_scrubber_stepped)
        below_preview.addWidget(self.scrubber)

        controls_row = QHBoxLayout()

        # self.preview.audio_output starts muted (see VideoPreview.__init__) -
        # match that here instead of hardcoding the unmuted icon/tooltip, or
        # the first click looks like a no-op (it really does flip the mute
        # state, but the icon/tooltip were already wrong to begin with, so
        # nothing visibly changes until the second click).
        muted = self.preview.audio_output.isMuted()
        self.mute_button = icon_button(
            "player-volume-muted-symbolic" if muted else "player-volume-symbolic",
            "Unmute" if muted else "Mute")
        self.mute_button.clicked.connect(self.toggle_mute)
        self.mute_button.setEnabled(False)
        controls_row.addWidget(self.mute_button)

        self.zoom_out_button = icon_button("zoom-out-symbolic", "Zoom Out")
        self.zoom_out_button.clicked.connect(lambda: self.preview.zoom_out())
        self.zoom_out_button.setEnabled(False)
        controls_row.addWidget(self.zoom_out_button)

        self.zoom_fit_button = icon_button("view-zoom-fit-symbolic", "Reset to Fit")
        self.zoom_fit_button.clicked.connect(self.preview.reset_zoom)
        self.zoom_fit_button.setEnabled(False)
        controls_row.addWidget(self.zoom_fit_button)

        self.zoom_in_button = icon_button("zoom-in-symbolic", "Zoom In")
        self.zoom_in_button.clicked.connect(lambda: self.preview.zoom_in())
        self.zoom_in_button.setEnabled(False)
        controls_row.addWidget(self.zoom_in_button)

        # Off by default (matches today's drag-to-resize behavior) - turning
        # it on protects against accidentally dragging a cut's start/end
        # handle when many cuts are packed tightly together on the timeline.
        self.lock_edges_button = icon_button("changes-allow-symbolic", "Lock Cut Edges")
        self.lock_edges_button.setCheckable(True)
        self.lock_edges_button.toggled.connect(self.on_lock_edges_toggled)
        self.lock_edges_button.setEnabled(False)
        controls_row.addWidget(self.lock_edges_button)

        # Off by default - loops playback within whichever single cut is
        # currently selected, instead of running past it. A separate toggle
        # rather than tying looping to selection itself, since selection is
        # also what drives the crop overlay/Start-End fields - overloading it
        # would make selecting a cut to edit also silently start looping it.
        self.loop_button = icon_button("media-playlist-repeat-symbolic", "Loop Cut")
        self.loop_button.setCheckable(True)
        self.loop_button.toggled.connect(self.on_loop_toggled)
        self.loop_button.setEnabled(False)
        controls_row.addWidget(self.loop_button)

        # Off by default - when on, dragging a cut's edge or the whole cut
        # snaps it to touch a neighboring cut's edge exactly, no gap/overlap.
        self.snap_button = icon_button("snap-symbolic", "Snap to Cuts")
        self.snap_button.setCheckable(True)
        self.snap_button.toggled.connect(self.on_snap_toggled)
        self.snap_button.setEnabled(False)
        controls_row.addWidget(self.snap_button)

        controls_row.addStretch()

        self.set_start_button = icon_button("bboxprev-symbolic", "Set Start")
        self.set_start_button.clicked.connect(self.set_start_from_playhead)
        self.set_start_button.setEnabled(False)
        controls_row.addWidget(self.set_start_button)

        self.step_back_button = icon_button("media-seek-backward-symbolic", "Step Back")
        self.step_back_button.clicked.connect(self.step_back)
        self.step_back_button.setEnabled(False)
        controls_row.addWidget(self.step_back_button)

        self.play_button = icon_button("media-playback-start-symbolic", "Play")
        self.play_button.clicked.connect(self.toggle_playback)
        self.play_button.setEnabled(False)
        controls_row.addWidget(self.play_button)

        self.step_forward_button = icon_button("media-seek-forward-symbolic", "Step Forward")
        self.step_forward_button.clicked.connect(self.step_forward)
        self.step_forward_button.setEnabled(False)
        controls_row.addWidget(self.step_forward_button)

        self.set_end_button = icon_button("bboxnext-symbolic", "Set End")
        self.set_end_button.clicked.connect(self.set_end_from_playhead)
        self.set_end_button.setEnabled(False)
        controls_row.addWidget(self.set_end_button)

        controls_row.addStretch()

        self.time_label = QLabel("0:00 / 0:00")
        controls_row.addWidget(self.time_label)

        below_preview.addLayout(controls_row)

        left.addLayout(below_preview)

        root.addLayout(left, stretch=1)

        root.addWidget(make_vseparator())

        # --- right column: existing sidebar controls ---
        # Two-layer layout: sidebar_outer (zero margin, full-bleed) holds separator
        # lines plus a series of padded "section" layouts, so dividers can span the
        # full sidebar width while everything else keeps its side padding.
        sidebar_outer = QVBoxLayout()
        sidebar_outer.setContentsMargins(0, 0, 0, 0)
        sidebar_outer.setSpacing(0)
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar_outer)
        sidebar_widget.setFixedWidth(280)

        sidebar = new_sidebar_section(sidebar_outer)
        sidebar.setContentsMargins(12, 12, 12, 12)

        self.filename_label = QLabel("Drag and drop or choose a file")
        sidebar.addWidget(self.filename_label)

        file_button_row = QHBoxLayout()
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_for_file)
        file_button_row.addWidget(self.browse_button)

        self.remove_file_button = QPushButton("Remove File")
        self.remove_file_button.clicked.connect(self.remove_file)
        self.remove_file_button.setEnabled(False)
        file_button_row.addWidget(self.remove_file_button)
        sidebar.addLayout(file_button_row)

        self.frame_mode = True
        self.cuts = []
        self.active_cut = 0
        self.shared_crop = (0, 0, 0, 0)
        self.per_cut_crop_enabled = False

        # --- Cut tab (page 1 of the outer Cut/Crop/Audio tabs) ---
        start_kf_row, self.start_keyframe_edit, self.start_keyframe_ref = make_frame_row("Start:")
        self.keyframe_start_widget = QWidget()
        self.keyframe_start_widget.setLayout(start_kf_row)

        end_kf_row, self.end_keyframe_edit, self.end_keyframe_ref = make_frame_row("End:")
        self.keyframe_end_widget = QWidget()
        self.keyframe_end_widget.setLayout(end_kf_row)

        self.keyframe_edits = (self.start_keyframe_edit, self.end_keyframe_edit)

        start_frame_row, self.start_frame_edit, self.start_frame_ref = make_frame_row("Start:")
        self.frame_start_widget = QWidget()
        self.frame_start_widget.setLayout(start_frame_row)

        end_frame_row, self.end_frame_edit, self.end_frame_ref = make_frame_row("End:")
        self.frame_end_widget = QWidget()
        self.frame_end_widget.setLayout(end_frame_row)

        self.cut_mode_frame = QRadioButton("Frame")
        self.cut_mode_keyframe = QRadioButton("Keyframe")
        self.cut_mode_frame.setChecked(True)
        self.cut_mode_group = QButtonGroup(self)
        self.cut_mode_group.addButton(self.cut_mode_frame)
        self.cut_mode_group.addButton(self.cut_mode_keyframe)
        cut_mode_row = QVBoxLayout()
        cut_mode_row.addWidget(self.cut_mode_frame)
        cut_mode_row.addWidget(self.cut_mode_keyframe)

        self.cut_list = QListWidget()
        self.cut_row_icon = QIcon.fromTheme("folder-videos")
        if self.cut_row_icon.isNull():
            self.cut_row_icon = QIcon.fromTheme("video-x-generic")
        self.cut_row_crop_icon = QIcon.fromTheme("image-crop-symbolic")
        self.cut_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.cut_list.itemSelectionChanged.connect(self.on_cut_list_selection_changed)

        cut_list_buttons = QHBoxLayout()
        self.add_cut_button = QPushButton("Add Cut")
        self.add_cut_button.clicked.connect(self.add_cut)
        cut_list_buttons.addWidget(self.add_cut_button)

        # Delete/Merge act on whatever's selected in cut_list (ctrl-click for an
        # out-of-order multi-select, shift-click for a contiguous range) rather
        # than a single "active" cut - Delete works on any selection, Merge only
        # on a contiguous one (there's nothing sensible to merge out of order).
        cut_selection_buttons = QHBoxLayout()
        self.delete_selected_button = QPushButton("Delete")
        self.delete_selected_button.clicked.connect(self.delete_selected_cuts)
        self.delete_selected_button.setEnabled(False)
        self.keep_selected_button = QPushButton("Keep")
        self.keep_selected_button.clicked.connect(self.keep_selected_cuts)
        self.keep_selected_button.setEnabled(False)
        self.merge_selected_button = QPushButton("Merge")
        self.merge_selected_button.clicked.connect(self.merge_selected_cuts)
        self.merge_selected_button.setEnabled(False)
        cut_selection_buttons.addWidget(self.delete_selected_button)
        cut_selection_buttons.addWidget(self.keep_selected_button)
        cut_selection_buttons.addWidget(self.merge_selected_button)

        cut_note = make_note(
            "Frame: precise, but needs re-encode (not fully lossless). "
            "Keyframe: lossless, snaps to nearest keyframe.")

        self.cut_mode_frame.toggled.connect(self.on_cut_mode_changed)
        self.frame_edits = (self.start_frame_edit, self.end_frame_edit)

        cut_tab_page = QWidget()
        cut_tab_layout = QVBoxLayout(cut_tab_page)
        cut_tab_layout.setContentsMargins(8, 8, 8, 8)
        cut_tab_layout.setSpacing(8)
        cut_tab_layout.addWidget(cut_note)
        cut_tab_layout.addLayout(cut_mode_row)
        cut_tab_layout.addWidget(self.frame_start_widget)
        cut_tab_layout.addWidget(self.frame_end_widget)
        cut_tab_layout.addWidget(self.keyframe_start_widget)
        cut_tab_layout.addWidget(self.keyframe_end_widget)
        cut_tab_layout.addWidget(make_separator())
        cut_tab_layout.addLayout(cut_list_buttons)
        cut_tab_layout.addWidget(self.cut_list, stretch=1)
        cut_tab_layout.addLayout(cut_selection_buttons)

        cut_tab_layout.addWidget(make_separator())
        cut_tab_layout.addWidget(make_heading("Scene Detection"))
        cut_tab_layout.addWidget(make_note(
            "Finds hard scene changes and replaces the entire cut list with one "
            "cut per detected scene, covering the whole video."))

        self.scene_threshold_edit = NumberLineEdit()
        self.scene_threshold_edit.setText("30")
        self.scene_threshold_edit.setMaximumWidth(60)
        self.detect_scenes_button = QPushButton("Detect Scenes...")
        self.detect_scenes_button.clicked.connect(self.detect_scenes)
        self.detect_scenes_button.setEnabled(False)
        scene_row = QHBoxLayout()
        scene_row.addWidget(QLabel("Threshold %"))
        scene_row.addWidget(self.scene_threshold_edit)
        scene_row.addWidget(self.detect_scenes_button)
        cut_tab_layout.addLayout(scene_row)

        self.keyframe_start_widget.hide()
        self.keyframe_end_widget.hide()

        # --- Crop tab (page 2) ---
        aspect_row = QVBoxLayout()
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItem("None")
        self.aspect_combo.addItem("Original")
        for label in ("16:9", "4:3", "1:1", "21:9", "4:5"):
            self.aspect_combo.addItem(label)
        self.aspect_combo.setEnabled(False)
        aspect_row.addLayout(make_label_row("Preset", self.aspect_combo))

        orient_row = QHBoxLayout()
        self.orient_horizontal = QRadioButton("Horizontal")
        self.orient_vertical = QRadioButton("Vertical")
        self.orient_horizontal.setChecked(True)
        self.orient_group = QButtonGroup(self)
        self.orient_group.addButton(self.orient_horizontal)
        self.orient_group.addButton(self.orient_vertical)
        for btn in (self.orient_horizontal, self.orient_vertical):
            btn.setEnabled(False)
            orient_row.addWidget(btn)
        orient_row.addStretch()
        aspect_row.addLayout(orient_row)

        self.lock_aspect_check = QCheckBox("Lock Aspect Ratio")
        self.lock_aspect_check.setChecked(True)
        self.lock_aspect_check.setToolTip(
            "When on, every crop edit - dragging a guide or corner, typing a field, "
            "double-clicking a guide - keeps the box's current ratio. When off, they all "
            "move independently.")
        self.lock_aspect_check.toggled.connect(self.preview.set_lock_aspect)
        self.lock_aspect_check.setEnabled(False)
        aspect_row.addWidget(self.lock_aspect_check)

        self.per_cut_crop_check = QCheckBox("Per-Cut Crop")
        self.per_cut_crop_check.toggled.connect(self.on_per_cut_crop_toggled)
        self.per_cut_crop_check.setEnabled(False)
        aspect_row.addWidget(self.per_cut_crop_check)

        crop_grid = QGridLayout()
        self.crop_left = NumberLineEdit()
        self.crop_top = NumberLineEdit()
        self.crop_right = NumberLineEdit()
        self.crop_bottom = NumberLineEdit()
        self.crop_edits = (self.crop_left, self.crop_top, self.crop_right, self.crop_bottom)
        for edit in self.crop_edits:
            edit.setMaximumWidth(60)
            edit.setPlaceholderText("0")

        crop_grid.addWidget(make_crop_group("T", self.crop_top), 0, 0, 1, 3, Qt.AlignCenter)
        crop_grid.addWidget(make_crop_group("L", self.crop_left), 1, 0, Qt.AlignLeft)
        crop_grid.addWidget(make_crop_group("R", self.crop_right), 1, 2, Qt.AlignRight)
        crop_grid.addWidget(make_crop_group("B", self.crop_bottom), 2, 0, 1, 3, Qt.AlignCenter)
        crop_grid.setColumnStretch(0, 1)
        crop_grid.setColumnStretch(1, 1)
        crop_grid.setColumnStretch(2, 1)
        self.crop_width_edit = NumberLineEdit()
        self.crop_height_edit = NumberLineEdit()
        for edit in (self.crop_width_edit, self.crop_height_edit):
            edit.setMaximumWidth(60)
            edit.setEnabled(False)
        size_row = QHBoxLayout()
        size_label = QLabel("Size")
        size_label.setFixedWidth(LABEL_ROW_WIDTH)
        size_row.addWidget(size_label)
        size_row.addWidget(self.crop_width_edit)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.crop_height_edit)
        size_row.addStretch()

        self.crop_x_edit = NumberLineEdit()
        self.crop_y_edit = NumberLineEdit()
        for edit in (self.crop_x_edit, self.crop_y_edit):
            edit.setMaximumWidth(60)
            edit.setPlaceholderText("0")
            edit.setEnabled(False)
        position_row = QHBoxLayout()
        position_label = QLabel("Position")
        position_label.setFixedWidth(LABEL_ROW_WIDTH)
        position_row.addWidget(position_label)
        position_row.addWidget(self.crop_x_edit)
        position_row.addWidget(QLabel(","))
        position_row.addWidget(self.crop_y_edit)
        position_row.addStretch()
        crop_note = make_note("Cropping requires re-encode (not fully lossless)")
        self.recenter_crop_button = QPushButton("Recenter Crop")
        self.recenter_crop_button.clicked.connect(self.recenter_crop)
        self.recenter_crop_button.setEnabled(False)
        self.clear_crop_button = QPushButton("Clear Crop")
        self.clear_crop_button.clicked.connect(self.clear_crop)
        self.clear_crop_button.setEnabled(False)
        self.show_center_check = QCheckBox("Show Center")
        self.show_center_check.toggled.connect(self.preview.set_show_center)
        self.show_center_check.setEnabled(False)

        self.show_thirds_check = QCheckBox("Show Rule of Thirds")
        self.show_thirds_check.toggled.connect(self.preview.set_show_thirds)
        self.show_thirds_check.setEnabled(False)

        self.crop_style_combo = QComboBox()
        self.crop_style_combo.addItems(("Default", "Preview"))
        self.crop_style_combo.currentIndexChanged.connect(
            lambda i: self.preview.set_black_preview(i == 1))
        self.crop_style_combo.setEnabled(False)
        crop_style_row = make_label_row("Crop style", self.crop_style_combo)

        self.bg_color_combo = QComboBox()
        self.bg_color_combo.addItems([name for name, _ in BG_COLOR_CHOICES])
        self.bg_color_combo.currentIndexChanged.connect(
            lambda i: self.preview.set_bg_color(BG_COLOR_CHOICES[i][1]))
        self.bg_color_combo.setEnabled(False)
        bg_color_row = make_label_row("Background", self.bg_color_combo)

        self.line_color_combo = QComboBox()
        self.line_color_combo.addItems([name for name, _ in LINE_COLOR_CHOICES])
        self.line_color_combo.currentIndexChanged.connect(
            lambda i: self.preview.set_line_color(LINE_COLOR_CHOICES[i][1]))
        self.line_color_combo.setEnabled(False)
        line_color_row = make_label_row("Crop lines", self.line_color_combo)

        crop_tab_page = QWidget()
        crop_tab_layout = QVBoxLayout(crop_tab_page)
        crop_tab_layout.setContentsMargins(8, 8, 8, 8)
        crop_tab_layout.setSpacing(8)
        crop_tab_layout.addWidget(crop_note)
        crop_tab_layout.addLayout(aspect_row)
        crop_tab_layout.addLayout(crop_grid)
        crop_tab_layout.addWidget(make_separator())
        crop_tab_layout.addLayout(size_row)
        crop_tab_layout.addLayout(position_row)
        crop_tab_layout.addWidget(make_separator())
        crop_tab_layout.addWidget(self.show_center_check)
        crop_tab_layout.addWidget(self.show_thirds_check)
        crop_tab_layout.addWidget(make_separator())
        crop_tab_layout.addLayout(crop_style_row)
        crop_tab_layout.addLayout(bg_color_row)
        crop_tab_layout.addLayout(line_color_row)
        crop_tab_layout.addWidget(make_separator())
        crop_tab_layout.addWidget(self.recenter_crop_button)
        crop_tab_layout.addWidget(self.clear_crop_button)
        crop_tab_layout.addStretch()

        # --- Export tab (page 3): audio options + everything that used to live in
        # the sticky footer except progress/status, which stays sticky (visible
        # from every tab) since export progress is worth seeing no matter what
        # you're doing when it finishes. Everything you'd set *before* clicking
        # Process lives here instead. ---
        self.remove_audio = QCheckBox("Remove audio")
        self.audio_only = QCheckBox("Audio only")

        export_tab_page = QWidget()
        export_tab_layout = QVBoxLayout(export_tab_page)
        export_tab_layout.setContentsMargins(8, 8, 8, 8)
        export_tab_layout.setSpacing(8)

        # GIF/WebP never carry audio (build_ffmpeg_args always passes -an for
        # them) - this whole section hides for those formats rather than
        # sitting there having no effect.
        self.audio_section_widget = QWidget()
        audio_section_layout = QVBoxLayout(self.audio_section_widget)
        audio_section_layout.setContentsMargins(0, 0, 0, 0)
        audio_section_layout.addWidget(make_heading("Audio"))
        audio_section_layout.addWidget(self.remove_audio)
        audio_section_layout.addWidget(self.audio_only)
        export_tab_layout.addWidget(self.audio_section_widget)
        export_tab_layout.addWidget(make_separator())

        # Scale doesn't apply to audio-only output, but the section stays in
        # place and just greys out (see on_audio_only_toggled) rather than
        # hiding - hiding it made the whole tab's layout jump every time the
        # checkbox was toggled.
        self.scale_section_widget = QWidget()
        scale_section_layout = QVBoxLayout(self.scale_section_widget)
        scale_section_layout.setContentsMargins(0, 0, 0, 0)
        scale_section_layout.addWidget(make_heading("Scale"))
        scale_row = QHBoxLayout()
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItems(["Percentage", "Longest side"])
        self.scale_mode_combo.currentTextChanged.connect(self.update_scale_default)
        # Fixed to the widest item's width so the combo (and everything
        # after it in the row) doesn't shift when switching selections.
        combo_metrics = QFontMetrics(self.scale_mode_combo.font())
        self.scale_mode_combo.setMinimumWidth(
            combo_metrics.horizontalAdvance("Longest side") + 40)
        scale_row.addWidget(self.scale_mode_combo)
        self.scale_value_edit = ScrollAdjustLineEdit(minimum=1, maximum=1000, step=5, allow_blank=True)
        scale_row.addWidget(self.scale_value_edit)
        self.scale_unit_label = QLabel()
        self.scale_unit_label.setForegroundRole(QPalette.PlaceholderText)
        # Fixed to the wider of the two units ("px") so the label itself
        # doesn't change the row's width switching between "%" and "px".
        self.scale_unit_label.setFixedWidth(combo_metrics.horizontalAdvance("px") + 4)
        scale_row.addWidget(self.scale_unit_label)
        scale_section_layout.addLayout(scale_row)
        # Establish the initial unit label/placeholder for the current
        # ("Percentage") selection - currentTextChanged only fires on a
        # later change.
        self.update_scale_default()
        scale_section_layout.addWidget(make_note(
            "Resize the export. 100% keeps the source size."))
        export_tab_layout.addWidget(self.scale_section_widget)
        export_tab_layout.addWidget(make_separator())

        # Format lives at the bottom of the tab, below Audio, so toggling
        # "Audio only" can repopulate this same combo with audio container
        # choices (see on_audio_only_toggled) instead of just disabling it -
        # the two lists (VIDEO_FORMAT_ITEMS/AUDIO_FORMAT_PRESETS' keys) live
        # in the same slot rather than side by side.
        export_tab_layout.addWidget(make_heading("Format"))
        self.format_combo = QComboBox()
        self.video_format_items = list(FORMAT_PRESETS.keys()) + list(IMAGE_FORMAT_EXTS.keys())
        self.audio_format_items = list(AUDIO_FORMAT_PRESETS.keys())
        self.format_combo.addItems(self.video_format_items)
        self.format_combo.currentTextChanged.connect(self.on_format_changed)
        export_tab_layout.addWidget(self.format_combo)
        self.format_note = make_note(
            "Original keeps the source format. Anything else re-encodes.")
        export_tab_layout.addWidget(self.format_note)

        # GIF/WebP-only quality knobs - a lower frame rate and, for WebP, a
        # content-appropriate preset are the main levers for bringing an
        # animated export down from a 1080p source and for reducing WebP's
        # blocky macroblock artifacts (its lossy mode is VP8-based - the
        # "Photo" preset tunes its quantization for real footage instead of
        # flat graphics, which is what actually cuts blockiness, not a raw
        # compression-effort number ffmpeg's libwebp wrapper doesn't even
        # expose). Rows hide individually depending on which of GIF/WebP is
        # selected; the whole widget hides for every ordinary video format.
        # Fixed-width fields (not a QFormLayout) so label/box/unit line up
        # as a compact 3-column table that still leaves room for the unit
        # column in the sidebar's fixed width.
        self.image_options_widget = QWidget()
        image_options_grid = QGridLayout(self.image_options_widget)
        image_options_grid.setContentsMargins(0, 0, 0, 0)
        image_options_grid.setHorizontalSpacing(8)
        # Column 1 absorbs any leftover row width - line edits stay capped at
        # their own maximumWidth regardless (the extra space just sits empty
        # in their cell), but it's what lets the unit-less Preset row's combo
        # box actually stretch out to the row's full width.
        image_options_grid.setColumnStretch(1, 1)

        self.image_fps_edit = ScrollAdjustLineEdit(minimum=1, maximum=120, step=1, allow_blank=True)
        self.image_fps_edit.setPlaceholderText("Original")
        self.image_fps_row = image_option_row(image_options_grid, 0, "Frame rate", self.image_fps_edit, "fps")
        self.webp_quality_edit = ScrollAdjustLineEdit("80", minimum=0, maximum=100, step=1)
        self.webp_quality_row = image_option_row(image_options_grid, 1, "Quality", self.webp_quality_edit, "0-100")
        self.webp_preset_combo = QComboBox()
        self.webp_preset_combo.addItems(list(WEBP_PRESETS.keys()))
        self.webp_preset_combo.setCurrentText("Default")
        self.webp_preset_row = image_option_row(image_options_grid, 2, "Preset", self.webp_preset_combo, "")
        self.gif_colors_edit = ScrollAdjustLineEdit("256", minimum=2, maximum=256, step=8)
        self.gif_colors_row = image_option_row(image_options_grid, 3, "Colors", self.gif_colors_edit, "2-256")
        self.gif_dither_combo = QComboBox()
        self.gif_dither_combo.addItems(list(GIF_DITHER_MODES.keys()))
        self.gif_dither_row = image_option_row(image_options_grid, 4, "Dither", self.gif_dither_combo, "")
        # Grayscale halves what the palette needs to represent (no hue/
        # chroma variation left at all) - a blunter, much easier-to-reason-
        # about version of the saturation slider this replaced.
        self.gif_grayscale_check = QCheckBox("Black and white")
        image_options_grid.addWidget(self.gif_grayscale_check, 5, 0, 1, 3)
        self.gif_grayscale_row = (self.gif_grayscale_check,)
        # gifsicle does real frame-to-frame delta compression ffmpeg's own
        # gif encoder can't - by far the biggest lever for actually small
        # files, so it's on by default (it's a hard package dependency, so
        # it's there whenever the app is; export_gif_optimize's own
        # GIFSICLE_PATH check is just a quiet safety net for running from
        # source without the package installed, not something the UI needs
        # to reflect).
        self.gif_optimize_check = QCheckBox("Optimize with gifsicle")
        self.gif_optimize_check.setChecked(True)
        image_options_grid.addWidget(self.gif_optimize_check, 6, 0, 1, 3)
        self.gif_optimize_row = (self.gif_optimize_check,)
        export_tab_layout.addWidget(self.image_options_widget)

        # Establish initial widget visibility for the current ("Original")
        # selection - currentTextChanged only fires on a later change.
        self.on_format_changed(self.format_combo.currentText())

        suffix_form = QFormLayout()
        self.suffix_edit = QLineEdit()
        suffix_form.addRow("Append text:", self.suffix_edit)
        export_tab_layout.addLayout(suffix_form)

        self.export_mode_separate = QRadioButton("Separate files")
        self.export_mode_combined = QRadioButton("Combined file")
        self.export_mode_separate.setChecked(True)
        self.export_mode_group = QButtonGroup(self)
        self.export_mode_group.addButton(self.export_mode_separate)
        self.export_mode_group.addButton(self.export_mode_combined)
        export_mode_col = QVBoxLayout()
        export_mode_col.addWidget(self.export_mode_separate)
        export_mode_col.addWidget(self.export_mode_combined)
        export_tab_layout.addLayout(export_mode_col)
        export_tab_layout.addWidget(make_note(
            "Separate files: one export per cut. Combined file: every cut is joined "
            "into a single output (only available when every cut shares the same crop)."))

        self.process_button = QPushButton("Process")
        self.process_button.clicked.connect(self.do_process)
        self.process_button.setEnabled(False)
        export_tab_layout.addWidget(self.process_button)

        export_tab_layout.addStretch()

        # Outer Cut/Crop/Export tabs - added directly to sidebar_outer (not a padded
        # section) so the tab bar spans the full sidebar width edge-to-edge, KDE
        # System-Settings style. documentMode drops the bordered content pane; the
        # tab bar's own expanding behaviour makes each tab share the width evenly.
        # Left Expanding (default) vertically so this fills the space between the
        # fixed file area above and the fixed status footer below.
        self.section_tabs = QTabWidget()
        self.section_tabs.setDocumentMode(True)
        self.section_tabs.tabBar().setExpanding(True)
        self.section_tabs.addTab(cut_tab_page, "Cut")
        self.section_tabs.addTab(crop_tab_page, "Crop")
        self.section_tabs.addTab(export_tab_page, "Export")
        sidebar_outer.addWidget(self.section_tabs)

        sidebar_hr(sidebar_outer)
        sidebar = new_sidebar_section(sidebar_outer)

        self.remove_audio.toggled.connect(self.on_remove_audio_toggled)
        self.audio_only.toggled.connect(self.on_audio_only_toggled)
        for widget in self.keyframe_edits:
            widget.textChanged.connect(self.on_keyframe_fields_changed)
        for widget in self.frame_edits:
            widget.textChanged.connect(self.on_frame_fields_changed)
        for which, widget in zip(("left", "top", "right", "bottom"), self.crop_edits):
            widget.textChanged.connect(lambda _text, w=which: self.on_crop_field_edited(w))
        self.crop_width_edit.textChanged.connect(lambda: self.on_crop_size_field_edited("width"))
        self.crop_height_edit.textChanged.connect(lambda: self.on_crop_size_field_edited("height"))
        self.crop_x_edit.textChanged.connect(lambda: self.on_crop_position_field_edited("x"))
        self.crop_y_edit.textChanged.connect(lambda: self.on_crop_position_field_edited("y"))
        self.aspect_combo.activated.connect(self.on_aspect_ratio_selected)
        self.orient_horizontal.toggled.connect(self.on_orientation_changed)
        self.remove_audio.toggled.connect(self.update_suffix_placeholder)
        self.audio_only.toggled.connect(self.update_suffix_placeholder)
        self.update_suffix_placeholder()

        self.progress_percent = 0
        self.export_total = 0
        self.export_current = 0
        self.spinner_label = "Working"

        status_row = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, stretch=1)

        self.error_details_button = QPushButton("Details...")
        self.error_details_button.clicked.connect(self.show_error_details)
        self.error_details_button.setVisible(False)
        status_row.addWidget(self.error_details_button)
        sidebar.addLayout(status_row)

        self.error_details = ""

        # Widgets that are simply enabled while a file is loaded and disabled
        # while none is - toggled together by set_file_dependent_widgets_enabled()
        # in load_file/remove_file so those two methods can't silently drift out
        # of sync with each other (as happened once already: remove_file forgot
        # to reset per_cut_crop_enabled, caught in the final multi-cut review).
        self.file_dependent_widgets = [
            self.aspect_combo, self.orient_horizontal, self.orient_vertical,
            self.recenter_crop_button, self.crop_width_edit, self.crop_height_edit,
            self.crop_x_edit, self.crop_y_edit, self.clear_crop_button,
            self.per_cut_crop_check,
            self.show_center_check, self.show_thirds_check, self.lock_aspect_check,
            self.crop_style_combo, self.bg_color_combo, self.line_color_combo,
            self.process_button, self.play_button, self.step_back_button,
            self.step_forward_button, self.mute_button,
            self.zoom_out_button, self.zoom_fit_button, self.zoom_in_button,
            self.lock_edges_button, self.loop_button, self.snap_button,
            self.remove_file_button, self.set_start_button, self.set_end_button,
            self.detect_scenes_button,
        ]

        root.addWidget(sidebar_widget)

        self.setAcceptDrops(True)
        # QGraphicsView forces drag-accept on its own viewport internally (to support
        # dragging QGraphicsItems within the scene), which swallows external file drops
        # before they can bubble up to us. Intercept at the viewport via an event filter.
        self.preview.viewport().installEventFilter(self)

        # App-wide, so Space toggles play/pause no matter what has focus - except
        # widgets where Space already means something (typing a space, activating
        # a button/checkbox, opening a combo box, moving a list selection), which
        # only get that meaning back once deliberately Tab-focused onto them.
        QApplication.instance().installEventFilter(self)

        self.preview.player.positionChanged.connect(self.on_player_position_changed)
        self.preview.player.playbackStateChanged.connect(self.on_playback_state_changed)

        if initial_path:
            self.load_file(initial_path)

