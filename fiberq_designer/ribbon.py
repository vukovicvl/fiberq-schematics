"""Compact, native Qt command ribbon and optional drawing inspector.

Equipment icons are unmodified assets from FiberQ.cuix (FiberQ CAD 1.0.0).
See icons/README.md and FIBERQ_CAD_LICENSE.txt for attribution.
"""

from pathlib import Path

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QColor, QIcon, QImage, QPalette, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .compat import DOCK_RIGHT, PLAIN_TEXT, enum_compat
from .domain import EQUIPMENT
from .cable_catalog import CATEGORIES
from .cable_dialog import CableDialog
from .fiber_model import PURPOSES


INSPECTOR_NAME = 'FiberQSchematicDesignerInspector'
DOCK_TOP = enum_compat(Qt, 'TopDockWidgetArea', 'DockWidgetArea')
DOCK_BOTTOM = enum_compat(Qt, 'BottomDockWidgetArea', 'DockWidgetArea')
DOCK_LEFT = enum_compat(Qt, 'LeftDockWidgetArea', 'DockWidgetArea')
VERTICAL = enum_compat(Qt, 'Vertical', 'Orientation')
_ICON_CACHE = {}


def command_icon(name, palette):
    if name.startswith('FQ_'):
        foreground = palette.color(
            enum_compat(QPalette, 'WindowText', 'ColorRole')
        )
        key = (name, foreground.rgba())
        if key not in _ICON_CACHE:
            source = QImage(str(Path(__file__).with_name('icons') / name))
            image = source.convertToFormat(
                enum_compat(QImage, 'Format_ARGB32', 'Format')
            )
            # CAD bitmap black is its transparency key. Grayscale strokes use
            # the native text color so the same glyph works in both Qt themes.
            for y in range(image.height()):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    if color.red() == color.green() == color.blue():
                        ink = QColor(foreground)
                        ink.setAlpha(color.red())
                        image.setPixelColor(x, y, ink)
            _ICON_CACHE[key] = QIcon(QPixmap.fromImage(image))
        icon = _ICON_CACHE[key]
    else:
        icon = QgsApplication.getThemeIcon('/' + name + '.svg')
    if icon.isNull():
        return QApplication.style().standardIcon(
            enum_compat(QStyle, 'SP_FileIcon', 'StandardPixmap')
        )
    return icon


class ElidedLabel(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self.full_text = ''
        self.setTextFormat(PLAIN_TEXT)
        self.setSizePolicy(
            enum_compat(QSizePolicy, 'Ignored', 'Policy'),
            enum_compat(QSizePolicy, 'Preferred', 'Policy'),
        )
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        self.full_text = str(text).replace('\n', ' · ')
        self.setToolTip(self.full_text)
        self._elide()

    def _elide(self):
        super().setText(
            self.fontMetrics().elidedText(
                self.full_text,
                enum_compat(Qt, 'ElideMiddle', 'TextElideMode'),
                max(20, self.width() - 4),
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


def _form_dialog(editor, key, title):
    dialog = QDialog(editor)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(380)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)
    buttons = QDialogButtonBox(
        enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
        | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    editor.parameter_dialogs[key] = dialog
    return form


def _build_parameters(editor):
    editor.parameter_dialogs = {}
    form = _form_dialog(editor, 'cable', 'Draw cable')
    editor.route_name = QLineEdit('CABLE-01')
    editor.route_count, editor.route_group = QSpinBox(), QSpinBox()
    for control in (editor.route_count, editor.route_group):
        control.setRange(1, 576)
        control.setValue(12)
    editor.route_length = QDoubleSpinBox()
    editor.route_length.setRange(0, 10000000)
    editor.route_length.setSuffix(' m')
    editor.route_length.setSpecialValueText('Unspecified')
    for title, widget in (
        ('Name', editor.route_name),
        ('Fibers', editor.route_count),
        ('Fibers per tube', editor.route_group),
        ('Real length', editor.route_length),
    ):
        form.addRow(title, widget)

    form = _form_dialog(editor, 'equipment', 'Place equipment')
    editor.equipment_combo = QComboBox()
    for kind in ('CABINET', 'OLT', 'ONT'):
        editor.equipment_combo.addItem(EQUIPMENT[kind], kind)
    editor.equipment_name, editor.equipment_count = (
        QLineEdit('CABINET-01'),
        QSpinBox(),
    )
    editor.equipment_count.setRange(1, 576)
    editor.equipment_count.setValue(24)
    editor.equipment_combo.currentIndexChanged.connect(
        editor.equipment_changed
    )
    for title, widget in (
        ('Type', editor.equipment_combo),
        ('Name', editor.equipment_name),
        ('Ports', editor.equipment_count),
    ):
        form.addRow(title, widget)

    form = _form_dialog(editor, 'splitter', 'Place splitter')
    editor.split_name, editor.split_combo = (
        QLineEdit('SPLITTER-01'),
        QComboBox(),
    )
    editor.split_combo.addItems(['1:2', '1:4', '1:8', '1:16', '1:32', '1:64'])
    editor.split_combo.setCurrentText('1:8')
    form.addRow('Name', editor.split_name)
    form.addRow('Ratio', editor.split_combo)

    form = _form_dialog(editor, 'cable_block', 'Place fiber port block')
    editor.name_edit, editor.count_combo, editor.facing_combo = (
        QLineEdit('BLOCK-01'),
        QComboBox(),
        QComboBox(),
    )
    editor.count_combo.addItems(['12', '24', '48', '96'])
    editor.facing_combo.addItem('Ports right', 'right')
    editor.facing_combo.addItem('Ports left', 'left')
    for title, widget in (
        ('Name', editor.name_edit),
        ('Fibers', editor.count_combo),
        ('Facing', editor.facing_combo),
    ):
        form.addRow(title, widget)
    editor.link_kind = QComboBox(editor)
    editor.link_kind.addItems(['splice', 'patch'])
    editor.link_kind.hide()


def _panel(page, title):
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(6, 2, 6, 0)
    layout.setSpacing(1)
    body = QGridLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setHorizontalSpacing(3)
    body.setVerticalSpacing(1)
    layout.addLayout(body, 1)
    caption = QLabel(title)
    caption.setAlignment(enum_compat(Qt, 'AlignCenter', 'AlignmentFlag'))
    font = caption.font()
    font.setPointSizeF(max(8, font.pointSizeF() - 1))
    caption.setFont(font)
    layout.addWidget(caption)
    page.addWidget(panel)
    separator = QFrame()
    separator.setFrameShape(enum_compat(QFrame, 'VLine', 'Shape'))
    page.addWidget(separator)
    return body


def _button(
    editor, layout, key, row=0, column=0, large=True, mode=None, menu=None
):
    button = QToolButton()
    button.setObjectName('fqsd_button_' + key)
    button.setAutoRaise(True)
    button.setDefaultAction(editor.command_actions[key])
    button.setToolButtonStyle(
        enum_compat(
            Qt,
            'ToolButtonTextUnderIcon' if large else 'ToolButtonTextBesideIcon',
            'ToolButtonStyle',
        )
    )
    button.setIconSize(QSize(32, 32) if large else QSize(20, 20))
    if large:
        button.setMinimumSize(64, 68)
    else:
        button.setMinimumHeight(25)
    if menu is not None:
        button.setMenu(menu)
        button.setPopupMode(
            enum_compat(QToolButton, 'MenuButtonPopup', 'ToolButtonPopupMode')
        )
    if isinstance(layout, QGridLayout):
        layout.addWidget(button, row, column, 3 if large else 1, 1)
    else:
        layout.addWidget(button)
    if mode:
        editor.tool_buttons[mode] = button
    return button


def build_designer_ui(editor):
    _build_parameters(editor)
    editor.catalog_dialog = CableDialog(editor)
    editor.command_actions, editor.workspace_commands, editor.mode_actions = (
        {},
        set(),
        {},
    )

    def command(
        key,
        title,
        callback,
        icon,
        tooltip,
        workspace=True,
        mode=None,
        connection_kind=None,
    ):
        action = QAction(command_icon(icon, editor.palette()), title, editor)
        action.setObjectName('fqsd_action_' + key)
        action.setToolTip(tooltip)
        action.setStatusTip(tooltip)
        action.triggered.connect(lambda checked=False: editor.guard(callback))
        if mode:
            action.setCheckable(True)
            editor.mode_actions[key] = (mode, connection_kind)
        editor.command_actions[key] = action
        if workspace:
            editor.workspace_commands.add(key)
        return action

    command(
        'new',
        'New',
        editor.create_new,
        'mActionFileNew',
        'New schematic workspace…',
        False,
    )
    command(
        'open',
        'Open',
        editor.open_existing,
        'mActionFileOpen',
        'Open a saved schematic workspace…',
        False,
    )
    command(
        'demo',
        'Demo',
        editor.create_demo,
        'FQ_KLOM_32.bmp',
        'Create the fiber-by-fiber demonstration…',
        False,
    )
    command(
        'save',
        'Save project',
        editor.save_project,
        'mActionFileSave',
        'Save the QGIS project next to this workspace',
    )
    command(
        'undo',
        'Undo',
        editor.undo,
        'mActionUndo',
        'Undo the last schematic edit',
    )
    command(
        'redo',
        'Redo',
        editor.redo,
        'mActionRedo',
        'Redo the last schematic edit',
    )
    command(
        'stop',
        'Stop / Pan',
        editor.stop_tool,
        'mActionPan',
        'Cancel the current drawing tool and return to pan (Esc)',
    )
    for key, title, icon, tooltip in (
        (
            'select',
            'Select',
            'mActionSelectRectangle',
            'Select / inspect a fiber, port, element or splice',
        ),
        ('move', 'Move', 'mActionMoveFeature', 'Move an element or closure'),
        (
            'vertex',
            'Edit bend',
            'mActionVertexTool',
            'Move or add a cable bend',
        ),
        (
            'remove_vertex',
            'Remove bend',
            'mActionDeleteSelected',
            'Remove an interior cable bend',
        ),
        (
            'trace',
            'Trace fiber',
            'FQ_TRA_32.bmp',
            'Trace continuity from a fiber line or port',
        ),
        (
            'auto',
            'Auto 1:1',
            'FQ_SP1_32.bmp',
            'Connect matching free ports on two fiber port blocks',
        ),
    ):
        command(
            key,
            title,
            editor.move_selected_annotation
            if key == 'move'
            else lambda mode=key: editor.activate(mode),
            icon,
            tooltip,
            mode=key,
        )
    command(
        'properties',
        'Properties',
        editor.edit_selected,
        'FQ_INF_32.bmp',
        'Edit the selected equipment, closure or cable',
    )
    command(
        'select_cable',
        'Select cable',
        editor.select_cable,
        'FQ_KRUC_32.bmp',
        'Select the complete cable of the selected fiber',
    )
    command(
        'delete',
        'Delete',
        editor.delete_selected,
        'mActionDeleteSelected',
        'Delete the selected object, with dependency confirmation',
    )
    command(
        'widen_closure',
        'Widen',
        editor.widen_selected_closure,
        'FQ_SIRI_32.bmp',
        'Widen the selected closure to include another cable',
    )
    command(
        'add_housing_cable',
        'Add cable…',
        editor.start_add_housing_cable,
        'mActionAdd',
        'Add a cable to an existing housing; retain connections and expand '
        'the combined bundle frame',
        mode='add_housing_cable',
    )
    command(
        'detach_housing_cable',
        'Detach cable end',
        editor.detach_selected_housing_cable,
        'mActionRemove',
        'Detach one cable end without deleting the cable or housing',
        mode='detach_housing_cable',
    )
    command(
        'frame_outlet',
        'Frame outlet…',
        editor.start_outlet,
        'FQ_ODV_32.bmp',
        'Create a staggered elbow or straight outlet cable from a housing or '
        'closure frame',
        mode='frame_outlet',
    )
    command(
        'loose_draw',
        'Cable…',
        editor.create_catalog_cable,
        'FQ_KLOM_32.bmp',
        'Create cable: category, model, tubes × fibers, '
        'straight or bent route',
        mode='cad_cable',
    )
    command(
        'manual_cable',
        'Manual cable…',
        lambda: editor.configure_parameter('loose_draw'),
        'FQ_KLOM_32.bmp',
        'Draw a manual cable with a custom fiber count and visual groups',
        mode='loose_draw',
    )
    command(
        'align_cable',
        'Align',
        editor.toggle_cable_alignment,
        'mActionAlignLeft',
        'Align the next cable to a fiber row or a free module connector; '
        'click again to clear',
        mode='align_cable',
    )
    command(
        'draw',
        'Between equipment…',
        lambda: editor.configure_parameter('draw'),
        'FQ_KRUC_32.bmp',
        'Original equipment-to-equipment route; use Cable… for physical tube '
        'catalogues and housing snapping',
        mode='draw',
    )
    command(
        'finish',
        'Finish',
        editor.finish_route,
        'mIconSuccess',
        'Finish the current cable (Enter)',
    )
    command(
        'closure',
        'Closure…',
        editor.create_closure,
        'FQ_NAS_32.bmp',
        'Create Closure… — N ordinary or NL latent',
    )
    for kind, title, icon in (
        ('ODF', 'ODF…', 'ODF24'),
        ('PATCH_PANEL', 'Panel…', 'PP'),
        ('FDH', 'FDH…', 'ODO48'),
        ('MDU', 'MDU…', 'IPAN'),
        ('FAT', 'FAT…', 'ZOK'),
        ('OTB', 'OTB…', 'ZOK'),
    ):
        command(
            kind,
            title,
            lambda k=kind: editor.create_housing(k),
            'FQ_' + icon + '_32.bmp',
            'Create '
            + {
                'ODF': 'ODF',
                'PATCH_PANEL': 'Patch Panel',
                'FDH': 'FDH Cabinet',
                'MDU': 'MDU Cabinet',
                'FAT': 'FAT Box',
                'OTB': 'OTB Box',
            }[kind]
            + '…',
        )
    command(
        'splitter',
        'Splitter…',
        lambda: editor.create_active('SPLITTER'),
        'FQ_S8_32.bmp',
        'Place a native standalone splitter with real optical ports…',
        mode='active_place',
    )
    command(
        'legacy_splitter',
        'Legacy splitter…',
        lambda: editor.configure_parameter('splitter'),
        'FQ_S8_32.bmp',
        'Place the original prototype splitter…',
        mode='splitter',
    )
    command(
        'OLT',
        'OLT…',
        lambda: editor.create_active('OLT'),
        'FQ_OLT8_32.bmp',
        'Create native OLT by its first PON endpoint…',
        mode='active_place',
    )
    command(
        'ONT',
        'ONT…',
        lambda: editor.create_active('ONT'),
        'FQ_ONT_32.bmp',
        'Create native ONT and its optical drop endpoint…',
        mode='active_place',
    )
    command(
        'equipment',
        'Equipment…',
        lambda: editor.configure_parameter('equipment'),
        'FQ_OLT8_32.bmp',
        'Place OLT, ONT or generic cabinet…',
        mode='equipment',
    )
    for key, title, mode, icon, tooltip in (
        (
            'cut_fiber',
            'Cut',
            'cut_fiber',
            'PRS',
            'Cut a fiber section or joined connection; its ends become free',
        ),
        (
            'cut_window',
            'Cut window',
            'cut_window',
            'PRS',
            'Cut fiber sections and joined connections within a window '
            'atomically',
        ),
        (
            'mark_fiber',
            'Splice point',
            'mark_fiber',
            'SP1',
            'Place a fusion splice point on a continuous fiber',
        ),
        (
            'mark_window',
            'Splice window',
            'mark_window',
            'SP1',
            'Place splice points on window-selected fibers',
        ),
        (
            'through_fiber',
            'Through',
            'through_fiber',
            'PRO',
            'Restore an unspliced through fiber without changing its purpose',
        ),
    ):
        command(
            key,
            title,
            lambda chosen=mode: editor.activate(chosen),
            'FQ_' + icon + '_32.bmp',
            tooltip,
            mode=mode,
        )
    command(
        'join_fiber',
        'Join',
        editor.start_joint,
        'FQ_SPJ_32.bmp',
        'Join two free fiber/cut ends or optical ports',
        mode='join_fiber',
    )
    command(
        'group_joint',
        'Group join…',
        lambda: editor.start_joint(grouped=True),
        'FQ_SPG_32.bmp',
        'Join consecutive fiber endpoints with a signed numbering step',
        mode='group_joint',
    )
    editor.join_snap_action = command(
        'join_snap',
        'Snap',
        lambda: editor.toggle_join_snap(editor.join_snap_action.isChecked()),
        'mIconSnapping',
        'Highlight and connect exact free fiber/cut ends and connector tips. '
        'Settings controls tolerance.',
    )
    editor.join_snap_action.setCheckable(True)
    editor.join_snap_action.setChecked(True)
    for key, title, callback, icon in (
        (
            'bom_report',
            'BOM…',
            lambda: editor.show_report('bom'),
            'mActionOpenTable',
        ),
        (
            'check_report',
            'Check…',
            lambda: editor.show_report('checks'),
            'mIconSuccess',
        ),
        (
            'route_report',
            'Route report',
            lambda: editor.show_report('route'),
            'FQ_TRA_32.bmp',
        ),
        ('budget', 'Optical budget…', editor.show_budget, 'mIconExpression'),
        ('legend', 'Legend', editor.start_legend, 'mActionAddLegend'),
        (
            'settings',
            'Settings…',
            editor.edit_workspace_settings,
            'mActionOptions',
        ),
    ):
        command(key, title, callback, icon, title)
    command(
        'branch_fiber',
        'Branch…',
        editor.start_branch,
        'FQ_ODV_32.bmp',
        'Reroute free A/B fiber ends with staggered bends. Click inside a '
        'fiber, then place its new end; use Join to connect it.',
        mode='branch_fiber',
    )
    command(
        'restore_branch',
        'Restore route',
        editor.reset_selected_branch,
        'FQ_ODV_32.bmp',
        'Return selected diverted fibers to the cable path. Select one fiber '
        'or a whole cable; restore cuts first.',
    )
    command(
        'slack',
        'Slack…',
        editor.start_slack,
        'FQ_REZ_32.bmp',
        'Place a source slack coil and metre annotation',
        mode='slack_place',
    )
    command(
        'fiber_data',
        'Fiber data…',
        editor.edit_fiber_data,
        'FQ_OPIS_32.bmp',
        'Edit fiber purpose, SM/MM and description; '
        'select a cable for a batch',
        mode='fiber_data',
    )
    for key, title in (('splice', 'Splice'), ('patch', 'Patch')):
        command(
            key,
            title,
            lambda kind=key: editor.start_connection(kind),
            'FQ_SPJ_32.bmp' if key == 'splice' else 'FQ_SP1_32.bmp',
            title + ' two free adapter ports',
            mode='splice',
            connection_kind=key,
        )
    command(
        'fit',
        'Fit view',
        editor.fit,
        'mActionZoomFullExtent',
        'Fit all schematic geometry and labels',
    )
    command(
        'csv',
        'Import CSV…',
        editor.import_csv,
        'FQ_SP1_32.bmp',
        'Import splice/patch links from CSV…',
    )
    command(
        'cable',
        'Port block…',
        lambda: editor.configure_parameter('cable'),
        'FQ_ODF24_32.bmp',
        'Place a legacy fiber port block for CSV workflows…',
        mode='cable',
    )
    command(
        'legacy_demo',
        'Splice demo…',
        lambda: editor.create_demo(legacy=True),
        'FQ_SPJ_32.bmp',
        'Create three 12F cables with direct splices and a separate 1:8 '
        'splitter…',
        False,
    )
    command(
        'basic_demo',
        'Network demo…',
        lambda: editor.create_demo(basic=True),
        'FQ_KRUC_32.bmp',
        'Create an ODF-to-FDH network with a closure and twelve real fiber '
        'splices…',
        False,
    )
    for ext, icon in (
        ('pdf', 'mActionSaveAsPDF'),
        ('svg', 'mActionSaveAsSVG'),
        ('dxf', 'mActionExport'),
    ):
        command(
            ext,
            ext.upper(),
            lambda extension=ext: editor.export_as(extension),
            icon,
            'Export ' + ext.upper() + ' drawing…',
        )

    editor.inspector = QDockWidget('FiberQ Details', editor.iface.mainWindow())
    editor.inspector.setObjectName(INSPECTOR_NAME)
    editor.inspector.setAllowedAreas(DOCK_LEFT | DOCK_RIGHT)
    editor.inspector.setMinimumWidth(240)
    contents = QWidget()
    column = QVBoxLayout(contents)
    editor.inspector_path = QLabel('No workspace opened')
    editor.inspector_path.setTextFormat(PLAIN_TEXT)
    editor.inspector_path.setProperty('fqsd_i18n_user', True)
    editor.inspector_path.setWordWrap(True)
    column.addWidget(editor.inspector_path)
    editor.details = QLabel(
        'Select an element, fiber or splice to inspect its details.'
    )
    editor.details.setWordWrap(True)
    editor.details.setTextFormat(PLAIN_TEXT)
    editor.details.setProperty('fqsd_i18n_user', True)
    editor.details.setAlignment(enum_compat(Qt, 'AlignTop', 'AlignmentFlag'))
    column.addWidget(editor.details, 1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(contents)
    editor.inspector.setWidget(scroll)
    editor.iface.addDockWidget(DOCK_RIGHT, editor.inspector)
    editor.inspector.hide()
    inspector_action = editor.inspector.toggleViewAction()
    inspector_action.setText('Details')
    inspector_action.setIcon(command_icon('mActionIdentify', editor.palette()))
    inspector_action.setToolTip('Show or hide the FiberQ selection inspector')
    editor.command_actions['inspector'] = inspector_action

    outer = QWidget(editor)
    layout = QVBoxLayout(outer)
    layout.setContentsMargins(5, 2, 5, 2)
    layout.setSpacing(2)
    quick = QHBoxLayout()
    quick.setSpacing(3)
    brand = QLabel('FIBERQ')
    font = brand.font()
    font.setBold(True)
    brand.setFont(font)
    quick.addWidget(brand)
    editor.quick_buttons = {}
    for key in ('new', 'open', 'save', 'undo', 'redo', 'select', 'stop'):
        button = _button(
            editor,
            quick,
            key,
            large=False,
            mode='select' if key == 'select' else None,
        )
        button.setToolButtonStyle(
            enum_compat(Qt, 'ToolButtonIconOnly', 'ToolButtonStyle')
        )
        editor.quick_buttons[key] = button
    editor.mode_label = ElidedLabel('Ready')
    editor.mode_label.setSizePolicy(
        enum_compat(QSizePolicy, 'Fixed', 'Policy'),
        enum_compat(QSizePolicy, 'Preferred', 'Policy'),
    )
    editor.mode_label.setFixedWidth(100)
    quick.addWidget(editor.mode_label)
    separator = QFrame()
    separator.setFrameShape(enum_compat(QFrame, 'VLine', 'Shape'))
    quick.addWidget(separator)
    editor.path_label = ElidedLabel('No workspace opened')
    editor.path_label.setProperty('fqsd_i18n_user', True)
    quick.addWidget(editor.path_label, 1)
    _button(editor, quick, 'inspector', large=False)
    editor.collapse_button = QToolButton()
    editor.collapse_button.setText('▴')
    editor.collapse_button.setToolTip('Collapse the ribbon panels')
    editor.collapse_button.setAutoRaise(True)
    editor.collapse_button.clicked.connect(lambda: editor.toggle_ribbon())
    quick.addWidget(editor.collapse_button)
    layout.addLayout(quick)
    editor.ribbon_tabs, editor.ribbon_stack = QTabBar(), QStackedWidget()
    editor.ribbon_tabs.setExpanding(False)
    editor.ribbon_tabs.setDrawBase(False)
    editor.ribbon_tabs.setObjectName('fqsd_ribbon_tabs')
    layout.addWidget(editor.ribbon_tabs)
    layout.addWidget(editor.ribbon_stack)
    editor.ribbon_pages = {}
    for title in ('Draw', 'Edit', 'Fibers', 'Workspace', 'Reports'):
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        scroll = QScrollArea()
        scroll.setFrameShape(enum_compat(QFrame, 'NoFrame', 'Shape'))
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(
            enum_compat(Qt, 'ScrollBarAlwaysOff', 'ScrollBarPolicy')
        )
        scroll.setWidget(page)
        scroll.setFixedHeight(118)
        editor.ribbon_pages[title] = (scroll, row)
        editor.ribbon_tabs.addTab(title)
        editor.ribbon_stack.addWidget(scroll)

    draw = editor.ribbon_pages['Draw'][1]
    panel = _panel(draw, 'Cables')
    cable_menu = QMenu(editor)
    for category, title in CATEGORIES.items():
        action = cable_menu.addAction(title + '…')
        action.triggered.connect(
            lambda checked=False, code=category: editor.guard(
                lambda: editor.create_catalog_cable(code)
            )
        )
    cable_menu.addSeparator()
    cable_menu.addAction(editor.command_actions['manual_cable'])
    _button(editor, panel, 'loose_draw', mode='cad_cable', menu=cable_menu)
    _button(editor, panel, 'draw', 0, 1, False, 'draw')
    _button(editor, panel, 'finish', 1, 1, False)
    _button(editor, panel, 'select_cable', 2, 1, False)
    _button(editor, panel, 'align_cable', 0, 2, False, 'align_cable')
    _button(editor, panel, 'frame_outlet', 1, 2, False, 'frame_outlet')
    panel = _panel(draw, 'Closures')
    editor.closure_button = _button(editor, panel, 'closure')
    panel = _panel(draw, 'ODF and panels')
    editor.odf_button = _button(editor, panel, 'ODF')
    editor.patch_button = _button(editor, panel, 'PATCH_PANEL', 0, 1)
    panel = _panel(draw, 'Cabinets and boxes')
    editor.fdh_button = _button(editor, panel, 'FDH', 0, 0, False)
    editor.mdu_button = _button(editor, panel, 'MDU', 1, 0, False)
    editor.fat_button = _button(editor, panel, 'FAT', 0, 1, False)
    editor.otb_button = _button(editor, panel, 'OTB', 1, 1, False)
    panel = _panel(draw, 'Splitters')
    splitter_menu = QMenu(editor)
    for ratio in (2, 4, 8, 16, 32, 64):
        action = splitter_menu.addAction('1:' + str(ratio))
        action.triggered.connect(
            lambda checked=False, value=ratio: editor.guard(
                lambda: editor.create_active('SPLITTER', value)
            )
        )
    splitter_menu.addSeparator()
    splitter_menu.addAction(editor.command_actions['legacy_splitter'])
    _button(editor, panel, 'splitter', mode='splitter', menu=splitter_menu)
    panel = _panel(draw, 'Equipment')
    equipment_menu = QMenu(editor)
    for kind in ('OLT', 'ONT', 'CABINET'):
        action = equipment_menu.addAction(EQUIPMENT[kind] + '…')
        action.triggered.connect(
            lambda checked=False, value=kind: editor.guard(
                lambda: editor.configure_parameter('equipment', kind=value)
            )
        )
    _button(editor, panel, 'equipment', mode='equipment', menu=equipment_menu)
    panel = _panel(draw, 'OLT and ONT')
    olt_menu = QMenu(editor)
    for count in (8, 16):
        action = olt_menu.addAction(str(count) + ' PON ports…')
        action.triggered.connect(
            lambda checked=False, value=count: editor.guard(
                lambda: editor.create_active('OLT', value)
            )
        )
    _button(editor, panel, 'OLT', menu=olt_menu)
    _button(editor, panel, 'ONT', 0, 1)
    draw.addStretch(1)

    edit = editor.ribbon_pages['Edit'][1]
    panel = _panel(edit, 'Selection and data')
    _button(editor, panel, 'select', mode='select')
    _button(editor, panel, 'properties', 0, 1)
    _button(editor, panel, 'select_cable', 0, 2)
    panel = _panel(edit, 'Modify')
    add_cable_menu = QMenu(editor)
    add_cable_menu.addAction(editor.command_actions['detach_housing_cable'])
    _button(editor, panel, 'add_housing_cable', 0, 3, menu=add_cable_menu)
    _button(editor, panel, 'move', mode='move')
    _button(editor, panel, 'delete', 0, 1)
    _button(editor, panel, 'widen_closure', 0, 2)
    panel = _panel(edit, 'Cable bends')
    _button(editor, panel, 'vertex', mode='vertex')
    _button(editor, panel, 'remove_vertex', 0, 1, mode='remove_vertex')
    panel = _panel(edit, 'Connections and tracing')
    _button(editor, panel, 'splice', 0, 0, False, 'splice')
    _button(editor, panel, 'patch', 1, 0, False)
    _button(editor, panel, 'trace', 0, 1, mode='trace')
    panel = _panel(edit, 'History')
    _button(editor, panel, 'undo', 0, 0, False)
    _button(editor, panel, 'redo', 1, 0, False)
    _button(editor, panel, 'stop', 2, 0, False)
    edit.addStretch(1)

    fibers = editor.ribbon_pages['Fibers'][1]
    panel = _panel(fibers, 'Cuts and continuity')
    cut_menu = QMenu(editor)
    cut_menu.addAction(editor.command_actions['cut_window'])
    _button(editor, panel, 'cut_fiber', mode='cut_fiber', menu=cut_menu)
    _button(editor, panel, 'through_fiber', 0, 1, mode='through_fiber')
    panel = _panel(fibers, 'Optical connections')
    _button(editor, panel, 'join_snap', 1, 3, False)
    join_menu = QMenu(editor)
    action = join_menu.addAction('Join at chosen point…')
    action.triggered.connect(
        lambda checked=False: editor.guard(
            lambda: editor.start_joint(manual=True)
        )
    )
    for title, kind in (
        ('Unspliced through connection', 'through'),
        ('Patch connection', 'patch'),
        ('Optical drop', 'drop'),
    ):
        action = join_menu.addAction(title)
        action.triggered.connect(
            lambda checked=False, value=kind: editor.guard(
                lambda: editor.start_joint(value)
            )
        )
    _button(editor, panel, 'join_fiber', mode='join_fiber', menu=join_menu)
    _button(editor, panel, 'group_joint', 0, 1, mode='group_joint')
    mark_menu = QMenu(editor)
    mark_menu.addAction(editor.command_actions['mark_window'])
    _button(
        editor, panel, 'mark_fiber', 0, 2, mode='mark_fiber', menu=mark_menu
    )
    panel = _panel(fibers, 'Branches and slack')
    branch_menu = QMenu(editor)
    branch_menu.addAction(editor.command_actions['restore_branch'])
    _button(
        editor, panel, 'branch_fiber', mode='branch_fiber', menu=branch_menu
    )
    _button(editor, panel, 'slack', 0, 1, mode='slack_place')
    panel = _panel(fibers, 'Fiber purpose, type and description')
    purpose_menu = QMenu(editor)
    source_icons = {
        'nmWork': 1,
        'nmPlan': 2,
        'nmBuild': 3,
        'nmAcc': 4,
        'nmRsvd': 5,
        'nmDmg': 6,
        'nmSpl': 7,
        'nmSpare': 8,
        'nmThru': 9,
        'nmFree': 10,
    }
    for code, purpose in PURPOSES.items():
        action = command(
            'purpose_' + code,
            purpose.title,
            lambda value=code: editor.set_purpose(value),
            'FQ_NM{}_32.bmp'.format(source_icons[code]),
            'Set presentation purpose (connectivity unchanged): '
            + purpose.title,
        )
        purpose_menu.addAction(action)
    _button(editor, panel, 'fiber_data', mode='fiber_data', menu=purpose_menu)
    _button(editor, panel, 'trace', 0, 1)
    fibers.addStretch(1)

    workspace = editor.ribbon_pages['Workspace'][1]
    panel = _panel(workspace, 'Workspace')
    for col, key in enumerate(('new', 'open', 'save')):
        _button(editor, panel, key, 0, col)
    panel = _panel(workspace, 'Drawing exports')
    for col, key in enumerate(('pdf', 'svg', 'dxf')):
        _button(editor, panel, key, 0, col)
    panel = _panel(workspace, 'View')
    _button(editor, panel, 'fit', 0, 0, False)
    _button(editor, panel, 'inspector', 1, 0, False)
    editor.dark_canvas = QCheckBox('Dark canvas')
    editor.dark_canvas.toggled.connect(editor.set_dark_canvas)
    panel.addWidget(editor.dark_canvas, 2, 0)
    panel = _panel(workspace, 'CSV and port blocks')
    _button(editor, panel, 'csv', 0, 0, False)
    _button(editor, panel, 'cable', 1, 0, False, 'cable')
    _button(editor, panel, 'auto', 2, 0, False, 'auto')
    panel = _panel(workspace, 'Demonstrations')
    _button(editor, panel, 'demo', 0, 0, False)
    _button(editor, panel, 'basic_demo', 1, 0, False)
    _button(editor, panel, 'legacy_demo', 2, 0, False)
    workspace.addStretch(1)
    reports = editor.ribbon_pages['Reports'][1]
    panel = _panel(reports, 'Data and checks')
    for column, key in enumerate(
        (
            'route_report',
            'check_report',
            'bom_report',
        )
    ):
        _button(editor, panel, key, 0, column)
    panel = _panel(reports, 'Optical budget and legend')
    _button(editor, panel, 'budget')
    _button(editor, panel, 'legend', 0, 1)
    panel = _panel(reports, 'Workspace settings')
    _button(editor, panel, 'settings')
    _button(editor, panel, 'join_snap', 0, 1)
    reports.addStretch(1)
    editor.status = ElidedLabel()
    editor.status.setMinimumHeight(20)
    layout.addWidget(editor.status)
    editor.setWidget(outer)
    editor.ribbon_collapsed = False
    editor.ribbon_tabs.currentChanged.connect(
        editor.ribbon_stack.setCurrentIndex
    )
    editor.ribbon_tabs.currentChanged.connect(
        lambda index: editor.toggle_ribbon(False)
    )
    editor.ribbon_tabs.tabBarClicked.connect(
        lambda index: editor.toggle_ribbon(False)
    )
    editor.setAllowedAreas(DOCK_TOP | DOCK_BOTTOM)
    editor.setMaximumHeight(236)
    editor.setMinimumWidth(450)
    editor.update_command_state()
