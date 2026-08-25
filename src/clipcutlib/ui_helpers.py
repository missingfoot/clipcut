"""Small widget-factory functions used while building MainWindow's UI.

icon_button/new_sidebar_section/sidebar_hr/make_note/image_option_row were
originally local closures inside MainWindow.__init__; new_sidebar_section,
sidebar_hr, and image_option_row took an explicit parameter for what they
used to capture (sidebar_outer / image_options_grid) instead, since a plain
module-level function can't close over a caller's local variable.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from clipcutlib.constants import LABEL_ROW_WIDTH
from clipcutlib.small_widgets import NumberLineEdit


def make_frame_row(label_text):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    label = QLabel(label_text)
    label.setFixedWidth(40)
    row.addWidget(label)
    frame_edit = NumberLineEdit()
    # No placeholder text: blank means "not set" (start of file / end of file), and a
    # "0" placeholder here would look identical to a real, deliberately-typed 0.
    frame_edit.setMaximumWidth(60)
    row.addWidget(frame_edit)
    ref_label = QLabel("(0:00)")
    ref_label.setStyleSheet("color: #999999;")
    row.addWidget(ref_label)
    row.addStretch()
    return row, frame_edit, ref_label


def make_heading(text):
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


def make_separator():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def make_vseparator():
    line = QFrame()
    line.setFrameShape(QFrame.VLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def make_crop_group(label_text, edit):
    group = QWidget()
    h = QHBoxLayout(group)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(QLabel(label_text))
    h.addWidget(edit)
    return group


def make_label_row(label_text, widget):
    row = QHBoxLayout()
    label = QLabel(label_text)
    label.setFixedWidth(LABEL_ROW_WIDTH)
    row.addWidget(label)
    row.addWidget(widget, stretch=1)
    return row


def icon_button(icon_name, tooltip):
    """Icon-only button - the label lives in the tooltip instead of on
    the button face, matching every other control in this row."""
    button = QPushButton()
    button.setIcon(QIcon.fromTheme(icon_name))
    button.setIconSize(QSize(16, 16))
    button.setToolTip(tooltip)
    return button


def new_sidebar_section(sidebar_outer):
    section = QVBoxLayout()
    section.setContentsMargins(12, 8, 12, 8)
    section.setSpacing(8)
    sidebar_outer.addLayout(section)
    return section


def sidebar_hr(sidebar_outer):
    sidebar_outer.addWidget(make_separator())


def make_note(text):
    note = QLabel(text)
    note.setStyleSheet("color: #999999; font-size: 10px;")
    note.setAlignment(Qt.AlignLeft)
    note.setWordWrap(True)
    return note


def image_option_row(image_options_grid, row, label_text, field, unit_text, max_width=90):
    label = QLabel(label_text)
    image_options_grid.addWidget(label, row, 0)
    if unit_text:
        field.setMaximumWidth(max_width)
        image_options_grid.addWidget(field, row, 1)
        unit_label = QLabel(unit_text)
        # QPalette.Mid (a 3D-bevel shading role, not text) is near-
        # black in this theme and made the label unreadable -
        # PlaceholderText is the actual "dim but legible" text role.
        unit_label.setForegroundRole(QPalette.PlaceholderText)
        image_options_grid.addWidget(unit_label, row, 2)
        return (label, field, unit_label)
    # No unit label (Preset) - span columns 1-2 and stretch full width.
    image_options_grid.addWidget(field, row, 1, 1, 2)
    return (label, field)
