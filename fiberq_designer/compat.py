"""Small Qt and QGIS compatibility adapters."""

from qgis.PyQt.QtCore import Qt, QIODevice
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.core import QgsDxfExport


def enum_compat(owner, name, group):
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, group), name)


DOCK_RIGHT = enum_compat(Qt, 'RightDockWidgetArea', 'DockWidgetArea')
MOUSE_RIGHT = enum_compat(Qt, 'RightButton', 'MouseButton')
MOUSE_LEFT = enum_compat(Qt, 'LeftButton', 'MouseButton')
MSG_YES = enum_compat(QMessageBox, 'Yes', 'StandardButton')
MSG_NO = enum_compat(QMessageBox, 'No', 'StandardButton')
FONT_BOLD = enum_compat(QFont, 'Bold', 'Weight')
WRITE_ONLY = enum_compat(QIODevice, 'WriteOnly', 'OpenModeFlag')
DXF_SUCCESS = enum_compat(QgsDxfExport, 'Success', 'ExportResult')
KEY_ESCAPE = enum_compat(Qt, 'Key_Escape', 'Key')
KEY_BACKSPACE = enum_compat(Qt, 'Key_Backspace', 'Key')
KEY_RETURN = enum_compat(Qt, 'Key_Return', 'Key')
KEY_ENTER = enum_compat(Qt, 'Key_Enter', 'Key')
PLAIN_TEXT = enum_compat(Qt, 'PlainText', 'TextFormat')
FILE_DIALOG_OPTIONS = enum_compat(QFileDialog, 'DontUseNativeDialog', 'Option')
