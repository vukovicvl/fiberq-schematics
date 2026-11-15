"""Independent QGIS ribbon: UI and canvas commands delegate to editing
services."""

import math
import os
import traceback
from dataclasses import replace

from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

from .i18n import ui_message
from . import exports, rendering
from .cad_view import CadView, COLOR_NAMES
from .compat import (
    FILE_DIALOG_OPTIONS,
    MOUSE_LEFT,
    MOUSE_RIGHT,
    MSG_NO,
    MSG_YES,
    enum_compat,
)
from .csv_io import import_connections
from .element_layout import housing_layout
from .geometry import housing_font_metrics, odf_properties, optional, pt
from .housing_catalog import (
    HOUSINGS,
    BOX_HOUSINGS,
    OdfProperties,
    module_labels,
)
from .housing_dialog import HousingDialog
from .closure_dialog import ClosureDialog
from .closure_layout import (
    ClosureProperties,
    DEFAULT_CLOSURE_WIDTH,
    MIN_CLOSURE_WIDTH,
    closure_layout,
    local_point,
)
from .geometry import closure_properties
from .map_tools import ClickTool
from .repository import GeoPackageRepository
from .schema import ID_FIELDS
from .services import NetworkService
from .ribbon import DOCK_TOP, VERTICAL, build_designer_ui
from .cable_catalog import CABLE_VERSION, properties_from_record
from .cable_tools import CableTools
from .operation_tools import OperationTools, OPERATION_MODES
from .fiber_model import PURPOSES
from .workspace_tools import WorkspaceTools
from .i18n import translate
from .housing_cable_tools import HousingCableTools
from .outlet_tools import OutletTools


CRS = QgsCoordinateReferenceSystem('EPSG:3857')
OBJECT_NAME = 'FiberQSchematicDesignerPhase1'


class FiberQSchematicDesigner(
    OutletTools,
    HousingCableTools,
    WorkspaceTools,
    OperationTools,
    CableTools,
    QDockWidget,
):
    def __init__(self, iface):
        super().__init__('FiberQ', iface.mainWindow())
        self.setObjectName(OBJECT_NAME)
        self.iface = iface
        self.project = QgsProject.instance()
        self.canvas = iface.mapCanvas()
        self.repository = None
        self.service = None
        self.layers = {}
        self.path = None
        self.splice_points = None
        self.scene = None
        self.previous_canvas_color = QColor(self.canvas.canvasColor())
        self.mode = None
        self.pending = None
        self.route = None
        self.catalog_placement = None
        self.cable_alignment = None
        self.custom_cable_models = []
        self.operation_placement = None
        self.operation_options = {}
        self.operation_last_point = None
        self.active_defaults = {}
        self.housing_placement = None
        self.housing_target = None
        self.housing_layout = None
        self.closure_placement = None
        self.closure_defaults = ClosureProperties()
        self.closure_series = False
        self.closure_same_cable = True
        self.odf_defaults = OdfProperties()
        self.odf_models = []
        self.patch_defaults = OdfProperties(kind='PATCH_PANEL')
        self.patch_models = []
        self.cabinet_defaults = {
            kind: OdfProperties(kind=kind, capacity=48, mounting='indoor')
            for kind in ('FDH', 'MDU')
        }
        self.cabinet_models = {kind: [] for kind in ('FDH', 'MDU')}
        self.box_defaults = OdfProperties(
            kind='FAT', capacity=12, mounting='wall'
        )
        self.otb_defaults = replace(self.box_defaults, kind='OTB')
        self.box_models = []
        self.selected = None
        self.selection_node = None
        self.old_tool = None
        self.disposed = False
        self.tool_buttons = {}
        self.setup_workspace_ui()
        self.build_ui()
        self.click_tool = ClickTool(self.canvas, self)
        iface.addDockWidget(DOCK_TOP, self)
        self.setFloating(False)
        iface.mainWindow().resizeDocks([self], [220], VERTICAL)
        self.note(
            'Open a blank QGIS project, then choose New or Workspace → Demo.'
        )

    def build_ui(self):
        build_designer_ui(self)

    def configure_parameter(self, mode, kind=None, ratio=None):
        if not self.ensure_ready():
            return
        self.stop_tool()
        key = {
            'loose_draw': 'cable',
            'draw': 'cable',
            'cable': 'cable_block',
        }.get(mode, mode)
        if kind is not None:
            self.equipment_combo.setCurrentIndex(
                self.equipment_combo.findData(kind)
            )
        if ratio is not None:
            self.split_combo.setCurrentText('1:' + str(ratio))
        dialog = self.parameter_dialogs[key]
        if mode in ('loose_draw', 'draw'):
            dialog.setWindowTitle(
                'Draw cable first'
                if mode == 'loose_draw'
                else 'Cable between equipment'
            )
            self.route_group.setEnabled(mode == 'loose_draw')
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.activate(mode)

    def start_connection(self, kind):
        self.link_kind.setCurrentText(kind)
        self.activate('splice')

    def update_command_state(self):
        if not hasattr(self, 'command_actions') or self.disposed:
            return
        ready = self.repository is not None
        for key in self.workspace_commands:
            self.command_actions[key].setEnabled(ready)
        selected = self.selected[0] if self.selected else None
        self.command_actions['properties'].setEnabled(
            ready and selected in ('elements', 'cables', 'fibers')
        )
        self.command_actions['delete'].setEnabled(
            ready
            and selected
            in (
                'elements',
                'cables',
                'fibers',
                'links',
                'fiber_joints',
                'fiber_marks',
                'slack',
                'annotations',
            )
        )
        self.command_actions['select_cable'].setEnabled(
            ready and selected == 'fibers'
        )
        self.command_actions['undo'].setEnabled(
            ready and bool(self.repository.history)
        )
        self.command_actions['redo'].setEnabled(
            ready and bool(self.repository.future)
        )
        closure = (
            self.repository.find('closures', self.selected[1])
            if ready and selected == 'elements'
            else None
        )
        self.command_actions['widen_closure'].setEnabled(closure is not None)
        self.command_actions['finish'].setEnabled(
            ready
            and self.mode in ('loose_draw', 'cad_cable')
            and self.route is not None
            and (
                len(self.route['vertices']) >= 2
                or (
                    self.mode == 'cad_cable'
                    and self.catalog_placement is not None
                    and not self.catalog_placement['bends']
                )
            )
        )
        self.command_actions['stop'].setEnabled(
            ready and self.mode is not None
        )
        for key, (mode, connection_kind) in self.mode_actions.items():
            self.command_actions[key].setChecked(
                self.mode == mode
                and (
                    connection_kind is None
                    or self.link_kind.currentText() == connection_kind
                )
            )
        for key, kind in (
            ('splitter', 'SPLITTER'),
            ('OLT', 'OLT'),
            ('ONT', 'ONT'),
        ):
            self.command_actions[key].setChecked(
                self.mode == 'active_place'
                and self.operation_placement is not None
                and self.operation_placement['properties'].kind == kind
            )
        self.command_actions['align_cable'].setChecked(
            self.mode == 'align_cable' or self.cable_alignment is not None
        )
        if hasattr(self, 'mode_label'):
            label = {
                'loose_draw': 'Manual cable',
                'cad_cable': 'Draw cable',
                'align_cable': 'Align cable',
                'draw': 'Draw fibers',
                'housing': 'Place housing',
                'attach_odf': 'Attach housing',
                'closure': 'Place closure',
                'widen_closure': 'Widen closure',
                'equipment': 'Equipment',
                'splitter': 'Splitter',
                'select': 'Select',
                'move': 'Move',
                'vertex': 'Edit bend',
                'remove_vertex': 'Remove bend',
                'trace': 'Trace fiber',
                'splice': self.link_kind.currentText().capitalize(),
                'cable': 'Port block',
                'auto': 'Auto 1:1',
                'active_place': 'Place equipment',
                'cut_fiber': 'Cut fiber',
                'cut_window': 'Cut window',
                'mark_fiber': 'Splice point',
                'mark_window': 'Splice window',
                'through_fiber': 'Through',
                'join_fiber': 'Join endpoints',
                'group_joint': 'Group join',
                'branch_fiber': 'Branch',
                'slack_place': 'Slack',
                'fiber_data': 'Fiber data',
                'fiber_purpose': 'Fiber purpose',
                'frame_outlet': 'Frame outlet',
                'add_housing_cable': 'Add cable',
                'detach_housing_cable': 'Detach cable end',
            }.get(self.mode, 'Ready')
            self.mode_label.setText(
                translate(label, self.workspace_values['language'])
            )

    def toggle_ribbon(self, collapsed=None):
        self.ribbon_collapsed = (
            not self.ribbon_collapsed if collapsed is None else collapsed
        )
        self.ribbon_stack.setVisible(not self.ribbon_collapsed)
        self.collapse_button.setText('▾' if self.ribbon_collapsed else '▴')
        self.collapse_button.setToolTip(
            'Expand the ribbon panels'
            if self.ribbon_collapsed
            else 'Collapse the ribbon panels'
        )
        self.widget().layout().activate()
        self.layout().activate()
        height = 98 if self.ribbon_collapsed else 236
        self.setMaximumHeight(height)
        if self.isFloating():
            self.resize(self.width(), height)
        else:
            self.iface.mainWindow().resizeDocks([self], [height], VERTICAL)

    def guard(self, action):
        try:
            return action()
        except Exception as error:
            self.pending = None
            self.route = None
            if self.catalog_placement is not None:
                self.catalog_placement.update(
                    endpoints={}, modules={}, reverse=False
                )
            self.housing_placement = None
            self.housing_target = None
            self.click_tool.clear()
            self.err(error)

    def note(self, message):
        message = translate(message, self.workspace_values['language'])
        self.status.setText(message)
        self.iface.mainWindow().statusBar().showMessage(message, 6000)
        self.update_command_state()

    def err(self, error):
        traceback.print_exc()
        QMessageBox.critical(self, 'FiberQ Schematic Designer', str(error))
        self.note('Error: ' + str(error))

    def equipment_changed(self):
        kind = self.equipment_combo.currentData()
        if kind is None:
            return
        self.equipment_name.setText(
            self.service.unique_name(kind) if self.service else kind + '-01'
        )
        self.equipment_count.setValue(
            1 if kind == 'ONT' else 8 if kind == 'OLT' else 24
        )

    def create_odf(self):
        self.create_housing('ODF')

    def create_patch_panel(self):
        self.create_housing('PATCH_PANEL')

    def housing_models(self):
        models = {
            'ODF': list(self.odf_models),
            'PATCH_PANEL': list(self.patch_models),
            **{kind: list(self.box_models) for kind in BOX_HOUSINGS},
            **{
                kind: list(values)
                for kind, values in self.cabinet_models.items()
            },
        }
        kinds = {
            e['obj_id']: e['kind']
            for e in self.repository.features('elements')
        }
        for housing in self.repository.features('housings'):
            kind = kinds.get(housing['obj_id'])
            if kind in BOX_HOUSINGS:
                for box_kind in BOX_HOUSINGS:
                    models[box_kind].append(housing['model'])
            elif kind in models:
                models[kind].append(housing['model'])
        return models

    def remember_housing(self, properties):
        if properties.kind == 'ODF':
            self.odf_defaults, models = properties, self.odf_models
        elif properties.kind == 'PATCH_PANEL':
            self.patch_defaults, models = properties, self.patch_models
        elif properties.kind == 'FAT':
            self.box_defaults, models = properties, self.box_models
        elif properties.kind == 'OTB':
            self.otb_defaults, models = properties, self.box_models
        else:
            self.cabinet_defaults[properties.kind] = properties
            models = self.cabinet_models[properties.kind]
        if properties.model and properties.model not in models:
            models.append(properties.model)

    def create_housing(self, kind):
        if not self.ensure_ready():
            return
        self.stop_tool()
        saved = (
            self.odf_defaults
            if kind == 'ODF'
            else self.patch_defaults
            if kind == 'PATCH_PANEL'
            else self.box_defaults
            if kind == 'FAT'
            else self.otb_defaults
            if kind == 'OTB'
            else self.cabinet_defaults[kind]
        )
        defaults = replace(
            saved,
            row_positions=(),
            mirrored=False,
            requested_used=None,
            splitter_feed=0,
            layout_state='',
            orientation=saved.orientation
            if saved.orientation in (0, 90, 270)
            else 0,
        )
        dialog = HousingDialog(
            self,
            defaults,
            models=self.housing_models(),
            can_attach=bool(self.service.free_cable_ends()),
        )
        if dialog.exec() != enum_compat(QDialog, 'Accepted', 'DialogCode'):
            return
        self.activate('attach_odf' if dialog.attach_to_cable() else 'housing')
        self.housing_placement = (dialog.label.text(), dialog.properties())
        self.housing_layout = self.preview_odf(dialog.properties())
        self.note(
            'Click a fiber near a free cable end.'
            if dialog.attach_to_cable()
            else 'Place the first connector on the canvas. Right-click or '
            'Esc cancels.'
        )

    @staticmethod
    def preview_odf(properties):
        width = max(
            housing_font_metrics(label)[1]
            for label in module_labels(
                properties.rack_module, properties.modules
            )
        )
        return housing_layout(properties, '', width * 1.7)

    def create_closure(self, repeated=False, cable_id=None):
        if not self.ensure_ready():
            return
        if not self.repository.features('cables'):
            self.note(
                'Draw a cable first; a closure encloses its existing fibers.'
            )
            return
        self.stop_tool()
        cable = self.repository.find('cables', cable_id) if cable_id else None
        dialog = ClosureDialog(
            self,
            self.closure_defaults,
            names=(e['name'] for e in self.repository.features('elements')),
            cable_name=cable['name'] if cable is not None else '',
            repeated=repeated,
            continue_series=self.closure_series,
            same_cable=self.closure_same_cable,
        )
        if dialog.exec() != enum_compat(QDialog, 'Accepted', 'DialogCode'):
            return
        self.activate('closure')
        same = dialog.same_cable.isChecked() and cable is not None
        self.closure_placement = {
            'label': dialog.label.text(),
            'properties': dialog.properties(),
            'stage': 'left' if same else 'cable',
            'cable_id': cable_id if same else None,
            'bundle': self.service.closure_bundle(cable_id) if same else None,
            'continue': dialog.continue_series.isChecked(),
            'same': dialog.same_cable.isChecked()
            if repeated
            else self.closure_same_cable,
        }
        self.note(
            'Place the left frame edge along the cable.'
            if same
            else 'Click a fiber on the cable to enclose.'
        )

    def closure_click(self, click):
        state = self.closure_placement
        if state['stage'] == 'cable':
            fiber = self.nearest('fibers', click)
            if fiber is None:
                raise ValueError('Click an individual fiber on the cable.')
            state['cable_id'] = fiber['cable_id']
            state['bundle'] = self.service.closure_bundle(fiber['cable_id'])
            state['stage'] = 'left'
            self.note(
                'Place the left frame edge along the cable. Right-click or '
                'Esc cancels.'
            )
        elif state['stage'] == 'left':
            state['plan'] = self.service.closure_plan(
                state['cable_id'], click, bundle=state['bundle']
            )
            state['stage'] = 'right'
            self.show_closure_preview(state['plan'])
            self.note(
                'Place the right frame edge; Enter uses 8.00 CAD units. '
                'Right-click or Esc cancels.'
            )
        else:
            plan = state['plan']
            x, _ = local_point(
                (click.x(), click.y()),
                (plan['anchor'].x(), plan['anchor'].y()),
                plan['angle'],
            )
            self.finish_closure(max(MIN_CLOSURE_WIDTH, x))

    def show_closure_preview(self, plan):
        state = self.closure_placement
        layout = closure_layout(
            state['properties'], '', plan['width'], plan['top'], plan['bottom']
        )
        self.click_tool.show_housing(layout, plan['anchor'], plan['angle'])

    def finish_closure(self, width=DEFAULT_CLOSURE_WIDTH):
        state = self.closure_placement
        if (
            self.mode != 'closure'
            or state is None
            or state['stage'] != 'right'
        ):
            return
        plan = state['plan']
        identity = self.service.add_closure(
            state['label'],
            state['properties'],
            state['cable_id'],
            plan['anchor'],
            width,
        )
        self.closure_defaults = state['properties']
        self.closure_series, self.closure_same_cable = (
            state['continue'],
            state['same'],
        )
        final = self.service.closure_plan(
            state['cable_id'], plan['anchor'], width, state['bundle']
        )
        self.stop_tool()
        self.selected = ('elements', identity)
        self.refresh()
        shape = self.scene.hit_frames[identity]
        interior_labels = sum(
            shape.intersects(label.geometry())
            for label in self.scene.annotations.getFeatures()
        )
        language = self.workspace_values['language']
        self.note(
            translate('Closure saved; through fibers remain uncut.', language)
            + (
                translate(
                    ' The frame crosses a cable bend; its envelope is '
                    'measured at the left edge.',
                    language,
                )
                if final['crosses_bend']
                else ''
            )
            + (
                translate(
                    ' Existing cable labels lie inside the frame; adjust '
                    'placement or width if needed.',
                    language,
                )
                if interior_labels
                else ''
            )
        )
        if state['continue']:
            self.create_closure(repeated=True, cable_id=state['cable_id'])

    def widen_selected_closure(self):
        if (
            not self.ensure_ready()
            or not self.selected
            or self.selected[0] != 'elements'
            or self.repository.find('closures', self.selected[1]) is None
        ):
            self.note(
                'Select a closure frame, then choose Widen Closure to Cable.'
            )
            return
        identity = self.selected[1]
        self.activate('widen_closure')
        self.pending = identity
        self.note(
            'Click a fiber on the additional cable. Existing frame edges '
            'only expand.'
        )

    def cable_end_at(self, click):
        fiber = self.nearest('fibers', click)
        if fiber is None:
            raise ValueError('Click a fiber near a cable end.')
        path = self.scene.drawn_fibers[fiber['fiber_id']].asPolyline()

        def distance(p):
            return math.hypot(click.x() - p.x(), click.y() - p.y())

        side = 'A' if distance(path[0]) <= distance(path[-1]) else 'B'
        return fiber['cable_id'], side

    def set_dark_canvas(self, enabled):
        if self.scene:
            self.scene.set_dark(enabled)
            self.canvas.setCanvasColor(
                QColor('#212930' if enabled else '#dedede')
            )
            self.canvas.refresh()

    def ensure_ready(self):
        if not self.repository:
            self.note('First choose New, Open, or Workspace → Demo.')
            return False
        try:
            valid = all(layer.isValid() for layer in self.layers.values())
        except RuntimeError:
            valid = False
        if not valid:
            self.note(
                'A workspace layer is unavailable. Reopen the GeoPackage.'
            )
            return False
        if any(layer.isEditable() for layer in self.layers.values()):
            self.note(
                'Commit or roll back QGIS edits before using schematic tools.'
            )
            return False
        return True

    def choose_create_path(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            ui_message(self, 'New schematic workspace'),
            os.path.expanduser('~/FiberQ_Schematic.gpkg'),
            'GeoPackage (*.gpkg)',
            options=FILE_DIALOG_OPTIONS,
        )
        if not path:
            return ''
        if not path.lower().endswith('.gpkg'):
            path += '.gpkg'
        if os.path.exists(path):
            raise ValueError(
                'File already exists. Choose a new name or use Open.'
            )
        return path

    def create_new(self):
        path = self.choose_create_path()
        if path:
            repo = GeoPackageRepository.create(path)
            self.attach(path, repo)

    def create_demo(self, legacy=False, basic=False):
        path = self.choose_create_path()
        if path:
            repo = GeoPackageRepository.create(path)
            service = NetworkService(repo)
            if legacy:
                service.splice_example()
            elif basic:
                service.network_example()
            else:
                service.cad_demo()
            self.attach(path, repo)
            self.note(
                'Splice demo: 8 direct splices and a 1:8 splitter feeding '
                'the third cable.'
                if legacy
                else 'Network demo: ODF, 12F feeder, 12 splices and FDH '
                'cabinet.'
                if basic
                else 'CAD demo: 24F feeder, two 12F branches, FDH/FAT and '
                '1:16 / 1:8 splitters.'
            )

    def open_existing(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            ui_message(self, 'Open schematic workspace'),
            '',
            'GeoPackage (*.gpkg)',
            options=FILE_DIALOG_OPTIONS,
        )
        if path:
            self.attach(path)

    def attach(self, path, repository=None):
        for layer in self.layers.values():
            try:
                if layer.isEditable():
                    self.note(
                        'Commit or roll back QGIS edits before switching '
                        'workspaces.'
                    )
                    return
            except RuntimeError:
                # Allow reopening after a managed layer was removed from QGIS.
                pass
        others = [
            layer_record
            for layer_record in self.project.mapLayers().values()
            if not layer_record.name().startswith('FQSD |')
        ]
        if (
            others
            and QMessageBox.question(
                self,
                'Schematic drawing space',
                'This workspace uses schematic positions and sets the '
                'project CRS to EPSG:3857. '
                'A blank QGIS project is recommended. Continue?',
                MSG_YES | MSG_NO,
                MSG_NO,
            )
            != MSG_YES
        ):
            return
        candidate = repository or GeoPackageRepository.open(path)
        service = NetworkService(candidate)
        service.validate()
        drawing_layouts = exports.drawing_layouts_for_workspace(
            self.project, candidate.path
        )
        for key, layer in candidate.layers.items():
            rendering.style(key, layer)
        scene = CadView()
        scene.rebuild(candidate)
        self.stop_tool()
        self.cable_alignment = None
        for layer in list(self.project.mapLayers().values()):
            if layer.name().startswith('FQSD |'):
                self.project.removeMapLayer(layer.id())
        root = self.project.layerTreeRoot()
        old_group = root.findGroup('FiberQ Schematic Designer')
        if old_group is not None:
            root.removeChildNode(old_group)
        group = root.addGroup('FiberQ Schematic Designer')
        self.repository, self.service = candidate, service
        self.layers, self.path = candidate.layers, candidate.path
        self.scene, self.splice_points = scene, scene.splices
        self.project.setCrs(CRS)
        self.canvas.setDestinationCrs(CRS)
        self.set_dark_canvas(self.dark_canvas.isChecked())
        data_group = group.addGroup('Saved network records')
        for layer in self.layers.values():
            self.project.addMapLayer(layer, False)
            data_group.addLayer(layer)
        data_group.setItemVisibilityChecked(False)
        data_group.setExpanded(False)
        drawing_group = group.insertGroup(0, 'Fiber schematic')
        for layer in scene.layers():
            self.project.addMapLayer(layer, False)
            drawing_group.addLayer(layer)
        if drawing_layouts:
            exports.rebind_drawing_layouts(
                self.project,
                scene.paper_layers(),
                candidate.path,
                drawing_layouts,
            )
        self.selected = None
        self.selection_node = None
        self.details.setText(
            translate(
                'Select an object or a fiber port to inspect it.',
                self.workspace_values['language'],
            )
        )
        migrated = os.path.abspath(path) != os.path.abspath(self.path)
        self.path_label.setText(
            os.path.basename(self.path)
            + (
                translate(
                    ' · migrated copy', self.workspace_values['language']
                )
                if migrated
                else ''
            )
        )
        self.path_label.setToolTip(
            self.path
            + ('\nMigrated copy; original file retained.' if migrated else '')
        )
        self.inspector_path.setText(
            self.path
            + (
                translate(
                    '\nMigrated copy; original file retained.',
                    self.workspace_values['language'],
                )
                if migrated
                else ''
            )
        )
        self.equipment_changed()
        self.route_name.setText(service.unique_name('CABLE', 'cables'))
        self.name_edit.setText(service.unique_name('BLOCK'))
        self.split_name.setText(service.unique_name('SPLITTER'))
        self.apply_workspace_settings()
        self.fit()
        self.note(
            'Opened migrated copy; original retained.'
            if migrated
            else 'Workspace ready. Place equipment on the canvas.'
        )

    def to_schematic(self, point):
        source = self.canvas.mapSettings().destinationCrs()
        return (
            QgsPointXY(point)
            if source == CRS
            else QgsCoordinateTransform(source, CRS, self.project).transform(
                point
            )
        )

    def to_canvas_geometry(self, geometry):
        target = self.canvas.mapSettings().destinationCrs()
        result = QgsGeometry(geometry)
        if target != CRS:
            result.transform(QgsCoordinateTransform(CRS, target, self.project))
        return result

    def tolerance(self, click, pixels=10):
        target = self.canvas.mapSettings().destinationCrs()
        center = (
            QgsPointXY(click)
            if target == CRS
            else QgsCoordinateTransform(CRS, target, self.project).transform(
                click
            )
        )
        other = self.to_schematic(
            pt(
                center.x() + self.canvas.mapUnitsPerPixel() * pixels,
                center.y(),
            )
        )
        return max(
            math.hypot(other.x() - click.x(), other.y() - click.y()), 1e-9
        )

    def get_element(self, click):
        point = QgsGeometry.fromPointXY(click)
        frames = [
            (identity, shape)
            for identity, shape in self.scene.hit_frames.items()
            if shape.intersects(point)
        ]
        if not frames:
            return None
        identity, _ = min(frames, key=lambda item: item[1].area())
        return self.repository.find('elements', identity)

    def nearest(self, key, click, pixels=9):
        geometry = QgsGeometry.fromPointXY(click)
        display = {
            'ports': self.scene.ports,
            'links': self.scene.links,
            'fibers': self.scene.fibers,
        }.get(key)
        features = (
            display.getFeatures() if display else self.repository.features(key)
        )
        matches = [(f.geometry().distance(geometry), f) for f in features]
        if not matches:
            if key == 'cables':
                fiber = self.nearest('fibers', click, pixels)
                return (
                    self.repository.find('cables', fiber['cable_id'])
                    if fiber is not None
                    else None
                )
            return None
        distance, feature = min(matches, key=lambda item: item[0])
        if distance <= self.tolerance(click, pixels):
            return (
                self.repository.find(key, feature[ID_FIELDS[key]])
                if display
                else feature
            )
        if key == 'cables':
            fiber = self.nearest('fibers', click, pixels)
            return (
                self.repository.find('cables', fiber['cable_id'])
                if fiber is not None
                else None
            )
        return None

    def get_nearest_port(self, click):
        return self.nearest('ports', click, 7)

    def clear_selection(self):
        self.selection_node = None
        for layer in self.layers.values():
            layer.removeSelection()
        if self.scene:
            for layer in self.scene.layers():
                layer.removeSelection()
            self.scene.highlight(set())

    def select(self, key, identity, start_node=None):
        def tr(value):
            return translate(value, self.workspace_values['language'])

        self.clear_selection()
        feature = self.repository.find(key, identity)
        self.selected = (key, identity) if feature is not None else None
        if feature is None:
            self.details.setText(
                translate(
                    'No object selected.', self.workspace_values['language']
                )
            )
            self.update_command_state()
            return
        self.layers[key].selectByIds([feature.id()])
        if key == 'annotations':
            self.details.setText(self.annotation_details(feature))
            self.scene.annotation_lines.selectByIds(
                [
                    f.id()
                    for f in self.scene.annotation_lines.getFeatures()
                    if f['annotation_id'] == identity
                ]
            )
            self.update_command_state()
            return
        if key in ('fiber_joints', 'fiber_marks', 'slack'):
            text = self.operation_details(key, feature)
            self.scene.optical_marks.selectByIds(
                [
                    f.id()
                    for f in self.scene.optical_marks.getFeatures()
                    if f['record_id'] == identity
                ]
            )
            self.scene.optical_lines.selectByIds(
                [
                    f.id()
                    for f in self.scene.optical_lines.getFeatures()
                    if f['joint_id'] == identity
                ]
            )
            summary = self.highlight_selected_path(key, feature, start_node)
            self.details.setText(text + ('\n\n' + summary if summary else ''))
            self.update_command_state()
            return
        if key == 'elements':
            frame = self.repository.find('frames', identity)
            self.layers['frames'].selectByIds([frame.id()])
            self.scene.frames.selectByIds(
                [
                    f.id()
                    for f in self.scene.frames.getFeatures()
                    if f['obj_id'] == identity
                ]
            )
            self.scene.ports.selectByIds(
                [
                    f.id()
                    for f in self.scene.ports.getFeatures()
                    if f['obj_id'] == identity
                ]
            )
            free = len(self.service.available_cable_ports(identity))
            text = ui_message(
                self,
                '{} · {}\n{} total ports · {} available for cables',
                feature['name'],
                tr(feature['kind']),
                len(self.service.ports_of(identity)),
                free,
            )
            housing = self.repository.find('housings', identity)
            if housing is not None:
                text += ui_message(
                    self,
                    '\n{} modules × {} ports',
                    housing['module_count'],
                    housing['capacity'],
                )
                text += ui_message(
                    self,
                    '\n{} · {} · {}',
                    housing['connector'],
                    tr(housing['mounting']),
                    tr(
                        'As-built'
                        if housing['status'] == 'as_built'
                        else 'Designed'
                    ),
                )
                if housing['model']:
                    text += '\n' + housing['model']
                for attachment in self.service.housing_attachments(identity):
                    cable = self.repository.find(
                        'cables', attachment['cable_id']
                    )
                    text += ui_message(
                        self,
                        '\n{} / {}: {} terminated, {} hanging',
                        cable['name'],
                        attachment['side'],
                        attachment['used_count'],
                        int(cable['count']) - int(attachment['used_count']),
                    )
            closure = self.repository.find('closures', identity)
            active = self.repository.find('active_equipment', identity)
            if active is not None:
                text += ui_message(
                    self,
                    '\nNative CAD symbol · {} · {}',
                    tr(active['status']),
                    active['address'],
                )
                if feature['kind'] == 'SPLITTER':
                    text += ui_message(
                        self,
                        '\n1:{} · {} dB',
                        feature['count'],
                        active['loss_db'],
                    )
            if closure is not None:
                members = [
                    m
                    for m in self.repository.features('closure_members')
                    if m['closure_id'] == identity
                ]
                text = ui_message(
                    self,
                    '{} · {} closure\n{} fibers · {} splices\nWidth: {:.2f} '
                    'CAD units',
                    feature['name'],
                    closure['closure_type'],
                    sum(m['kind'] == 'fiber' for m in members),
                    sum(
                        m['kind'] in ('link', 'joint', 'mark') for m in members
                    ),
                    float(closure['width']),
                )
                caption = closure_properties(closure).caption_in(
                    self.workspace_values['language']
                )
                if caption:
                    text += '\n' + caption
        elif key == 'cables':
            self.scene.highlight(
                {
                    f['fiber_id']
                    for f in self.repository.features('fibers')
                    if f['cable_id'] == identity
                }
            )
            a, b = (
                self.repository.find('elements', optional(feature[k]))
                for k in ('from_obj', 'to_obj')
            )
            text = ui_message(
                self,
                '{} · {} fibers\n{} → {}\nReal length: {} m (0 = unspecified)',
                feature['name'],
                feature['count'],
                a['name'] if a is not None else tr('Unattached A end'),
                b['name'] if b is not None else tr('Unattached B end'),
                feature['length_m'],
            )
            if feature['cable_version'] == CABLE_VERSION:
                props = properties_from_record(feature)
                text += ui_message(
                    self,
                    '\n{}\n{} · {} · {}',
                    props.description,
                    props.color_code,
                    props.fiber_type,
                    tr(props.status),
                )
                outlet = self.repository.find('outlets', identity)
                if outlet is not None:
                    frame = self.repository.find(
                        'elements', optional(outlet['frame_id'])
                    )
                    text += ui_message(
                        self,
                        '\nFrame outlet · {}\n{}',
                        tr(outlet['mode']),
                        frame['name']
                        if frame is not None
                        else tr('Source frame removed'),
                    )
                    text += tr(
                        '\nUse Join / Group join for splices; Add cable for '
                        'adapters.'
                    )
                else:
                    text += '\n' + tr(props.category)
        elif key == 'fibers':
            cable = self.repository.find('cables', feature['cable_id'])
            a, b = (
                self.repository.find('ports', optional(feature[k]))
                for k in ('a_id', 'b_id')
            )
            color_name = COLOR_NAMES[(int(feature['fiber_no']) - 1) % 12]
            text = ui_message(
                self,
                '{} / fiber {} · {}\n{} ↔ {}',
                cable['name'],
                feature['fiber_no'],
                tr(color_name),
                self.service.port_address(a)
                if a is not None
                else tr('Unattached A end'),
                self.service.port_address(b)
                if b is not None
                else tr('Unattached B end'),
            )
            if cable['cable_version'] == CABLE_VERSION:
                text += ui_message(
                    self,
                    '\nTube {} / fiber {} · {}',
                    feature['tube_no'],
                    feature['tube_fiber'],
                    properties_from_record(cable).color_caption(
                        int(feature['tube_fiber']),
                        self.workspace_values['language'],
                    ),
                )
            sections = [
                s
                for s in self.scene.fiber_sections.values()
                if s['fiber_id'] == identity
            ]
            span = next(
                (s for s in sections if start_node in (s['a_id'], s['b_id'])),
                sections[0],
            )
            override = self.repository.find('fiber_purposes', span['span_id'])
            code = (
                override['purpose']
                if override is not None
                else feature['purpose']
            )
            text += ui_message(
                self,
                '\nPurpose: {} · type: {}',
                tr(PURPOSES[code].title if code in PURPOSES else 'Unknown'),
                tr(feature['fiber_type'] or 'Unknown'),
            )
            if feature['description']:
                text += '\n' + feature['description']
            spans = [
                s
                for s in self.service.fiber_sections()
                if s['fiber_id'] == identity
            ]
            text += ui_message(self, '\n{} continuous section(s)', len(spans))
        elif key == 'ports':
            self.scene.ports.selectByIds(
                [
                    p.id()
                    for p in self.scene.ports.getFeatures()
                    if p['port_id'] == identity
                ]
            )
            for layer in (self.scene.housing_lines, self.scene.housing_dots):
                layer.selectByIds(
                    [
                        f.id()
                        for f in layer.getFeatures()
                        if f['port_id'] == identity
                    ]
                )
            self.repository.find('elements', feature['obj_id'])
            text = ui_message(
                self,
                '{} · {}\nSplice/patch: {}',
                self.service.port_address(feature),
                tr(feature['role']),
                tr(
                    'occupied'
                    if identity in self.service.occupied()
                    else 'free'
                ),
            )
            fibers = [
                f
                for f in self.repository.features('fibers')
                if identity in (f['a_id'], f['b_id'])
            ]
            for fiber in fibers:
                cable = self.repository.find('cables', fiber['cable_id'])
                text += ui_message(
                    self,
                    '\nCable {} · fiber {}',
                    cable['name'],
                    fiber['fiber_no'],
                )
        else:
            self.scene.highlight(set(), {identity})
            ends = [
                self.repository.find('ports', feature[k])
                for k in ('from_id', 'to_id')
            ]
            descriptions = [
                ui_message(
                    self,
                    '{} / {}',
                    self.repository.find('elements', p['obj_id'])['name'],
                    p['label'],
                )
                for p in ends
            ]
            text = ui_message(
                self, '{}: {} ↔ {}', tr(feature['kind']), *descriptions
            )
            self.splice_points.selectByIds(
                [
                    f.id()
                    for f in self.splice_points.getFeatures()
                    if f['link_id'] == identity
                ]
            )
        summary = self.highlight_selected_path(key, feature, start_node)
        self.details.setText(text + ('\n\n' + summary if summary else ''))
        self.update_command_state()

    def select_at(self, click):
        if self.select_operation_at(click):
            return
        point = QgsGeometry.fromPointXY(click)
        markers = [
            (f.geometry().distance(point), f)
            for f in self.splice_points.getFeatures()
        ]
        if markers:
            distance, marker = min(markers, key=lambda item: item[0])
            if distance <= self.tolerance(click, 7):
                self.select('links', marker['link_id'])
                return
        port = self.get_nearest_port(click)
        if port is not None:
            self.select('ports', port['port_id'])
            return
        try:
            fiber = self.fiber_hit(click)
        except ValueError:
            fiber = None
        if fiber is not None:
            self.select(
                'fibers', fiber['fiber_id'], start_node=fiber['a_node']
            )
            return
        element = self.get_element(click)
        if element is not None:
            self.select('elements', element['obj_id'])
            return
        link = self.nearest('links', click)
        if link is not None:
            self.select('links', link['link_id'])
            return
        self.clear_selection()
        self.selected = None
        self.details.setText(
            translate(
                'No object at this position.',
                self.workspace_values['language'],
            )
        )
        self.update_command_state()

    def refresh(self):
        self.join_nodes_cache = None
        if self.workspace_values != self.service.settings():
            self.apply_workspace_settings()
        self.scene.rebuild(self.repository)
        if self.selected:
            self.select(*self.selected, start_node=self.selection_node)
        self.update_command_state()
        self.canvas.refresh()

    def activate(self, mode):
        if not self.ensure_ready():
            return
        if self.canvas.mapTool() is not self.click_tool:
            self.old_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self.click_tool)
        self.mode, self.pending, self.route = mode, None, None
        self.catalog_placement = None
        self.operation_placement = None
        self.closure_placement = None
        self.housing_placement = None
        self.housing_target = None
        self.housing_layout = None
        for key, button in self.tool_buttons.items():
            button.setChecked(key == mode)
        self.canvas.setFocus()
        self.click_tool.clear()
        hints = {
            'frame_outlet': 'Click a housing/module or closure frame to '
            'configure its outlet. Esc cancels.',
            'add_housing_cable': 'Click the housing/module frame, then a '
            'cable end to add.',
            'detach_housing_cable': 'Click a fiber near the cable end to '
            'detach from its housing.',
            'report_route': 'Choose the optical start, then its destination. '
            'Snap highlights exact endpoints.',
            'annotation_place': 'Choose the table/legend insertion point. '
            'Esc cancels.',
            'annotation_move': 'Click the new table/legend insertion point.',
            'active_place': 'Place equipment by its actual optical '
            'connector. Esc cancels.',
            'cut_fiber': 'Click a fiber section or joined connection to cut. '
            'Esc stops.',
            'cut_window': 'Choose two opposite corners to cut fiber sections '
            'and joined connections atomically.',
            'mark_fiber': 'Click fibers to add fusion splice points. Purpose '
            'stays unchanged.',
            'mark_window': 'Choose a window to add splice points to its '
            'fibers.',
            'through_fiber': 'Click a cut fiber arm to restore through '
            'continuity, or a marked fiber to remove its '
            'splice points.',
            'join_fiber': 'Click two free fiber ends, cut ends or optical '
            'ports.',
            'group_joint': 'Click the first source endpoint and first '
            'destination endpoint, then configure numbering.',
            'branch_fiber': 'Click inside the first fiber where diversion '
            'begins, away from its endpoints. Then place its '
            'new end.',
            'slack_place': 'Click to place the slack coils and metre '
            'annotation.',
            'fiber_data': 'Click a fiber to edit its purpose, type and '
            'description.',
            'fiber_purpose': 'Click fibers to set their purpose. Esc stops.',
            'cad_cable': 'Place the first cable fiber. Esc cancels without '
            'saving.',
            'align_cable': 'Click a cable fiber or the first housing '
            'connector to align the next cable.',
            'closure': 'Click a cable fiber, then place the left and right '
            'closure frame edges.',
            'widen_closure': 'Click a fiber on the additional cable to '
            'include in the selected closure.',
            'housing': (
                'Place the first connector. Right-click or Esc cancels.'
            ),
            'attach_odf': 'Click a fiber near a free cable end to attach the '
            'housing.',
            'loose_draw': 'Click the start, bends, and end. Enter or Finish '
            'saves it. Esc cancels.',
            'equipment': 'Click to place equipment. Right-click or Esc '
            'cancels.',
            'cable': 'Click to place a legacy fiber port block.',
            'splitter': 'Click to place a splitter.',
            'draw': 'Click start equipment, add bends in empty space, then '
            'click destination equipment. Esc cancels.',
            'splice': 'Click two free ports. A closure permits internal '
            'splices. Right-click cancels.',
            'auto': 'Click two legacy CABLE port blocks for matching free '
            'fibers.',
            'move': 'Click an element frame, then its new position.',
            'select': 'Click an individual fiber, port, equipment, or '
            'splice. Select cable selects its complete bundle.',
            'trace': 'Click a fiber line or port to trace continuity, '
            'including splitter branches.',
            'vertex': 'Click a cable bend or segment, then the new bend '
            'position. Endpoints stay attached.',
            'remove_vertex': 'Click an interior cable bend to remove it.',
        }
        self.note(hints[mode])

    def canvas_click(self, click, button):
        if button == MOUSE_RIGHT:
            self.stop_tool()
        elif button == MOUSE_LEFT and self.ensure_ready():
            self.guard(lambda: self._canvas_click(click))

    def _canvas_click(self, click):
        if self.mode == 'frame_outlet':
            self.outlet_click(click)
            return
        if self.mode == 'add_housing_cable':
            self.add_housing_cable_click(click)
            return
        if self.mode == 'detach_housing_cable':
            self.detach_housing_cable_click(click)
            return
        if self.mode in (
            'report_route',
            'annotation_place',
            'annotation_move',
        ):
            self.workspace_click(click)
            return
        if self.mode in OPERATION_MODES:
            self.operation_click(click)
            return
        elif self.mode == 'cad_cable' and self.catalog_placement is not None:
            self.catalog_cable_click(click)
            return
        elif self.mode == 'align_cable':
            self.align_cable_at(click)
            return
        elif self.mode == 'closure' and self.closure_placement is not None:
            self.closure_click(click)
            return
        elif self.mode == 'widen_closure':
            fiber = self.nearest('fibers', click)
            if fiber is None:
                raise ValueError('Click a fiber on the additional cable.')
            self.service.widen_closure(self.pending, fiber['cable_id'])
            self.stop_tool()
            self.refresh()
            self.note('Closure expanded; existing user placement is retained.')
            return
        elif self.mode == 'attach_odf' and self.housing_placement is not None:
            label, properties = self.housing_placement
            cable_id, side = self.cable_end_at(click)
            identity = self.service.attach_housing(
                cable_id, side, label, properties, self.housing_target
            )
            self.remember_housing(properties)
            self.stop_tool()
            self.selected = ('elements', identity)
            self.refresh()
            self.fit()
            self.note(
                HOUSINGS[properties.kind][0]
                + ' attached. Unused fiber ends remain available; '
                'connections are saved.'
            )
            return
        elif self.mode == 'housing' and self.housing_placement is not None:
            label, properties = self.housing_placement
            first = not self.repository.features('elements')
            identity = self.service.add_housing(label, properties, click)
            self.remember_housing(properties)
            self.stop_tool()
            self.selected = ('elements', identity)
            if first:
                self.dark_canvas.setChecked(True)
            self.refresh()
            if first:
                self.fit()
            self.note(
                HOUSINGS[properties.kind][0]
                + ' saved. Select its frame and choose Properties to edit it.'
            )
            return
        elif self.mode == 'equipment':
            kind = self.equipment_combo.currentData()
            identity = self.service.add_element(
                kind,
                self.equipment_name.text(),
                self.equipment_count.value(),
                'center',
                click,
            )
            self.equipment_name.setText(self.service.unique_name(kind))
            self.selected = ('elements', identity)
        elif self.mode == 'cable':
            identity = self.service.add_element(
                'CABLE',
                self.name_edit.text(),
                int(self.count_combo.currentText()),
                'left' if self.facing_combo.currentIndex() == 0 else 'right',
                click,
            )
            self.name_edit.setText(self.service.unique_name('BLOCK'))
            self.selected = ('elements', identity)
        elif self.mode == 'splitter':
            count = int(self.split_combo.currentText().split(':')[1])
            identity = self.service.add_element(
                'SPLITTER', self.split_name.text(), count, 'center', click
            )
            self.split_name.setText(self.service.unique_name('SPLITTER'))
            self.selected = ('elements', identity)
        elif self.mode == 'draw':
            self.draw_click(click)
            return
        elif self.mode == 'loose_draw':
            if self.route is None:
                self.route = {
                    'vertices': [QgsPointXY(click)],
                    'name': self.route_name.text(),
                    'count': self.route_count.value(),
                    'length': self.route_length.value(),
                    'group_size': self.route_group.value(),
                }
            else:
                point = self.orthogonal_point(
                    self.route['vertices'][-1], click
                )
                if point != self.route['vertices'][-1]:
                    self.route['vertices'].append(point)
                self.click_tool.show_route(self.route['vertices'])
            self.note(
                'Continue with bends or the end; Enter or Finish saves the '
                'cable.'
            )
            return
        elif self.mode in ('select', 'trace'):
            if self.mode == 'select':
                self.select_at(click)
            else:
                self.trace_at(click)
            return
        elif self.mode == 'splice':
            port = self.get_nearest_port(click)
            if port is None:
                self.note(
                    'Click a numbered port square on the equipment outline.'
                )
                return
            if self.pending is None:
                self.pending = port['port_id']
                self.select('ports', self.pending)
                self.note('First port selected. Click the destination port.')
                return
            identity = self.service.add_link(
                self.repository.find('ports', self.pending),
                port,
                self.link_kind.currentText(),
            )
            self.pending = None
            self.selected = ('links', identity)
        elif self.mode in ('move', 'auto'):
            element = (
                self.get_element(click)
                if self.pending is None or self.mode == 'auto'
                else None
            )
            if self.pending is None:
                if element is None or (
                    self.mode == 'auto' and element['kind'] != 'CABLE'
                ):
                    self.note('Click inside the appropriate element frame.')
                    return
                self.pending = element['obj_id']
                self.select('elements', self.pending)
                if self.mode == 'move':
                    housing = self.repository.find('housings', self.pending)
                    self.note(
                        'Click the new first-connector position.'
                        if housing is not None
                        else 'Click the new center.'
                    )
                else:
                    self.note('Click the second cable block.')
                return
            if self.mode == 'move':
                self.service.move_element(self.pending, click)
            else:
                if element is None:
                    return
                self.service.auto_connect(self.pending, element['obj_id'])
            self.pending = None
        elif self.mode in ('vertex', 'remove_vertex'):
            self.vertex_click(click)
            return
        else:
            return
        self.refresh()
        self.note('Saved. Continue drawing, or use Select / Inspect.')

    def draw_click(self, click):
        element = self.get_element(click)
        if self.route is None:
            if element is None:
                self.note('Start the cable inside an equipment frame.')
                return
            count = self.route_count.value()
            if (
                len(self.service.available_cable_ports(element['obj_id']))
                < count
            ):
                raise ValueError(
                    'The start equipment has too few available cable ports.'
                )
            self.route = {
                'start': element['obj_id'],
                'vertices': [element.geometry().asPoint()],
                'name': self.route_name.text(),
                'count': count,
                'length': self.route_length.value(),
            }
            self.note(
                'Click empty space for bends; click different equipment to '
                'finish. Backspace removes a bend.'
            )
        elif element is not None:
            if element['obj_id'] == self.route['start']:
                self.note('Choose different destination equipment.')
                return
            route = self.route
            identity = self.service.add_cable(
                route['name'],
                route['count'],
                route['start'],
                element['obj_id'],
                route['vertices'] + [element.geometry().asPoint()],
                route['length'],
            )
            self.route = None
            self.click_tool.clear()
            self.selected = ('cables', identity)
            self.route_name.setText(
                self.service.unique_name('CABLE', 'cables')
            )
            self.refresh()
            self.note(
                'Cable and fiber endpoints saved. Click equipment to start '
                'another cable.'
            )
        elif click != self.route['vertices'][-1]:
            self.route['vertices'].append(QgsPointXY(click))
            self.click_tool.show_route(self.route['vertices'])

    def canvas_move(self, click):
        if self.mode == 'add_housing_cable':
            self.preview_add_housing_cable(click)
        elif self.mode in (
            'report_route',
            'annotation_place',
            'annotation_move',
        ):
            self.workspace_move(click)
        elif self.mode in OPERATION_MODES:
            self.operation_move(click)
        elif self.mode == 'cad_cable' and self.catalog_placement is not None:
            self.preview_catalog_cable(click)
        elif self.mode == 'closure' and self.closure_placement is not None:
            state = self.closure_placement
            if state['stage'] == 'left':
                self.show_closure_preview(
                    self.service.closure_plan(
                        state['cable_id'], click, bundle=state['bundle']
                    )
                )
            elif state['stage'] == 'right':
                plan = state['plan']
                x, _ = local_point(
                    (click.x(), click.y()),
                    (plan['anchor'].x(), plan['anchor'].y()),
                    plan['angle'],
                )
                self.show_closure_preview(
                    dict(plan, width=max(MIN_CLOSURE_WIDTH, x))
                )
        elif self.mode == 'attach_odf' and self.housing_placement is not None:
            try:
                cable_id, side = self.cable_end_at(click)
                properties, at = self.service.housing_attachment_plan(
                    cable_id, side, self.housing_placement[1]
                )
                self.click_tool.show_housing(
                    self.preview_odf(properties),
                    at,
                    properties.orientation,
                    properties.mirrored,
                )
            except ValueError:
                self.click_tool.clear()
        elif self.mode == 'housing' and self.housing_placement is not None:
            label, properties = self.housing_placement
            self.click_tool.show_housing(
                self.housing_layout or self.preview_odf(properties),
                click,
                properties.orientation,
                properties.mirrored,
            )
        elif self.route is not None:
            target = (
                self.get_element(click) if self.mode != 'loose_draw' else None
            )
            target = (
                (target.geometry().asPoint() if target is not None else click)
                if self.mode != 'loose_draw'
                else self.orthogonal_point(self.route['vertices'][-1], click)
            )
            self.click_tool.show_route(self.route['vertices'] + [target])
        elif self.mode == 'vertex' and self.pending is not None:
            vertices = list(self.pending['vertices'])
            vertices[self.pending['index']] = QgsPointXY(click)
            self.click_tool.show_route(vertices)

    @staticmethod
    def orthogonal_point(start, target):
        return (
            pt(target.x(), start.y())
            if abs(target.x() - start.x()) >= abs(target.y() - start.y())
            else pt(start.x(), target.y())
        )

    def finish_route(self):
        if self.mode == 'cad_cable':
            return self.finish_catalog_cable()
        if (
            self.mode != 'loose_draw'
            or self.route is None
            or len(self.route['vertices']) < 2
        ):
            self.note(
                'Draw at least two cable points, then press Enter or Finish.'
            )
            return
        route = self.route
        identity = self.service.add_loose_cable(
            route['name'],
            route['count'],
            route['vertices'],
            route['length'],
            route['group_size'],
        )
        self.stop_tool()
        self.selected = ('cables', identity)
        self.route_name.setText(self.service.unique_name('CABLE', 'cables'))
        self.refresh()
        self.fit()
        self.note(
            'Cable saved with unattached fiber ends. Create an ODF or patch '
            'panel and choose A cable to terminate it.'
        )

    def remove_last_bend(self):
        if self.route is not None and len(self.route['vertices']) > 1:
            self.route['vertices'].pop()
            if self.mode == 'cad_cable':
                self.catalog_placement['endpoints'].pop('B', None)
                self.catalog_placement['modules'].pop('B', None)
            self.click_tool.show_route(self.route['vertices'])
            self.update_command_state()

    def vertex_click(self, click):
        if self.pending is not None and self.mode == 'vertex':
            vertices = self.pending['vertices']
            vertices[self.pending['index']] = QgsPointXY(click)
            self.service.edit_vertices(self.pending['cable_id'], vertices)
            self.pending = None
            self.click_tool.clear()
            self.refresh()
            self.note('Cable bend saved.')
            return
        cable = self.nearest('cables', click)
        if cable is None:
            self.note('Click closer to a cable route.')
            return
        vertices = cable.geometry().asPolyline()
        near = min(
            range(len(vertices)),
            key=lambda i: math.hypot(
                vertices[i].x() - click.x(), vertices[i].y() - click.y()
            ),
        )
        distance = math.hypot(
            vertices[near].x() - click.x(), vertices[near].y() - click.y()
        )
        if distance <= self.tolerance(click, 9):
            if near in (0, len(vertices) - 1):
                self.note(
                    'Cable endpoints stay attached. Move the equipment '
                    'instead.'
                )
                return
            if self.mode == 'remove_vertex':
                del vertices[near]
                self.service.edit_vertices(cable['cable_id'], vertices)
                self.refresh()
                self.note('Cable bend removed.')
                return
            index = near
        elif self.mode == 'remove_vertex':
            self.note('Click directly on an interior cable bend.')
            return
        else:
            index = cable.geometry().closestSegmentWithContext(click)[2]
            vertices.insert(index, QgsPointXY(click))
        self.pending = {
            'cable_id': cable['cable_id'],
            'index': index,
            'vertices': vertices,
        }
        self.select('cables', cable['cable_id'])
        self.note('Click the new bend position. Esc cancels without saving.')

    def trace_at(self, click):
        return self.trace_optical_at(click)

    def select_cable(self):
        if self.selected and self.selected[0] == 'fibers':
            fiber = self.repository.find('fibers', self.selected[1])
            if fiber is not None:
                self.select('cables', fiber['cable_id'])

    def edit_selected(self):
        if not self.ensure_ready() or not self.selected:
            return
        key, identity = self.selected
        if key == 'fibers':
            self.select_cable()
            key, identity = self.selected
        if key not in ('elements', 'cables'):
            self.note('Select equipment or a cable route to edit properties.')
            return
        feature = self.repository.find(key, identity)
        if feature is None:
            return
        self.stop_tool()
        if key == 'cables' and feature['cable_version'] == CABLE_VERSION:
            if self.repository.find('outlets', identity) is not None:
                return self.edit_outlet_selected(feature)
            return self.edit_catalog_selected(feature)
        active = (
            self.repository.find('active_equipment', identity)
            if key == 'elements'
            else None
        )
        if active is not None:
            return self.edit_active_dialog(feature, active)
        closure = (
            self.repository.find('closures', identity)
            if key == 'elements'
            else None
        )
        if closure is not None:
            dialog = ClosureDialog(
                self,
                closure_properties(closure),
                feature['name'],
                names=(
                    e['name']
                    for e in self.repository.features('elements')
                    if e['obj_id'] != identity
                ),
                width=float(closure['width']),
                language=self.service.drawing_language('closures', identity),
            )
            if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
                self.service.edit_closure(
                    identity,
                    dialog.label.text(),
                    dialog.properties(),
                    dialog.width.value(),
                )
                self.refresh()
                self.note('Closure properties saved; fibers remain unchanged.')
            return
        housing = (
            self.repository.find('housings', identity)
            if key == 'elements'
            else None
        )
        if housing is not None:
            attached = any(
                identity in (optional(c['from_obj']), optional(c['to_obj']))
                and c['layout_mode'] == 'rows'
                for c in self.repository.features('cables')
            )
            dialog = HousingDialog(
                self,
                odf_properties(feature, housing),
                feature['name'],
                self.housing_models(),
                can_attach=bool(self.service.free_cable_ends())
                and not attached,
                attached=attached,
                lock_kind=True,
                language=self.service.drawing_language('housings', identity),
            )
            if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
                if dialog.attach_to_cable():
                    self.activate('attach_odf')
                    self.housing_target = identity
                    self.housing_placement = (
                        dialog.label.text(),
                        dialog.properties(),
                    )
                    return
                self.service.edit_housing(
                    identity, dialog.label.text(), dialog.properties()
                )
                self.refresh()
                self.note('Housing properties saved.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(ui_message(self, 'Edit {}', feature['name']))
        form = QFormLayout(dialog)
        name = QLineEdit(feature['name'])
        form.addRow('Name', name)
        count = QSpinBox()
        count.setRange(1, 576)
        count.setValue(int(feature['count']))
        length = QDoubleSpinBox()
        length.setRange(0, 10000000)
        if key == 'elements':
            form.addRow('Ports / splitter outputs', count)
        else:
            length.setValue(float(feature['length_m']))
            form.addRow('Real length (m; 0 = unspecified)', length)
            form.addRow(
                QLabel(
                    ui_message(
                        self,
                        '{} fibers; disconnect and redraw to change fiber '
                        'count.',
                        feature['count'],
                    )
                )
            )
        buttons = QDialogButtonBox(
            enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            if key == 'elements':
                self.service.edit_element(identity, name.text(), count.value())
            else:
                self.service.edit_cable(identity, name.text(), length.value())
            self.refresh()
            self.note('Properties saved.')

    def delete_selected(self):
        if not self.ensure_ready() or not self.selected:
            return
        key, identity = self.selected
        if key == 'annotations':
            self.stop_tool()
            self.service.delete_annotation(identity)
            self.selected = None
            self.refresh()
            self.note('Drawing annotation deleted. Undo restores it.')
            return
        if key in ('fiber_joints', 'fiber_marks', 'slack'):
            self.stop_tool()
            self.service.delete_optical(key, identity)
            self.selected = None
            self.refresh()
            self.note(
                'Deleted optical annotation/connection. Undo restores it.'
            )
            return
        if key == 'fibers':
            self.select_cable()
            key, identity = self.selected
        if key not in ('elements', 'cables', 'links'):
            self.note(
                'Select an element, cable route, or connection to delete.'
            )
            return
        language = self.workspace_values['language']
        message = translate(
            'Delete the entire cable and ALL its fibers '
            'and attached splices? ',
            language,
        )
        if key == 'elements':
            attached = [
                c
                for c in self.repository.features('cables')
                if identity in (optional(c['from_obj']), optional(c['to_obj']))
            ]
            retained = sum(c['layout_mode'] == 'rows' for c in attached)
            removed = len(attached) - retained
            message = translate(
                'Delete this element, its connectors and splice/patch links? ',
                language,
            )
            if removed:
                message += ui_message(
                    self,
                    '{} attached cable(s) and their fibers will be removed. ',
                    removed,
                )
            if retained:
                message += ui_message(
                    self,
                    '{} cable(s) will stay in the drawing with this end '
                    'disconnected. ',
                    retained,
                )
            if self.repository.find('closures', identity) is not None:
                message = translate(
                    'Delete this closure enclosure? Its fibers, cables and '
                    'splices are retained. ',
                    language,
                )
        if (
            key != 'links'
            and QMessageBox.question(
                self,
                'Delete selected object',
                message
                + translate(
                    'Equipment at the other cable end is retained. You can '
                    'Undo this operation.',
                    language,
                ),
                MSG_YES | MSG_NO,
                MSG_NO,
            )
            != MSG_YES
        ):
            return
        self.stop_tool()
        {
            'elements': self.service.delete_element,
            'cables': self.service.delete_cable,
            'links': self.service.delete_link,
        }[key](identity)
        self.refresh()
        self.note('Deleted. Undo is available for this session.')

    def undo(self):
        if self.ensure_ready():
            self.stop_tool()
            name = self.repository.undo()
            self.refresh()
            self.note(
                translate('Undid: ', self.workspace_values['language'])
                + translate(name, self.workspace_values['language'])
                if name
                else 'Nothing to undo.'
            )

    def redo(self):
        if self.ensure_ready():
            self.stop_tool()
            name = self.repository.redo()
            self.refresh()
            self.note(
                translate('Redid: ', self.workspace_values['language'])
                + translate(name, self.workspace_values['language'])
                if name
                else 'Nothing to redo.'
            )

    def import_csv(self):
        if not self.ensure_ready():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            ui_message(self, 'Import splice links'),
            '',
            'CSV (*.csv)',
            options=FILE_DIALOG_OPTIONS,
        )
        if path:
            self.stop_tool()
            count, skipped = import_connections(self.service, path)
            self.refresh()
            self.note(
                ui_message(
                    self,
                    'Imported {}; skipped {}. {}',
                    count,
                    len(skipped),
                    '; '.join(skipped[:3]),
                )
            )

    def stop_tool(self):
        self.pending, self.route, self.mode = None, None, None
        self.catalog_placement = None
        self.operation_placement = None
        self.annotation_placement = None
        self.join_nodes_cache = None
        self.housing_placement = None
        self.housing_target = None
        self.housing_layout = None
        self.closure_placement = None
        for button in self.tool_buttons.values():
            button.setChecked(False)
        if self.disposed:
            return
        self.click_tool.clear()
        if self.canvas.mapTool() is self.click_tool:
            try:
                if (
                    self.old_tool is not None
                    and self.old_tool is not self.click_tool
                ):
                    self.canvas.setMapTool(self.old_tool)
                else:
                    self.iface.actionPan().trigger()
            except RuntimeError:
                self.iface.actionPan().trigger()
        self.note('Drawing stopped. Select a command to continue.')

    def diagram_extent(self):
        rectangles = (
            [
                layer.extent()
                for layer in self.scene.layers()
                if layer.featureCount()
            ]
            if self.scene
            else []
        )
        if self.scene and not self.scene.text_bounds.isEmpty():
            rectangles.append(self.scene.text_bounds)
        if not rectangles:
            return QgsRectangle(-80, -95, 310, 260)
        extent = QgsRectangle(rectangles[0])
        for rectangle in rectangles[1:]:
            extent.combineExtentWith(rectangle)
        extent.grow(35)
        return extent

    def fit(self):
        if self.repository:
            self.canvas.setExtent(
                self.to_canvas_geometry(
                    QgsGeometry.fromRect(self.diagram_extent())
                ).boundingBox()
            )
            self.canvas.refresh()

    def save_project(self):
        if self.ensure_ready():
            path = os.path.splitext(self.path)[0] + '.qgz'
            self.project.setFileName(path)
            if not self.project.write():
                raise RuntimeError('Cannot save QGIS project: ' + path)
            self.note(
                ui_message(
                    self,
                    'Saved {}. GeoPackage changes are already saved.',
                    path,
                )
            )

    def drawing_layers(self, paper=False):
        return self.scene.paper_layers() if paper else self.scene.layers()

    def make_layout(self):
        return exports.make_layout(
            self.project,
            self.drawing_layers(paper=True),
            self.path,
            self.diagram_extent(),
            self.workspace_values['language'],
        )

    def export_as(self, extension):
        if not self.ensure_ready():
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            'Export ' + extension.upper(),
            os.path.splitext(self.path)[0] + '.' + extension,
            extension.upper() + ' (*.' + extension + ')',
            options=FILE_DIALOG_OPTIONS,
        )
        if not path:
            return
        if not path.lower().endswith('.' + extension):
            path += '.' + extension
        if extension == 'dxf':
            exports.export_dxf(
                self.canvas,
                self.drawing_layers(paper=True),
                self.diagram_extent(),
                path,
            )
        else:
            layout = self.make_layout()
            exports.export_layout(layout, path, extension)
            manager = self.project.layoutManager()
            for old in list(manager.layouts()):
                if old.name() == layout.name():
                    manager.removeLayout(old)
            manager.addLayout(layout)
            self.note(ui_message(self, 'Exported {}', path))

    def closeEvent(self, event):
        self.stop_tool()
        if not self.disposed:
            self.inspector.hide()
        super().closeEvent(event)

    def dispose(self):
        if self.disposed:
            return
        self.stop_tool()
        from qgis.PyQt.QtWidgets import QApplication

        QApplication.instance().removeEventFilter(self.language_filter)
        self.click_tool.dispose()
        self.click_tool.deleteLater()
        self.disposed = True
        self.hide()
        self.inspector.hide()
        self.iface.removeDockWidget(self.inspector)
        self.inspector.setObjectName('')
        self.inspector.deleteLater()
        self.canvas.setCanvasColor(self.previous_canvas_color)


def launch(iface):
    existing = iface.mainWindow().findChild(QDockWidget, OBJECT_NAME)
    if existing is not None:
        existing.stop_tool()
        iface.removeDockWidget(existing)
        if hasattr(existing, 'dispose'):
            existing.dispose()
        existing.close()
        existing.setObjectName('')
        existing.deleteLater()
    dock = FiberQSchematicDesigner(iface)
    dock.show()
    return dock
