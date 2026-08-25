"""MainWindow: composes all the mixins into the actual application window.

No __init__ here - UIBuildMixin's __init__ (first in the base list) runs
via MRO, since none of the other mixins define one."""

from PySide6.QtWidgets import QMainWindow

from clipcutlib.main_window.ui_build import UIBuildMixin
from clipcutlib.main_window.window_events import WindowEventsMixin
from clipcutlib.main_window.file_io import FileMixin
from clipcutlib.main_window.crop_mixin import CropMixin
from clipcutlib.main_window.keyframe_fields import KeyframeFieldsMixin
from clipcutlib.main_window.cut_list import CutListMixin
from clipcutlib.main_window.cut_crop_timeline import PerCutCropTimelineMixin
from clipcutlib.main_window.transport import TransportMixin
from clipcutlib.main_window.export_options import ExportOptionsMixin
from clipcutlib.main_window.export_args import ExportArgsMixin
from clipcutlib.main_window.export_process import ExportProcessMixin


class MainWindow(
    UIBuildMixin,
    WindowEventsMixin,
    FileMixin,
    CropMixin,
    KeyframeFieldsMixin,
    CutListMixin,
    PerCutCropTimelineMixin,
    TransportMixin,
    ExportOptionsMixin,
    ExportArgsMixin,
    ExportProcessMixin,
    QMainWindow,
):
    pass
