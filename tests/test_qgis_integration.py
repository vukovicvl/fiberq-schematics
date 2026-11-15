"""Real QGIS/GDAL integration in disposable workspaces; no plugin required."""

import gc
from dataclasses import asdict, replace
from collections import Counter
from contextlib import closing, nullcontext
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from osgeo import ogr
    from qgis.core import (Qgis, QgsApplication, QgsGeometry, QgsProject,
                           QgsRectangle, QgsVectorFileWriter, QgsVectorLayer)
    from qgis.gui import QgsLayerTreeMapCanvasBridge, QgsMapCanvas, QgsMapToolPan
    from qgis.PyQt.QtCore import QPoint, QTimer, QT_VERSION_STR, PYQT_VERSION_STR
    from qgis.PyQt.QtTest import QTest
    from qgis.PyQt.QtWidgets import QApplication, QDialog, QDockWidget, QLineEdit, QMainWindow, QSpinBox
    try:
        from qgis.PyQt.QtGui import QAction
    except ImportError:
        from qgis.PyQt.QtWidgets import QAction
except ImportError as error:
    raise unittest.SkipTest('Use the QGIS Python environment: ' + str(error))

from fiberq_designer.compat import MOUSE_LEFT, MOUSE_RIGHT, MSG_YES
from fiberq_designer.csv_io import import_connections
from fiberq_designer import exports
from fiberq_designer.geometry import odf_properties, optional, pt
from fiberq_designer.housing_catalog import HousingProperties, OdfProperties
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.schema import LAYERS, LEGACY_KEYS
from fiberq_designer.services import NetworkService
from fiberq_designer.cad_view import CadView
from workspace_dialog_checks import WorkspaceDialogChecks
from desktop_review_checks import DesktopReviewChecks
from purpose_path_checks import PurposePathChecks


def remove_operation_tables(dataset):
    from fiberq_designer.schema import KEY_VERSIONS
    for key, version in KEY_VERSIONS.items():
        if version >= 8:
            index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == 'fqsd_' + key)
            if dataset.DeleteLayer(index) != 0:
                raise RuntimeError('Cannot strip future operation table from legacy fixture.')


class TestIface:
    def __init__(self):
        self.window = QMainWindow()
        self.window.resize(1400, 1000)
        self.canvas = QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.bridge = QgsLayerTreeMapCanvasBridge(QgsProject.instance().layerTreeRoot(), self.canvas)
        self.pan = QAction('Pan', self.window)
        self.pan_tool = QgsMapToolPan(self.canvas)
        self.pan.triggered.connect(lambda: self.canvas.setMapTool(self.pan_tool))

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas

    def addDockWidget(self, area, dock):
        self.window.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        self.window.removeDockWidget(dock)

    def actionPan(self):
        return self.pan


class QgisTests(PurposePathChecks, DesktopReviewChecks, WorkspaceDialogChecks, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = tempfile.TemporaryDirectory(prefix='fqsd-qgis-profile-')
        cls.app = QgsApplication.instance() or QgsApplication([], True, cls.profile.name)
        cls.app.initQgis()
        print('\nRuntime: QGIS {}, Qt {}, PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))

    @classmethod
    def tearDownClass(cls):
        QgsProject.instance().clear()
        cls.app.exitQgis()
        cls.profile.cleanup()

    def setUp(self):
        QgsProject.instance().clear()
        self.directory = tempfile.TemporaryDirectory(prefix='fqsd-test-')
        self.path = str(Path(self.directory.name) / 'design.gpkg')
        self.repo = GeoPackageRepository.create(self.path)
        self.service = NetworkService(self.repo)
        self.iface = None
        self.dock = None

    def tearDown(self):
        if self.dock:
            self.dock.dispose()
            self.dock.close()
        if self.iface:
            self.iface.window.close()
        QgsProject.instance().clear()
        self.dock = self.iface = self.service = self.repo = None
        QgsApplication.processEvents()
        gc.collect()
        self.directory.cleanup()

    def equipment(self, kind, name, count, x, y=0):
        return self.service.add_element(kind, name, count, 'center', pt(x, y))

    def ports(self, identity):
        return self.service.ports_of(identity)

    def build_route(self):
        a = self.equipment('ODF', 'ODF-01', 12, 0)
        b = self.equipment('CLOSURE', 'CLOSURE-01', 24, 250)
        c = self.equipment('CABINET', 'CABINET-01', 12, 500)
        first = self.service.add_cable('FEEDER', 12, a, b, [pt(0, 0), pt(120, 100), pt(250, 0)], 250)
        second = self.service.add_cable('DISTRIBUTION', 12, b, c, [pt(250, 0), pt(500, 0)])
        return a, b, c, first, second

    def open_dock(self):
        from fiberq_designer.app import launch
        self.iface = TestIface()
        self.dock = launch(self.iface)
        self.dock.attach(self.path, self.repo)
        self.iface.window.show()
        self.iface.canvas.setExtent(QgsRectangle(-100, -150, 650, 250))
        QgsApplication.processEvents()
        self.iface.canvas.refresh()
        return self.dock

    def test_demo_rejections_movement_and_history(self):
        self.service.demo()
        self.assertEqual({'frames': 4, 'elements': 4, 'links': 17, 'ports': 45,
                          'cables': 0, 'fibers': 0, 'housings': 0, 'modules': 0,
                          'fiber_ends': 0, 'contained_splitters': 0, 'closures': 0, 'closure_members': 0,
                          'active_equipment': 0, 'fiber_nodes': 0, 'fiber_cuts': 0, 'fiber_joints': 0,
                          'fiber_marks': 0, 'fiber_routes': 0, 'slack': 0, 'workspace_settings': 0, 'annotations': 0, 'housing_cables': 0, 'outlets': 0, 'drawing_languages': 0, 'fiber_purposes': 0}, self.service.validate())
        state = self.repo.snapshot()
        a, b = self.repo.features('elements')[:2]
        with self.assertRaises(ValueError):
            self.service.add_link(self.ports(a['obj_id'])[0], self.ports(b['obj_id'])[0])
        self.assertEqual(state, self.repo.snapshot())
        self.service.move_element(a['obj_id'], pt(-100, 300))
        self.service.validate()
        self.repo.undo()
        self.assertEqual(state, self.repo.snapshot())
        self.repo.redo()
        self.service.validate()

    def test_cables_splices_trace_move_reopen_delete_and_undo(self):
        a, b, c, first, second = self.build_route()
        self.assertEqual(0, len(self.service.available_cable_ports(b)))
        ports = self.ports(b)
        splice = self.service.add_link(ports[0], ports[12])
        self.assertIn(self.ports(c)[0]['port_id'], self.service.trace(self.ports(a)[0]['port_id']))
        ids = {f['fiber_id'] for f in self.repo.features('fibers')}
        bend = self.repo.find('cables', first).geometry().asPolyline()[1]
        self.service.move_element(b, pt(300, 50))
        self.assertEqual(bend, self.repo.find('cables', first).geometry().asPolyline()[1])
        self.assertEqual(pt(300, 50), self.repo.find('cables', first).geometry().asPolyline()[-1])
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(ids, {f['fiber_id'] for f in reopened.features('fibers')})
        before_delete = self.repo.snapshot()
        self.service.delete_element(b)
        self.assertEqual(0, len(self.repo.features('cables')))
        self.assertEqual(0, len(self.repo.features('links')))
        self.assertEqual(2, len(self.repo.features('elements')))
        self.repo.undo()
        self.assertEqual(before_delete, self.repo.snapshot())
        self.assertIsNotNone(self.repo.find('links', splice))
        self.service.validate()

    def test_capacity_and_invalid_cables_do_not_change_workspace(self):
        a, b, c, first, second = self.build_route()
        before = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.add_cable('EXTRA', 1, a, b, [pt(0, 0), pt(250, 0)])
        with self.assertRaises(ValueError):
            self.service.edit_element(b, 'CLOSURE-01', 12)
        with self.assertRaises(ValueError):
            self.service.add_cable('LOOP', 1, a, a, [pt(0, 0), pt(10, 0)])
        self.assertEqual(before, self.repo.snapshot())
        port_ids = [p['port_id'] for p in self.ports(a)]
        self.service.edit_element(a, 'ODF Renamed', 24)
        self.assertEqual(port_ids, [p['port_id'] for p in self.ports(a)[:12]])
        self.service.validate()

    def test_failed_write_rolls_back_all_layers_and_preserves_history(self):
        a = self.equipment('ODF', 'ODF', 12, 0)
        before = self.repo.snapshot()
        history = len(self.repo.history)
        original = self.repo.update
        def fail(key, *args, **kwargs):
            if key == 'ports':
                raise RuntimeError('Injected write failure')
            return original(key, *args, **kwargs)
        with patch.object(self.repo, 'update', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected write failure'):
                self.service.move_element(a, pt(100, 100))
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual(history, len(self.repo.history))
        self.service.validate()

    def test_failed_fiber_insert_rolls_back_route_and_all_endpoints(self):
        a = self.equipment('ODF', 'A', 12, 0)
        b = self.equipment('CABINET', 'B', 12, 250)
        state = self.repo.snapshot()
        insert = self.repo.insert
        def fail(key, attrs, *args, **kwargs):
            if key == 'fibers' and attrs['fiber_no'] == 3:
                raise RuntimeError('Injected fiber failure')
            return insert(key, attrs, *args, **kwargs)
        with patch.object(self.repo, 'insert', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected fiber failure'):
                self.service.add_cable('ROUTE', 12, a, b, [pt(0, 0), pt(250, 0)])
        self.assertEqual(state, self.repo.snapshot())
        self.service.validate()

    def test_splitter_continuity_across_multiple_cable_routes(self):
        source = self.equipment('OLT', 'OLT', 1, -200)
        splitter = self.equipment('SPLITTER', 'SPLITTER', 2, 0)
        first = self.equipment('ONT', 'ONT-A', 1, 250, 100)
        second = self.equipment('ONT', 'ONT-B', 1, 250, -100)
        for name, a, b, vertices in [('FEED', source, splitter, [pt(-200, 0), pt(0, 0)]),
                                    ('DROP-A', splitter, first, [pt(0, 0), pt(250, 100)]),
                                    ('DROP-B', splitter, second, [pt(0, 0), pt(250, -100)])]:
            self.service.add_cable(name, 1, a, b, vertices)
        trace = self.service.trace(self.ports(source)[0]['port_id'])
        self.assertIn(self.ports(first)[0]['port_id'], trace)
        self.assertIn(self.ports(second)[0]['port_id'], trace)
        self.service.validate()

    def test_cable_property_route_edits_and_deletion_restore_exactly(self):
        a, b, c, first, second = self.build_route()
        self.service.add_link(self.ports(b)[0], self.ports(b)[12])
        self.service.edit_cable(first, 'RENAMED', 400)
        self.service.edit_vertices(first, [pt(-999, -999), pt(100, 200), pt(-999, -999)])
        cable = self.repo.find('cables', first)
        self.assertEqual(400, cable['length_m'])
        self.assertEqual(pt(0, 0), cable.geometry().asPolyline()[0])
        self.assertEqual(pt(250, 0), cable.geometry().asPolyline()[-1])
        self.service.validate()
        state = self.repo.snapshot()
        self.service.delete_cable(first)
        self.assertEqual(1, len(self.repo.features('cables')))
        self.assertEqual(0, len(self.repo.features('links')))
        self.assertEqual(12, len(self.service.available_cable_ports(a)))
        self.repo.undo()
        self.assertEqual(state, self.repo.snapshot())
        self.service.validate()

    def test_csv_aliases_occupancy_and_ambiguous_names(self):
        a = self.service.add_element('CABLE', 'LEFT_12F', 12, 'left', pt(0, 0))
        b = self.service.add_element('CABLE', 'RIGHT_12F', 12, 'right', pt(250, 0))
        example = Path(__file__).resolve().parents[1] / 'example_connections.csv'
        self.assertEqual((4, []), import_connections(self.service, example))
        count, skipped = import_connections(self.service, example)
        self.assertEqual((0, 4), (count, len(skipped)))
        self.service.validate()
        with self.repo.command('Simulate legacy duplicate name'):
            self.repo.update('elements', self.repo.find('elements', b).id(), {'name': 'LEFT_12F'})
        sample = Path(self.directory.name) / 'ambiguous.csv'
        sample.write_text('from_element,from_port,to_element,to_port\nLEFT_12F,1,LEFT_12F,2\n')
        count, skipped = import_connections(self.service, sample)
        self.assertEqual(0, count)
        self.assertIn('ambiguous', skipped[0])

    def test_auto_matching_skips_occupied_and_handles_different_capacities(self):
        a = self.service.add_element('CABLE', 'A', 12, 'left', pt(0, 0))
        b = self.service.add_element('CABLE', 'B', 24, 'right', pt(250, 0))
        self.service.add_link(self.ports(a)[0], self.ports(b)[3])
        self.assertEqual(10, self.service.auto_connect(a, b))
        self.service.validate()

    def test_csv_provider_failure_rolls_back_the_whole_batch(self):
        self.service.add_element('CABLE', 'LEFT_12F', 12, 'left', pt(0, 0))
        self.service.add_element('CABLE', 'RIGHT_12F', 12, 'right', pt(250, 0))
        example = Path(__file__).resolve().parents[1] / 'example_connections.csv'
        insert = self.repo.insert
        writes = []
        def fail(key, *args, **kwargs):
            if key == 'links':
                writes.append(1)
                if len(writes) == 2:
                    raise RuntimeError('Injected CSV write failure')
            return insert(key, *args, **kwargs)
        state = self.repo.snapshot()
        with patch.object(self.repo, 'insert', side_effect=fail):
            with self.assertRaises(RuntimeError):
                import_connections(self.service, example)
        self.assertEqual(state, self.repo.snapshot())

    def test_legacy_migration_retains_original_and_ids(self):
        self.service.demo()
        legacy = Path(self.directory.name) / 'legacy.gpkg'
        for index, key in enumerate(LEGACY_KEYS):
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.layerName = 'fqsd_' + key
            options.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if index == 0
                                            else QgsVectorFileWriter.CreateOrOverwriteLayer)
            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                self.repo.layers[key], str(legacy), QgsProject.instance().transformContext(), options)
            self.assertEqual(QgsVectorFileWriter.NoError, result[0])
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(str(legacy))
        self.assertNotEqual(str(legacy), migrated.path)
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual({f['port_id'] for f in self.repo.features('ports')},
                         {f['port_id'] for f in migrated.features('ports')})
        NetworkService(migrated).validate()

    def test_corrupt_schema_and_references_rejected_before_attach(self):
        dock = self.open_dock()
        active_ids = set(QgsProject.instance().mapLayers())
        bad = Path(self.directory.name) / 'bad.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(bad))) as destination:
            source.backup(destination)
        with closing(sqlite3.connect(str(bad))) as db, db:
            db.execute('DROP TABLE fqsd_fibers')
        with self.assertRaises(ValueError):
            dock.attach(str(bad))
        self.assertEqual(active_ids, set(QgsProject.instance().mapLayers()))
        self.assertEqual(self.path, dock.path)

    def test_removed_layer_can_be_reopened(self):
        self.equipment('ODF', 'ODF', 12, 0)
        dock = self.open_dock()
        QgsProject.instance().removeMapLayer(dock.layers['ports'].id())
        self.assertFalse(dock.ensure_ready())
        dock.attach(self.path)
        self.assertTrue(dock.ensure_ready())
        dock.service.validate()

    def test_properties_dialog_and_delete_undo(self):
        a = self.equipment('ODF', 'ODF', 12, 0)
        dock = self.open_dock()
        dock.select('elements', a)
        def accept_properties():
            dialog = QApplication.activeModalWidget()
            dialog.findChild(QLineEdit).setText('EDITED ODF')
            dialog.findChild(QSpinBox).setValue(24)
            dialog.accept()
        QTimer.singleShot(0, accept_properties)
        dock.edit_selected()
        self.assertEqual('EDITED ODF', self.repo.find('elements', a)['name'])
        self.assertEqual(24, len(self.ports(a)))
        with patch('fiberq_designer.app.QMessageBox.question', return_value=MSG_YES):
            dock.delete_selected()
        self.assertEqual(0, len(self.repo.features('elements')))
        dock.undo()
        self.assertEqual(1, len(self.repo.features('elements')))
        self.service.validate()

    def test_legacy_console_launch_outside_source_directory(self):
        self.open_dock()
        launcher = Path(__file__).resolve().parents[1] / 'fiberq_schematic_designer.py'
        previous = os.getcwd()
        namespace = {'iface': self.iface}
        try:
            os.chdir(self.directory.name)
            with patch('qgis.PyQt.QtWidgets.QFileDialog.getOpenFileName', return_value=(str(launcher), '')) as picker:
                exec(launcher.read_text(), namespace)
                self.dock = namespace['FIBERQ_SCHEMATIC_DESIGNER']
                self.assertEqual(1, picker.call_count)
                self.assertEqual(1, len(self.iface.window.findChildren(QDockWidget, 'FiberQSchematicDesignerPhase1')))
                exec(launcher.read_text(), namespace)
                self.dock = namespace['FIBERQ_SCHEMATIC_DESIGNER']
                self.assertEqual(1, picker.call_count)
                self.assertEqual(1, len(self.iface.window.findChildren(QDockWidget, 'FiberQSchematicDesignerPhase1')))
        finally:
            os.chdir(previous)

    def test_canvas_drawing_cancellation_bend_editing_and_splice_selection(self):
        a = self.equipment('ODF', 'A', 12, 0)
        b = self.equipment('CABINET', 'B', 12, 500)
        dock = self.open_dock()
        dock.activate('draw')
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        dock.canvas_click(pt(200, 150), MOUSE_LEFT)
        dock.canvas_move(pt(300, 120))
        dock.canvas_click(pt(300, 120), MOUSE_RIGHT)
        self.assertEqual(0, self.repo.layers['cables'].featureCount())
        dock.activate('draw')
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        dock.canvas_click(pt(200, 150), MOUSE_LEFT)
        dock.canvas_click(pt(500, 0), MOUSE_LEFT)
        self.assertEqual(1, self.repo.layers['cables'].featureCount())
        cable = self.repo.features('cables')[0]
        dock.activate('vertex')
        dock.canvas_click(pt(200, 150), MOUSE_LEFT)
        dock.canvas_click(pt(250, 170), MOUSE_LEFT)
        self.assertEqual(pt(250, 170), self.repo.find('cables', cable['cable_id']).geometry().asPolyline()[1])
        link = self.service.add_link(self.ports(a)[0], self.ports(b)[0])
        dock.refresh()
        marker = next(dock.splice_points.getFeatures())
        dock.activate('select')
        dock.canvas_click(marker.geometry().asPoint(), MOUSE_LEFT)
        self.assertEqual(('links', link), dock.selected)
        self.service.validate()

    def test_qt_mouse_event_places_equipment(self):
        dock = self.open_dock()
        dock.activate('equipment')
        p = self.iface.canvas.mapSettings().mapToPixel().transform(100, 0)
        QTest.mouseClick(self.iface.canvas.viewport(), MOUSE_LEFT, pos=QPoint(round(p.x()), round(p.y())))
        QgsApplication.processEvents()
        self.assertEqual(1, len(self.repo.features('elements')))
        self.service.validate()

    def test_twelve_fibers_are_separate_lines_with_exact_port_endpoints(self):
        a = self.equipment('ODF', 'A', 12, 0)
        b = self.equipment('CABINET', 'B', 12, 700)
        cable = self.service.add_cable('CABLE', 12, a, b, [pt(0, 0), pt(700, 0)])
        before = self.repo.snapshot()
        view = CadView()
        view.rebuild(self.repo)
        self.assertEqual(12, view.fibers.featureCount())
        records = {f['fiber_id']: f for f in self.repo.features('fibers')}
        shapes = set()
        for line in view.fibers.getFeatures():
            points = line.geometry().asPolyline()
            record = records[line['fiber_id']]
            self.assertEqual(view.positions[record['a_id']], points[0])
            self.assertEqual(view.positions[record['b_id']], points[-1])
            self.assertGreater(line.geometry().length(), 0)
            shapes.add(bytes(line.geometry().asWkb()))
        self.assertEqual(12, len(shapes))
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual({cable}, {f['cable_id'] for f in view.fibers.getFeatures()})

    def test_expanded_fibers_select_and_trace_individually(self):
        a = self.equipment('ODF', 'A', 12, 0)
        b = self.equipment('CABINET', 'B', 12, 700)
        cable = self.service.add_cable('CABLE', 12, a, b, [pt(0, 0), pt(700, 0)])
        dock = self.open_dock()
        fiber = next(f for f in self.repo.features('fibers') if f['fiber_no'] == 7)
        dock.activate('select')
        dock.canvas_click(pt(350, -4), MOUSE_LEFT)
        self.assertEqual(('fibers', fiber['fiber_id']), dock.selected)
        active = {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']}
        self.assertEqual({fiber['fiber_id']}, active)
        dock.activate('trace')
        dock.canvas_click(pt(350, -4), MOUSE_LEFT)
        self.assertEqual(active, {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
        dock.select('fibers', fiber['fiber_id'])
        dock.select_cable()
        self.assertEqual(('cables', cable), dock.selected)
        self.assertEqual(12, sum(f['active'] for f in dock.scene.fibers.getFeatures()))

    def test_spliced_path_single_click_from_fiber_or_joint_highlights_whole_chain(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        first = self.service.add_catalog_cable('K1', CableProperties(tubes=2), [pt(0, 0), pt(400, 0)])
        second = self.service.add_catalog_cable('K2', CableProperties(tubes=2, fibers_per_tube=6), [pt(560, 0), pt(960, 0)])
        third = self.service.add_catalog_cable('K3', CableProperties(), [pt(1120, 0), pt(1520, 0)])
        self.service.add_closure('1', ClosureProperties(), first, pt(360, 0), 24)
        a, b, c = (self.service.ends_of(cable, side) for cable, side in ((first, 'B'), (second, 'A'), (third, 'A')))
        joints = self.service.join_group([e['end_id'] for e in a[:12]], [e['end_id'] for e in b], 'splice')
        second_b = self.service.ends_of(second, 'B')
        further = self.service.join_nodes(second_b[0]['end_id'], c[0]['end_id'], 'through')
        before = self.repo.snapshot()
        dock = self.open_dock()
        expected = {a[0]['fiber_id'], b[0]['fiber_id'], c[0]['fiber_id']}
        expected_joints = {joints[0], further}
        for point, key, identity in ((pt(200, 0), 'fibers', a[0]['fiber_id']),
                                     (pt(380, 0), 'fibers', a[0]['fiber_id']),
                                     (pt(480, 0), 'fiber_joints', joints[0]),
                                     (pt(1300, 0), 'fibers', c[0]['fiber_id'])):
            with self.subTest(clicked=key, point=point):
                dock.select_at(point)
                self.assertEqual((key, identity), dock.selected)
                self.assertEqual(expected, {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
                self.assertEqual(expected_joints, {j['joint_id'] for j in dock.scene.optical_lines.getFeatures() if j['active']})
                self.assertFalse(dock.scene.optical_lines.selectedFeatureIds(), 'A local yellow selection must not mask the continuous red path.')
                self.assertEqual(before, self.repo.snapshot())
        self.assertEqual([c[0]['fiber_id']], dock.selected_fibers())
        dock.refresh()
        self.assertEqual(expected, {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
        dock.trace_at(pt(1040, 0))
        self.assertEqual(expected, {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
        self.assertEqual(expected_joints, {j['joint_id'] for j in dock.scene.optical_lines.getFeatures() if j['active']})
        self.service.validate()

    def test_spliced_path_single_click_stops_at_open_cut_and_keeps_clicked_section(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('A', CableProperties(), [pt(0, 0), pt(600, 0)])
        second = self.service.add_catalog_cable('B', CableProperties(), [pt(800, 0), pt(1400, 0)])
        fiber = self.service.ends_of(first, 'A')[0]['fiber_id']
        destination = self.service.ends_of(second, 'A')[0]
        joint = self.service.join_nodes(self.service.ends_of(first, 'B')[0]['end_id'], destination['end_id'])
        self.service.cut_fibers([fiber], pt(300, 0))
        cut = self.repo.features('fiber_cuts')[0]
        dock = self.open_dock()
        before = self.repo.snapshot()
        dock.select_at(pt(450, 0))
        self.assertEqual({cut['right_id'], destination['fiber_id']}, {f['span_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
        self.assertEqual({joint}, {j['joint_id'] for j in dock.scene.optical_lines.getFeatures() if j['active']})
        dock.refresh()
        self.assertEqual({cut['right_id'], destination['fiber_id']}, {f['span_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
        dock.select_at(pt(150, 0))
        self.assertEqual({fiber}, {f['span_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
        self.assertFalse(any(j['active'] for j in dock.scene.optical_lines.getFeatures()))
        self.assertEqual(before, self.repo.snapshot())
        self.service.add_splice_marks([fiber], pt(450, 0))
        dock.refresh()
        mark = self.repo.features('fiber_marks')[0]
        dock.select_at(mark.geometry().asPoint())
        self.assertEqual(('fiber_marks', mark['mark_id']), dock.selected)
        self.assertEqual({cut['right_id'], destination['fiber_id']}, {f['span_id'] for f in dock.scene.fibers.getFeatures() if f['active']})

    def test_spliced_path_group_join_dialog_all_kinds_keep_pairs_separate(self):
        from fiberq_designer.cable_catalog import CableProperties
        dock = self.open_dock()
        for index, kind in enumerate(('splice', 'through', 'patch')):
            with self.subTest(kind=kind):
                base = index * 2000
                first = self.service.add_catalog_cable('A-' + kind, CableProperties(tubes=2), [pt(base, 0), pt(base + 400, 0)])
                second = self.service.add_catalog_cable('B-' + kind, CableProperties(tubes=2, fibers_per_tube=6), [pt(base + 560, 0), pt(base + 960, 0)])
                a, b = self.service.ends_of(first, 'B'), self.service.ends_of(second, 'A')
                dock.refresh()
                dock.command_actions['group_joint'].trigger()
                dock.canvas_click(a[0].geometry().asPoint(), MOUSE_LEFT)
                def accept():
                    dock.group_dialog.count.setValue(12)
                    dock.group_dialog.kind.setCurrentIndex(dock.group_dialog.kind.findData(kind))
                    dock.group_dialog.accept()
                QTimer.singleShot(0, accept)
                dock.canvas_click(b[0].geometry().asPoint(), MOUSE_LEFT)
                dock.command_actions['select'].trigger()
                joints = [j for j in self.repo.features('fiber_joints') if j['a_id'] in {e['end_id'] for e in a}]
                self.assertEqual(12, len(joints))
                self.assertEqual({kind}, {j['kind'] for j in joints})
                nodes = self.service.optical_nodes()
                for joint in joints:
                    path = joint.geometry().asPolyline()
                    self.assertLess(path[0].distance(nodes[joint['a_id']]['point']), 1e-6)
                    self.assertLess(path[-1].distance(nodes[joint['b_id']]['point']), 1e-6)
                before = self.repo.snapshot()
                for number in (0, 6, 11):
                    row = b[number].geometry().asPoint().y()
                    dock.canvas_click(pt(base + 750, row), MOUSE_LEFT)
                    self.assertEqual({a[number]['fiber_id'], b[number]['fiber_id']},
                        {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
                    self.assertEqual({j['joint_id'] for j in joints if j['a_id'] == a[number]['end_id']},
                        {j['joint_id'] for j in dock.scene.optical_lines.getFeatures() if j['active']})
                self.assertEqual(before, self.repo.snapshot())
        self.service.validate()

    def test_spliced_path_legacy_splice_and_port_selection_follow_connected_fibers(self):
        self.service.network_demo()
        dock = self.open_dock()
        fiber = self.repo.features('fibers')[0]
        found = self.service.trace(fiber['a_id'])
        expected = {f['fiber_id'] for f in self.repo.features('fibers') if f['a_id'] in found and f['b_id'] in found}
        links = {l['link_id'] for l in self.repo.features('links') if l['from_id'] in found and l['to_id'] in found}
        self.assertEqual(2, len(expected))
        self.assertEqual(1, len(links))
        before = self.repo.snapshot()
        for key, identity in (('ports', fiber['a_id']), ('links', next(iter(links)))):
            with self.subTest(clicked=key):
                dock.select(key, identity)
                self.assertEqual((key, identity), dock.selected)
                self.assertEqual(expected, {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']})
                self.assertEqual(links, {l['link_id'] for l in dock.scene.links.getFeatures() if l['active']})
                self.assertEqual(before, self.repo.snapshot())

    def test_expanded_view_follows_moves_undo_and_reopen(self):
        a, b, c, first, second = self.build_route()
        dock = self.open_dock()
        original = {f['fiber_id']: bytes(f.geometry().asWkb()) for f in dock.scene.fibers.getFeatures()}
        self.service.move_element(b, pt(320, 80))
        dock.refresh()
        moved = {f['fiber_id']: bytes(f.geometry().asWkb()) for f in dock.scene.fibers.getFeatures()}
        self.assertEqual(set(original), set(moved))
        self.assertTrue(any(original[k] != moved[k] for k in original))
        dock.undo()
        self.assertEqual(original, {f['fiber_id']: bytes(f.geometry().asWkb()) for f in dock.scene.fibers.getFeatures()})
        dock.attach(self.path)
        self.assertEqual(original, {f['fiber_id']: bytes(f.geometry().asWkb()) for f in dock.scene.fibers.getFeatures()})

    def test_expanded_geometry_handles_vertical_reverse_and_bent_routes(self):
        cases = [([pt(0, 0), pt(700, 0)], 0),
                 ([pt(700, 300), pt(0, 300)], 300),
                 ([pt(0, 600), pt(0, 1100)], 600),
                 ([pt(0, 1300), pt(200, 1500), pt(500, 1500), pt(700, 1300)], 1300)]
        for index, (route, _) in enumerate(cases):
            with self.subTest(case=index):
                a = self.equipment('ODF', 'A' + str(index), 12, route[0].x(), route[0].y())
                b = self.equipment('CABINET', 'B' + str(index), 12, route[-1].x(), route[-1].y())
                cable = self.service.add_cable('C' + str(index), 12, a, b, route)
                view = CadView()
                view.rebuild(self.repo)
                drawn = [f for f in view.fibers.getFeatures() if f['cable_id'] == cable]
                self.assertEqual(12, len(drawn))
                self.assertTrue(all(f.geometry().isGeosValid() and f.geometry().length() > 0 for f in drawn))

    def test_new_cable_uses_closure_ports_facing_its_neighbor(self):
        a = self.equipment('CLOSURE', 'A', 24, 0)
        b = self.equipment('CLOSURE', 'B', 24, 500)
        self.service.add_cable('CABLE', 1, a, b, [pt(0, 0), pt(500, 0)])
        fiber = self.repo.features('fibers')[0]
        self.assertGreater(self.repo.find('ports', fiber['a_id'])['dx'], 0)
        self.assertLess(self.repo.find('ports', fiber['b_id'])['dx'], 0)
        self.service.validate()

    def test_cad_demo_has_expanded_feeder_and_branch_fibers(self):
        dock = self.open_dock()
        path = str(Path(self.directory.name) / 'cad-demo.gpkg')
        with patch.object(dock, 'choose_create_path', return_value=path):
            dock.create_demo()
        counts = dock.service.validate()
        self.assertEqual((6, 3, 48, 26), tuple(counts[k] for k in ('elements', 'cables', 'fibers', 'links')))
        self.assertEqual(48, dock.scene.fibers.featureCount())
        cables = dock.repository.features('cables')
        self.assertEqual([12, 12, 24], sorted(int(c['count']) for c in cables))
        self.assertEqual(24, dock.scene.splices.featureCount())
        self.assertIn(dock.scene.fibers, dock.drawing_layers())
        self.assertNotIn(dock.layers['cables'], dock.drawing_layers())
        splitter = next(e for e in dock.repository.features('elements') if e['kind'] == 'SPLITTER')
        self.assertEqual(splitter['obj_id'], dock.get_element(splitter.geometry().asPoint())['obj_id'])
        self.iface.canvas.setLayers(dock.drawing_layers())
        dock.fit()
        self.iface.canvas.refresh()
        QgsApplication.processEvents()
        self.iface.canvas.waitWhileRendering()
        labels = self.iface.canvas.labelingResults().allLabels()
        self.assertTrue(any('Blue' in p.labelText for p in labels))
        self.assertTrue(any('FDH' in p.labelText for p in labels))

    def test_editing_controls_remain_visible_in_short_dock(self):
        dock = self.open_dock()
        self.iface.window.resize(1200, 780)
        QgsApplication.processEvents()
        dock.ribbon_tabs.setCurrentIndex(1)
        QgsApplication.processEvents()
        self.assertTrue(dock.tool_buttons['select'].isVisible())
        self.assertTrue(dock.tool_buttons['move'].isVisible())
        self.assertLess(dock.tool_buttons['move'].mapTo(dock, QPoint()).y(), 210)
        self.assertLessEqual(dock.height(), 236)

    def test_ribbon_workspace_enablement_and_native_actions(self):
        from fiberq_designer.app import launch
        from fiberq_designer.ribbon import DOCK_TOP
        self.iface = TestIface()
        self.dock = dock = launch(self.iface)
        self.iface.window.show()
        QgsApplication.processEvents()
        self.assertEqual(DOCK_TOP, self.iface.window.dockWidgetArea(dock))
        self.assertFalse(dock.isFloating())
        self.assertFalse(dock.inspector.isVisible())
        self.assertGreater(dock.path_label.x(), dock.mode_label.geometry().right())
        self.assertEqual(['Draw', 'Edit', 'Fibers', 'Workspace', 'Reports'],
                         [dock.ribbon_tabs.tabText(i) for i in range(dock.ribbon_tabs.count())])
        for key in ('new', 'open', 'demo', 'inspector'):
            self.assertTrue(dock.command_actions[key].isEnabled(), key)
        for key in dock.workspace_commands:
            self.assertFalse(dock.command_actions[key].isEnabled(), key)
        dock.attach(self.path, self.repo)
        for key in ('ODF', 'PATCH_PANEL', 'FDH', 'MDU', 'FAT', 'OTB', 'closure', 'loose_draw'):
            self.assertTrue(dock.command_actions[key].isEnabled(), key)
            self.assertFalse(dock.command_actions[key].icon().isNull(), key)
        self.assertFalse(dock.command_actions['properties'].isEnabled())
        self.assertFalse(dock.command_actions['undo'].isEnabled())
        self.assertIs(dock.quick_buttons['select'].defaultAction(), dock.tool_buttons['select'].defaultAction())
        self.assertTrue(all(not dialog.isVisible() for dialog in dock.parameter_dialogs.values()))
        self.assertEqual(['CABINET', 'OLT', 'ONT'],
                         [dock.equipment_combo.itemData(i) for i in range(dock.equipment_combo.count())])

    def test_ribbon_cable_dialog_cancel_finish_and_history(self):
        dock = self.open_dock()
        initial = self.repo.snapshot()
        dialog = dock.parameter_dialogs['cable']
        QTimer.singleShot(0, dialog.reject)
        dock.command_actions['manual_cable'].trigger()
        self.assertIsNone(dock.mode)
        self.assertEqual(initial, self.repo.snapshot())
        dock.route_name.setText('RIBBON-24')
        dock.route_count.setValue(24)
        dock.route_group.setValue(6)
        QTimer.singleShot(0, dialog.accept)
        dock.command_actions['manual_cable'].trigger()
        self.assertEqual('loose_draw', dock.mode)
        self.assertTrue(dock.command_actions['manual_cable'].isChecked())
        self.assertFalse(dock.command_actions['finish'].isEnabled())
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        dock.canvas_click(pt(500, 0), MOUSE_LEFT)
        self.assertTrue(dock.command_actions['finish'].isEnabled())
        dock.remove_last_bend()
        self.assertFalse(dock.command_actions['finish'].isEnabled())
        dock.canvas_click(pt(500, 0), MOUSE_LEFT)
        dock.command_actions['finish'].trigger()
        self.assertIsNone(dock.mode)
        self.assertFalse(dock.command_actions['finish'].isEnabled())
        self.assertFalse(dock.command_actions['manual_cable'].isChecked())
        cable = self.repo.features('cables')[0]
        self.assertEqual(('RIBBON-24', 24, 6), (cable['name'], cable['count'], cable['group_size']))
        self.assertEqual(24, len(self.repo.features('fibers')))
        created = self.repo.snapshot()
        self.assertTrue(dock.command_actions['undo'].isEnabled())
        dock.command_actions['undo'].trigger()
        self.assertEqual(initial, self.repo.snapshot())
        self.assertTrue(dock.command_actions['redo'].isEnabled())
        dock.command_actions['redo'].trigger()
        self.assertEqual(created, self.repo.snapshot())
        dock.select('cables', cable['cable_id'])
        def rename_cable():
            properties = QApplication.activeModalWidget()
            properties.findChild(QLineEdit).setText('RENAMED-24')
            properties.accept()
        QTimer.singleShot(0, rename_cable)
        dock.command_actions['properties'].trigger()
        self.assertEqual('RENAMED-24', self.repo.find('cables', cable['cable_id'])['name'])
        dock.command_actions['undo'].trigger()
        self.assertEqual(created, self.repo.snapshot())
        self.service.validate()

    def test_ribbon_equipment_and_splitter_presets_place_after_acceptance(self):
        dock = self.open_dock()
        initial = self.repo.snapshot()
        equipment = dock.parameter_dialogs['equipment']
        QTimer.singleShot(0, equipment.accept)
        dock.tool_buttons['equipment'].menu().actions()[1].trigger()
        self.assertEqual('ONT', dock.equipment_combo.currentData())
        self.assertEqual(initial, self.repo.snapshot())
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        ont = self.repo.features('elements')[0]
        self.assertEqual(('ONT', 1), (ont['kind'], len(self.service.ports_of(ont['obj_id']))))
        after_ont = self.repo.snapshot()
        splitter = dock.parameter_dialogs['splitter']
        QTimer.singleShot(0, splitter.reject)
        dock.configure_parameter('splitter', ratio=4)
        self.assertEqual('1:4', dock.split_combo.currentText())
        self.assertEqual(after_ont, self.repo.snapshot())
        self.assertIsNone(dock.mode)
        QTimer.singleShot(0, splitter.accept)
        dock.command_actions['legacy_splitter'].trigger()
        self.assertEqual(after_ont, self.repo.snapshot())
        dock.canvas_click(pt(300, 0), MOUSE_LEFT)
        placed = next(e for e in self.repo.features('elements') if e['kind'] == 'SPLITTER')
        self.assertEqual(5, len(self.service.ports_of(placed['obj_id'])))
        self.service.validate()

    def test_ribbon_selection_connections_and_external_pan(self):
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(1000, 0)])
        closure = self.service.add_closure('', ClosureProperties(), cable, pt(300, 0))
        dock = self.open_dock()
        dock.command_actions['select'].trigger()
        self.assertTrue(dock.quick_buttons['select'].isChecked())
        self.assertTrue(dock.command_actions['stop'].isEnabled())
        fiber = self.repo.features('fibers')[0]
        dock.select('fibers', fiber['fiber_id'])
        self.assertTrue(dock.command_actions['properties'].isEnabled())
        self.assertTrue(dock.command_actions['select_cable'].isEnabled())
        dock.command_actions['select_cable'].trigger()
        self.assertEqual(('cables', cable), dock.selected)
        self.assertFalse(dock.command_actions['select_cable'].isEnabled())
        dock.select('elements', closure)
        self.assertTrue(dock.command_actions['widen_closure'].isEnabled())
        dock.command_actions['patch'].trigger()
        self.assertTrue(dock.command_actions['patch'].isChecked())
        self.assertFalse(dock.command_actions['splice'].isChecked())
        self.assertEqual('patch', dock.link_kind.currentText())
        dock.command_actions['splice'].trigger()
        self.assertTrue(dock.command_actions['splice'].isChecked())
        self.assertFalse(dock.command_actions['patch'].isChecked())
        self.iface.actionPan().trigger()
        self.assertIsNone(dock.mode)
        self.assertFalse(dock.command_actions['splice'].isChecked())
        self.assertFalse(dock.command_actions['stop'].isEnabled())

    def test_ribbon_collapse_overflow_inspector_and_reopen(self):
        identity = self.service.add_odf('', OdfProperties(), pt(0, 0))
        dock = self.open_dock()
        before = self.repo.snapshot()
        dock.command_actions['inspector'].trigger()
        QgsApplication.processEvents()
        self.assertTrue(dock.inspector.isVisible())
        dock.select('elements', identity)
        self.assertIn('OR 1', dock.details.text())
        dock.setFloating(True)
        dock.resize(560, 236)
        QgsApplication.processEvents()
        scroll = dock.ribbon_pages['Draw'][0]
        self.assertGreater(scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(0, scroll.verticalScrollBar().maximum())
        scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
        QgsApplication.processEvents()
        equipment = dock.tool_buttons['equipment']
        self.assertTrue(equipment.isVisible())
        point = equipment.mapTo(scroll.viewport(), equipment.rect().center())
        self.assertTrue(scroll.viewport().rect().contains(point))
        dock.toggle_ribbon(True)
        QgsApplication.processEvents()
        self.assertLessEqual(dock.height(), 98)
        self.assertFalse(dock.ribbon_stack.isVisible())
        dock.ribbon_tabs.setCurrentIndex(1)
        self.assertTrue(dock.ribbon_stack.isVisible())
        self.assertFalse(dock.ribbon_collapsed)
        dock.command_actions['select'].trigger()
        dock.close()
        self.assertIsNone(dock.mode)
        self.assertFalse(dock.inspector.isVisible())
        self.assertFalse(dock.disposed)
        dock.show()
        dock.command_actions['select'].trigger()
        self.assertIs(self.iface.canvas.mapTool(), dock.click_tool)
        dock.select('elements', identity)
        self.assertEqual(('elements', identity), dock.selected)
        self.assertEqual(before, self.repo.snapshot())

    def test_new_network_demo_and_workspace_switch(self):
        dock = self.open_dock()
        target = str(Path(self.directory.name) / 'network-demo.gpkg')
        with patch.object(dock, 'choose_create_path', return_value=target):
            dock.create_demo(basic=True)
        result = dock.service.validate()
        self.assertEqual((3, 2, 24, 12), tuple(result[k] for k in ('elements', 'cables', 'fibers', 'fiber_joints')))
        self.assertEqual(2, result['housings'])
        self.assertEqual(1, result['closures'])
        self.assertEqual(0, result['links'])
        closure = dock.repository.features('closures')[0]
        self.assertTrue(all(joint['closure_id'] == closure['obj_id'] for joint in dock.repository.features('fiber_joints')))
        odf = next(e for e in dock.repository.features('elements') if e['kind'] == 'ODF')
        cabinet = next(e for e in dock.repository.features('elements') if e['kind'] == 'FDH')
        self.assertIn(dock.service.ports_of(cabinet['obj_id'])[-1]['port_id'],
                      dock.service.trace(dock.service.ports_of(odf['obj_id'])[0]['port_id']))
        dock.attach(self.path)
        self.assertEqual(0, len(dock.repository.features('elements')))
        self.assertTrue(Path(target).exists())

    def test_splice_example_preserves_connections_without_crossing_unrelated_fibers(self):
        dock = self.open_dock()
        target = str(Path(self.directory.name) / 'splice-example.gpkg')
        with patch.object(dock, 'choose_create_path', return_value=target):
            dock.create_demo(legacy=True)
        service, repo = dock.service, dock.repository
        counts = service.validate()
        self.assertEqual((1, 3, 36, 17), tuple(counts[key] for key in ('elements', 'cables', 'fibers', 'fiber_joints')))
        self.assertEqual(1, counts['active_equipment'])
        self.assertEqual(0, counts['links'])
        cables = {c['name']: c['cable_id'] for c in repo.features('cables')}
        first, second, third = (cables['CABLE-' + name + ' / 12F'] for name in ('A', 'B', 'C'))
        a, b, c = (service.ends_of(identity, 'A') for identity in (first, second, third))
        for number in range(8):
            self.assertIn(b[number]['end_id'], service.trace(a[number]['end_id']))
        split_path = service.trace(a[8]['end_id'])
        self.assertTrue(all(end['end_id'] in split_path for end in c[:8]))
        self.assertTrue(all(end['end_id'] not in split_path for end in c[8:]))
        self.assertTrue(all(end['end_id'] not in split_path for end in b))
        nodes = service.optical_nodes()
        for joint in repo.features('fiber_joints'):
            connected = {nodes[joint[key]]['fiber_id'] for key in ('a_id', 'b_id')}
            for identity, geometry in dock.scene.drawn_fibers.items():
                if identity not in connected:
                    self.assertFalse(joint.geometry().intersects(geometry), 'A demo splice crosses an unrelated fiber.')
        before = repo.snapshot()
        reopened = GeoPackageRepository.open(target)
        self.assertEqual(before, reopened.snapshot())
        repo.undo()
        self.assertTrue(all(count == 0 for count in service.validate().values()))
        repo.redo()
        self.assertEqual(before, repo.snapshot())

    def test_native_demo_examples_rollback_as_one_command(self):
        for method in ('network_example', 'splice_example'):
            with self.subTest(example=method):
                before = self.repo.snapshot()
                insert = self.repo.insert
                writes = []
                def fail(key, *args, **kwargs):
                    if key == 'fiber_joints':
                        writes.append(True)
                        if len(writes) == 2:
                            raise RuntimeError('Injected demo splice failure')
                    return insert(key, *args, **kwargs)
                with patch.object(self.repo, 'insert', side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, 'Injected demo splice failure'):
                        getattr(self.service, method)()
                self.assertEqual(before, self.repo.snapshot())
                self.assertFalse(self.repo.history)

    def test_exports_project_save_and_repeated_launcher(self):
        self.build_route()
        self.service.add_link(self.ports(self.repo.features('elements')[0]['obj_id'])[0],
                              self.ports(self.repo.features('elements')[1]['obj_id'])[0])
        dock = self.open_dock()
        extent = dock.diagram_extent()
        for extension in ('pdf', 'svg'):
            path = str(Path(self.directory.name) / ('drawing.' + extension))
            exports.export_layout(dock.make_layout(), path, extension)
            self.assertGreater(Path(path).stat().st_size, 100)
        dxf = str(Path(self.directory.name) / 'drawing.dxf')
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(), extent, dxf)
        self.assertGreater(Path(dxf).stat().st_size, 100)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(dxf)
            shapes = []
            annotation_types = []
            for feature in dataset.GetLayer(0):
                name = feature.GetField('Layer')
                geometry = feature.GetGeometryRef()
                if name.endswith('fiber lines'):
                    shape = QgsGeometry()
                    shape.fromWkb(bytes(geometry.ExportToWkb()))
                    shapes.append(tuple((round(p.x(), 6), round(p.y(), 6)) for p in shape.asPolyline()))
                elif name.endswith('equipment outlines'):
                    self.assertNotEqual('POLYGON', geometry.GetGeometryName())
                elif name.endswith('fiber and equipment labels'):
                    annotation_types.append(geometry.GetGeometryName())
            expected = {tuple((round(p.x(), 6), round(p.y(), 6)) for p in f.geometry().asPolyline())
                        for f in dock.scene.fibers.getFeatures()}
            self.assertTrue(expected.issubset(set(shapes)))
            self.assertTrue(annotation_types)
            self.assertEqual({'POINT'}, set(annotation_types))
            dataset = None
        dock.save_project()
        self.assertTrue(Path(self.path).with_suffix('.qgz').is_file())
        old_ids = set(QgsProject.instance().mapLayers())
        namespace = {'iface': self.iface}
        launcher = Path(__file__).resolve().parents[1] / 'fiberq_schematic_designer.py'
        exec(compile(launcher.read_text(), str(launcher), 'exec'), namespace)
        self.dock = namespace['FIBERQ_SCHEMATIC_DESIGNER']
        self.dock.attach(self.path)
        self.assertEqual(1, len(self.iface.window.findChildren(QDockWidget, 'FiberQSchematicDesignerPhase1')))
        self.assertEqual(1, len(self.iface.window.findChildren(QDockWidget, 'FiberQSchematicDesignerInspector')))
        self.assertEqual(len(old_ids), len(QgsProject.instance().mapLayers()))
        self.assertEqual(len(self.dock.repository.layers) + len(self.dock.scene.layers()),
                         len(QgsProject.instance().mapLayers()))
        self.dock.service.validate()

    def test_odf_housing_exact_geometry_saved_properties_and_history(self):
        identity = self.service.add_odf('', OdfProperties(), pt(0, 0))
        element = self.repo.find('elements', identity)
        self.assertEqual('OR 1', element['name'])
        ports = self.ports(identity)
        port_ids = [p['port_id'] for p in ports]
        self.assertEqual([str(n) for n in range(1, 25)], [p['label'] for p in ports])
        self.assertAlmostEqual(-88, ports[11].geometry().asPoint().y())
        self.assertAlmostEqual(-104, ports[12].geometry().asPoint().y())
        self.assertAlmostEqual(-192, ports[23].geometry().asPoint().y())
        bounds = self.repo.find('frames', identity).geometry().boundingBox()
        self.assertAlmostEqual(116, bounds.width())
        self.assertAlmostEqual(224, bounds.height())
        view = CadView()
        view.rebuild(self.repo)
        self.assertEqual(73, view.housing_lines.featureCount())
        self.assertEqual(24, view.housing_dots.featureCount())
        self.assertEqual(['OR 1'] + [str(n) for n in range(1, 25)],
                         [f['text'] for f in view.housing_labels.getFeatures()])
        for port in ports:
            self.assertEqual(port.geometry().asPoint(), view.positions[port['port_id']])
        other = self.equipment('CABINET', 'CAB-1', 12, 300)
        link = self.service.add_link(ports[0], self.ports(other)[0], 'patch')
        module_id = self.repo.find('housings', identity)['module_id']
        before = self.repo.snapshot()
        properties = OdfProperties(model='Test ODF', connector='LC/APC', rack_module='R1, M1',
                                   orientation=90, status='as_built', requested_used=12)
        self.service.edit_odf(identity, '2', properties)
        self.service.move_element(identity, pt(100, 200))
        self.service.validate()
        self.assertEqual(port_ids, [p['port_id'] for p in self.ports(identity)])
        self.assertEqual(module_id, self.repo.find('housings', identity)['module_id'])
        self.assertEqual(self.ports(identity)[0].geometry().asPoint(),
                         self.repo.find('links', link).geometry().asPolyline()[0])
        self.repo.undo()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.repo.redo()
        self.repo.redo()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(asdict(properties), asdict(odf_properties(reopened.find('elements', identity),
                                                                  reopened.find('housings', identity))))
        self.service.delete_element(identity)
        self.assertEqual([], self.repo.features('housings'))
        self.assertIsNone(self.repo.find('links', link))
        self.repo.undo()
        self.assertEqual(port_ids, [p['port_id'] for p in self.ports(identity)])
        self.service.validate()

    def test_odf_dialog_preview_cancel_place_and_edit(self):
        from qgis.PyQt.QtWidgets import QApplication
        dock = self.open_dock()
        before = self.repo.snapshot()
        seen = []
        def accept():
            dialog = QApplication.activeModalWidget()
            seen.append(dialog)
            dialog.label.setText('4')
            dialog.model.setText('My ODF')
            dialog.connector.setCurrentText('LC/APC')
            dialog.as_built.setChecked(True)
            self.assertTrue(dialog.modules.isEnabled())
            self.assertFalse(dialog.cable.isEnabled())
            self.assertIn('OR 4', dialog.preview.text())
            self.assertIn('24xLC/APC', dialog.preview.text())
            dialog.accept()
        QTimer.singleShot(0, accept)
        dock.create_odf()
        dock.canvas_move(pt(100, 100))
        self.assertEqual('housing', dock.mode)
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(pt(100, 100), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        self.assertIsNone(dock.housing_placement)
        QTimer.singleShot(0, accept)
        dock.create_odf()
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        self.assertEqual(1, len(self.repo.features('housings')))
        element = self.repo.features('elements')[0]
        self.assertEqual('OR 4', element['name'])
        self.assertTrue(dock.dark_canvas.isChecked())
        def edit():
            dialog = QApplication.activeModalWidget()
            self.assertEqual('My ODF', dialog.model.text())
            dialog.model.setText('Changed model')
            dialog.rack_module.setText('R2, M1')
            dialog.accept()
        QTimer.singleShot(0, edit)
        dock.edit_selected()
        self.assertEqual('Changed model', self.repo.features('housings')[0]['model'])
        dock.service.validate()

    def test_odf_invalid_properties_and_occupied_reduction_are_atomic(self):
        from fiberq_designer.housing_dialog import HousingDialog
        dialog = HousingDialog()
        dialog.used.setText('25')
        dialog.accept()
        self.assertNotEqual(1, dialog.result())
        self.assertTrue(dialog.error.text())
        dialog.close()
        identity = self.service.add_odf('1', OdfProperties(), pt(0, 0))
        other = self.equipment('CABINET', 'CAB-1', 12, 300)
        self.service.add_link(self.ports(identity)[23], self.ports(other)[0])
        state = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.edit_odf(identity, '1', OdfProperties(capacity=12))
        self.assertEqual(state, self.repo.snapshot())
        with self.assertRaises(ValueError):
            self.service.add_odf('2', OdfProperties(capacity=13), pt(0, 0))
        self.assertEqual(state, self.repo.snapshot())

    def test_odf_write_failure_rolls_back_housing_and_ports(self):
        state = self.repo.snapshot()
        original = self.repo.insert
        for target in ('housings', 'ports'):
            def fail(key, *args, **kwargs):
                if key == target:
                    raise RuntimeError('Injected ODF write failure')
                return original(key, *args, **kwargs)
            with patch.object(self.repo, 'insert', side_effect=fail):
                with self.assertRaises(RuntimeError):
                    self.service.add_odf('1', OdfProperties(), pt(0, 0))
            self.assertEqual(state, self.repo.snapshot())
            self.assertEqual([], self.repo.history)

    def test_v2_migration_preserves_original_and_layout(self):
        self.build_route()
        legacy = Path(self.directory.name) / 'v2.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            remove_operation_tables(dataset)
            for key in ('housings', 'modules', 'fiber_ends', 'contained_splitters', 'closures', 'closure_members'):
                index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == 'fqsd_' + key)
                self.assertEqual(0, dataset.DeleteLayer(index))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='2' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(str(legacy))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertNotEqual(str(legacy), migrated.path)
        self.assertEqual([], migrated.features('housings'))
        for key, records in self.repo.snapshot().items():
            if key == 'fiber_ends':
                continue  # Independent endpoint identities did not exist in v2.
            self.assertEqual(records, migrated.snapshot()[key])
        NetworkService(migrated).validate()

    def test_odf_labels_and_paper_exports_from_dark_canvas(self):
        self.service.add_odf('1', OdfProperties(), pt(0, 0))
        dock = self.open_dock()
        dock.dark_canvas.setChecked(True)
        dock.fit()
        self.iface.canvas.setLayers(dock.scene.layers())
        self.iface.canvas.refresh()
        QgsApplication.processEvents()
        self.iface.canvas.waitWhileRendering()
        labels = self.iface.canvas.labelingResults().allLabels()
        self.assertEqual({'OR 1'} | {str(n) for n in range(1, 25)}, {p.labelText for p in labels})
        for extension in ('pdf', 'svg'):
            path = str(Path(self.directory.name) / ('odf.' + extension))
            exports.export_layout(dock.make_layout(), path, extension)
            self.assertGreater(Path(path).stat().st_size, 500)
        dxf = str(Path(self.directory.name) / 'odf.dxf')
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), dxf)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(dxf)
            labels = []
            line_count = dot_count = 0
            for feature in dataset.GetLayer(0):
                name = feature.GetField('Layer')
                if name.endswith('housing labels'):
                    labels.append(feature.GetField('Text'))
                elif name.endswith('housing connector symbols'):
                    line_count += 1
                elif name.endswith('housing connector dots'):
                    dot_count += 1
            self.assertEqual({'OR 1'} | {str(n) for n in range(1, 25)}, set(labels))
            self.assertEqual((73, 24), (line_count, dot_count))
            dataset = None
        self.assertTrue(dock.scene.dark)

    def test_odf_cable_endpoints_capacity_and_extreme_catalog_choices(self):
        identity = self.service.add_odf('1', OdfProperties(), pt(0, 0))
        other = self.equipment('CLOSURE', 'CLOSURE', 24, 400)
        cable_id = self.service.add_cable('FEEDER', 12, identity, other, [pt(0, 0), pt(400, 0)])
        fiber_ids = {f['fiber_id'] for f in self.repo.features('fibers')}
        port_ids = [p['port_id'] for p in self.ports(identity)]
        self.service.edit_odf(identity, '1', OdfProperties(capacity=12, orientation=270))
        self.service.move_element(identity, pt(50, 100))
        self.service.validate()

        self.assertEqual(port_ids[:12], [p['port_id'] for p in self.ports(identity)])
        self.assertEqual(fiber_ids, {f['fiber_id'] for f in self.repo.features('fibers')})
        view = CadView()
        view.rebuild(self.repo)
        for fiber in self.repo.features('fibers'):
            self.assertEqual(self.repo.find('ports', fiber['a_id']).geometry().asPoint(),
                             view.drawn_fibers[fiber['fiber_id']].asPolyline()[0])
        self.assertEqual(cable_id, self.repo.features('cables')[0]['cable_id'])
        for capacity in (48, 576):
            standalone = self.service.add_odf('', OdfProperties(capacity=capacity), pt(1000, 0))
            self.assertEqual(capacity, len(self.ports(standalone)))
        self.service.validate()

    def test_odf_long_model_is_in_fit_and_export_extent(self):
        self.service.add_odf('1', OdfProperties(model='A very long model designation ' * 4), pt(0, 0))
        dock = self.open_dock()
        bounds = self.repo.features('frames')[0].geometry().boundingBox()
        self.assertGreater(dock.diagram_extent().width(), bounds.width() * 3)
        self.assertTrue(dock.diagram_extent().contains(dock.scene.text_bounds))

    def test_m1b_modules_preserve_addresses_ids_and_connections(self):
        properties = OdfProperties(modules=2, rack_module='R1, M1')
        identity = self.service.add_odf('1', properties, pt(0, 0))
        modules = self.service.modules_of(identity)
        self.assertEqual(['R1, M1', 'R1, M2'], [m['label'] for m in modules])
        self.assertEqual(48, self.repo.find('elements', identity)['count'])
        old = {(p['module_id'], int(p['module_port'])): (p['port_id'], p['fiber_no']) for p in self.ports(identity)}
        port = self.ports(identity)[24]
        self.assertEqual('OR 1 R1, M2/1', self.service.port_address(port))
        other = self.equipment('CABINET', 'CAB', 12, 800)
        self.service.add_link(port, self.ports(other)[0], 'patch')
        before = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.edit_odf(identity, '1', replace(properties, modules=1))
        self.assertEqual(before, self.repo.snapshot())
        self.service.edit_odf(identity, '1', replace(properties, capacity=48, modules=3))
        self.assertEqual([m['module_id'] for m in modules], [m['module_id'] for m in self.service.modules_of(identity)[:2]])
        after = {(p['module_id'], int(p['module_port'])): (p['port_id'], p['fiber_no']) for p in self.ports(identity)}
        self.assertTrue(all(after[key] == value for key, value in old.items()))
        self.service.validate()
        self.service.move_element(identity, pt(50, 50))
        self.service.validate()
        self.repo.undo()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.repo.redo()
        NetworkService(GeoPackageRepository.open(self.path)).validate()

    def test_m1b_partial_termination_tracing_mirroring_and_deletion(self):
        cable = self.service.add_loose_cable('FEEDER-48', 48, [pt(0, 0), pt(800, 0)], 250)
        fiber_ids = {f['fiber_id'] for f in self.repo.features('fibers')}
        end_ids = {e['end_id'] for e in self.repo.features('fiber_ends')}
        left = self.service.attach_odf(cable, 'A', '1', OdfProperties(modules=2, requested_used=36))
        right = self.service.attach_odf(cable, 'B', '2', OdfProperties(requested_used=12))
        self.assertEqual(36, sum(optional(e['port_id']) is not None for e in self.service.ends_of(cable, 'A')))
        self.assertEqual(12, sum(optional(e['port_id']) is not None for e in self.service.ends_of(cable, 'B')))
        props = odf_properties(self.repo.find('elements', right), self.repo.find('housings', right))
        self.assertTrue(props.mirrored)
        self.assertEqual(0, props.orientation)
        view = CadView()
        view.rebuild(self.repo)
        self.assertGreater(view.hit_frames[right].boundingBox().xMaximum(), 800)
        self.assertLess(view.hit_frames[left].boundingBox().xMinimum(), 0)
        for fiber in self.repo.features('fibers'):
            path = view.drawn_fibers[fiber['fiber_id']].asPolyline()
            for side, point in (('A', path[0]), ('B', path[-1])):
                end = next(e for e in self.service.ends_of(cable, side) if e['fiber_id'] == fiber['fiber_id'])
                self.assertEqual(end.geometry().asPoint(), point)
        end = self.service.ends_of(cable, 'B')[24]
        traced = self.service.trace(end['end_id'])
        self.assertIn(self.service.ends_of(cable, 'A')[24]['port_id'], traced)
        self.assertNotIn(self.service.ends_of(cable, 'A')[25]['end_id'], traced)
        self.service.edit_odf(right, '2', replace(props, requested_used=24))
        self.assertEqual(24, sum(optional(e['port_id']) is not None for e in self.service.ends_of(cable, 'B')))
        self.service.move_element(left, pt(50, 30))
        self.service.validate()
        self.assertEqual(end_ids, {e['end_id'] for e in self.repo.features('fiber_ends')})
        state = self.repo.snapshot()
        self.service.delete_element(left)
        self.assertEqual(fiber_ids, {f['fiber_id'] for f in self.repo.features('fibers')})
        self.assertIn((cable, 'A'), self.service.free_cable_ends())
        self.service.validate()
        self.repo.undo()
        self.assertEqual(state, self.repo.snapshot())
        NetworkService(GeoPackageRepository.open(self.path)).validate()

    def test_m1b_inherited_rows_orientation_and_atomic_attachment(self):
        cable = self.service.add_loose_cable('TWO-TUBES', 12, [pt(0, 0), pt(0, 500)], group_size=6)
        before = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.attach_odf(cable, 'A', '1', OdfProperties(requested_used=13))
        self.assertEqual(before, self.repo.snapshot())
        original = self.repo.update
        def fail(key, *args, **kwargs):
            if key == 'fiber_ends':
                raise RuntimeError('Injected attachment failure')
            return original(key, *args, **kwargs)
        history = len(self.repo.history)
        with patch.object(self.repo, 'update', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.service.attach_odf(cable, 'A', '1', OdfProperties())
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual(history, len(self.repo.history))
        identity = self.service.attach_odf(cable, 'A', '1', OdfProperties())
        props = odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))
        self.assertEqual(90, props.orientation)
        self.assertAlmostEqual(-5.6, props.row_positions[6])
        self.assertAlmostEqual(56, self.ports(identity)[6].geometry().asPoint().x())
        self.assertEqual(12, props.requested_used)
        with self.assertRaises(ValueError):
            self.service.attach_odf(cable, 'A', '2', OdfProperties())
        self.service.validate()

    def test_m1b_module_write_failure_and_ambiguous_csv(self):
        before = self.repo.snapshot()
        original = self.repo.insert
        def fail(key, attrs, *args, **kwargs):
            if key == 'modules' and attrs['number'] == 2:
                raise RuntimeError('Injected module failure')
            return original(key, attrs, *args, **kwargs)
        with patch.object(self.repo, 'insert', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.service.add_odf('1', OdfProperties(modules=2), pt(0, 0))
        self.assertEqual(before, self.repo.snapshot())
        identity = self.service.add_odf('1', OdfProperties(modules=2), pt(0, 0))
        self.equipment('CABINET', 'CAB', 12, 800)
        path = Path(self.directory.name) / 'modules.csv'
        path.write_text('from_element,from_port,to_element,to_port\nOR 1,1,CAB,1\nOR 1,M2/1,CAB,2\n')
        count, skipped = import_connections(self.service, path)
        self.assertEqual(1, count)
        self.assertEqual(1, len(skipped))
        self.assertIn('ambiguous port', skipped[0])
        self.assertEqual(self.ports(identity)[24]['port_id'], self.repo.features('links')[0]['from_id'])
        self.service.validate()

    def test_m1b_ui_unattached_cable_then_two_module_attachment(self):
        from qgis.PyQt.QtWidgets import QApplication
        dock = self.open_dock()
        dock.route_count.setValue(48)
        dock.activate('loose_draw')
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        dock.canvas_move(pt(800, 0))
        dock.canvas_click(pt(800, 0), MOUSE_LEFT)
        dock.finish_route()
        self.assertEqual(48, self.repo.features('cables')[0]['count'])
        before = self.repo.snapshot()
        def accept():
            dialog = QApplication.activeModalWidget()
            dialog.modules.setValue(2)
            dialog.cable.setChecked(True)
            dialog.used.setText('36')
            dialog.accept()
        QTimer.singleShot(0, accept)
        dock.create_odf()
        self.assertEqual('attach_odf', dock.mode)
        dock.canvas_move(pt(5, 0))
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(pt(5, 0), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, accept)
        dock.create_odf()
        dock.canvas_click(pt(5, 0), MOUSE_LEFT)
        self.assertEqual(2, len(self.repo.features('modules')))
        self.assertEqual(2, dock.odf_defaults.modules)
        self.assertEqual(36, sum(optional(e['port_id']) is not None for e in self.repo.features('fiber_ends')))
        dock.select('fibers', self.repo.features('fibers')[40]['fiber_id'])
        self.assertIn('Unattached', dock.details.text())
        dock.trace_at(dock.scene.drawn_fibers[self.repo.features('fibers')[40]['fiber_id']].interpolate(400).asPoint())
        self.assertIn('1 cable routes', dock.details.text())
        before_delete = self.repo.snapshot()
        dock.select('elements', self.repo.features('elements')[0]['obj_id'])
        with patch('fiberq_designer.app.QMessageBox.question', return_value=MSG_YES) as question:
            dock.delete_selected()
        self.assertIn('stay in the drawing', question.call_args.args[2])
        self.assertEqual(48, len(self.repo.features('fibers')))
        dock.undo()
        self.assertEqual(before_delete, self.repo.snapshot())
        self.service.validate()

    def test_m1b_two_module_dxf_and_reopen(self):
        self.service.add_odf('1', OdfProperties(modules=2), pt(0, 0))
        dock = self.open_dock()
        dock.dark_canvas.setChecked(True)
        self.assertEqual(2, dock.scene.frames.featureCount())
        self.assertEqual(48, dock.scene.housing_dots.featureCount())
        labels = Counter(f['text'] for f in dock.scene.housing_labels.getFeatures())
        self.assertEqual(1, labels['OR 1'])
        self.assertEqual(1, labels['M1'])
        self.assertEqual(1, labels['M2'])
        self.assertTrue(all(labels[str(n)] == 2 for n in range(1, 25)))
        for extension in ('pdf', 'svg'):
            paper = str(Path(self.directory.name) / ('modules.' + extension))
            exports.export_layout(dock.make_layout(), paper, extension)
            self.assertGreater(Path(paper).stat().st_size, 1000)
        path = str(Path(self.directory.name) / 'modules.dxf')
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), path)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(path)
            labels = Counter(f.GetField('Text') for f in dataset.GetLayer(0)
                             if f.GetField('Layer').endswith('housing labels'))
            self.assertTrue(all(labels[str(n)] == 2 for n in range(1, 25)))
            self.assertEqual(1, labels['M2'])
            dataset = None
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        NetworkService(reopened).validate()

    def test_m1b_v3_migration_keeps_first_module_and_port_ids(self):
        from fiberq_designer.schema import V4_FIELDS, V5_FIELDS
        identity = self.service.add_odf('1', OdfProperties(model='Saved ODF'), pt(0, 0))
        self.equipment('CABINET', 'CAB', 12, 800)
        old = self.repo.snapshot()
        legacy = Path(self.directory.name) / 'v3.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            remove_operation_tables(dataset)
            for key in ('modules', 'fiber_ends', 'contained_splitters', 'closures', 'closure_members'):
                index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == 'fqsd_' + key)
                self.assertEqual(0, dataset.DeleteLayer(index))
            for key, names in {k: V4_FIELDS.get(k, set()) | V5_FIELDS.get(k, set()) for k in set(V4_FIELDS) | set(V5_FIELDS)}.items():
                layer = dataset.GetLayerByName('fqsd_' + key)
                for name in names:
                    index = layer.GetLayerDefn().GetFieldIndex(name)
                    self.assertEqual(0, layer.DeleteField(index))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='3' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(str(legacy))
        service = NetworkService(migrated)
        service.validate()
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(self.repo.find('housings', identity)['module_id'], service.modules_of(identity)[0]['module_id'])
        self.assertEqual([p['port_id'] for p in self.ports(identity)], [p['port_id'] for p in service.ports_of(identity)])
        self.assertEqual(old['frames'], migrated.snapshot()['frames'])
        self.assertEqual(old['elements'], migrated.snapshot()['elements'])
        self.assertEqual('Saved ODF', migrated.find('housings', identity)['model'])
        self.assertNotEqual(str(legacy), migrated.path)

    def test_m1b_reverse_bent_cables_zero_termination_and_existing_odf(self):
        reverse = self.service.add_loose_cable('REVERSE', 12, [pt(800, 0), pt(0, 0)])
        identity = self.service.add_odf('1', OdfProperties(modules=2), pt(-300, 0))
        modules = [m['module_id'] for m in self.service.modules_of(identity)]
        ports = [p['port_id'] for p in self.ports(identity)]
        self.service.attach_odf(reverse, 'A', '1', OdfProperties(modules=2, requested_used=0), identity)
        self.assertEqual(modules, [m['module_id'] for m in self.service.modules_of(identity)])
        self.assertEqual(ports, [p['port_id'] for p in self.ports(identity)])
        self.assertEqual(180, self.repo.find('housings', identity)['angle'])
        self.assertTrue(all(optional(e['port_id']) is None for e in self.service.ends_of(reverse, 'A')))
        trace = self.service.trace(self.service.ends_of(reverse, 'A')[0]['end_id'])
        self.assertFalse(trace & set(ports))
        props = odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))
        self.service.edit_odf(identity, '1', replace(props, requested_used=12))
        self.assertTrue(all(optional(e['port_id']) is not None for e in self.service.ends_of(reverse, 'A')))
        bent = self.service.add_loose_cable('BENT', 24, [pt(0, 800), pt(600, 800), pt(600, 1400)])
        target = self.service.attach_odf(bent, 'B', '2', OdfProperties())
        self.assertEqual(90, self.repo.find('housings', target)['angle'])
        self.assertEqual(1, self.repo.find('housings', target)['mirrored'])
        self.service.validate()
        self.service.detach_cable_end(bent, 'B')
        self.assertIn((bent, 'B'), self.service.free_cable_ends())
        self.repo.undo()
        self.service.validate()

    def test_m1b_corrupt_endpoint_cache_rejected_before_workspace_switch(self):
        dock = self.open_dock()
        active_ids = set(QgsProject.instance().mapLayers())
        path = Path(self.directory.name) / 'bad-endpoints.gpkg'
        repo = GeoPackageRepository.create(path)
        service = NetworkService(repo)
        cable = service.add_loose_cable('CABLE', 12, [pt(0, 0), pt(800, 0)])
        identity = service.attach_odf(cable, 'A', '1', OdfProperties())
        fiber = repo.features('fibers')[0]
        with repo.command('Inject inconsistent endpoint cache'):
            repo.update('fibers', fiber.id(), {'a_id': service.ports_of(identity)[1]['port_id']})
        with self.assertRaisesRegex(ValueError, 'references disagree'):
            dock.attach(str(path))
        self.assertEqual(active_ids, set(QgsProject.instance().mapLayers()))
        self.assertEqual(self.path, dock.path)

    def test_m1b_partial_attachment_preserves_actual_unterminated_rows(self):
        cable = self.service.add_loose_cable('CUSTOM-ROWS', 12, [pt(0, 0), pt(800, 0)], group_size=6)
        end = self.service.ends_of(cable, 'A')[6]
        with self.repo.command('Set a saved endpoint row'):
            self.repo.update('fiber_ends', end.id(), geometry=QgsGeometry.fromPointXY(pt(0, -60)))
        identity = self.service.attach_odf(cable, 'A', '1', OdfProperties(requested_used=6))
        saved = self.repo.find('fiber_ends', end['end_id'])
        self.assertIsNone(optional(saved['port_id']))
        self.assertEqual(pt(0, -60), saved.geometry().asPoint())
        self.assertAlmostEqual(-60, self.ports(identity)[6].geometry().asPoint().y())
        self.service.validate()


    def test_patch_panel_kind_persistence_modules_and_retained_connections(self):
        odf = self.service.add_odf('1', OdfProperties(), pt(-400, 0))
        props = HousingProperties(kind='PATCH_PANEL', modules=2, model='Panel model', connector='LC/APC',
                                  rack_module='R1, M1', status='as_built')
        panel = self.service.add_housing('', props, pt(0, 0))
        self.assertEqual('PP 1', self.repo.find('elements', panel)['name'])
        self.assertEqual('PATCH_PANEL', self.repo.find('elements', panel)['kind'])
        self.assertEqual('PATCH_PANEL', self.repo.find('frames', panel)['kind'])
        ids = {(p['module_id'], int(p['module_port'])): p['port_id'] for p in self.ports(panel)}
        modules = [m['module_id'] for m in self.service.modules_of(panel)]
        cable = self.service.add_cable('PATCH-12', 12, panel, odf, [pt(0, 0), pt(-400, 0)])
        fibers = {f['fiber_id'] for f in self.repo.features('fibers')}
        cabinet = self.equipment('CABINET', 'CAB', 12, 800)
        self.service.add_link(self.ports(panel)[24], self.ports(cabinet)[0], 'patch')
        before = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.edit_housing(panel, '1', replace(props, modules=1))
        self.assertEqual(before, self.repo.snapshot())
        self.service.edit_housing(panel, '1', replace(props, capacity=48, modules=3, model='Edited panel', orientation=90))
        self.service.move_element(panel, pt(50, 50))
        kept = {(p['module_id'], int(p['module_port'])): p['port_id'] for p in self.ports(panel)}
        self.assertTrue(all(kept[key] == value for key, value in ids.items()))
        self.assertEqual(modules, [m['module_id'] for m in self.service.modules_of(panel)[:2]])
        self.assertEqual(fibers, {f['fiber_id'] for f in self.repo.features('fibers')})
        self.assertEqual('PP 1 R1, M2/1', self.service.port_address(self.ports(panel)[48]))
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.path, reopened.path)
        self.assertEqual('PATCH_PANEL', odf_properties(reopened.find('elements', panel), reopened.find('housings', panel)).kind)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        self.repo.undo()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.repo.redo()
        self.repo.redo()
        final = self.repo.snapshot()
        self.service.delete_element(panel)
        self.assertIsNone(self.repo.find('cables', cable))
        self.repo.undo()
        self.assertEqual(final, self.repo.snapshot())

    def test_patch_panel_partial_mirrored_attachment_and_family_guards(self):
        cable = self.service.add_loose_cable('FEEDER', 48, [pt(0, 0), pt(1200, 0)], group_size=6)
        self.service.attach_odf(cable, 'A', '1', OdfProperties(requested_used=12))
        props = HousingProperties(kind='PATCH_PANEL', modules=2, requested_used=36)
        panel = self.service.attach_housing(cable, 'B', '', props)
        saved = odf_properties(self.repo.find('elements', panel), self.repo.find('housings', panel))
        self.assertTrue(saved.mirrored)
        self.assertEqual('PATCH_PANEL', saved.kind)
        self.assertAlmostEqual(-5.6, saved.row_positions[6])
        ids = {e['end_id'] for e in self.repo.features('fiber_ends')}
        self.assertEqual(36, sum(optional(e['port_id']) is not None for e in self.service.ends_of(cable, 'B')))
        self.service.edit_housing(panel, '1', replace(saved, requested_used=0))
        self.assertTrue(all(optional(e['port_id']) is None for e in self.service.ends_of(cable, 'B')))
        self.service.edit_housing(panel, '1', replace(saved, requested_used=48))
        self.assertTrue(all(optional(e['port_id']) is not None for e in self.service.ends_of(cable, 'B')))
        self.assertEqual(ids, {e['end_id'] for e in self.repo.features('fiber_ends')})
        before = self.repo.snapshot()
        for invalid in (replace(saved, capacity=288), replace(saved, kind='ODF')):
            with self.assertRaises(ValueError):
                self.service.edit_housing(panel, '1', invalid)
            self.assertEqual(before, self.repo.snapshot())
        self.service.validate()

    def test_patch_panel_dialog_switches_catalogue_models_and_preview(self):
        from fiberq_designer.housing_dialog import HousingDialog
        dialog = HousingDialog(properties=OdfProperties(capacity=576, model='ODF typed'),
                               models={'ODF': ['ODF stored'], 'PATCH_PANEL': ['Panel stored']})
        self.assertTrue(dialog.kind_radios['PATCH_PANEL'].isEnabled())
        self.assertTrue(dialog.kind_radios['FDH'].isEnabled())
        self.assertTrue(dialog.kind_radios['MDU'].isEnabled())
        self.assertTrue(dialog.kind_radios['FAT'].isEnabled())
        dialog.kind_radios['PATCH_PANEL'].setChecked(True)
        self.assertEqual([12, 24, 48, 72, 96, 144],
                         [int(dialog.capacity.itemText(i)) for i in range(dialog.capacity.count())])
        self.assertEqual('12', dialog.capacity.currentText())
        self.assertEqual('Panel stored', dialog.models.itemText(0))
        self.assertEqual('', dialog.model.text())
        dialog.model.setText('Panel typed')
        dialog.kind_radios['ODF'].setChecked(True)
        self.assertEqual('ODF typed', dialog.model.text())
        self.assertEqual('ODF stored', dialog.models.itemText(0))
        dialog.kind_radios['PATCH_PANEL'].setChecked(True)
        self.assertEqual('Panel typed', dialog.model.text())
        dialog.capacity.setCurrentText('24')
        dialog.modules.setValue(2)
        dialog.connector.setCurrentText('LC/APC')
        dialog.label.setText('7')
        self.assertIn('PP 7', dialog.preview.text())
        self.assertIn('24xLC/APC', dialog.preview.text())
        self.assertIn('2 modules, 48 in total', dialog.preview.text())
        self.assertEqual('PATCH_PANEL', dialog.properties().kind)
        dialog.close()

    def test_patch_panel_ui_creation_cancel_properties_and_kind_lock(self):
        from qgis.PyQt.QtWidgets import QApplication
        dock = self.open_dock()
        before = self.repo.snapshot()
        def accept():
            dialog = QApplication.activeModalWidget()
            dialog.modules.setValue(2)
            dialog.model.setText('Panel UI model')
            dialog.connector.setCurrentText('LC/APC')
            dialog.accept()
        QTimer.singleShot(0, accept)
        dock.create_patch_panel()
        dock.canvas_move(pt(0, 0))
        dock.canvas_click(pt(0, 0), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, accept)
        dock.create_patch_panel()
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        panel = self.repo.features('elements')[0]
        self.assertEqual('PP 1', panel['name'])
        self.assertEqual('PATCH_PANEL', panel['kind'])
        self.assertEqual(2, dock.patch_defaults.modules)
        self.assertEqual(1, dock.odf_defaults.modules)
        self.assertEqual([], dock.housing_models()['ODF'])
        self.assertIn('Panel UI model', dock.housing_models()['PATCH_PANEL'])
        seen = []
        def edit():
            dialog = QApplication.activeModalWidget()
            seen.append(dialog)
            dialog.model.setText('Panel edited')
            dialog.capacity.setCurrentText('48')
            dialog.accept()
        QTimer.singleShot(0, edit)
        dock.edit_selected()
        self.assertFalse(seen[0].kind_radios['ODF'].isEnabled())
        self.assertTrue(seen[0].kind_radios['PATCH_PANEL'].isEnabled())
        self.assertEqual('Panel edited', self.repo.features('housings')[0]['model'])
        self.assertEqual(96, len(self.ports(panel['obj_id'])))
        self.service.validate()

    def test_patch_panel_ui_cable_attachment_and_odf_entry_type_selection(self):
        from qgis.PyQt.QtWidgets import QApplication
        cable = self.service.add_loose_cable('FEEDER', 48, [pt(0, 0), pt(1200, 0)])
        dock = self.open_dock()
        def accept():
            dialog = QApplication.activeModalWidget()
            dialog.kind_radios['PATCH_PANEL'].setChecked(True)
            dialog.modules.setValue(2)
            dialog.used.setText('36')
            dialog.cable.setChecked(True)
            dialog.accept()
        QTimer.singleShot(0, accept)
        dock.create_odf()
        self.assertEqual('PATCH_PANEL', dock.housing_placement[1].kind)
        before = self.repo.snapshot()
        dock.canvas_move(pt(1195, 0))
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(pt(1195, 0), MOUSE_LEFT)
        panel = self.repo.features('elements')[0]
        self.assertEqual('PATCH_PANEL', panel['kind'])
        self.assertEqual('PP 1', panel['name'])
        self.assertTrue(odf_properties(panel, self.repo.features('housings')[0]).mirrored)
        self.assertEqual(36, sum(optional(e['port_id']) is not None for e in self.service.ends_of(cable, 'B')))
        self.service.validate()

    def test_patch_panel_catalogue_capacities_rollback_and_qualified_csv(self):
        for capacity in (12, 24, 48, 72, 96, 144):
            panel = self.service.add_housing('', HousingProperties(kind='PATCH_PANEL', capacity=capacity), pt(capacity * 20, 0))
            self.assertEqual(capacity, len(self.ports(panel)))
        self.service.validate()
        before = self.repo.snapshot()
        original = self.repo.insert
        def fail(key, attrs, *args, **kwargs):
            if key == 'modules' and attrs['number'] == 2:
                raise RuntimeError('Injected panel failure')
            return original(key, attrs, *args, **kwargs)
        with patch.object(self.repo, 'insert', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.service.add_housing('7', HousingProperties(kind='PATCH_PANEL', modules=2), pt(0, 0))
        self.assertEqual(before, self.repo.snapshot())
        panel = self.service.add_housing('7', HousingProperties(kind='PATCH_PANEL', modules=2), pt(0, 0))
        self.equipment('CABINET', 'CAB', 12, 800)
        csv = Path(self.directory.name) / 'panels.csv'
        csv.write_text('from_element,from_port,to_element,to_port\nPP 7,1,CAB,1\nPP 7,M2/1,CAB,2\n')
        count, skipped = import_connections(self.service, csv)
        self.assertEqual(1, count)
        self.assertEqual(1, len(skipped))
        self.assertIn('ambiguous port', skipped[0])
        self.assertEqual(self.ports(panel)[24]['port_id'], self.repo.features('links')[0]['from_id'])

    def test_patch_panel_scene_and_pdf_svg_dxf_exports(self):
        panel = self.service.add_housing('1', HousingProperties(kind='PATCH_PANEL', modules=2), pt(0, 0))
        dock = self.open_dock()
        dock.dark_canvas.setChecked(True)
        self.assertEqual({'PATCH_PANEL'}, {f['kind'] for f in dock.scene.frames.getFeatures()})
        expected = Counter({'PP 1': 1, 'M1': 1, 'M2': 1})
        expected.update({str(n): 2 for n in range(1, 25)})
        self.assertEqual(expected, Counter(f['text'] for f in dock.scene.housing_labels.getFeatures()))
        for extension in ('pdf', 'svg'):
            path = str(Path(self.directory.name) / ('panel.' + extension))
            exports.export_layout(dock.make_layout(), path, extension)
            self.assertGreater(Path(path).stat().st_size, 1000)
        path = str(Path(self.directory.name) / 'panel.dxf')
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), path)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(path)
            actual = Counter(f.GetField('Text') for f in dataset.GetLayer(0)
                             if f.GetField('Layer').endswith('housing labels'))
            self.assertEqual(expected, actual)
            dataset = None
        self.assertEqual('PATCH_PANEL', self.repo.find('elements', panel)['kind'])
        self.assertTrue(dock.scene.dark)


    def test_cabinet_catalogues_descending_allocation_and_reopen(self):
        for kind, capacities in (('FDH', (12, 24, 48, 72, 96)), ('MDU', (12, 24, 48, 72, 96, 144))):
            for capacity in capacities:
                props = HousingProperties(kind=kind, capacity=capacity, mounting='indoor', requested_used=4, address='Entrance 2')
                identity = self.service.add_housing('', props, pt(capacity * 20, 0))
                ports = self.service.available_cable_ports(identity)
                self.assertEqual(list(range(capacity, capacity - 4, -1)), [int(p['module_port']) for p in ports[:4]])
                saved = odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))
                self.assertEqual('Entrance 2', saved.address)
                self.assertEqual(kind, saved.kind)
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())

    def test_cabinet_splitter_real_graph_loss_movement_and_history(self):
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(800, 0)])
        props = HousingProperties(kind='FDH', capacity=12, mounting='indoor', requested_used=1, splitter_ratio=8)
        identity = self.service.attach_housing(cable, 'B', '', props)
        record = self.service.contained_splitter(identity)
        self.assertEqual(10.5, record['loss_db'])
        child = record['splitter_id']
        self.assertEqual(8, self.repo.find('elements', child)['count'])
        self.assertEqual(9, len(self.repo.features('links')))
        parent_ports = {int(p['module_port']): p for p in self.ports(identity)}
        self.assertEqual(parent_ports[12]['port_id'], record['feed_port_id'])
        start = self.service.ends_of(cable, 'A')[0]['end_id']
        trace = self.service.trace(start)
        self.assertTrue({parent_ports[n]['port_id'] for n in range(1, 9)}.issubset(trace))
        self.assertNotIn(parent_ports[11]['port_id'], trace)
        self.service.validate()
        before = self.repo.snapshot()
        link_ids = {l['link_id'] for l in self.repo.features('links')}
        self.service.move_element(identity, pt(1000, 100))
        self.service.validate()
        self.assertEqual(link_ids, {l['link_id'] for l in self.repo.features('links')})
        self.assertEqual(child, self.service.contained_splitter(identity)['splitter_id'])
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.repo.redo()
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        self.service.delete_cable(cable)
        self.service.validate()
        self.assertEqual(9, len(self.repo.features('links')))
        self.service.delete_element(identity)
        self.assertEqual([], self.repo.features('contained_splitters'))
        self.assertEqual([], self.repo.features('elements'))
        self.repo.undo()
        self.service.validate()

    def test_cabinet_splitter_edits_guards_and_retained_ids(self):
        props = HousingProperties(kind='MDU', capacity=48, mounting='free-standing', requested_used=4)
        identity = self.service.add_housing('', props, pt(0, 0))
        ports = [p['port_id'] for p in self.ports(identity)]
        self.service.edit_housing(identity, '1', replace(props, splitter_ratio=8))
        record = self.service.contained_splitter(identity)
        child = record['splitter_id']
        old_child_ports = {p['fiber_no']: p['port_id'] for p in self.ports(child)}
        self.service.validate()
        before = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.edit_housing(identity, '1', replace(props, splitter_ratio=64))
        with self.assertRaises(ValueError):
            self.service.delete_element(child)
        with self.assertRaises(ValueError):
            self.service.move_element(child, pt(0, 0))
        with self.assertRaises(ValueError):
            self.service.delete_link(self.repo.features('links')[0]['link_id'])
        self.assertEqual(before, self.repo.snapshot())
        saved = odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))
        self.service.edit_housing(identity, 'Building A', replace(saved, splitter_ratio=16, address='Address', orientation=90))
        self.service.validate()
        self.assertEqual(ports, [p['port_id'] for p in self.ports(identity)])
        self.assertEqual(13.7, self.service.contained_splitter(identity)['loss_db'])
        self.assertEqual(child, self.service.contained_splitter(identity)['splitter_id'])
        self.assertEqual(old_child_ports, {p['fiber_no']: p['port_id'] for p in self.ports(child) if p['fiber_no'] <= 8})
        self.service.edit_housing(identity, 'Building A', replace(saved, splitter_ratio=0, splitter_feed=0))
        self.service.validate()
        self.assertEqual([], self.repo.features('contained_splitters'))
        self.assertIsNone(self.repo.find('elements', child))
        self.assertEqual(ports, [p['port_id'] for p in self.ports(identity)])
        self.repo.undo()
        self.service.validate()

    def test_cabinet_attachment_partial_zero_vertical_and_mirrored(self):
        for index, vertices in enumerate(([pt(0, 0), pt(800, 0)], [pt(1800, 0), pt(1800, 800)])):
            cable = self.service.add_loose_cable('C{}'.format(index), 12, vertices, group_size=6)
            props = HousingProperties(kind='FDH', capacity=48, mounting='outdoor', requested_used=4)
            identity = self.service.attach_housing(cable, 'A', '', props)
            by_id = {p['port_id']: int(p['module_port']) for p in self.ports(identity)}
            self.assertEqual([48, 47, 46, 45], [by_id[e['port_id']] for e in self.service.ends_of(cable, 'A')[:4]])
            mdu = self.service.attach_housing(cable, 'B', '', replace(props, kind='MDU', mounting='indoor', requested_used=0))
            saved = odf_properties(self.repo.find('elements', mdu), self.repo.find('housings', mdu))
            self.assertTrue(saved.mirrored)
            self.assertTrue(all(optional(e['port_id']) is None for e in self.service.ends_of(cable, 'B')))
            self.service.edit_housing(mdu, self.repo.find('elements', mdu)['name'], replace(saved, requested_used=3))
            self.service.validate()
            self.assertEqual(3, sum(optional(e['port_id']) is not None for e in self.service.ends_of(cable, 'B')))

    def test_cabinet_dialog_enablement_models_and_creation(self):
        from fiberq_designer.housing_dialog import HousingDialog
        from qgis.PyQt.QtWidgets import QApplication
        dialog = HousingDialog(properties=OdfProperties(modules=2), models={'FDH': ['FDH model'], 'MDU': ['MDU model']})
        dialog.kind_radios['FDH'].setChecked(True)
        self.assertEqual(['indoor', 'outdoor', 'free-standing'], [dialog.mounting.itemText(i) for i in range(dialog.mounting.count())])
        self.assertFalse(dialog.modules.isEnabled())
        self.assertFalse(dialog.rack_module.isEnabled())
        self.assertTrue(dialog.address.isEnabled())
        self.assertTrue(dialog.splitter.isEnabled())
        self.assertEqual('FDH model', dialog.models.itemText(0))
        dialog.splitter.setChecked(True)
        self.assertFalse(dialog.used.isEnabled())
        self.assertTrue(dialog.ratio.isEnabled())
        self.assertEqual(1, dialog.properties().requested_used)
        self.assertIn('with splitter 1:8', dialog.preview.text())
        dialog.kind_radios['MDU'].setChecked(True)
        self.assertEqual('MDU model', dialog.models.itemText(0))
        self.assertEqual(['indoor', 'free-standing'], [dialog.mounting.itemText(i) for i in range(dialog.mounting.count())])
        self.assertEqual(1, dialog.properties().modules)
        dialog.kind_radios['ODF'].setChecked(True)
        self.assertTrue(dialog.modules.isEnabled())
        self.assertEqual(2, dialog.properties().modules)
        self.assertEqual(0, dialog.properties().splitter_ratio)
        dialog.close()
        dock = self.open_dock()
        before = self.repo.snapshot()
        def accept():
            current = QApplication.activeModalWidget()
            current.capacity.setCurrentText('12')
            current.address.setText('Entrance 2')
            current.splitter.setChecked(True)
            current.accept()
        QTimer.singleShot(0, accept)
        dock.create_housing('FDH')
        dock.canvas_move(pt(0, 0))
        dock.canvas_click(pt(0, 0), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, accept)
        dock.create_housing('MDU')
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        self.service.validate()
        self.assertEqual('MDU', self.repo.features('elements')[0]['kind'])
        self.assertEqual('Entrance 2', self.repo.features('housings')[0]['address'])

    def test_cabinet_containment_write_failure_is_atomic(self):
        props = HousingProperties(kind='FDH', capacity=12, mounting='indoor', splitter_ratio=8)
        state = self.repo.snapshot()
        original = self.repo.insert
        for target in ('contained_splitters', 'links'):
            def fail(key, *args, **kwargs):
                if key == target:
                    raise RuntimeError('injected cabinet write failure')
                return original(key, *args, **kwargs)
            with patch.object(self.repo, 'insert', side_effect=fail):
                with self.assertRaises(RuntimeError):
                    self.service.add_housing('', props, pt(0, 0))
            self.assertEqual(state, self.repo.snapshot())
            self.assertEqual([], self.repo.history)

    def test_cabinet_output_cable_and_ordinary_expansion(self):
        identity = self.service.add_housing('', HousingProperties(kind='FDH', capacity=12, mounting='indoor',
                                            requested_used=1, splitter_ratio=8), pt(0, 0))
        destination = self.service.add_odf('', OdfProperties(capacity=12), pt(800, 0))
        cable = self.service.add_cable('DISTRIBUTION', 8, identity, destination, [pt(0, 0), pt(800, 0)])
        parent_ports = {int(p['module_port']): p for p in self.ports(identity)}
        self.assertEqual([parent_ports[n]['port_id'] for n in range(1, 9)],
                         [e['port_id'] for e in self.service.ends_of(cable, 'A')])
        self.assertIn(self.ports(destination)[0]['port_id'], self.service.trace(parent_ports[12]['port_id']))
        self.service.validate()
        before = self.repo.snapshot()
        self.service.delete_element(identity)
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.service.validate()
        ordinary = self.service.add_housing('', HousingProperties(kind='MDU', capacity=24, mounting='indoor',
                                            requested_used=2), pt(1600, 0))
        other = self.service.add_odf('', OdfProperties(capacity=12), pt(2400, 0))
        self.service.add_cable('EQUIPMENT FIRST', 12, other, ordinary, [pt(2400, 0), pt(1600, 0)])
        self.assertEqual(12, self.repo.find('housings', ordinary)['requested_used'])
        self.service.validate()

    def test_cabinet_splitter_capacity_growth_preserves_feed_and_connections(self):
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(800, 0)])
        identity = self.service.attach_housing(cable, 'B', '', HousingProperties(kind='FDH', capacity=12,
                              mounting='indoor', splitter_ratio=8, requested_used=1))
        saved = odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))
        feed = self.service.contained_splitter(identity)['feed_port_id']
        self.service.edit_housing(identity, '1', replace(saved, capacity=24, requested_used=2))
        self.service.validate()
        self.assertEqual(feed, self.service.contained_splitter(identity)['feed_port_id'])
        ends = self.service.ends_of(cable, 'B')
        self.assertEqual(feed, ends[0]['port_id'])
        numbers = {p['port_id']: int(p['module_port']) for p in self.ports(identity)}
        self.assertEqual([12, 11], [numbers[e['port_id']] for e in ends[:2]])

    def test_invalid_cabinet_containment_rejected_before_switch(self):
        identity = self.service.add_housing('', HousingProperties(kind='FDH', capacity=12,
                              mounting='indoor', splitter_ratio=8), pt(0, 0))
        dock = self.open_dock()
        for changes in ({'parent_id': 'missing'}, {'loss_db': 0.0}, {'feed_port_id': 'missing'}):
            record = self.service.contained_splitter(identity)
            saved = {name: record[name] for name in changes}
            with self.repo.command('Inject inconsistent containment'):
                self.repo.update('contained_splitters', record.id(), changes)
            with self.assertRaises(ValueError):
                dock.attach(self.path)
            self.assertIs(self.repo, dock.repository)
            with self.repo.command('Restore containment'):
                self.repo.update('contained_splitters', record.id(), saved)
        housing = self.repo.find('housings', identity)
        feed = housing['splitter_feed']
        with self.repo.command('Inject invalid saved feed number'):
            self.repo.update('housings', housing.id(), {'splitter_feed': 0})
        with self.assertRaises(ValueError):
            dock.attach(self.path)
        with self.repo.command('Restore saved feed number'):
            self.repo.update('housings', housing.id(), {'splitter_feed': feed})
        self.service.validate()

    def test_cabinet_all_splitter_ratios_and_source_losses(self):
        from fiberq_designer.housing_catalog import SPLITTER_LOSSES
        for index, (ratio, loss) in enumerate(SPLITTER_LOSSES.items()):
            props = HousingProperties(kind='MDU', capacity=144, mounting='indoor',
                                      splitter_ratio=ratio, orientation=(0, 90, 270)[index % 3])
            identity = self.service.add_housing('', props, pt(index * 800, 0))
            record = self.service.contained_splitter(identity)
            self.assertEqual(loss, record['loss_db'])
            self.assertEqual(ratio + 1, len(self.ports(record['splitter_id'])))
        self.service.validate()

    def test_v4_migration_copies_odf_panel_endpoints_and_ids(self):
        from fiberq_designer.schema import V5_FIELDS
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(800, 0)])
        self.service.attach_odf(cable, 'A', '', OdfProperties())
        self.service.attach_housing(cable, 'B', '', HousingProperties(kind='PATCH_PANEL', requested_used=4))
        old = self.repo.snapshot()
        legacy = Path(self.directory.name) / 'v4.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            remove_operation_tables(dataset)
            for key in ('contained_splitters', 'closures', 'closure_members'):
                index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == 'fqsd_' + key)
                self.assertEqual(0, dataset.DeleteLayer(index))
            layer = dataset.GetLayerByName('fqsd_housings')
            for name in V5_FIELDS['housings']:
                self.assertEqual(0, layer.DeleteField(layer.GetLayerDefn().GetFieldIndex(name)))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='4' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(str(legacy))
        self.assertTrue(migrated.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(old, migrated.snapshot())
        NetworkService(migrated).validate()

    def test_cabinet_splitter_scene_and_paper_exports(self):
        identity = self.service.add_housing('1', HousingProperties(kind='FDH', capacity=12, mounting='indoor',
                                            requested_used=1, splitter_ratio=8), pt(0, 0))
        dock = self.open_dock()
        expected = Counter(['FDH 1 (12)', '12', 'indoor', '1:8', '10.5 dB', '12'] + [str(n) for n in range(1, 9)])
        self.assertEqual(expected, Counter(f['text'] for f in dock.scene.housing_labels.getFeatures()))
        self.assertEqual(9, dock.scene.ports.featureCount())
        self.assertEqual(9, dock.scene.housing_dots.featureCount())
        self.assertEqual(1, dock.scene.frames.featureCount())
        for port in self.ports(identity):
            self.assertEqual(port.geometry().asPoint(), dock.scene.positions[port['port_id']])
        for extension in ('pdf', 'svg'):
            path = str(Path(self.directory.name) / ('cabinet.' + extension))
            exports.export_layout(dock.make_layout(), path, extension)
            self.assertGreater(Path(path).stat().st_size, 1000)
        path = str(Path(self.directory.name) / 'cabinet.dxf')
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), path)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(path)
            actual = Counter(f.GetField('Text') for f in dataset.GetLayer(0)
                             if f.GetField('Layer').endswith('housing labels'))
            self.assertEqual(expected, actual)
            dataset = None


    def test_fat_catalogue_capacities_mounting_persistence_and_ids(self):
        for index, capacity in enumerate((1, 2, 4, 6, 8, 12, 16, 24)):
            props = HousingProperties(kind='FAT', capacity=capacity, mounting=('wall', 'pole', 'indoor')[index % 3],
                                      connector='LC/APC', address='Box {}'.format(index + 1), status='as_built')
            identity = self.service.add_housing('', props, pt(index * 300, 0))
            self.assertEqual('FAT {}'.format(index + 1), self.repo.find('elements', identity)['name'])
            self.assertEqual('FAT', self.repo.find('frames', identity)['kind'])
            self.assertEqual(capacity, len(self.ports(identity)))
            self.assertEqual('fiberq_box_1', self.repo.find('housings', identity)['layout_version'])
            self.assertEqual(asdict(props), asdict(odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))))
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())

    def test_fat_splitter_feed_outputs_spares_trace_and_output_cable(self):
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(800, 0)])
        props = HousingProperties(kind='FAT', capacity=12, mounting='pole', splitter_ratio=8)
        identity = self.service.attach_housing(cable, 'B', '', props)
        record = self.service.contained_splitter(identity)
        ports = {int(p['module_port']): p for p in self.ports(identity)}
        ends = self.service.ends_of(cable, 'B')
        self.assertEqual(ports[12]['port_id'], ends[0]['port_id'])
        self.assertTrue(all(optional(e['port_id']) is None for e in ends[1:]))
        self.assertEqual(10.5, record['loss_db'])
        self.assertEqual(9, len(self.repo.features('links')))
        trace = self.service.trace(self.service.ends_of(cable, 'A')[0]['end_id'])
        self.assertTrue({ports[n]['port_id'] for n in range(1, 9)}.issubset(trace))
        self.assertTrue({ports[n]['port_id'] for n in (9, 10, 11)}.isdisjoint(trace))
        self.assertEqual(12, len(self.service.available_cable_ports(identity)) + 1)
        destination = self.service.add_odf('', OdfProperties(capacity=12), pt(1600, 0))
        outgoing = self.service.add_cable('DISTRIBUTION', 8, identity, destination, [pt(800, 0), pt(1600, 0)])
        self.assertEqual([ports[n]['port_id'] for n in range(1, 9)], [e['port_id'] for e in self.service.ends_of(outgoing, 'A')])
        self.assertIn(self.ports(destination)[0]['port_id'], self.service.trace(ports[12]['port_id']))
        self.service.validate()

    def test_fat_partial_zero_full_attachment_inherits_only_used_rows(self):
        for index, (used, vertices) in enumerate(((4, [pt(0, 0), pt(800, 0)]),
                (0, [pt(1800, 0), pt(1800, 800)]), (None, [pt(2800, 0), pt(2000, 0)]))):
            cable = self.service.add_loose_cable('FEEDER {}'.format(index), 24, vertices, group_size=6)
            identity = self.service.attach_housing(cable, 'B', '', HousingProperties(kind='FAT', capacity=24,
                                      mounting='wall', requested_used=used))
            by_id = {p['port_id']: int(p['module_port']) for p in self.ports(identity)}
            ends = self.service.ends_of(cable, 'B')
            actual = [by_id[e['port_id']] for e in ends if optional(e['port_id']) is not None]
            self.assertEqual(list(range(24, 24 - (24 if used is None else used), -1)), actual)
            saved = odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))
            self.assertTrue(saved.mirrored)
            from fiberq_designer.geometry import odf_drawing
            layout = odf_drawing(self.repo.find('elements', identity), self.repo.find('housings', identity))
            self.assertEqual(24, len(layout.dots))
            self.assertAlmostEqual(-18.4 if used in (0, 4) else -20.8, layout.ports[1][1])
        self.service.validate()

    def test_fat_edit_move_occupied_reduction_and_deletion_undo(self):
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(800, 0)])
        identity = self.service.attach_housing(cable, 'A', '', HousingProperties(kind='FAT', capacity=12,
                                      mounting='wall', requested_used=4))
        saved = odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))
        original_ports = [p['port_id'] for p in self.ports(identity)]
        state = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.edit_housing(identity, '1', replace(saved, capacity=8))
        self.assertEqual(state, self.repo.snapshot())
        self.service.edit_housing(identity, 'Building A', replace(saved, capacity=24, model='Box model', address='Entrance 2'))
        self.assertEqual(original_ports, [p['port_id'] for p in self.ports(identity) if p['module_port'] <= 12])
        self.service.move_element(identity, pt(-200, 100))
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        state = self.repo.snapshot()
        self.service.delete_element(identity)
        self.assertIsNotNone(self.repo.find('cables', cable))
        self.assertTrue(all(optional(e['port_id']) is None for e in self.service.ends_of(cable, 'A')))
        self.service.validate()
        self.repo.undo()
        self.assertEqual(state, self.repo.snapshot())

    def test_fat_splitter_edits_keep_child_feed_and_patch_ids(self):
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(800, 0)])
        identity = self.service.attach_housing(cable, 'B', '', HousingProperties(kind='FAT', capacity=12,
                                      mounting='indoor', splitter_ratio=8))
        saved = odf_properties(self.repo.find('elements', identity), self.repo.find('housings', identity))
        record = self.service.contained_splitter(identity)
        child, feed = record['splitter_id'], record['feed_port_id']
        patches = {l['link_id'] for l in self.repo.features('links')}
        self.service.edit_housing(identity, '1', replace(saved, capacity=24))
        self.assertEqual(child, self.service.contained_splitter(identity)['splitter_id'])
        self.assertEqual(feed, self.service.contained_splitter(identity)['feed_port_id'])
        self.assertEqual(patches, {l['link_id'] for l in self.repo.features('links')})
        self.service.move_element(identity, pt(1000, 200))
        self.service.validate()
        self.service.delete_cable(cable)
        self.assertEqual(9, len(self.repo.features('links')))
        self.service.validate()
        self.service.delete_element(identity)
        self.assertEqual([], self.repo.features('contained_splitters'))
        self.assertEqual([], self.repo.features('elements'))
        self.repo.undo()
        self.service.validate()

    def test_fat_dialog_enablement_models_cancel_and_create(self):
        from fiberq_designer.housing_dialog import HousingDialog
        from qgis.PyQt.QtWidgets import QApplication
        dialog = HousingDialog(properties=OdfProperties(modules=2), models={'FAT': ['Box model']})
        dialog.kind_radios['FAT'].setChecked(True)
        self.assertEqual(['1', '2', '4', '6', '8', '12', '16', '24'], [dialog.capacity.itemText(i) for i in range(dialog.capacity.count())])
        self.assertEqual(['wall', 'pole', 'indoor'], [dialog.mounting.itemText(i) for i in range(dialog.mounting.count())])
        self.assertFalse(dialog.modules.isEnabled())
        self.assertTrue(dialog.address.isEnabled())
        self.assertTrue(dialog.splitter.isEnabled())
        self.assertEqual('Box model', dialog.models.itemText(0))
        dialog.splitter.setChecked(True)
        self.assertFalse(dialog.used.isEnabled())
        self.assertIn('with splitter 1:8', dialog.preview.text())
        dialog.capacity.setCurrentText('8')
        self.assertFalse(dialog.splitter.isEnabled())
        self.assertFalse(dialog.ratio.isEnabled())
        self.assertTrue(dialog.used.isEnabled())
        self.assertEqual(0, dialog.properties().splitter_ratio)
        self.assertEqual(8, dialog.used.validator().top())
        dialog.capacity.setCurrentText('12')
        self.assertEqual(8, dialog.properties().splitter_ratio)
        dialog.ratio.setCurrentText('1:16')
        dialog.accept()
        self.assertTrue(dialog.error.isVisibleTo(dialog))
        self.assertNotEqual(QDialog.Accepted if hasattr(QDialog, 'Accepted') else QDialog.DialogCode.Accepted, dialog.result())
        dialog.close()
        dock = self.open_dock()
        before = self.repo.snapshot()
        def accept():
            current = QApplication.activeModalWidget()
            current.capacity.setCurrentText('12')
            current.model.setText('Box UI')
            current.address.setText('Entrance 2')
            current.mounting.setCurrentText('pole')
            current.splitter.setChecked(True)
            current.accept()
        QTimer.singleShot(0, accept)
        dock.create_housing('FAT')
        dock.canvas_move(pt(0, 0))
        dock.canvas_click(pt(0, 0), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, accept)
        dock.create_housing('FAT')
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        self.assertEqual('FAT', self.repo.features('elements')[0]['kind'])
        self.assertEqual('Box UI', dock.box_defaults.model)
        self.assertIn('Box UI', dock.housing_models()['FAT'])
        self.assertNotIn('Box UI', dock.housing_models()['FDH'])
        self.service.validate()
        dock.box_defaults = replace(dock.box_defaults, capacity=24, splitter_feed=12)
        def accept_new_box():
            current = QApplication.activeModalWidget()
            self.assertEqual('24', current.capacity.currentText())
            self.assertEqual(0, current.properties().splitter_feed)
            current.accept()
        QTimer.singleShot(0, accept_new_box)
        dock.create_housing('FAT')
        dock.canvas_click(pt(600, 0), MOUSE_LEFT)
        self.assertEqual(24, self.repo.features('housings')[-1]['splitter_feed'])
        self.service.validate()

    def test_fat_splitter_failures_and_csv_are_atomic(self):
        props = HousingProperties(kind='FAT', capacity=12, mounting='wall', splitter_ratio=8)
        before = self.repo.snapshot()
        original = self.repo.insert
        for target in ('links', 'contained_splitters', 'ports'):
            def fail(key, *args, **kwargs):
                if key == target:
                    raise RuntimeError('injected FAT write failure')
                return original(key, *args, **kwargs)
            with patch.object(self.repo, 'insert', side_effect=fail):
                with self.assertRaises(RuntimeError):
                    self.service.add_housing('', props, pt(0, 0))
            self.assertEqual(before, self.repo.snapshot())
        identity = self.service.add_housing('', props, pt(0, 0))
        self.service.add_odf('', OdfProperties(capacity=12), pt(800, 0))
        csv = Path(self.directory.name) / 'boxes.csv'
        csv.write_text('from_element,from_port,to_element,to_port\nFAT 1,9,OR 1,1\nFAT 1,10,OR 1,2\nFAT 1,1,OR 1,3\n')
        count, skipped = import_connections(self.service, str(csv))
        self.assertEqual(2, count)
        self.assertEqual(1, len(skipped))
        self.service.validate()

    def test_fat_properties_insert_splitter_and_preserve_cable_identities(self):
        from qgis.PyQt.QtWidgets import QApplication
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(800, 0)])
        identity = self.service.attach_housing(cable, 'A', '', HousingProperties(kind='FAT', capacity=12,
                                      mounting='wall', requested_used=4))
        end_ids = [e['end_id'] for e in self.service.ends_of(cable)]
        fiber_ids = [f['fiber_id'] for f in self.repo.features('fibers')]
        port_ids = [p['port_id'] for p in self.ports(identity)]
        dock = self.open_dock()
        dock.selected = ('elements', identity)
        before = self.repo.snapshot()
        def edit():
            dialog = QApplication.activeModalWidget()
            self.assertFalse(dialog.kind_radios['FDH'].isEnabled())
            self.assertTrue(dialog.kind_radios['FAT'].isEnabled())
            dialog.model.setText('Changed box')
            dialog.address.setText('Entrance 2')
            dialog.splitter.setChecked(True)
            dialog.accept()
        QTimer.singleShot(0, edit)
        dock.edit_selected()
        self.assertEqual(1, self.repo.find('housings', identity)['requested_used'])
        self.assertEqual(1, sum(optional(e['port_id']) is not None for e in self.service.ends_of(cable, 'A')))
        self.assertEqual(end_ids, [e['end_id'] for e in self.service.ends_of(cable)])
        self.assertEqual(fiber_ids, [f['fiber_id'] for f in self.repo.features('fibers')])
        self.assertEqual(port_ids, [p['port_id'] for p in self.ports(identity)])
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())

    def test_fat_small_capacity_and_splitter_variants_use_source_rules(self):
        from fiberq_designer.geometry import odf_drawing
        for index, (capacity, ratio) in enumerate(((12, 2), (12, 4), (12, 8), (24, 16))):
            props = HousingProperties(kind='FAT', capacity=capacity, mounting='wall', splitter_ratio=ratio,
                                      orientation=(0, 90, 270)[index % 3])
            identity = self.service.add_housing('', props, pt(index * 300, 0))
            self.assertEqual(capacity, len(odf_drawing(self.repo.find('elements', identity), self.repo.find('housings', identity)).dots))
        self.service.validate()
        before = self.repo.snapshot()
        for props in (HousingProperties(kind='FAT', capacity=8, mounting='wall', splitter_ratio=2),
                      HousingProperties(kind='FAT', capacity=12, mounting='wall', splitter_ratio=16),
                      HousingProperties(kind='FAT', capacity=24, mounting='wall', splitter_ratio=32)):
            with self.assertRaises(ValueError):
                self.service.add_housing('', props, pt(0, 0))
        self.assertEqual(before, self.repo.snapshot())

    def test_fat_splitter_scene_and_paper_exports(self):
        identity = self.service.add_housing('1', HousingProperties(kind='FAT', capacity=12, mounting='pole',
                                            splitter_ratio=8, address='Entrance 2'), pt(0, 0))
        dock = self.open_dock()
        expected = Counter(['Entrance 2', 'FAT 1', '12xSC/APC', 'pole', '1:8', '10.5 dB'] + [str(n) for n in range(1, 13)])
        self.assertEqual(expected, Counter(f['text'] for f in dock.scene.housing_labels.getFeatures()))
        self.assertEqual(12, dock.scene.ports.featureCount())
        self.assertEqual(12, dock.scene.housing_dots.featureCount())
        self.assertEqual(1, dock.scene.frames.featureCount())
        for port in self.ports(identity):
            self.assertEqual(port.geometry().asPoint(), dock.scene.positions[port['port_id']])
        for extension in ('pdf', 'svg'):
            path = str(Path(self.directory.name) / ('box.' + extension))
            exports.export_layout(dock.make_layout(), path, extension)
            self.assertGreater(Path(path).stat().st_size, 1000)
        path = str(Path(self.directory.name) / 'box.dxf')
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), path)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(path)
            actual = Counter(f.GetField('Text') for f in dataset.GetLayer(0)
                             if f.GetField('Layer').endswith('housing labels'))
            self.assertEqual(expected, actual)
            dataset = None


    def test_otb_catalogue_names_saved_kind_and_shared_box_models(self):
        self.service.add_housing('', HousingProperties(kind='FAT', mounting='wall', model='Shared maker'), pt(-300, 0))
        for index, capacity in enumerate((1, 2, 4, 6, 8, 12, 16, 24)):
            identity = self.service.add_housing('', HousingProperties(kind='OTB', capacity=capacity, mounting='pole'), pt(index * 300, 0))
            self.assertEqual('OTB {}'.format(index + 1), self.repo.find('elements', identity)['name'])
            self.assertEqual('OTB', self.repo.find('frames', identity)['kind'])
            self.assertEqual('fiberq_box_1', self.repo.find('housings', identity)['layout_version'])
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        dock = self.open_dock()
        self.assertIn('Shared maker', dock.housing_models()['FAT'])
        self.assertIn('Shared maker', dock.housing_models()['OTB'])
        self.assertNotIn('Shared maker', dock.housing_models()['FDH'])

    def test_otb_cable_attachment_splitter_trace_move_reopen_and_delete(self):
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(800, 0)])
        identity = self.service.attach_housing(cable, 'B', '', HousingProperties(kind='OTB', capacity=12,
                                              mounting='wall', splitter_ratio=8))
        ports = {int(p['module_port']): p for p in self.ports(identity)}
        record = self.service.contained_splitter(identity)
        self.assertEqual(ports[12]['port_id'], record['feed_port_id'])
        self.assertEqual(10.5, record['loss_db'])
        trace = self.service.trace(self.service.ends_of(cable, 'A')[0]['end_id'])
        self.assertTrue({ports[n]['port_id'] for n in range(1, 9)}.issubset(trace))
        self.assertTrue({ports[n]['port_id'] for n in (9, 10, 11)}.isdisjoint(trace))
        old_links = {l['link_id'] for l in self.repo.features('links')}
        self.service.move_element(identity, pt(1200, 200))
        self.assertEqual(old_links, {l['link_id'] for l in self.repo.features('links')})
        self.service.validate()
        NetworkService(GeoPackageRepository.open(self.path)).validate()
        before = self.repo.snapshot()
        self.service.delete_element(identity)
        self.assertIsNotNone(self.repo.find('cables', cable))
        self.assertEqual([], self.repo.features('contained_splitters'))
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())

    def test_otb_creation_dialog_cancel_and_locked_properties(self):
        from qgis.PyQt.QtWidgets import QApplication
        dock = self.open_dock()
        self.assertEqual('Create OTB Box…', dock.otb_button.toolTip())
        before = self.repo.snapshot()
        def accept():
            dialog = QApplication.activeModalWidget()
            self.assertEqual('OTB', dialog.properties().kind)
            dialog.model.setText('OTB maker')
            dialog.address.setText('Entrance 2')
            dialog.accept()
        QTimer.singleShot(0, accept)
        dock.create_housing('OTB')
        dock.canvas_click(pt(0, 0), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, accept)
        dock.create_housing('OTB')
        dock.canvas_click(pt(0, 0), MOUSE_LEFT)
        self.assertEqual('OTB 1', self.repo.features('elements')[0]['name'])
        self.assertIn('OTB maker', dock.housing_models()['FAT'])
        self.assertIn('OTB maker', dock.housing_models()['OTB'])
        def edit():
            dialog = QApplication.activeModalWidget()
            self.assertTrue(dialog.kind_radios['OTB'].isEnabled())
            self.assertFalse(dialog.kind_radios['FAT'].isEnabled())
            dialog.mounting.setCurrentText('indoor')
            dialog.accept()
        QTimer.singleShot(0, edit)
        dock.edit_selected()
        self.assertEqual('indoor', self.repo.features('housings')[0]['mounting'])
        self.service.validate()

    def test_otb_box_scene_and_all_export_labels(self):
        self.service.add_housing('', HousingProperties(kind='OTB', capacity=12, mounting='pole', splitter_ratio=8), pt(0, 0))
        dock = self.open_dock()
        expected = Counter(['OTB 1', '12xSC/APC', 'pole', '1:8', '10.5 dB'] + [str(n) for n in range(1, 13)])
        self.assertEqual(expected, Counter(f['text'] for f in dock.scene.housing_labels.getFeatures()))
        self.assertEqual({'OTB'}, {f['kind'] for f in dock.scene.frames.getFeatures()})
        self.assertEqual(12, dock.scene.ports.featureCount())
        for extension in ('pdf', 'svg'):
            path = str(Path(self.directory.name) / ('otb.' + extension))
            exports.export_layout(dock.make_layout(), path, extension)
            self.assertGreater(Path(path).stat().st_size, 1000)
        path = str(Path(self.directory.name) / 'otb.dxf')
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), path)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(path)
            actual = Counter(f.GetField('Text') for f in dataset.GetLayer(0) if f.GetField('Layer').endswith('housing labels'))
            self.assertEqual(expected, actual)
            dataset = None


    def test_closure_n_nl_creation_preserves_uncut_fibers_and_source_bounds(self):
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(1000, 0)])
        keys = ('cables', 'fibers', 'fiber_ends', 'ports', 'links')
        before = {k: self.repo.snapshot()[k] for k in keys}
        first = self.service.add_closure('', ClosureProperties(location_number='12'), cable, pt(200, 0))
        second = self.service.add_closure('', ClosureProperties(closure_type='NL', location='pole', location_number='3'), cable, pt(500, 0))
        self.assertEqual('N 1', self.repo.find('elements', first)['name'])
        self.assertEqual('NL 1', self.repo.find('elements', second)['name'])
        self.assertEqual([], self.ports(first))
        self.assertEqual([], self.ports(second))
        self.assertEqual(before, {k: self.repo.snapshot()[k] for k in keys})
        record = self.repo.find('closures', first)
        self.assertEqual(8, record['width'])
        self.assertAlmostEqual(2, record['top'])
        self.assertAlmostEqual(-10.8, record['bottom'])
        self.assertEqual(24, len(self.repo.features('closure_members')))
        self.assertEqual(2, len(self.service.trace(self.service.ends_of(cable, 'A')[0]['end_id'])))
        self.service.validate()

    def test_closure_move_edit_delete_and_undo_preserve_graph_and_member_ids(self):
        from fiberq_designer.closure_layout import ClosureProperties
        props = ClosureProperties()
        cable = self.service.add_loose_cable('FEEDER', 12, [pt(0, 0), pt(1000, 0)])
        identity = self.service.add_closure('', props, cable, pt(200, 0))
        graph = {k: self.repo.snapshot()[k] for k in ('fibers', 'fiber_ends', 'cables', 'ports', 'links')}
        members = [m['member_id'] for m in self.repo.features('closure_members')]
        self.service.move_element(identity, pt(500, 60))
        self.service.edit_closure(identity, '7', replace(props, closure_type='NL', location='facade', location_number='B', status='as_built'), 12)
        self.assertEqual('NL 7', self.repo.find('elements', identity)['name'])
        self.assertEqual(members, [m['member_id'] for m in self.repo.features('closure_members')])
        self.assertEqual(graph, {k: self.repo.snapshot()[k] for k in graph})
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        state = self.repo.snapshot()
        self.service.delete_element(identity)
        self.assertEqual(graph, {k: self.repo.snapshot()[k] for k in graph})
        self.service.validate()
        self.repo.undo()
        self.assertEqual(state, self.repo.snapshot())


    def test_closure_envelope_uses_actual_segment_and_does_not_clamp_at_bend(self):
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_loose_cable('BENT', 12, [pt(0, 0), pt(1000, 0), pt(1000, 800)])
        horizontal = self.service.add_closure('', ClosureProperties(), cable, pt(400, 0))
        vertical = self.service.add_closure('', ClosureProperties(closure_type='NL'), cable, pt(1000, 400))
        crossing = self.service.closure_plan(cable, pt(970, 0))
        self.assertTrue(crossing['crosses_bend'])
        self.assertEqual(8, crossing['width'])
        for identity, angle in ((horizontal, 0), (vertical, 90)):
            record = self.repo.find('closures', identity)
            self.assertAlmostEqual(angle, record['angle'])
            self.assertAlmostEqual(2, record['top'])
            self.assertAlmostEqual(-10.8, record['bottom'])
        self.service.validate()

    def test_closure_widening_and_network_edits_are_one_atomic_history_entry(self):
        from fiberq_designer.closure_layout import ClosureProperties
        first = self.service.add_loose_cable('FIRST', 12, [pt(0, 0), pt(1000, 0)])
        second = self.service.add_loose_cable('SECOND', 12, [pt(0, -200), pt(1000, -200)])
        identity = self.service.add_closure('', ClosureProperties(), first, pt(300, 0))
        members = {m['ref_id']: m['member_id'] for m in self.repo.features('closure_members')}
        anchor = self.repo.find('elements', identity).geometry().asPoint()
        self.service.widen_closure(identity, second)
        self.assertEqual(24, len(self.repo.features('closure_members')))
        self.assertEqual(anchor, self.repo.find('elements', identity).geometry().asPoint())
        self.assertEqual(8, self.repo.find('closures', identity)['width'])
        self.assertAlmostEqual(-30.8, self.repo.find('closures', identity)['bottom'])
        self.assertTrue(all(members[m['ref_id']] == m['member_id'] for m in self.repo.features('closure_members') if m['ref_id'] in members))
        before = self.repo.snapshot()
        history = len(self.repo.history)
        self.service.edit_vertices(second, [pt(0, -240), pt(1000, -240)])
        self.assertEqual(history + 1, len(self.repo.history))
        self.assertAlmostEqual(-34.8, self.repo.find('closures', identity)['bottom'])
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.repo.redo()
        self.service.edit_vertices(second, [pt(0, -180), pt(1000, -180)])
        self.assertAlmostEqual(-34.8, self.repo.find('closures', identity)['bottom'])
        self.service.validate()

    def test_closure_membership_retains_existing_splice_identity(self):
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_loose_cable('THROUGH', 1, [pt(0, 0), pt(1000, 0)])
        a = self.service.add_element('CABLE', 'A', 1, 'left', pt(0, 0))
        b = self.service.add_element('CABLE', 'B', 1, 'left', pt(800, 0))
        link = self.service.add_link(self.ports(a)[0], self.ports(b)[0])
        identity = self.service.add_closure('', ClosureProperties(), cable, pt(450, 0))
        members = {(m['kind'], m['ref_id']) for m in self.repo.features('closure_members')}
        self.assertIn(('link', link), members)
        self.assertEqual([], self.ports(identity))
        self.service.validate()
        before = self.repo.snapshot()
        self.service.delete_link(link)
        self.assertTrue(all(m['kind'] != 'link' for m in self.repo.features('closure_members')))
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.service.delete_element(identity)
        self.assertIsNotNone(self.repo.find('links', link))
        self.assertIn(self.ports(b)[0]['port_id'], self.service.trace(self.ports(a)[0]['port_id']))

    def test_closure_deleted_cable_retains_empty_enclosure_and_undo(self):
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_loose_cable('THROUGH', 12, [pt(0, 0), pt(1000, 0)])
        identity = self.service.add_closure('', ClosureProperties(closure_type='NL'), cable, pt(400, 0))
        before = self.repo.snapshot()
        self.service.delete_cable(cable)
        self.assertIsNotNone(self.repo.find('closures', identity))
        self.assertIsNone(optional(self.repo.find('closures', identity)['cable_id']))
        self.assertEqual([], self.repo.features('closure_members'))
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.service.validate()

    def test_closure_write_failures_and_invalid_width_are_atomic(self):
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_loose_cable('THROUGH', 12, [pt(0, 0), pt(1000, 0)])
        before = self.repo.snapshot()
        history = list(self.repo.history)
        for width in (0, 1.59, float('nan')):
            with self.assertRaises(ValueError):
                self.service.add_closure('', ClosureProperties(), cable, pt(400, 0), width)
        original = self.repo.insert
        for target in ('closures', 'closure_members'):
            def fail(key, *args, **kwargs):
                if key == target:
                    raise RuntimeError('injected enclosure write failure')
                return original(key, *args, **kwargs)
            with patch.object(self.repo, 'insert', side_effect=fail):
                with self.assertRaises(RuntimeError):
                    self.service.add_closure('', ClosureProperties(), cable, pt(400, 0))
            self.assertEqual(before, self.repo.snapshot())
            self.assertEqual(history, self.repo.history)

    def test_closure_dialog_two_edges_series_same_cable_and_properties(self):
        from qgis.PyQt.QtWidgets import QApplication
        cable = self.service.add_loose_cable('THROUGH', 12, [pt(0, 0), pt(1000, 0)])
        dock = self.open_dock()
        original = self.repo.snapshot()['fibers']
        def first():
            dialog = QApplication.activeModalWidget()
            self.assertFalse(dialog.same_cable.isEnabled())
            self.assertEqual(6, dialog.location.count())
            dialog.number.setText('12')
            dialog.continue_series.setChecked(True)
            dialog.accept()
        QTimer.singleShot(0, first)
        dock.closure_button.click()
        self.assertEqual('closure', dock.mode)
        dock.canvas_click(pt(100, 0), MOUSE_LEFT)
        dock.canvas_move(pt(200, 0))
        dock.canvas_click(pt(200, 0), MOUSE_LEFT)
        self.assertEqual([], self.repo.features('closures'))
        def second():
            dialog = QApplication.activeModalWidget()
            self.assertTrue(dialog.same_cable.isEnabled())
            self.assertIn('THROUGH', dialog.cable_hint.text())
            dialog.latent.setChecked(True)
            dialog.location.setCurrentIndex(dialog.location.findData('pole'))
            dialog.number.setText('13')
            dialog.continue_series.setChecked(False)
            dialog.same_cable.setChecked(True)
            dialog.accept()
        QTimer.singleShot(0, second)
        dock.canvas_click(pt(280, 0), MOUSE_LEFT)
        self.assertEqual('left', dock.closure_placement['stage'])
        dock.canvas_click(pt(500, 0), MOUSE_LEFT)
        dock.canvas_click(pt(580, 0), MOUSE_LEFT)
        self.assertEqual(2, len(self.repo.features('closures')))
        self.assertEqual({'N 1', 'NL 1'}, {e['name'] for e in self.repo.features('elements')})
        self.assertEqual(original, self.repo.snapshot()['fibers'])
        def edit():
            dialog = QApplication.activeModalWidget()
            self.assertIsNotNone(dialog.width)
            dialog.width.setValue(12)
            dialog.as_built.setChecked(True)
            dialog.accept()
        QTimer.singleShot(0, edit)
        dock.edit_selected()
        self.assertEqual(12, self.repo.features('closures')[-1]['width'])
        self.assertEqual('as_built', self.repo.features('closures')[-1]['status'])
        self.service.validate()

    def test_closure_cancellation_enter_default_and_equipment_entry(self):
        from qgis.PyQt.QtWidgets import QApplication
        from fiberq_designer.compat import KEY_ESCAPE, KEY_RETURN
        self.service.add_loose_cable('THROUGH', 12, [pt(0, 0), pt(1000, 0)])
        dock = self.open_dock()
        before = self.repo.snapshot()
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        dock.create_closure()
        self.assertEqual(before, self.repo.snapshot())
        for cancel in ('right', 'escape'):
            QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())
            dock.create_closure()
            dock.canvas_click(pt(100, 0), MOUSE_LEFT)
            dock.canvas_click(pt(200, 0), MOUSE_LEFT)
            if cancel == 'right':
                dock.canvas_click(pt(280, 0), MOUSE_RIGHT)
            else:
                QTest.keyClick(self.iface.canvas, KEY_ESCAPE)
                QgsApplication.processEvents()
            self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())
        dock.command_actions['closure'].trigger()
        dock.canvas_click(pt(100, 0), MOUSE_LEFT)
        dock.canvas_click(pt(300, 0), MOUSE_LEFT)
        dock.canvas_move(pt(450, 0))
        QTest.keyClick(self.iface.canvas, KEY_RETURN)
        QgsApplication.processEvents()
        self.assertEqual(8, self.repo.features('closures')[0]['width'])
        self.service.validate()

    def test_closure_scene_red_title_and_pdf_svg_dxf_exports(self):
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_loose_cable('THROUGH', 12, [pt(0, 0), pt(1000, 0)])
        identity = self.service.add_closure('', ClosureProperties(location_number='12'), cable, pt(400, 0))
        dock = self.open_dock()
        expected = Counter(['N 1', 'manhole 12'])
        self.assertEqual(expected, Counter(f['text'] for f in dock.scene.housing_labels.getFeatures()))
        self.assertEqual(0, dock.scene.ports.featureCount())
        self.assertEqual(0, dock.scene.housing_dots.featureCount())
        self.assertEqual(0, dock.scene.housing_lines.featureCount())
        self.assertEqual('#ff0000', next(f for f in dock.scene.housing_labels.getFeatures() if f['text'] == 'N 1')['color'])
        dock.select('elements', identity)
        self.assertIn('12 fibers', dock.details.text())
        self.assertNotIn('ports', dock.details.text())
        for extension in ('pdf', 'svg'):
            path = str(Path(self.directory.name) / ('closure.' + extension))
            exports.export_layout(dock.make_layout(), path, extension)
            self.assertGreater(Path(path).stat().st_size, 1000)
        path = str(Path(self.directory.name) / 'closure.dxf')
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), path)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(path)
            actual = Counter(f.GetField('Text') for f in dataset.GetLayer(0) if f.GetField('Layer').endswith('housing labels'))
            self.assertEqual(expected, actual)
            dataset = None

    def test_v5_migration_preserves_box_cabinet_splitters_and_legacy_layout(self):
        for index, kind in enumerate(('FDH', 'OTB')):
            self.service.add_housing('', HousingProperties(kind=kind, capacity=12, mounting='indoor',
                         splitter_ratio=8, address='Address {}'.format(index), model='Maker'), pt(index * 400, 0))
        self.equipment('CLOSURE', 'PROTOTYPE', 24, 1000)
        old = self.repo.snapshot()
        legacy = Path(self.directory.name) / 'v5.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            remove_operation_tables(dataset)
            for key in ('closures', 'closure_members'):
                index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == 'fqsd_' + key)
                self.assertEqual(0, dataset.DeleteLayer(index))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='5' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(str(legacy))
        self.assertTrue(migrated.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(old, migrated.snapshot())
        NetworkService(migrated).validate()


    def test_closure_widen_ui_and_adapter_endpoint_guards(self):
        from fiberq_designer.closure_layout import ClosureProperties
        first = self.service.add_loose_cable('FIRST', 12, [pt(0, 0), pt(1000, 0)])
        self.service.add_loose_cable('SECOND', 12, [pt(0, -200), pt(1000, -200)])
        identity = self.service.add_closure('', ClosureProperties(), first, pt(300, 0))
        odf = self.service.add_odf('', OdfProperties(capacity=12), pt(1200, 0))
        before = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.add_cable('INVALID', 1, identity, odf, [pt(300, 0), pt(1200, 0)])
        with self.assertRaises(ValueError):
            self.service.edit_element(identity, 'N 1', 12)
        self.assertEqual(before, self.repo.snapshot())
        dock = self.open_dock()
        dock.selected = ('elements', identity)
        dock.widen_selected_closure()
        self.assertEqual('widen_closure', dock.mode)
        dock.canvas_click(pt(300, -200), MOUSE_LEFT)
        self.assertAlmostEqual(-30.8, self.repo.find('closures', identity)['bottom'])
        self.assertEqual(24, len(self.repo.features('closure_members')))
        self.service.validate()

    def test_invalid_closure_membership_rejected_before_workspace_switch(self):
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_loose_cable('THROUGH', 12, [pt(0, 0), pt(1000, 0)])
        identity = self.service.add_closure('', ClosureProperties(), cable, pt(300, 0))
        dock = self.open_dock()
        for key, record, changes in (
                ('closures', self.repo.find('closures', identity), {'cable_id': 'missing'}),
                ('closures', self.repo.find('closures', identity), {'width': 1.0}),
                ('closure_members', self.repo.features('closure_members')[0], {'ref_id': 'missing'})):
            original = {name: record[name] for name in changes}
            with patch.object(self.repo, 'before_commit', None):
                with self.repo.command('Inject inconsistent enclosure'):
                    self.repo.update(key, record.id(), changes)
            with self.assertRaises(ValueError):
                dock.attach(self.path)
            self.assertIs(self.repo, dock.repository)
            with patch.object(self.repo, 'before_commit', None):
                with self.repo.command('Restore enclosure'):
                    self.repo.update(key, record.id(), original)
        self.service.validate()


    def test_m6_physical_tubes_colors_rows_and_source_end_furniture(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('', CableProperties(tubes=2, fibers_per_tube=6), [pt(0, 0), pt(1000, 0)])
        second = self.service.add_catalog_cable('', CableProperties(), [pt(0, -300), pt(1000, -300)])
        self.assertEqual(['K1', 'K2'], [c['name'] for c in self.repo.features('cables')])
        fibers = sorted((f for f in self.repo.features('fibers') if f['cable_id'] == first), key=lambda f: f['fiber_no'])
        self.assertEqual([(1, i) for i in range(1, 7)] + [(2, i) for i in range(1, 7)],
                         [(f['tube_no'], f['tube_fiber']) for f in fibers])
        self.assertEqual(fibers[0]['color'], fibers[6]['color'])
        self.assertEqual(pt(0, -56), self.service.ends_of(first, 'A')[6].geometry().asPoint())
        view = CadView()
        before = self.repo.snapshot()
        view.rebuild(self.repo)
        self.assertEqual(6, view.details.featureCount())
        self.assertTrue(all(f['cad'] == 1 for f in view.fibers.getFeatures()))
        labels = [f['text'] for f in view.housing_labels.getFeatures()]
        self.assertEqual(2, labels.count('7.Blue'))
        self.assertEqual(2, labels.count('7.Red'))
        self.assertIn('K1   ADSS 03 (2x6)', labels)
        first_label = next(f for f in view.housing_labels.getFeatures() if f['obj_id'] == first and f['text'] == '1.Blue')
        self.assertEqual(pt(65.5, 4.14), first_label.geometry().asPoint())
        ellipse = next(view.details.getFeatures()).geometry().boundingBox()
        self.assertAlmostEqual(40.5, ellipse.xMinimum())
        self.assertAlmostEqual(59.5, ellipse.xMaximum())
        self.assertAlmostEqual(-44.4, ellipse.yMinimum())
        self.assertAlmostEqual(4.4, ellipse.yMaximum())
        self.assertEqual(before, self.repo.snapshot())
        self.service.validate()

    def test_m6_catalogue_categories_and_large_tube_identities(self):
        from fiberq_designer.cable_catalog import CATALOGUE, CableProperties
        for index, (category, models) in enumerate(CATALOGUE.items()):
            for model, capacities in models.items():
                tubes, fibers = capacities[-1]
                identity = self.service.add_catalog_cable('', CableProperties(category, model, model, tubes, fibers),
                    [pt(0, -index * 6000), pt(10000, -index * 6000)])
                cable = self.repo.find('cables', identity)
                self.assertEqual((category, model, tubes, fibers),
                    (cable['category'], cable['model'], cable['tube_count'], cable['group_size']))
                owned = [f for f in self.repo.features('fibers') if f['cable_id'] == identity]
                self.assertEqual(tubes * fibers, len(owned))
                self.assertEqual(tubes, max(f['tube_no'] for f in owned))
        self.service.validate()

    def test_m6_bends_all_directions_exact_ends_and_order(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.geometry import parallel_path
        props = CableProperties(tubes=2, fibers_per_tube=6)
        for angle in (0, 90, 180, 270):
            import math
            cosine, sine = round(math.cos(math.radians(angle))), round(math.sin(math.radians(angle)))
            route = [pt(x * cosine - y * sine, x * sine + y * cosine) for x, y in
                     ((0, 0), (600, 0), (600, 600), (1200, 600))]
            identity = self.service.add_catalog_cable('', props, route)
            a = self.service.attach_odf(identity, 'A', '', OdfProperties(capacity=12))
            b = self.service.attach_housing(identity, 'B', '', HousingProperties(kind='OTB', capacity=12, mounting='wall'))
            before = self.repo.snapshot()
            view = CadView()
            view.rebuild(self.repo)
            fibers = sorted((f for f in self.repo.features('fibers') if f['cable_id'] == identity), key=lambda f: f['fiber_no'])
            for index, fiber in enumerate(fibers):
                path = view.drawn_fibers[fiber['fiber_id']].asPolyline()
                expected = parallel_path(route, props.rows[index])
                self.assertEqual(expected, path)
                self.assertLess(path[0].distance(self.repo.find('ports', fiber['a_id']).geometry().asPoint()), 1e-6)
                self.assertLess(path[-1].distance(self.repo.find('ports', fiber['b_id']).geometry().asPoint()), 1e-6)
            self.assertEqual(before, self.repo.snapshot())
            self.service.validate()

    def test_m6_short_and_reversed_routes_reject_without_records_or_history(self):
        from fiberq_designer.cable_catalog import CableProperties
        state, history = self.repo.snapshot(), list(self.repo.history)
        routes = [[pt(0, 0), pt(119, 0)], [pt(0, 0), pt(500, 0), pt(0, 0)],
                  [pt(0, 0), pt(500, 0), pt(500, -80), pt(0, -80)],
                  [pt(0, 0), pt(500, 30)], [pt(0, 0), pt(0, 0)]]
        for route in routes:
            with self.assertRaises(ValueError):
                self.service.add_catalog_cable('', CableProperties(), route)
            self.assertEqual(state, self.repo.snapshot())
            self.assertEqual(history, self.repo.history)
        identity = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1000, 0)])
        owner = self.service.attach_odf(identity, 'A', '', OdfProperties())
        saved = self.repo.snapshot()
        with self.assertRaises(ValueError):
            self.service.move_element(owner, pt(900, 0))
        self.assertEqual(saved, self.repo.snapshot())
        end = self.service.ends_of(identity, 'B')[0]
        with self.repo.command('Corrupt free end for validation check'):
            self.repo.update('fiber_ends', end.id(), geometry=QgsGeometry.fromPointXY(pt(1001, 0)))
        with self.assertRaisesRegex(ValueError, 'detached from its row'):
            self.service.validate()
        self.repo.undo()
        self.assertEqual(saved, self.repo.snapshot())
        self.service.validate()

    def test_m6_housing_first_widening_two_ends_and_atomic_failure(self):
        from fiberq_designer.cable_catalog import CableProperties
        a = self.service.add_odf('', OdfProperties(capacity=24), pt(0, 0))
        b = self.service.add_housing('', HousingProperties(kind='FAT', capacity=12, requested_used=4, mounting='wall'), pt(1000, 0))
        ports = [p['port_id'] for p in self.service.ports_of(a)]
        before = self.repo.snapshot()
        props = CableProperties(tubes=2, fibers_per_tube=6)
        original = self.repo.update
        def fail(key, *args, **kwargs):
            if key == 'ports':
                raise RuntimeError('Injected snap attachment failure')
            return original(key, *args, **kwargs)
        with patch.object(self.repo, 'update', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected snap'):
                self.service.add_catalog_cable('', props, [pt(0, 0), pt(1000, 0)], endpoints={'A': a, 'B': b})
        self.assertEqual(before, self.repo.snapshot())
        identity = self.service.add_catalog_cable('', props, [pt(0, 0), pt(1000, 0)], endpoints={'A': a, 'B': b})
        self.assertEqual(ports, [p['port_id'] for p in self.service.ports_of(a)])
        self.assertEqual(12, sum(optional(e['port_id']) is not None for e in self.service.ends_of(identity, 'A')))
        self.assertEqual(4, sum(optional(e['port_id']) is not None for e in self.service.ends_of(identity, 'B')))
        self.assertEqual([12, 11, 10, 9], [self.repo.find('ports', e['port_id'])['module_port']
            for e in self.service.ends_of(identity, 'B')[:4]])
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.repo.redo()
        self.service.validate()

    def test_m6_native_properties_move_reopen_and_stable_ids(self):
        from fiberq_designer.cable_catalog import CableProperties, properties_from_record
        identity = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        owner = self.service.attach_odf(identity, 'A', '', OdfProperties())
        ids = [(f['fiber_id'], f['tube_no'], f['tube_fiber']) for f in self.repo.features('fibers')]
        ends = [e['end_id'] for e in self.repo.features('fiber_ends')]
        props = properties_from_record(self.repo.find('cables', identity))
        self.service.edit_catalog_cable(identity, 'RENAMED', replace(props, model='Custom "fiber"', color_code='IEC 60304'), 150.5)
        self.service.move_element(owner, pt(-200, 200))
        self.service.validate()
        self.assertEqual(4, len(self.repo.find('cables', identity).geometry().asPolyline()))
        self.assertEqual(ids, [(f['fiber_id'], f['tube_no'], f['tube_fiber']) for f in self.repo.features('fibers')])
        self.assertEqual(ends, [e['end_id'] for e in self.repo.features('fiber_ends')])
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        NetworkService(reopened).validate()
        with self.assertRaises(ValueError):
            self.service.edit_catalog_cable(identity, 'RENAMED', replace(props, tubes=2, fibers_per_tube=6), 0)

    def test_m6_dialog_grouped_capacities_controls_custom_models_and_cancel(self):
        from fiberq_designer.cable_dialog import CableDialog
        from fiberq_designer.cable_catalog import CableProperties
        dock = self.open_dock()
        dialog = dock.catalog_dialog
        before = self.repo.snapshot()
        dialog.category.setCurrentIndex(dialog.category.findData('kbIndoor'))
        self.assertEqual('BUTTERFLY H', dialog.model.currentText())
        self.assertFalse(dialog.capacity.model().item(0).isEnabled())
        self.assertEqual((1, 1), dialog.capacity.currentData())
        dialog.model.setCurrentIndex(dialog.model.findText('RISER H'))
        dialog.select_capacity(6, 4)
        dialog.model.setEditText('Quoted "riser"')
        self.assertEqual((6, 4, 24), (dialog.properties().tubes, dialog.properties().fibers_per_tube, dialog.properties().count))
        dialog.bends.setChecked(True)
        self.assertFalse(dialog.length.isEnabled())
        self.assertFalse(dialog.drag_length.isEnabled())
        QTimer.singleShot(0, dialog.reject)
        dock.command_actions['loose_draw'].trigger()
        self.assertIsNone(dock.mode)
        self.assertEqual(before, self.repo.snapshot())
        editor = CableDialog(dock, CableProperties(), editing=True)
        self.assertFalse(editor.capacity.isEnabled())

    def test_m6_ui_straight_snapping_preview_commit_and_no_ghosts(self):
        a = self.service.add_odf('', OdfProperties(), pt(0, 0))
        b = self.service.add_odf('', OdfProperties(), pt(400, 0))
        dock = self.open_dock()
        initial = self.repo.snapshot()
        dialog = dock.catalog_dialog
        QTimer.singleShot(0, dialog.accept)
        dock.command_actions['loose_draw'].trigger()
        dock.canvas_move(pt(1, 1))
        self.assertEqual(initial, self.repo.snapshot())
        self.assertTrue(dock.click_tool.snap_marker.isVisible())
        preview = dock.click_tool.preview.asGeometry().asMultiPolyline()
        self.assertEqual(pt(0, 0), preview[0][0])
        dock.canvas_click(pt(1, 1), MOUSE_RIGHT)
        self.assertEqual(initial, self.repo.snapshot())
        self.assertFalse(dock.click_tool.preview.isVisible())
        QTimer.singleShot(0, dialog.accept)
        dock.command_actions['loose_draw'].trigger()
        dock.canvas_click(pt(1, 1), MOUSE_LEFT)
        cable = self.repo.features('cables')[0]
        self.assertEqual((a, b), (cable['from_obj'], cable['to_obj']))
        self.assertEqual([pt(0, 0), pt(400, 0)], cable.geometry().asPolyline())
        self.assertEqual(preview[6], dock.scene.drawn_fibers[self.repo.features('fibers')[6]['fiber_id']].asPolyline())
        self.assertIsNone(dock.mode)
        self.service.validate()

    def test_m6_ui_bends_backspace_drag_length_and_alignment_grid(self):
        from fiberq_designer.cable_catalog import CableProperties
        source = self.service.add_catalog_cable('', CableProperties(tubes=2, fibers_per_tube=6), [pt(0, 0), pt(1600, 0)])
        dock = self.open_dock()
        dock.command_actions['align_cable'].trigger()
        dock.canvas_click(pt(300, 0), MOUSE_LEFT)
        self.assertIsNotNone(dock.cable_alignment)
        dialog = dock.catalog_dialog
        dialog.bends.setChecked(True)
        QTimer.singleShot(0, dialog.accept)
        dock.command_actions['loose_draw'].trigger()
        dock.canvas_click(pt(2000, 20), MOUSE_LEFT)
        dock.canvas_click(pt(1500, 20), MOUSE_LEFT)
        self.assertEqual(1, len(dock.route['vertices']))
        dock.canvas_click(pt(2600, 20), MOUSE_LEFT)
        self.assertEqual(pt(2600, 0), dock.route['vertices'][-1])
        dock.remove_last_bend()
        self.assertFalse(dock.command_actions['finish'].isEnabled())
        dock.canvas_click(pt(2600, 20), MOUSE_LEFT)
        dock.canvas_click(pt(2600, 600), MOUSE_LEFT)
        dock.command_actions['finish'].trigger()
        cable = self.repo.features('cables')[-1]
        self.assertEqual([0, -8, -16, -24, -32, -40, -56, -64, -72, -80, -88, -96], __import__('json').loads(cable['row_positions']))
        self.assertIsNone(dock.cable_alignment)
        dialog.bends.setChecked(False)
        dialog.drag_length.setChecked(True)
        dialog.orientation.setCurrentIndex(1)
        QTimer.singleShot(0, dialog.accept)
        dock.command_actions['loose_draw'].trigger()
        dock.canvas_click(pt(4000, 0), MOUSE_LEFT)
        dock.canvas_click(pt(4030, 600), MOUSE_LEFT)
        self.assertEqual([pt(4000, 0), pt(4000, 600)], self.repo.features('cables')[-1].geometry().asPolyline())
        self.service.validate()

    def test_cable_snap_second_module_preview_cancel_attach_and_reopen(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('Module 1 cable', CableProperties(tubes=4), [pt(0, 0), pt(1200, 0)])
        housing = self.service.attach_odf(first, 'A', '', OdfProperties(capacity=48, modules=2, requested_used=48))
        module = self.service.modules_of(housing)[1]['module_id']
        ports = [p for p in self.ports(housing) if p['module_id'] == module]
        anchor = ports[0].geometry().asPoint()
        original = {p['port_id']: p.geometry().asWkt() for p in self.ports(housing)}
        old_ends = {e['end_id']: optional(e['port_id']) for e in self.service.ends_of(first, 'A')}
        before = self.repo.snapshot()
        dock = self.open_dock()
        dialog = dock.catalog_dialog
        def accept():
            dialog.select_capacity(4, 12)
            dialog.accept()
        QTimer.singleShot(0, accept)
        dock.create_catalog_cable()
        dock.canvas_move(pt(anchor.x() + 1, anchor.y() + 1))
        self.assertTrue(dock.click_tool.snap_marker.isVisible())
        preview = dock.click_tool.preview.asGeometry().asMultiPolyline()
        self.assertEqual(anchor, preview[0][0])
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(anchor, MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, accept)
        dock.create_catalog_cable()
        dock.canvas_click(pt(anchor.x() + 1, anchor.y() + 1), MOUSE_LEFT)
        second = self.repo.features('cables')[-1]
        self.assertNotEqual(first, second['cable_id'])
        self.assertEqual(housing, second['from_obj'])
        self.assertEqual(anchor, second.geometry().asPolyline()[0])
        ends = self.service.ends_of(second['cable_id'], 'A')
        self.assertEqual(48, len(ends))
        self.assertEqual({module}, {self.repo.find('ports', e['port_id'])['module_id'] for e in ends})
        self.assertEqual(96, self.repo.find('housings', housing)['requested_used'])
        self.assertTrue(all(self.repo.find('ports', identity).geometry().asWkt() == geometry for identity, geometry in original.items()))
        self.assertEqual(old_ends, {e['end_id']: optional(e['port_id']) for e in self.service.ends_of(first, 'A')})
        for end, path in zip(ends, preview):
            self.assertLess(end.geometry().asPoint().distance(path[0]), 1e-6)
        self.service.validate()
        saved = self.repo.snapshot()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.repo.redo()
        self.assertEqual(saved, self.repo.snapshot())
        self.assertEqual(saved, GeoPackageRepository.open(self.path).snapshot())

    def test_cable_snap_module_rotation_mirror_and_alignment_preserve_housing(self):
        import math
        from fiberq_designer.cable_catalog import CableProperties
        dock = self.open_dock()
        for index, (kind, angle, side) in enumerate((kind, angle, side) for kind in ('ODF', 'PATCH_PANEL')
                                                    for angle in (0, 90, 270) for side in ('A', 'B')):
            with self.subTest(kind=kind, angle=angle, side=side):
                theta = math.radians(angle)
                world = lambda x, y: pt(index * 3000 + x * math.cos(theta) - y * math.sin(theta),
                                       x * math.sin(theta) + y * math.cos(theta))
                first = self.service.add_catalog_cable('', CableProperties(), [world(0, 0), world(1200, 0)])
                housing = self.service.attach_housing(first, side, '',
                    OdfProperties(kind=kind, capacity=12, modules=2, requested_used=12))
                module = self.service.modules_of(housing)[1]['module_id']
                anchor = next(p for p in self.ports(housing) if p['module_id'] == module).geometry().asPoint()
                original = {p['port_id']: p.geometry().asWkt() for p in self.ports(housing)}
                props = odf_properties(self.repo.find('elements', housing), self.repo.find('housings', housing))
                dock.refresh()
                if side == 'B':
                    dock.align_cable_at(anchor)
                    self.assertEqual(2, dock.cable_alignment['module_no'])
                QTimer.singleShot(0, dock.catalog_dialog.accept)
                dock.create_catalog_cable()
                dock.canvas_move(anchor)
                self.assertTrue(dock.click_tool.snap_marker.isVisible())
                preview = dock.click_tool.preview.asGeometry().asMultiPolyline()
                dock.canvas_click(anchor, MOUSE_LEFT)
                cable = self.repo.features('cables')[-1]
                self.assertEqual(housing, cable['from_obj' if side == 'A' else 'to_obj'])
                ends = self.service.ends_of(cable['cable_id'], side)
                self.assertEqual({module}, {self.repo.find('ports', e['port_id'])['module_id'] for e in ends})
                for end, path in zip(ends, preview):
                    self.assertLess(end.geometry().asPoint().distance(path[0 if side == 'A' else -1]), 1e-6)
                saved = odf_properties(self.repo.find('elements', housing), self.repo.find('housings', housing))
                self.assertEqual((props.orientation, props.mirrored), (saved.orientation, saved.mirrored))
                self.assertTrue(all(self.repo.find('ports', identity).geometry().asWkt() == geometry for identity, geometry in original.items()))
                self.assertFalse(any(t.owner == housing for t in dock.housing_cable_targets()))
                self.service.validate()

    def test_cable_snap_partial_module_free_rows_and_disabled_option(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1200, 0)])
        housing = self.service.attach_odf(first, 'A', '', OdfProperties(capacity=24, modules=2, requested_used=12))
        module = self.service.modules_of(housing)[1]['module_id']
        dock = self.open_dock()
        for expected in (1, 13):
            anchor = next(p for p in self.ports(housing) if p['module_id'] == module and p['module_port'] == expected).geometry().asPoint()
            before = self.repo.snapshot()
            dialog = dock.catalog_dialog
            dialog.snap.setChecked(False)
            QTimer.singleShot(0, dialog.accept)
            dock.create_catalog_cable()
            dock.canvas_move(anchor)
            self.assertFalse(dock.click_tool.snap_marker.isVisible())
            dock.canvas_click(anchor, MOUSE_RIGHT)
            self.assertEqual(before, self.repo.snapshot())
            dialog.snap.setChecked(True)
            QTimer.singleShot(0, dialog.accept)
            dock.create_catalog_cable()
            dock.canvas_move(anchor)
            self.assertTrue(dock.click_tool.snap_marker.isVisible())
            dock.canvas_click(anchor, MOUSE_LEFT)
            cable = self.repo.features('cables')[-1]
            ends = self.service.ends_of(cable['cable_id'], 'A')
            self.assertEqual(list(range(expected, expected + 12)), [self.repo.find('ports', e['port_id'])['module_port'] for e in ends])
            self.assertEqual({module}, {self.repo.find('ports', e['port_id'])['module_id'] for e in ends})
        self.assertFalse(any(t.owner == housing and t.module_no == 2 for t in dock.housing_cable_targets()))
        self.service.validate()

    def test_cable_snap_both_second_modules_fixed_length_and_reverse_drag(self):
        from fiberq_designer.cable_catalog import CableProperties
        dock = self.open_dock()
        for index, drag in enumerate((False, True)):
            with self.subTest(reverse_drag=drag):
                base = index * 3000
                first = self.service.add_catalog_cable('', CableProperties(), [pt(base, 0), pt(base + 1200, 0)])
                left = self.service.attach_odf(first, 'A', '', OdfProperties(capacity=12, modules=2, requested_used=12))
                right = self.service.attach_odf(first, 'B', '', OdfProperties(capacity=12, modules=2, requested_used=12))
                anchors = {}
                for side, housing in (('A', left), ('B', right)):
                    module = self.service.modules_of(housing)[1]['module_id']
                    anchors[side] = next(p for p in self.ports(housing) if p['module_id'] == module).geometry().asPoint()
                dock.refresh()
                dialog = dock.catalog_dialog
                dialog.length.setValue(120)
                dialog.drag_length.setChecked(drag)
                QTimer.singleShot(0, dialog.accept)
                dock.create_catalog_cable()
                dock.canvas_click(anchors['B' if drag else 'A'], MOUSE_LEFT)
                if drag:
                    point = pt(anchors['A'].x() + 1, anchors['A'].y() + 1)
                    dock.canvas_move(point)
                    self.assertTrue(dock.click_tool.snap_marker.isVisible())
                    dock.canvas_click(point, MOUSE_LEFT)
                cable = self.repo.features('cables')[-1]
                self.assertEqual((left, right), (cable['from_obj'], cable['to_obj']))
                self.assertEqual([anchors['A'], anchors['B']], cable.geometry().asPolyline())
                for side, housing in (('A', left), ('B', right)):
                    module = self.service.modules_of(housing)[1]['module_id']
                    self.assertEqual({module}, {self.repo.find('ports', e['port_id'])['module_id'] for e in self.service.ends_of(cable['cable_id'], side)})
                self.service.validate()

    def test_cable_snap_second_module_failure_rolls_back_both_housing_ends(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1200, 0)])
        left = self.service.attach_odf(first, 'A', '', OdfProperties(capacity=12, modules=2, requested_used=12))
        right = self.service.attach_odf(first, 'B', '', OdfProperties(capacity=12, modules=2, requested_used=12))
        module = self.service.modules_of(left)[1]['module_id']
        anchor = next(p for p in self.ports(left) if p['module_id'] == module).geometry().asPoint()
        before, history = self.repo.snapshot(), list(self.repo.history)
        update, writes = self.repo.update, []
        def fail(key, *args, **kwargs):
            if key == 'ports':
                writes.append(True)
                if len(writes) == 25:
                    raise RuntimeError('Injected second module attachment failure')
            return update(key, *args, **kwargs)
        with patch.object(self.repo, 'update', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected second module'):
                self.service.add_catalog_cable('', CableProperties(), [anchor, pt(1200, anchor.y())],
                    endpoints={'A': left, 'B': right}, endpoint_modules={'A': 2, 'B': 2})
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual(history, self.repo.history)
        self.service.validate()

    def test_m6_v6_copy_migration_preserves_original_graph_geometry_and_colors(self):
        from fiberq_designer.schema import V7_FIELDS
        self.build_route()
        loose = self.service.add_loose_cable('OLD-2x6', 12, [pt(0, -600), pt(1200, -600)], group_size=6)
        original = self.repo.snapshot()
        legacy = Path(self.directory.name) / 'old-v6.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            remove_operation_tables(dataset)
            for key, fields in V7_FIELDS.items():
                layer = dataset.GetLayerByName('fqsd_' + key)
                for name in fields:
                    self.assertEqual(0, layer.DeleteField(layer.GetLayerDefn().GetFieldIndex(name)))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='6' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(legacy)
        self.assertTrue(migrated.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(original, migrated.snapshot())
        self.assertEqual(0, migrated.find('cables', loose)['tube_count'])
        NetworkService(migrated).validate()

    def test_m6_closure_end_expands_without_moving_existing_frame_edges(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        original = self.service.add_catalog_cable('THROUGH', CableProperties(tubes=1, fibers_per_tube=2),
                                                 [pt(0, 0), pt(1600, 0)])
        closure = self.service.add_closure('', ClosureProperties(), original, pt(500, 0), 12)
        before = self.repo.find('closures', closure)
        anchor = self.repo.find('elements', closure).geometry().asPoint()
        self.service.add_catalog_cable('NEW', CableProperties(), [pt(550, 0), pt(1600, 0)])
        after = self.repo.find('closures', closure)
        self.assertEqual(anchor, self.repo.find('elements', closure).geometry().asPoint())
        self.assertEqual(before['width'], after['width'])
        self.assertLess(after['bottom'], before['bottom'])
        self.assertEqual(14, len([m for m in self.repo.features('closure_members') if m['closure_id'] == closure]))
        self.service.validate()

    def test_m6_first_row_alignment_housing_grid_and_properties_ui(self):
        from fiberq_designer.cable_catalog import CableProperties
        source = self.service.add_catalog_cable('', CableProperties(tubes=2, fibers_per_tube=6), [pt(0, 0), pt(1600, 0)])
        dock = self.open_dock()
        dock.align_cable_at(pt(300, 0))
        dock.catalog_dialog.inherit_grid.setChecked(False)
        QTimer.singleShot(0, dock.catalog_dialog.accept)
        dock.create_catalog_cable()
        dock.catalog_cable_click(pt(2000, 40))
        cable = self.repo.features('cables')[-1]
        self.assertEqual(-48, self.service.ends_of(cable['cable_id'], 'A')[6].geometry().asPoint().y())
        dock.select('cables', cable['cable_id'])
        fibers = [f['fiber_id'] for f in self.repo.features('fibers')]
        def edit():
            dialog = QApplication.activeModalWidget()
            dialog.label.setText('UI-NAME')
            dialog.color_code.setCurrentText('IEC 60304')
            dialog.accept()
        QTimer.singleShot(0, edit)
        dock.command_actions['properties'].trigger()
        self.assertEqual('UI-NAME', self.repo.find('cables', cable['cable_id'])['name'])
        self.assertEqual(fibers, [f['fiber_id'] for f in self.repo.features('fibers')])
        housing = self.service.add_odf('', OdfProperties(capacity=24), pt(3500, 0))
        dock.refresh()
        dock.align_cable_at(pt(3500, 0))
        self.assertEqual(housing, dock.cable_alignment['owner'])
        self.assertTrue(dock.command_actions['align_cable'].isChecked())
        dock.toggle_cable_alignment()
        self.assertIsNone(dock.cable_alignment)
        self.service.validate()


    def test_m7_cut_join_through_graph_stable_fiber_and_end_ids(self):
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        fiber = self.repo.features('fibers')[0]
        identity = fiber['fiber_id']
        a, b = self.service.ends_of(cable, 'A')[0], self.service.ends_of(cable, 'B')[0]
        original = self.repo.snapshot()
        self.assertIn(b['end_id'], self.service.trace(a['end_id']))
        self.service.cut_fibers([identity], pt(800, 0))
        cut = self.repo.features('fiber_cuts')[0]
        self.assertNotIn(b['end_id'], self.service.trace(a['end_id']))
        self.assertIn(cut['left_id'], self.service.trace(a['end_id']))
        self.assertEqual(identity, self.service.fiber_sections()[0]['span_id'])
        joint = self.service.join_nodes(cut['left_id'], cut['right_id'])
        self.assertIn(b['end_id'], self.service.trace(a['end_id']))
        self.assertEqual(1, self.service.fiber_scene().optical_marks.featureCount())
        self.service.validate()
        self.service.restore_through(identity)
        self.assertEqual(original, self.repo.snapshot())
        self.repo.undo()
        self.assertIsNotNone(self.repo.find('fiber_joints', joint))
        self.repo.redo()
        self.service.validate()

    def test_m7_group_join_occupied_rejection_and_atomic_failure(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('A', CableProperties(), [pt(0, 0), pt(1200, 0)])
        second = self.service.add_catalog_cable('B', CableProperties(), [pt(1600, 0), pt(2800, 0)])
        left = [e['end_id'] for e in self.service.ends_of(first, 'B')]
        right = [e['end_id'] for e in self.service.ends_of(second, 'A')]
        before = self.repo.snapshot()
        original = self.repo.insert
        def fail(key, *args, **kwargs):
            if key == 'fiber_joints' and len(self.repo.features('fiber_joints')) == 3:
                raise RuntimeError('Injected group joint failure')
            return original(key, *args, **kwargs)
        with patch.object(self.repo, 'insert', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected group'):
                self.service.join_group(left, right)
        self.assertEqual(before, self.repo.snapshot())
        self.service.join_group(left, right)
        self.assertEqual(12, len(self.repo.features('fiber_joints')))
        self.assertIn(self.service.ends_of(second, 'B')[4]['end_id'],
                      self.service.trace(self.service.ends_of(first, 'A')[4]['end_id']))
        state = self.repo.snapshot()
        with self.assertRaisesRegex(ValueError, 'occupied'):
            self.service.join_nodes(left[0], right[1])
        self.assertEqual(state, self.repo.snapshot())
        self.service.validate()

    def cut_join_fixture(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        first = self.service.add_catalog_cable('CUT-A', CableProperties(tubes=2), [pt(0, 0), pt(400, 0)])
        second = self.service.add_catalog_cable('CUT-B', CableProperties(tubes=2, fibers_per_tube=6), [pt(560, 0), pt(960, 0)])
        closure = self.service.add_closure('1', ClosureProperties(), first, pt(360, 0), 24)
        a, b = self.service.ends_of(first, 'B'), self.service.ends_of(second, 'A')
        joints = self.service.join_group([e['end_id'] for e in a[:12]], [e['end_id'] for e in b])
        return first, second, closure, a, b, joints

    def test_cut_joined_closure_single_and_stub_rejoin_trace_and_undo(self):
        first, second, closure, a, b, joints = self.cut_join_fixture()
        dock = self.open_dock()
        before = self.repo.snapshot()
        for point in (pt(480, 0), pt(390, 0)):
            with self.subTest(cut_at=point):
                dock.command_actions['cut_fiber'].trigger()
                dock.canvas_click(point, MOUSE_LEFT)
                self.assertIsNone(self.repo.find('fiber_joints', joints[0]))
                self.assertEqual(11, len(self.repo.features('fiber_joints')))
                self.assertFalse(self.repo.features('fiber_cuts'))
                self.assertFalse(self.repo.features('fiber_nodes'))
                self.assertNotIn(b[0]['end_id'], self.service.trace(a[0]['end_id']))
                self.assertIn(a[0]['end_id'], self.service.free_optical_nodes())
                self.assertIn(b[0]['end_id'], self.service.free_optical_nodes())
                self.assertIn(b[1]['end_id'], self.service.trace(a[1]['end_id']))
                saved = self.repo.snapshot()
                self.repo.undo()
                self.assertEqual(before, self.repo.snapshot())
                self.repo.redo()
                self.assertEqual(saved, self.repo.snapshot())
                dock.refresh()
                dock.command_actions['join_fiber'].trigger()
                dock.canvas_click(a[0].geometry().asPoint(), MOUSE_LEFT)
                dock.canvas_click(b[0].geometry().asPoint(), MOUSE_LEFT)
                self.assertIn(b[0]['end_id'], self.service.trace(a[0]['end_id']))
                self.repo.undo()
                self.repo.undo()
                self.assertEqual(before, self.repo.snapshot())
                dock.refresh()
        self.service.validate()

    def test_cut_joined_closure_window_center_whole_and_cancel(self):
        first, second, closure, a, b, joints = self.cut_join_fixture()
        dock = self.open_dock()
        before = self.repo.snapshot()
        dock.command_actions['cut_window'].trigger()
        dock.canvas_click(pt(440, 2), MOUSE_LEFT)
        dock.canvas_move(pt(520, -43))
        dock.canvas_click(pt(520, -43), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        for start, end, count in ((pt(440, 2), pt(520, -43), 6),
                                  (pt(350, 5), pt(650, -200), 12)):
            with self.subTest(window=(start, end)):
                dock.command_actions['cut_window'].trigger()
                dock.canvas_click(start, MOUSE_LEFT)
                dock.canvas_move(end)
                dock.canvas_click(end, MOUSE_LEFT)
                self.assertEqual(12 - count, len(self.repo.features('fiber_joints')))
                self.assertFalse(self.repo.features('fiber_cuts'), 'Incoming/open stubs must not be cut again.')
                self.assertFalse(self.repo.features('fiber_nodes'))
                for number in range(12):
                    self.assertEqual(number >= count, b[number]['end_id'] in self.service.trace(a[number]['end_id']))
                saved = self.repo.snapshot()
                self.assertEqual(saved, GeoPackageRepository.open(self.path).snapshot())
                self.repo.undo()
                self.assertEqual(before, self.repo.snapshot())
                self.repo.redo()
                self.assertEqual(saved, self.repo.snapshot())
                self.repo.undo()
                dock.refresh()
        self.service.validate()

    def test_cut_mixed_join_and_continuous_fiber_is_atomic(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_catalog_cable('THROUGH', CableProperties(), [pt(0, 0), pt(1200, 0)])
        self.service.add_closure('1', ClosureProperties(), cable, pt(400, 0), 20)
        identities = [f['fiber_id'] for f in self.repo.features('fibers')]
        self.service.cut_fibers(identities[:1], pt(500, 0))
        cut = self.repo.features('fiber_cuts')[0]
        joint = self.service.join_nodes(cut['left_id'], cut['right_id'])
        before, history = self.repo.snapshot(), list(self.repo.history)
        insert = self.repo.insert
        def fail(key, *args, **kwargs):
            if key == 'fiber_nodes':
                raise RuntimeError('Injected mixed cut failure')
            return insert(key, *args, **kwargs)
        with patch.object(self.repo, 'insert', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected mixed cut'):
                self.service.cut_optical(identities[:2], pt(500, 0), [joint])
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual(history, self.repo.history)
        self.assertEqual(2, self.service.cut_optical(identities[:2], pt(500, 0), [joint]))
        self.assertEqual(2, len(self.repo.features('fiber_cuts')))
        self.assertIsNone(self.repo.find('fiber_joints', joint))
        self.assertEqual(cut['cut_id'], self.repo.features('fiber_cuts')[0]['cut_id'])
        self.assertTrue(all(cut[field] in self.service.free_optical_nodes() for field in ('left_id', 'right_id')))
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())

    def test_cut_end_ticks_removed_and_join_strokes_match_single_fiber(self):
        from qgis.core import QgsExpressionContext, QgsSymbolLayer
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('K1', CableProperties(), [pt(0, 0), pt(1200, 0)])
        identities = [f['fiber_id'] for f in self.repo.features('fibers')[:2]]
        raw = self.service.fiber_scene()
        self.service.cut_fibers(identities, pt(600, 0))
        cuts = self.repo.features('fiber_cuts')
        joint = self.service.join_nodes(cuts[0]['left_id'], cuts[0]['right_id'])
        view = self.service.fiber_scene()
        self.assertEqual(raw.details.featureCount(), view.details.featureCount(), 'Cuts must not add vertical tick geometry.')
        for dark in (False, True):
            view.set_dark(dark)
            for layers in (view.layers(), view.paper_layers()):
                lines = next(layer for layer in layers if layer.name().endswith('optical joints'))
                fibers = next(layer for layer in layers if layer.name().endswith('fiber lines'))
                connection = next(lines.getFeatures())
                fiber = next(f for f in fibers.getFeatures() if f['fiber_id'] == identities[0])
                def width(layer, feature, index):
                    context = QgsExpressionContext()
                    context.setFields(layer.fields())
                    context.setFeature(feature)
                    return layer.renderer().symbol().symbolLayer(index).dataDefinedProperties().property(QgsSymbolLayer.PropertyStrokeWidth).valueAsDouble(context)[0]
                self.assertAlmostEqual(.13, width(lines, connection, 0))
                self.assertAlmostEqual(width(fibers, fiber, 1), width(lines, connection, 0))
                for feature in fibers.getFeatures():
                    self.assertLess(connection.geometry().intersection(feature.geometry()).length(), 1e-6)
        view.highlight(set(identities), joints={joint})
        connection = next(view.optical_lines.getFeatures())
        self.assertEqual(1, connection['active'])
        self.service.validate()

    def test_cut_legacy_splice_and_protected_splitter_patch(self):
        from fiberq_designer.cable_catalog import CableProperties
        self.service.network_demo()
        dock = self.open_dock()
        link = self.repo.features('links')[0]
        displayed = next(f for f in dock.scene.links.getFeatures() if f['link_id'] == link['link_id'])
        before = self.repo.snapshot()
        dock.command_actions['cut_fiber'].trigger()
        dock.canvas_click(displayed.geometry().interpolate(displayed.geometry().length() / 2).asPoint(), MOUSE_LEFT)
        self.assertIsNone(self.repo.find('links', link['link_id']))
        self.assertNotIn(link['to_id'], self.service.trace(link['from_id']))
        self.assertEqual(before['fiber_ends'], self.repo.snapshot()['fiber_ends'])
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        cable = self.service.add_catalog_cable('CABINET-FEED', CableProperties(), [pt(0, 1000), pt(1200, 1000)])
        housing = self.service.attach_housing(cable, 'A', '', HousingProperties(kind='FDH', capacity=12,
            requested_used=1, mounting='indoor', splitter_ratio=8))
        child = self.service.contained_splitter(housing)['splitter_id']
        ports = {p['port_id'] for p in self.ports(child)}
        patch_link = next(l for l in self.repo.features('links') if l['from_id'] in ports or l['to_id'] in ports)
        before, history = self.repo.snapshot(), list(self.repo.history)
        with self.assertRaisesRegex(ValueError, 'contained splitter'):
            self.service.cut_optical([], pt(0, 1000), link_ids=[patch_link['link_id']])
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual(history, self.repo.history)
        self.service.validate()

    def test_m7_splice_marks_purposes_types_descriptions_and_closure_members(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        from fiberq_designer.fiber_model import PURPOSES
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        closure = self.service.add_closure('', ClosureProperties(), cable, pt(500, 0), 12)
        fibers = self.repo.features('fibers')
        original_ids = [f['fiber_id'] for f in fibers]
        for fiber, purpose in zip(fibers, PURPOSES):
            self.service.set_fiber_data([fiber['fiber_id']], purpose, 'MM', 'Description ' + purpose)
        self.service.add_splice_marks(original_ids[:3], pt(550, 0))
        self.service.add_splice_marks(original_ids[:3], pt(550, 0))
        self.assertEqual(3, len(self.repo.features('fiber_marks')))
        self.assertEqual(3, sum(m['kind'] == 'mark' for m in self.repo.features('closure_members')))
        self.service.restore_through(original_ids[0])
        self.assertEqual('nmFree', self.repo.find('fibers', original_ids[0])['purpose'])
        self.assertEqual(2, len(self.repo.features('fiber_marks')))
        view = self.service.fiber_scene()
        self.assertEqual(set(PURPOSES), {f['purpose'] for f in view.fibers.getFeatures()})
        self.assertTrue(any(f['text'] == 'Description nmWork' for f in view.housing_labels.getFeatures()))
        self.assertEqual(original_ids, [f['fiber_id'] for f in self.repo.features('fibers')])
        self.service.validate()

    def test_m7_native_active_source_ports_move_edit_and_trace(self):
        from fiberq_designer.active_layout import ActiveProperties
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1200, 0)])
        splitter = self.service.add_active('', ActiveProperties(anchor='input'), pt(1400, 0))
        ports = self.service.ports_of(splitter)
        self.assertLess(ports[0].geometry().asPoint().distance(pt(1400, 0)), 1e-6)
        joint = self.service.join_nodes(self.service.ends_of(cable, 'B')[0]['end_id'], ports[0]['port_id'])
        ont = self.service.add_active('', ActiveProperties(kind='ONT', count=1, address='Customer'), pt(2200, -120))
        ont_port = self.service.ports_of(ont)[0]
        self.service.join_nodes(ports[1]['port_id'], ont_port['port_id'], 'drop')
        found = self.service.trace(self.service.ends_of(cable, 'A')[0]['end_id'])
        self.assertIn(ont_port['port_id'], found)
        self.assertTrue(all(p['port_id'] in found for p in ports))
        before = [p['port_id'] for p in ports]
        self.service.move_element(splitter, pt(1600, -200))
        self.service.edit_active(splitter, 'S-EDIT', ActiveProperties(count=16, anchor='input'))
        self.assertEqual(before, [p['port_id'] for p in self.service.ports_of(splitter)[:9]])
        self.assertEqual(13.7, self.repo.find('active_equipment', splitter)['loss_db'])
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        NetworkService(reopened).validate()

    def test_m7_branch_slack_and_cable_deletion_undo(self):
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        fibers = self.repo.features('fibers')
        identities = [f['fiber_id'] for f in fibers[:6]]
        before = self.repo.snapshot()
        self.service.branch_fibers(identities, pt(1800, -600), click=pt(1000, 0))
        self.assertEqual(6, len(self.repo.features('fiber_routes')))
        self.assertEqual(pt(1800, -600), self.service.ends_of(cable, 'B')[0].geometry().asPoint())
        self.assertEqual(identities, [f['fiber_id'] for f in self.repo.features('fibers')[:6]])
        slack = self.service.add_slack(pt(800, -200), 12.5, cable)
        self.assertEqual(13, self.repo.find('slack', slack)['length_m'])
        self.service.validate()
        branched = self.repo.snapshot()
        self.service.delete_cable(cable)
        self.assertFalse(self.repo.features('fiber_routes'))
        self.assertFalse(self.repo.features('slack'))
        self.repo.undo()
        self.assertEqual(branched, self.repo.snapshot())
        self.service.validate()

    def test_branch_restore_round_trip_both_ends_keeps_identity_and_metadata(self):
        from fiberq_designer.cable_catalog import CableProperties
        for side, y, destination, source in (('A', 0, -200, 600), ('B', -700, 1800, 1000)):
            cable = self.service.add_catalog_cable(side, CableProperties(), [pt(0, y), pt(1600, y)])
            identities = [end['fiber_id'] for end in self.service.ends_of(cable, side)[:6]]
            self.service.set_fiber_data(identities, purpose='nmWork', description='Branch customer {A/B}')
            original = self.repo.snapshot()
            with self.assertRaisesRegex(ValueError, 'away from either endpoint'):
                self.service.branch_fibers(identities, pt(destination, y - 300), side, pt(0, y))
            self.assertEqual(original, self.repo.snapshot())
            self.service.branch_fibers(identities, pt(destination, y - 300), side, pt(source, y), preview=True)
            self.assertEqual(original, self.repo.snapshot())
            self.service.branch_fibers(identities, pt(destination, y - 300), side, pt(source, y))
            branched = self.repo.snapshot()
            self.assertEqual(6, len(self.repo.features('fiber_routes')))
            self.repo.undo()
            self.assertEqual(original, self.repo.snapshot())
            self.repo.redo()
            self.assertEqual(branched, self.repo.snapshot())
            reopened = GeoPackageRepository.open(self.path)
            self.assertEqual(branched, reopened.snapshot())
            NetworkService(reopened).validate()
            self.service.clear_branch(identities[0])
            self.assertEqual(5, len(self.repo.features('fiber_routes')))
            with self.repo.command('Restore remaining branch routes'):
                for identity in identities[1:]:
                    self.service.clear_branch(identity)
            self.assertEqual(original, self.repo.snapshot())
            self.repo.undo()
            self.assertEqual(5, len(self.repo.features('fiber_routes')))
            self.repo.redo()
            self.assertEqual(original, self.repo.snapshot())
            self.service.validate()

    def test_branch_restore_cable_rolls_back_if_one_diverted_fiber_has_cuts_ui(self):
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('Restore group', CableProperties(), [pt(0, 0), pt(1600, 0)])
        identities = [end['fiber_id'] for end in self.service.ends_of(cable, 'B')[:2]]
        self.service.branch_fibers(identities, pt(1800, -300), click=pt(1000, 0))
        self.service.cut_fibers(identities[1:], pt(400, -8))
        dock = self.open_dock()
        dock.select('cables', cable)
        before = self.repo.snapshot()
        with self.assertRaisesRegex(ValueError, 'Restore cut sections'):
            dock.reset_selected_branch()
        self.assertEqual(before, self.repo.snapshot())
        self.service.restore_through(identities[1], pt(400, -8))
        before_restore = self.repo.snapshot()
        dock.command_actions['restore_branch'].trigger()
        self.assertFalse(self.repo.features('fiber_routes'))
        self.repo.undo()
        self.assertEqual(before_restore, self.repo.snapshot())
        self.repo.redo()
        self.assertFalse(self.repo.features('fiber_routes'))
        self.service.validate()


    def test_m7_window_cut_cancel_trace_sections_and_through_ui(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        self.service.add_closure('', ClosureProperties(), cable, pt(500, 0), 8)
        dock = self.open_dock()
        before = self.repo.snapshot()
        dock.command_actions['cut_window'].trigger()
        dock.canvas_click(pt(530, 6), MOUSE_LEFT)
        dock.canvas_move(pt(540, -45))
        dock.canvas_click(pt(540, -45), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        dock.command_actions['cut_window'].trigger()
        dock.canvas_click(pt(530, 6), MOUSE_LEFT)
        dock.canvas_click(pt(540, -45), MOUSE_LEFT)
        self.assertEqual(6, len(self.repo.features('fiber_cuts')))
        self.assertEqual(18, dock.scene.fibers.featureCount())
        fiber = self.repo.features('fibers')[0]
        dock.trace_at(pt(400, 0))
        active = [f for f in dock.scene.fibers.getFeatures() if f['active']]
        self.assertEqual(1, len(active))
        self.assertEqual(fiber['fiber_id'], active[0]['span_id'])
        dock.command_actions['through_fiber'].trigger()
        dock.canvas_click(pt(600, 0), MOUSE_LEFT)
        self.assertEqual(5, len(self.repo.features('fiber_cuts')))
        self.assertIn(self.service.ends_of(cable, 'B')[0]['end_id'], self.service.trace(self.service.ends_of(cable, 'A')[0]['end_id']))
        self.service.validate()

    def test_m7_group_join_ui_and_splice_mark_selection_delete_undo(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('A', CableProperties(), [pt(0, 0), pt(1200, 0)])
        second = self.service.add_catalog_cable('B', CableProperties(), [pt(1600, 0), pt(2800, 0)])
        dock = self.open_dock()
        dock.fit()
        dock.command_actions['group_joint'].trigger()
        dock.canvas_click(pt(1200, 0), MOUSE_LEFT)
        def accept():
            dialog = QApplication.activeModalWidget()
            dialog.count.setValue(6)
            dialog.accept()
        QTimer.singleShot(0, accept)
        dock.canvas_click(pt(1600, 0), MOUSE_LEFT)
        self.assertEqual(6, len(self.repo.features('fiber_joints')))
        marker = next(dock.scene.optical_marks.getFeatures())
        dock.select_at(marker.geometry().asPoint())
        self.assertEqual('fiber_joints', dock.selected[0])
        snapshot = self.repo.snapshot()
        dock.command_actions['delete'].trigger()
        self.assertEqual(5, len(self.repo.features('fiber_joints')))
        dock.command_actions['undo'].trigger()
        self.assertEqual(snapshot, self.repo.snapshot())
        self.service.validate()

    def test_m7_active_dialog_cancel_input_anchor_snap_and_ont_ui(self):
        from fiberq_designer.active_layout import ActiveProperties
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1200, 0)])
        dock = self.open_dock()
        dock.fit()
        before = self.repo.snapshot()
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        dock.command_actions['splitter'].trigger()
        self.assertEqual(before, self.repo.snapshot())
        dock.active_defaults['SPLITTER'] = ActiveProperties(anchor='input')
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())
        dock.command_actions['splitter'].trigger()
        dock.canvas_move(pt(1201, 1))
        self.assertTrue(dock.click_tool.snap_marker.isVisible())
        self.assertEqual(before, self.repo.snapshot())
        dock.toggle_active_anchor()
        self.assertEqual('output', dock.operation_placement['properties'].anchor)
        dock.toggle_active_anchor()
        dock.canvas_click(pt(1201, 1), MOUSE_LEFT)
        splitter = self.repo.features('active_equipment')[0]
        input_port = self.service.ports_of(splitter['obj_id'])[0]
        self.assertLess(input_port.geometry().asPoint().distance(pt(1200, 0)), 1e-6)
        self.assertEqual(1, len(self.repo.features('fiber_joints')))
        output = self.service.ports_of(splitter['obj_id'])[1]
        def accept_ont():
            dialog = QApplication.activeModalWidget()
            dialog.address.setText('Customer A')
            dialog.accept()
        QTimer.singleShot(0, accept_ont)
        dock.command_actions['ONT'].trigger()
        dock.canvas_click(output.geometry().asPoint(), MOUSE_LEFT)
        ont = next(e for e in self.repo.features('elements') if e['kind'] == 'ONT')
        self.assertEqual('Customer A', self.repo.find('active_equipment', ont['obj_id'])['address'])
        self.assertIn(self.service.ports_of(ont['obj_id'])[0]['port_id'], self.service.trace(self.service.ends_of(cable, 'A')[0]['end_id']))
        self.service.validate()

    def test_m7_purpose_data_and_branch_slack_dialogs_ui(self):
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        dock = self.open_dock()
        fiber = self.repo.features('fibers')[0]
        dock.select('fibers', fiber['fiber_id'])
        dock.command_actions['purpose_nmWork'].trigger()
        self.assertEqual('nmWork', self.repo.find('fibers', fiber['fiber_id'])['purpose'])
        def accept_data():
            dialog = QApplication.activeModalWidget()
            dialog.fiber_type.setCurrentIndex(dialog.fiber_type.findData('MM'))
            dialog.replace_description.setChecked(True)
            dialog.description.setText('Subscriber A')
            dialog.accept()
        QTimer.singleShot(0, accept_data)
        dock.command_actions['fiber_data'].trigger()
        self.assertEqual('Subscriber A', self.repo.find('fibers', fiber['fiber_id'])['description'])
        def accept_branch():
            dialog = QApplication.activeModalWidget()
            dialog.count.setValue(1)
            dialog.accept()
        QTimer.singleShot(0, accept_branch)
        dock.command_actions['branch_fiber'].trigger()
        dock.canvas_click(pt(1000, 0), MOUSE_LEFT)
        before = self.repo.snapshot()
        dock.canvas_move(pt(1800, -600))
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(pt(1800, -600), MOUSE_LEFT)
        self.assertEqual(1, len(self.repo.features('fiber_routes')))
        dock.select('cables', cable)
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())
        dock.command_actions['slack'].trigger()
        dock.canvas_move(pt(800, -250))
        dock.canvas_click(pt(800, -250), MOUSE_LEFT)
        self.assertEqual(15, self.repo.features('slack')[0]['length_m'])
        self.service.validate()

    def test_m7_recut_through_and_type_rejection_keep_purpose_and_history(self):
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        identity = self.repo.features('fibers')[0]['fiber_id']
        self.service.set_fiber_data([identity], purpose='nmWork')
        self.service.cut_fibers([identity], pt(800, 0))
        cut = self.repo.features('fiber_cuts')[0]
        self.service.join_nodes(cut['left_id'], cut['right_id'])
        self.service.cut_fibers([identity], pt(800, 0))
        self.assertFalse(self.repo.features('fiber_joints'))
        self.assertEqual(cut['cut_id'], self.repo.features('fiber_cuts')[0]['cut_id'])
        self.service.join_nodes(cut['left_id'], cut['right_id'], 'through')
        self.assertFalse(self.repo.features('fiber_nodes'))
        self.assertEqual('nmWork', self.repo.find('fibers', identity)['purpose'])
        second = self.service.add_catalog_cable('MM', CableProperties(fiber_type='MM'), [pt(1800, 0), pt(3000, 0)])
        a, b = self.service.ends_of(cable, 'B')[0], self.service.ends_of(second, 'A')[0]
        before = self.repo.snapshot()
        with self.assertRaisesRegex(ValueError, 'SM and MM'):
            self.service.join_nodes(a['end_id'], b['end_id'])
        self.assertEqual(before, self.repo.snapshot())
        self.service.set_fiber_data([b['fiber_id']], fiber_type='SM')
        self.service.join_nodes(a['end_id'], b['end_id'])
        before = self.repo.snapshot()
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            self.service.set_fiber_data([b['fiber_id']], fiber_type='MM')
        self.assertEqual(before, self.repo.snapshot())
        self.service.validate()

    def test_m7_manual_joint_preview_cancel_and_retained_column_ui(self):
        from fiberq_designer.active_layout import ActiveProperties
        a = self.service.add_active('OLT', ActiveProperties(kind='OLT', count=8), pt(0, 0))
        b = self.service.add_active('ONT', ActiveProperties(kind='ONT', count=1), pt(600, -100))
        dock = self.open_dock()
        dock.fit()
        before = self.repo.snapshot()
        dock.start_joint(manual=True)
        dock.canvas_click(self.service.ports_of(a)[0].geometry().asPoint(), MOUSE_LEFT)
        dock.canvas_click(self.service.ports_of(b)[0].geometry().asPoint(), MOUSE_LEFT)
        dock.canvas_move(pt(350, -50))
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(pt(350, -50), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        dock.start_joint(manual=True)
        dock.canvas_click(self.service.ports_of(a)[0].geometry().asPoint(), MOUSE_LEFT)
        dock.canvas_click(self.service.ports_of(b)[0].geometry().asPoint(), MOUSE_LEFT)
        dock.canvas_click(pt(350, -50), MOUSE_LEFT)
        joint = self.repo.features('fiber_joints')[0]
        self.assertEqual(350, joint['manual_x'])
        self.service.move_element(b, pt(900, -250))
        vertices = self.repo.find('fiber_joints', joint['joint_id']).geometry().asPolyline()
        self.assertEqual([350, 350], [p.x() for p in vertices[1:3]])
        self.service.validate()

    def test_m7_active_all_ratios_orientations_capacity_and_occupied_ports(self):
        from fiberq_designer.active_layout import ActiveProperties
        from fiberq_designer.housing_catalog import SPLITTER_LOSSES
        for index, (ratio, loss) in enumerate(SPLITTER_LOSSES.items()):
            for angle in (0, 90, 180, 270):
                anchor = pt(index * 1000, angle * 10)
                identity = self.service.add_active('', ActiveProperties(count=ratio, orientation=angle, anchor='input'), anchor)
                ports = self.service.ports_of(identity)
                self.assertEqual(ratio + 1, len(ports))
                self.assertLess(ports[0].geometry().asPoint().distance(anchor), 1e-6)
                self.assertEqual(loss, self.repo.find('active_equipment', identity)['loss_db'])
        olt = self.service.add_active('', ActiveProperties(kind='OLT', count=16), pt(8000, 0))
        ont = self.service.add_active('', ActiveProperties(kind='ONT', count=1), pt(9000, 0))
        port = self.service.ports_of(olt)[-1]['port_id']
        target = self.service.ports_of(ont)[0]['port_id']
        self.service.join_nodes(port, target, 'drop')
        before = self.repo.snapshot()
        with self.assertRaisesRegex(ValueError, 'occupied'):
            self.service.edit_active(olt, 'OLT-REDUCED', ActiveProperties(kind='OLT', count=8))
        self.assertEqual(before, self.repo.snapshot())
        with self.assertRaisesRegex(ValueError, 'already have'):
            self.service.add_link(self.repo.find('ports', port), self.service.ports_of(identity)[0])
        self.service.validate()

    def test_m7_branch_both_ends_preserves_existing_cut_and_rejects_removed_cut(self):
        from fiberq_designer.cable_catalog import CableProperties
        for side, y, destination, cut_x, click_x in (('A', 0, -200, 1200, 600), ('B', -600, 1800, 400, 1000)):
            cable = self.service.add_catalog_cable(side, CableProperties(), [pt(0, y), pt(1600, y)])
            identity = next(f['fiber_id'] for f in self.repo.features('fibers') if f['cable_id'] == cable)
            self.service.cut_fibers([identity], pt(cut_x, y))
            before_nodes = [n.geometry().asWkt() for n in self.repo.features('fiber_nodes') if n['fiber_id'] == identity]
            before = self.repo.snapshot()
            self.service.branch_fibers([identity], pt(destination, y - 300), side, pt(click_x, y), preview=True)
            self.assertEqual(before, self.repo.snapshot())
            self.service.branch_fibers([identity], pt(destination, y - 300), side, pt(click_x, y))
            self.assertEqual(before_nodes, [n.geometry().asWkt() for n in self.repo.features('fiber_nodes') if n['fiber_id'] == identity])
            self.assertEqual(destination, next(e for e in self.service.ends_of(cable, side) if e['fiber_id'] == identity).geometry().asPoint().x())
            with self.assertRaisesRegex(ValueError, 'Restore cut'):
                self.service.clear_branch(identity)
            second = next(f['fiber_id'] for f in self.repo.features('fibers')
                          if f['cable_id'] == cable and f['fiber_id'] != identity)
            self.service.cut_fibers([second], pt(400 if side == 'A' else 1200, y - 8))
            before = self.repo.snapshot()
            with self.assertRaisesRegex(ValueError, 'retained part'):
                self.service.branch_fibers([second], pt(destination, y - 300), side, pt(click_x, y))
            self.assertEqual(before, self.repo.snapshot())
        self.service.validate()

    def test_m7_invalid_optical_records_rejected_before_workspace_switch(self):
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        identities = [f['fiber_id'] for f in self.repo.features('fibers')]
        self.service.cut_fibers(identities[:1], pt(800, 0))
        cut = self.repo.features('fiber_cuts')[0]
        joint = self.service.join_nodes(cut['left_id'], cut['right_id'])
        self.service.add_splice_marks(identities[1:2], pt(800, -8))
        slack = self.service.add_slack(pt(600, -200), cable_id=cable)
        dock = self.open_dock()
        for key, record, changes in (
                ('fiber_nodes', self.repo.features('fiber_nodes')[0], {'at': 1.5}),
                ('fiber_joints', self.repo.find('fiber_joints', joint), {'b_id': 'missing'}),
                ('fiber_marks', self.repo.features('fiber_marks')[0], {'loss_db': -1}),
                ('slack', self.repo.find('slack', slack), {'cable_id': 'missing'})):
            original = {name: record[name] for name in changes}
            with patch.object(self.repo, 'before_commit', None):
                with self.repo.command('Inject inconsistent optical record'):
                    self.repo.update(key, record.id(), changes)
            with self.assertRaises(ValueError):
                dock.attach(self.path)
            self.assertIs(self.repo, dock.repository)
            with patch.object(self.repo, 'before_commit', None):
                with self.repo.command('Restore optical record'):
                    self.repo.update(key, record.id(), original)
        self.service.validate()

    def test_m7_v7_copy_migration_keeps_original_ids_geometry_and_default_data(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.schema import V8_FIELDS
        native = self.service.add_catalog_cable('NATIVE', CableProperties(), [pt(0, 0), pt(1600, 0)])
        self.service.add_loose_cable('LEGACY', 12, [pt(0, -500), pt(1600, -500)])
        original = self.repo.snapshot()
        legacy = Path(self.directory.name) / 'v7.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            remove_operation_tables(dataset)
            for key, fields in V8_FIELDS.items():
                layer = dataset.GetLayerByName('fqsd_' + key)
                for name in fields:
                    self.assertEqual(0, layer.DeleteField(layer.GetLayerDefn().GetFieldIndex(name)))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='7' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(legacy)
        self.assertTrue(migrated.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(original, migrated.snapshot())
        self.assertEqual({'nmFree'}, {f['purpose'] for f in migrated.features('fibers') if f['cable_id'] == native})
        NetworkService(migrated).validate()


    def m8_network(self):
        from fiberq_designer.active_layout import ActiveProperties
        from fiberq_designer.cable_catalog import CableProperties
        cable = self.service.add_catalog_cable('FEEDER', CableProperties(), [pt(0, 0), pt(1600, 0)], 1000)
        olt = self.service.add_active('OLT1', ActiveProperties(kind='OLT', count=8), pt(-200, 0))
        splitter = self.service.add_active('S1', ActiveProperties(anchor='input'), pt(1800, 0))
        ont = self.service.add_active('ONT1', ActiveProperties(kind='ONT', count=1), pt(2300, 0))
        ports = self.service.ports_of(splitter)
        first, last = self.service.ports_of(olt)[0]['port_id'], self.service.ports_of(ont)[0]['port_id']
        self.service.join_nodes(first, self.service.ends_of(cable, 'A')[0]['end_id'], 'patch')
        self.service.join_nodes(self.service.ends_of(cable, 'B')[0]['end_id'], ports[0]['port_id'])
        self.service.join_nodes(ports[1]['port_id'], last, 'drop')
        return cable, first, last

    def test_m8_path_budget_counts_real_splices_and_one_splitter_branch(self):
        from fiberq_designer.active_layout import ActiveProperties
        from fiberq_designer.workspace_model import budget_calculation
        cable, first, last = self.m8_network()
        identity = self.service.ends_of(cable, 'A')[0]['fiber_id']
        self.service.add_splice_marks([identity], pt(500, 0))
        self.service.cut_fibers([identity], pt(800, 0))
        with self.assertRaisesRegex(ValueError, 'No continuous'):
            self.service.route_report(first, last)
        cut = self.repo.features('fiber_cuts')[0]
        self.service.join_nodes(cut['left_id'], cut['right_id'])
        sibling = self.service.add_active('ONT2', ActiveProperties(kind='ONT', count=1), pt(2300, -200))
        splitter = next(e for e in self.repo.features('elements') if e['kind'] == 'SPLITTER')
        self.service.join_nodes(self.service.ports_of(splitter['obj_id'])[2]['port_id'], self.service.ports_of(sibling)[0]['port_id'], 'drop')
        before, history = self.repo.snapshot(), len(self.repo.history)
        report = self.service.route_report(first, last)
        self.assertEqual((3, 2, [10.5], 1000), (report['splices'], report['connectors'], report['splitter_losses'], report['known_length_m']))
        result = budget_calculation(1, report['splices'], report['connectors'], report['splitter_losses'])
        self.assertAlmostEqual(14.64, result['total_db'])
        self.assertNotIn(self.service.ports_of(sibling)[0]['port_id'], report['nodes'])
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual(history, len(self.repo.history))

    def test_m8_bom_and_checks_include_actual_counts_and_exclude_latent_warning(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        self.service.add_closure('N-CHECK', ClosureProperties(), cable, pt(400, 0))
        self.service.add_closure('NL-CHECK', ClosureProperties(closure_type='NL'), cable, pt(900, 0))
        fiber = self.repo.features('fibers')[0]['fiber_id']
        self.service.set_fiber_data([fiber], purpose='nmWork')
        self.service.cut_fibers([fiber], pt(700, 0))
        self.service.add_slack(pt(200, -200), 15, cable)
        before = self.repo.snapshot()
        report = self.service.bill_of_materials()
        self.assertEqual(12, sum(float(row[3]) for row in report['rows'] if row[0] == 'Fiber'))
        self.assertTrue(any(row[0] == 'Slack' for row in report['rows']))
        checks = self.service.network_checks()
        self.assertTrue(any(row[1] == 'N-CHECK' for row in checks['rows']))
        self.assertFalse(any(row[1] == 'NL-CHECK' for row in checks['rows']))
        self.assertEqual(2, sum(row[1] == 'Working fiber' for row in checks['rows']))
        self.assertEqual(before, self.repo.snapshot())

    def test_m8_saved_annotation_move_delete_undo_reopen_and_staleness(self):
        from fiberq_designer.annotation_layout import table_payload
        cable, first, last = self.m8_network()
        dock = self.open_dock()
        dock.start_annotation('bom', table_payload(self.service.bill_of_materials()))
        before = self.repo.snapshot()
        dock.canvas_move(pt(0, -600))
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(pt(0, -600), MOUSE_RIGHT)
        self.assertEqual(before, self.repo.snapshot())
        dock.start_annotation('bom', table_payload(self.service.bill_of_materials()))
        dock.canvas_click(pt(0, -600), MOUSE_LEFT)
        record = self.repo.features('annotations')[0]
        self.assertIn('created_at', __import__('json').loads(record['payload']))
        original = record['payload']
        dock.move_selected_annotation()
        dock.canvas_click(pt(100, -700), MOUSE_LEFT)
        self.assertEqual(pt(100, -700), self.repo.find('annotations', record['annotation_id']).geometry().asPoint())
        self.service.set_fiber_data([self.repo.features('fibers')[0]['fiber_id']], description='Changed')
        dock.refresh()
        self.assertTrue(any('STALE REPORT' in f['text'] for f in dock.scene.housing_labels.getFeatures()))
        self.assertEqual(original, self.repo.find('annotations', record['annotation_id'])['payload'])
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        snapshot = self.repo.snapshot()
        dock.select('annotations', record['annotation_id'])
        dock.delete_selected()
        self.repo.undo()
        self.assertEqual(snapshot, self.repo.snapshot())

    def test_m8_settings_custom_catalogue_colors_survive_removal_and_reopen(self):
        from fiberq_designer.cable_dialog import CableDialog
        values = self.service.settings()
        values['catalogues'] = [dict(category='kbAerial', name='CUSTOM-4', capacities=[[2, 6], [4, 12]])]
        values['color_codes'] = [dict(name='CUSTOM-CODE', colors=[6, 2, 0], suffixes=['-a', '-b'])]
        values['color_code'] = 'CUSTOM-CODE'
        self.service.save_settings(values)
        dialog = CableDialog(settings=values)
        dialog.model.setCurrentText('CUSTOM-4')
        dialog.select_capacity(2, 6)
        dialog.color_code.setCurrentText('CUSTOM-CODE')
        props = dialog.properties()
        cable = self.service.add_catalog_cable('', props, [pt(0, 0), pt(1600, 0)])
        self.assertEqual('#ff0000', self.repo.features('fibers')[0]['color'])
        before = self.service.fiber_scene().drawn_fibers
        values.update(catalogues=[], color_codes=[], color_code='TIA-598')
        self.service.save_settings(values)
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        NetworkService(reopened).validate()
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        self.assertEqual(before[self.repo.features('fibers')[0]['fiber_id']].asWkt(), self.service.fiber_scene().drawn_fibers[self.repo.features('fibers')[0]['fiber_id']].asWkt())

    def test_m8_snap_hover_exact_join_tolerance_cancel_and_occupied_splice(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('A', CableProperties(), [pt(0, 0), pt(1200, 0)])
        second = self.service.add_catalog_cable('B', CableProperties(), [pt(1600, 0), pt(2800, 0)])
        dock = self.open_dock()
        dock.fit()
        dock.start_joint()
        before = self.repo.snapshot()
        dock.canvas_move(pt(1201, 1))
        self.assertTrue(dock.click_tool.snap_marker.isVisible())
        self.assertIn('Snap:', dock.status.full_text)
        dock.canvas_click(pt(1201, 1), MOUSE_LEFT)
        dock.canvas_move(pt(1601, 1))
        self.assertTrue(dock.click_tool.snap_marker.isVisible())
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(pt(1601, 1), MOUSE_LEFT)
        joint = self.repo.features('fiber_joints')[0]
        self.assertEqual([pt(1200, 0), pt(1400, 0), pt(1600, 0)], joint.geometry().asPolyline())
        dock.canvas_move(pt(1400, 0))
        self.assertIn('splice point', dock.status.full_text)
        dock.join_snap_action.trigger()
        self.assertFalse(self.service.settings()['snap'])
        self.repo.undo()
        dock.refresh()
        self.assertTrue(dock.join_snap_action.isChecked())
        self.service.validate()

    def test_m8_budget_and_reports_production_dialogs_no_write_until_placement(self):
        from fiberq_designer.report_dialogs import BudgetDialog, export_report_csv
        cable, first, last = self.m8_network()
        before = self.repo.snapshot()
        dialog = BudgetDialog(None, self.service, first, last)
        self.assertTrue(dialog.load_path())
        self.assertEqual(1, dialog.km.value())
        self.assertTrue(dialog.calculate())
        self.assertAlmostEqual(14.44, dialog.result['inputs']['total_db'])
        path = Path(self.directory.name) / 'budget.csv'
        export_report_csv(dialog.result, path)
        self.assertIn('14.440', path.read_text(encoding='utf-8-sig'))
        self.assertEqual(before, self.repo.snapshot())
        dock = self.open_dock()
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().place_table())
        dock.command_actions['bom_report'].trigger()
        self.assertEqual('annotation_place', dock.mode)
        self.assertEqual(before, self.repo.snapshot())
        dock.canvas_click(pt(0, -400), MOUSE_LEFT)
        self.assertEqual(1, len(self.repo.features('annotations')))

    def test_m8_settings_language_scoped_cancel_save_undo_and_reopen(self):
        dock = self.open_dock()
        before = self.repo.snapshot()
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        dock.command_actions['settings'].trigger()
        self.assertEqual(before, self.repo.snapshot())
        def save():
            dialog = QApplication.activeModalWidget()
            dialog.language.setCurrentIndex(dialog.language.findData('sr'))
            dialog.tolerance.setValue(14)
            dialog.accept()
        QTimer.singleShot(0, save)
        dock.command_actions['settings'].trigger()
        self.assertEqual('sr', self.service.settings()['language'])
        self.assertEqual('Izvestaji', dock.ribbon_tabs.tabText(4))
        self.assertEqual('Podesavanja…', dock.command_actions['settings'].text())
        self.assertEqual('Pan', self.iface.pan.text())
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        self.repo.undo()
        dock.refresh()
        self.assertEqual('Reports', dock.ribbon_tabs.tabText(4))

    def test_m8_unicode_report_and_equipment_text_survive_dxf_and_layout_exports(self):
        from fiberq_designer.active_layout import ActiveProperties
        from fiberq_designer.annotation_layout import table_payload
        from fiberq_designer.reports import budget_report
        from fiberq_designer.workspace_model import budget_calculation
        self.service.add_active('ONT-Čačak', ActiveProperties(kind='ONT', count=1, address='Željko · λ'), pt(0, 0))
        report = budget_report(budget_calculation(2, 8, 4, [7.3, 10.5]))
        self.service.add_annotation('budget', table_payload(report), pt(0, -150))
        dock = self.open_dock()
        dock.fit()
        expected = Counter(f['text'] for f in dock.scene.housing_labels.getFeatures())
        path = Path(self.directory.name) / 'unicode.dxf'
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), str(path))
        self.assertTrue(path.read_bytes().isascii())
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(path))
            actual = Counter(f.GetField('Text') for f in dataset.GetLayer(0) if f.GetField('Layer').endswith('housing labels'))
            self.assertEqual(expected, actual)
            dataset = None
        for extension in ('pdf', 'svg'):
            target = str(Path(self.directory.name) / ('unicode.' + extension))
            exports.export_layout(dock.make_layout(), target, extension)
            self.assertGreater(Path(target).stat().st_size, 1000)

    def test_m8_invalid_settings_annotation_and_failed_write_preserve_workspace(self):
        from fiberq_designer.annotation_layout import legend_payload
        identity = self.service.add_annotation('legend', legend_payload(self.service.settings()), pt(0, 0))
        dock = self.open_dock()
        original = self.repo.find('annotations', identity)['payload']
        with patch.object(self.repo, 'before_commit', None):
            with self.repo.command('Inject invalid legend'):
                self.repo.update('annotations', self.repo.find('annotations', identity).id(), {'payload': '{"version":999}'})
        with self.assertRaises(ValueError):
            dock.attach(self.path)
        self.assertIs(self.repo, dock.repository)
        with patch.object(self.repo, 'before_commit', None):
            with self.repo.command('Restore legend'):
                self.repo.update('annotations', self.repo.find('annotations', identity).id(), {'payload': original})
        before = self.repo.snapshot()
        with patch.object(self.repo, 'insert', side_effect=RuntimeError('Injected settings write failure')):
            with self.assertRaisesRegex(RuntimeError, 'Injected settings'):
                self.service.save_settings(self.service.settings())
        self.assertEqual(before, self.repo.snapshot())
        self.service.validate()

    def test_m8_v8_copied_migration_preserves_optical_network_and_original_hash(self):
        from fiberq_designer.schema import KEY_VERSIONS
        self.m8_network()
        original = self.repo.snapshot()
        legacy = Path(self.directory.name) / 'v8.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            for key, version in KEY_VERSIONS.items():
                if version >= 9:
                    index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == 'fqsd_' + key)
                    self.assertEqual(0, dataset.DeleteLayer(index))
            layer = dataset.GetLayerByName('fqsd_cables')
            self.assertEqual(0, layer.DeleteField(layer.GetLayerDefn().GetFieldIndex('catalogue_data')))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='8' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(legacy)
        self.assertTrue(migrated.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(original, migrated.snapshot())
        NetworkService(migrated).validate()


    def m9a_pair(self, kind='ODF', capacity=48, side='A', angle=0, used=4, splitter=0):
        from fiberq_designer.cable_catalog import CableProperties
        import math
        theta = math.radians(angle)
        world = lambda x, y: pt(x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta))
        first = self.service.add_catalog_cable('', CableProperties(), [world(0, 0), world(1200, 0)])
        props = HousingProperties(kind=kind, capacity=capacity, mounting='rack 19"' if kind in ('ODF', 'PATCH_PANEL') else
                                  'wall' if kind in ('FAT', 'OTB') else 'indoor', requested_used=used, splitter_ratio=splitter)
        housing = self.service.attach_housing(first, side, '', props)
        second = self.service.add_catalog_cable('', CableProperties(), [world(0, -180), world(1200, -180)])
        return first, second, housing

    def test_m9a_three_cables_hanging_priority_stable_connected_ports_and_used_total(self):
        from fiberq_designer.cable_catalog import CableProperties
        first, second, housing = self.m9a_pair()
        original = {p['port_id']: p.geometry().asWkt() for p in self.ports(housing)[:4]}
        ids = [f['fiber_id'] for f in self.repo.features('fibers')]
        self.service.add_housing_cable(housing, second, 'A', 10)
        counts = {r['cable_id']: r['used_count'] for r in self.service.housing_attachments(housing)}
        self.assertEqual({first: 12, second: 2}, counts)
        self.assertEqual(14, self.repo.find('housings', housing)['requested_used'])
        self.service.add_housing_cable(housing, second, 'A')
        third = self.service.add_catalog_cable('', CableProperties(), [pt(0, -360), pt(1200, -360)])
        self.service.add_housing_cable(housing, third, 'A')
        self.assertEqual(36, self.repo.find('housings', housing)['requested_used'])
        self.assertEqual(ids, [f['fiber_id'] for f in self.repo.features('fibers')[:24]])
        self.assertTrue(all(self.repo.find('ports', key).geometry().asWkt() == value for key, value in original.items()))
        self.assertEqual(3, len(self.service.housing_attachments(housing)))
        self.service.validate()

    def test_m9a_families_both_ends_all_directions_preserve_port_ids_and_positions(self):
        for kind, capacity in (('ODF', 48), ('PATCH_PANEL', 48), ('FDH', 48), ('MDU', 48), ('FAT', 24), ('OTB', 24)):
            for side, angle in (('A', 0), ('B', 90), ('A', 180), ('B', 270)):
                first, second, housing = self.m9a_pair(kind, capacity, side, angle)
                old = {e['port_id']: self.repo.find('ports', e['port_id']).geometry().asWkt()
                       for e in self.service.ends_of(first, side) if optional(e['port_id'])}
                self.service.add_housing_cable(housing, second, side)
                self.assertEqual(capacity, len(self.ports(housing)))
                self.assertTrue(all(self.repo.find('ports', key).geometry().asWkt() == value for key, value in old.items()))
                self.assertEqual(24, self.repo.find('housings', housing)['requested_used'])
        self.service.validate()

    def test_m9a_contained_splitter_body_ports_patches_and_box_widen_only(self):
        first, second, housing = self.m9a_pair('FDH', 48, used=1, splitter=8)
        child = self.service.contained_splitter(housing)['splitter_id']
        old_ports = {p['port_id']: p.geometry().asWkt() for p in self.ports(child)}
        old_frame = self.repo.find('frames', child).geometry().asWkt()
        links = {f['link_id'] for f in self.repo.features('links')}
        self.service.add_housing_cable(housing, second, 'A')
        self.assertEqual(24, self.repo.find('housings', housing)['requested_used'])
        self.assertEqual(old_frame, self.repo.find('frames', child).geometry().asWkt())
        self.assertEqual(links, {f['link_id'] for f in self.repo.features('links')})
        self.assertTrue(all(self.repo.find('ports', key).geometry().asWkt() == value for key, value in old_ports.items()))
        first, second, box = self.m9a_pair('FAT', 12, used=1, splitter=8)
        old_box_ports = {p['port_id']: p.geometry().asWkt() for p in self.ports(box)}
        self.assertEqual(0, self.service.housing_cable_plan(box, second, 'A')['limit'])
        self.service.add_housing_cable(box, second, 'A', 0)
        self.assertEqual(1, self.repo.find('housings', box)['requested_used'])
        self.assertTrue(all(self.repo.find('ports', key).geometry().asWkt() == value for key, value in old_box_ports.items()))
        self.service.validate()

    def test_m9a_capacity_overlap_angle_side_and_wrong_column_reject_without_changes(self):
        from fiberq_designer.cable_catalog import CableProperties
        first, second, housing = self.m9a_pair()
        bad = [self.service.add_catalog_cable('', CableProperties(), points) for points in
               ([pt(0, 0), pt(1200, 0)], [pt(0, -180), pt(0, 1000)], [pt(50, -360), pt(1250, -360)])]
        before, history = self.repo.snapshot(), len(self.repo.history)
        for cable, side, additional in ((bad[0], 'A', 1), (bad[1], 'A', 1), (bad[2], 'A', 1), (second, 'B', 1), (second, 'A', 99)):
            with self.assertRaises(ValueError):
                self.service.add_housing_cable(housing, cable, side, additional)
            self.assertEqual(before, self.repo.snapshot())
            self.assertEqual(history, len(self.repo.history))

    def test_m9a_near_column_alignment_and_two_ended_moves_keep_other_connections(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1200, 0)])
        left = self.service.attach_odf(first, 'A', '', OdfProperties(capacity=48, requested_used=12))
        right = self.service.attach_odf(first, 'B', '', OdfProperties(capacity=48, requested_used=12))
        second = self.service.add_catalog_cable('', CableProperties(), [pt(5, -180), pt(1205, -180)])
        self.service.add_housing_cable(left, second, 'A')
        self.service.add_housing_cable(right, second, 'B')
        self.assertEqual(pt(0, -180), self.repo.find('cables', second).geometry().asPolyline()[0])
        self.assertEqual(pt(1200, -180), self.repo.find('cables', second).geometry().asPolyline()[-1])
        right_ports = {p['port_id']: p.geometry().asWkt() for p in self.ports(right)}
        self.service.move_element(left, pt(-100, 200))
        self.assertTrue(all(self.repo.find('ports', key).geometry().asWkt() == value for key, value in right_ports.items()))
        self.service.move_element(right, pt(1400, -100))
        self.assertEqual(pt(-100, 20), self.repo.find('cables', second).geometry().asPolyline()[0])
        self.assertEqual(pt(1400, -280), self.repo.find('cables', second).geometry().asPolyline()[-1])
        self.service.validate()

    def test_m9a_module_targeting_and_frame_overlap_guard(self):
        from fiberq_designer.cable_catalog import CableProperties
        first = self.service.add_catalog_cable('', CableProperties(tubes=2), [pt(0, 0), pt(1200, 0)])
        housing = self.service.attach_odf(first, 'A', '', OdfProperties(capacity=24, modules=2, requested_used=12))
        second = self.service.add_catalog_cable('', CableProperties(), [pt(0, -240), pt(1200, -240)])
        before = self.repo.snapshot()
        with self.assertRaisesRegex(ValueError, 'Module frames overlap'):
            self.service.add_housing_cable(housing, second, 'A', module_no=1)
        self.assertEqual(before, self.repo.snapshot())
        self.service.add_housing_cable(housing, second, 'A', module_no=2)
        modules = {m['module_id']: m['number'] for m in self.repo.features('modules')}
        self.assertEqual({2}, {modules[self.repo.find('ports', e['port_id'])['module_id']] for e in self.service.ends_of(second, 'A')})
        self.service.validate()
        props = odf_properties(self.repo.find('elements', housing), self.repo.find('housings', housing))
        self.service.edit_housing(housing, 'Resized', replace(props, capacity=12))
        # The primary cable's hanging rows still belong to module 1 after a
        # capacity reduction; do not reclassify them by the new port count.
        self.assertEqual(0, self.service.housing_cable_plan(housing, second, 'A', additional=0, module_no=2)['limit'])
        self.service.validate()

    def test_m9a_failed_assignment_is_atomic_and_no_preview_writes(self):
        first, second, housing = self.m9a_pair()
        before, history = self.repo.snapshot(), len(self.repo.history)
        self.service.housing_cable_plan(housing, second, 'A')
        self.assertEqual(before, self.repo.snapshot())
        original, calls = self.repo.update, [0]
        def fail(key, *args, **kwargs):
            if key == 'fiber_ends':
                calls[0] += 1
                if calls[0] == 3:
                    raise RuntimeError('Injected additional termination failure')
            return original(key, *args, **kwargs)
        with patch.object(self.repo, 'update', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected additional'):
                self.service.add_housing_cable(housing, second, 'A')
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual(history, len(self.repo.history))

    def test_m9a_properties_capacity_change_detach_delete_undo_and_reopen(self):
        first, second, housing = self.m9a_pair()
        self.service.add_housing_cable(housing, second, 'A', 10)
        old = {p['port_id']: p.geometry().asWkt() for p in self.ports(housing)}
        props = odf_properties(self.repo.find('elements', housing), self.repo.find('housings', housing))
        self.service.edit_housing(housing, 'RENAMED', replace(props, capacity=72, model='Edited model'))
        self.assertTrue(all(self.repo.find('ports', key).geometry().asWkt() == value for key, value in old.items()))
        with self.assertRaises(ValueError):
            self.service.edit_housing(housing, 'BAD', replace(props, capacity=12))
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        NetworkService(reopened).validate()
        before = self.repo.snapshot()
        self.service.detach_cable_end(second, 'A')
        self.assertEqual(1, len(self.service.housing_attachments(housing)))
        self.assertEqual(12, self.repo.find('housings', housing)['requested_used'])
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.service.delete_cable(second)
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.service.delete_element(housing)
        self.assertFalse(self.repo.features('housing_cables'))
        self.assertEqual(2, len(self.repo.features('cables')))
        self.service.validate()

    def test_m9a_add_dialog_cancel_commit_properties_and_detach_ui(self):
        first, second, housing = self.m9a_pair()
        dock = self.open_dock()
        dock.fit()
        dock.select('elements', housing)
        before = self.repo.snapshot()
        dock.command_actions['add_housing_cable'].trigger()
        dock.canvas_move(pt(0, -180))
        self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        dock.canvas_click(pt(0, -180), MOUSE_LEFT)
        self.assertEqual(before, self.repo.snapshot())
        def add():
            dialog = QApplication.activeModalWidget()
            dialog.additional.setValue(10)
            dialog.accept()
        QTimer.singleShot(0, add)
        dock.canvas_click(pt(0, -180), MOUSE_LEFT)
        self.assertEqual(14, self.repo.find('housings', housing)['requested_used'])
        def edit():
            dialog = QApplication.activeModalWidget()
            self.assertTrue(dialog.used.isReadOnly())
            dialog.model.setText('UI MODEL')
            dialog.accept()
        QTimer.singleShot(0, edit)
        dock.command_actions['properties'].trigger()
        self.assertEqual('UI MODEL', self.repo.find('housings', housing)['model'])
        dock.command_actions['detach_housing_cable'].trigger()
        dock.canvas_click(pt(0, -180), MOUSE_LEFT)
        self.assertIsNone(optional(self.repo.find('cables', second)['from_obj']))
        self.service.validate()

    def test_m9a_existing_front_patch_accepts_a_back_fiber_without_moving_the_port(self):
        from fiberq_designer.cable_catalog import CableProperties
        housing = self.service.add_odf('', OdfProperties(capacity=24, requested_used=0), pt(0, 0))
        other = self.equipment('CABINET', 'PATCH-DEST', 12, -400)
        port = self.ports(housing)[0]
        link = self.service.add_link(port, self.ports(other)[0], 'patch')
        original = self.repo.find('links', link).geometry().asWkt()
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1200, 0)])
        self.service.add_housing_cable(housing, cable, 'A')
        self.assertEqual(port['port_id'], self.service.ends_of(cable, 'A')[0]['port_id'])
        self.assertEqual(original, self.repo.find('links', link).geometry().asWkt())
        self.assertIn(self.ports(other)[0]['port_id'], self.service.trace(self.service.ends_of(cable, 'B')[0]['end_id']))
        self.service.validate()

    def test_m9a_v9_copy_migration_retains_network_and_report_freshness(self):
        from fiberq_designer.annotation_layout import table_payload
        from fiberq_designer.schema import KEY_VERSIONS, V10_FIELDS
        self.m9a_pair()
        report = self.service.add_annotation('bom', table_payload(self.service.bill_of_materials()), pt(0, -800))
        original = self.repo.snapshot()
        snapshot = {k: v for k, v in original.items() if k != 'annotations'}
        legacy_state = {k: [(fid, {name: value for name, value in attrs.items() if name not in V10_FIELDS.get(k, set())}, wkb)
                            for fid, attrs, wkb in records] for k, records in snapshot.items() if KEY_VERSIONS[k] <= 9}
        import json
        old_hash = hashlib.sha256(json.dumps(legacy_state, sort_keys=True, default=str).encode('utf-8')).hexdigest()
        payload = json.loads(self.repo.find('annotations', report)['payload'])
        payload['source_hash'] = old_hash
        legacy = Path(self.directory.name) / 'v9.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            for future in ('fqsd_fiber_purposes', 'fqsd_drawing_languages', 'fqsd_outlets', 'fqsd_housing_cables'):
                index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == future)
                self.assertEqual(0, dataset.DeleteLayer(index))
            layer = dataset.GetLayerByName('fqsd_housings')
            self.assertEqual(0, layer.DeleteField(layer.GetLayerDefn().GetFieldIndex('layout_state')))
            annotations = dataset.GetLayerByName('fqsd_annotations')
            saved = annotations.GetNextFeature()
            saved.SetField('payload', json.dumps(payload, ensure_ascii=False))
            self.assertEqual(0, annotations.SetFeature(saved))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='9' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        migrated = GeoPackageRepository.open(legacy)
        self.assertTrue(migrated.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(original, migrated.snapshot())
        self.assertFalse(any('STALE REPORT' in f['text'] for f in NetworkService(migrated).fiber_scene().housing_labels.getFeatures()))

    def test_m9a_bad_attachment_rejected_before_switch_and_missing_primary_detected(self):
        first, second, housing = self.m9a_pair()
        self.service.add_housing_cable(housing, second, 'A')
        dock = self.open_dock()
        record = self.service.housing_attachments(housing)[0]
        with patch.object(self.repo, 'before_commit', None):
            with self.repo.command('Inject bad multi-cable count'):
                self.repo.update('housing_cables', record.id(), {'used_count': 99})
        with self.assertRaisesRegex(ValueError, 'count is stale'):
            dock.attach(self.path)
        self.assertIs(self.repo, dock.repository)
        self.repo.undo()
        with patch.object(self.repo, 'before_commit', None):
            with self.repo.command('Remove primary attachment record'):
                self.repo.delete('housing_cables', [record.id()])
        with self.assertRaisesRegex(ValueError, 'Missing housing cable'):
            self.service.validate()
        self.repo.undo()
        self.service.validate()


    def m9b_properties(self, tubes=2, fibers=6):
        from fiberq_designer.cable_catalog import CableProperties
        return CableProperties(category='', catalog_model='', model='Distribution', tubes=tubes, fibers_per_tube=fibers)

    def m9b_housing(self):
        first, unused, housing = self.m9a_pair()
        self.service.delete_cable(unused)
        return first, None, housing

    def test_m9b_elbow_exact_stagger_repeated_outlet_and_no_automatic_splices(self):
        first, unused, housing = self.m9b_housing()
        connected = {e['port_id']: self.repo.find('ports', e['port_id']).geometry().asWkt()
                     for e in self.service.ends_of(first, 'A') if optional(e['port_id'])}
        before = self.repo.snapshot()
        plan = self.service.outlet_plan(housing, self.m9b_properties(), 40)
        self.assertEqual(before, self.repo.snapshot())
        self.assertAlmostEqual(-10.4, plan['context']['first_row'])
        outlet = self.service.add_outlet(housing, self.m9b_properties(), name='OUT1')
        self.assertEqual(4, self.repo.find('housings', housing)['requested_used'])
        self.assertFalse(self.repo.features('fiber_joints'))
        self.assertTrue(all(optional(e['port_id']) is None for e in self.service.ends_of(outlet)))
        scene = self.service.fiber_scene()
        fibers = [f for f in self.repo.features('fibers') if f['cable_id'] == outlet]
        for index, fiber in enumerate(fibers):
            actual = scene.drawn_fibers[fiber['fiber_id']].asPolyline()
            for a, b in zip(actual, plan['paths'][index]):
                self.assertLess(a.distance(b), 1e-6)
        old = {f['fiber_id']: scene.drawn_fibers[f['fiber_id']].asWkt() for f in fibers}
        second_plan = self.service.outlet_plan(housing, self.m9b_properties())
        self.assertTrue(second_plan['context']['repeated'])
        self.assertAlmostEqual(-13.2, second_plan['context']['bend_column'])
        self.service.add_outlet(housing, self.m9b_properties(), name='OUT2')
        scene = self.service.fiber_scene()
        self.assertTrue(all(scene.drawn_fibers[key].asWkt() == value for key, value in old.items()))
        self.assertTrue(all(self.repo.find('ports', key).geometry().asWkt() == value for key, value in connected.items()))
        self.service.validate()

    def test_m9b_all_housing_families_and_rotated_elbows(self):
        for index, (kind, angle) in enumerate((('ODF', 0), ('PATCH_PANEL', 90), ('FDH', 180), ('MDU', 270), ('FAT', 0), ('OTB', 90))):
            housing = self.service.add_housing('', HousingProperties(kind=kind, capacity=24, requested_used=0,
                orientation=angle, mounting='rack 19"' if kind in ('ODF', 'PATCH_PANEL') else 'wall' if kind in ('FAT', 'OTB') else 'indoor'), pt(index * 1200, 0))
            outlet = self.service.add_outlet(housing, self.m9b_properties(1, 4), 12)
            self.assertEqual(housing, self.repo.find('cables', outlet)['to_obj'])
            self.assertEqual('B', self.service.housing_attachments(housing)[0]['side'])
            self.assertEqual(0, self.repo.find('housings', housing)['requested_used'])
        self.service.validate()

    def test_m9b_straight_top_ends_stagger_without_expanding_housing(self):
        first, unused, housing = self.m9b_housing()
        frame = self.repo.find('frames', housing).geometry().asWkt()
        old = {e['end_id']: e.geometry().asWkt() for e in self.service.ends_of(first)}
        plan = self.service.outlet_plan(housing, self.m9b_properties(), mode='straight')
        outlet = self.service.add_outlet(housing, self.m9b_properties(), mode='straight')
        self.assertEqual(frame, self.repo.find('frames', housing).geometry().asWkt())
        self.assertIsNone(optional(self.repo.find('cables', outlet)['from_obj']))
        for index, end in enumerate(self.service.ends_of(outlet, 'A')):
            self.assertLess(end.geometry().asPoint().distance(plan['paths'][index][0]), 1e-6)
        scene = self.service.fiber_scene()
        for fiber in self.repo.features('fibers'):
            if fiber['cable_id'] == outlet:
                path = scene.drawn_fibers[fiber['fiber_id']]
                self.assertEqual(2, len(path.asPolyline()))
                self.assertTrue(path.isSimple())
        self.assertTrue(all(self.repo.find('fiber_ends', key).geometry().asWkt() == value for key, value in old.items()))
        self.service.validate()

    def test_m9b_closure_expands_left_and_bottom_retains_through_fibers_and_accepts_join(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1600, 0)])
        closure = self.service.add_closure('', ClosureProperties(closure_type='NL'), cable, pt(600, 0))
        record = self.repo.find('closures', closure)
        element = self.repo.find('elements', closure)
        right, top = element.geometry().asPoint().x() + record['width'] * 10, record['top']
        original = {f['fiber_id']: self.service.fiber_scene().drawn_fibers[f['fiber_id']].asWkt() for f in self.repo.features('fibers')}
        outlet = self.service.add_outlet(closure, self.m9b_properties(2, 6), mode='straight')
        self.assertFalse(self.repo.features('fiber_cuts'))
        new = self.repo.find('closures', closure)
        anchor = self.repo.find('elements', closure).geometry().asPoint()
        self.assertLess(anchor.x(), 600)
        self.assertAlmostEqual(right, anchor.x() + new['width'] * 10)
        self.assertEqual(top, new['top'])
        self.assertLess(new['bottom'], record['bottom'])
        scene = self.service.fiber_scene()
        self.assertTrue(all(scene.drawn_fibers[key].asWkt() == value for key, value in original.items()))
        incoming = [f['fiber_id'] for f in self.repo.features('fibers') if f['cable_id'] == cable][:2]
        self.service.cut_fibers(incoming, pt(630, 0))
        cuts = self.repo.features('fiber_cuts')
        ends = self.service.ends_of(outlet, 'A')
        self.service.join_group([c['left_id'] for c in cuts], [e['end_id'] for e in ends[:2]])
        start = self.service.ends_of(cable, 'A')[0]['end_id']
        self.assertIn(self.service.ends_of(outlet, 'B')[0]['end_id'], self.service.trace(start))
        original_joints = {j['joint_id']: j.geometry().asWkt() for j in self.repo.features('fiber_joints')}
        self.service.add_outlet(closure, self.m9b_properties(1, 4), mode='straight')
        self.assertTrue(all(self.repo.find('fiber_joints', key).geometry().asWkt() == value for key, value in original_joints.items()))
        self.assertIn(self.service.ends_of(outlet, 'B')[0]['end_id'], self.service.trace(start))
        self.service.validate()

    def test_m9b_add_terminations_far_end_attachment_moves_and_reopen(self):
        first, unused, housing = self.m9b_housing()
        outlet = self.service.add_outlet(housing, self.m9b_properties())
        self.service.add_housing_cable(housing, outlet, 'B', additional=10)
        self.assertEqual(14, self.repo.find('housings', housing)['requested_used'])
        far = self.service.attach_housing(outlet, 'A', '', HousingProperties(kind='OTB', capacity=12, mounting='wall'))
        far_ports = {p['port_id']: p.geometry().asWkt() for p in self.ports(far)}
        self.service.move_element(housing, pt(-100, 20))
        self.assertTrue(all(self.repo.find('ports', key).geometry().asWkt() == value for key, value in far_ports.items()))
        first_port = self.service.ends_of(outlet, 'B')[0]['port_id']
        self.assertIn(self.service.ends_of(outlet, 'A')[0]['port_id'], self.service.trace(first_port))
        self.service.move_element(far, pt(-116, -800))
        self.service.validate()
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        NetworkService(reopened).validate()

    def test_m9b_source_restrictions_module_target_and_failed_insert_are_atomic(self):
        props = self.m9b_properties()
        mirrored = self.service.add_housing('', OdfProperties(mirrored=True), pt(0, 0))
        split = self.service.add_housing('', HousingProperties(kind='FDH', capacity=48, splitter_ratio=8, mounting='indoor'), pt(1400, 0))
        for identity in (mirrored, split):
            before = self.repo.snapshot()
            with self.assertRaises(ValueError):
                self.service.add_outlet(identity, props)
            self.assertEqual(before, self.repo.snapshot())
        housing = self.service.add_housing('', OdfProperties(capacity=24, modules=2, requested_used=0), pt(2800, 0))
        before = self.repo.snapshot()
        with self.assertRaisesRegex(ValueError, 'Module frames overlap'):
            self.service.add_outlet(housing, props, module_no=1)
        self.assertEqual(before, self.repo.snapshot())
        original = self.repo.insert
        def failing(key, *args, **kwargs):
            if key == 'outlets':
                raise RuntimeError('Injected outlet failure')
            return original(key, *args, **kwargs)
        with patch.object(self.repo, 'insert', failing):
            with self.assertRaisesRegex(RuntimeError, 'Injected outlet'):
                self.service.add_outlet(housing, props, module_no=2)
        self.assertEqual(before, self.repo.snapshot())
        self.service.add_outlet(housing, props, module_no=2)
        self.service.validate()

    def test_m9b_production_dialog_preview_cancel_commit_and_edit(self):
        from fiberq_designer.outlet_dialog import OutletDialog
        first, unused, housing = self.m9b_housing()
        dock = self.open_dock()
        dock.select('elements', housing)
        before = self.repo.snapshot()
        dialog = OutletDialog(dock, housing)
        dialog.length.setValue(12)
        dialog.fibers.setValue(4)
        self.assertIsNotNone(dialog.plan)
        dialog.reject()
        self.assertEqual(before, self.repo.snapshot())
        QTimer.singleShot(0, lambda: dock.outlet_dialog.reject())
        dock.command_actions['frame_outlet'].trigger()
        self.assertEqual(before, self.repo.snapshot())
        def accept():
            dock.outlet_dialog.label.setText('OUT-UI')
            dock.outlet_dialog.accept()
        dock.select('elements', housing)
        QTimer.singleShot(0, accept)
        dock.command_actions['frame_outlet'].trigger()
        cable = self.repo.features('outlets')[0]['cable_id']
        self.assertEqual('OUT-UI', self.repo.find('cables', cable)['name'])
        dock.select('cables', cable)
        QTimer.singleShot(0, lambda: (dock.outlet_dialog.model.setText('Edited outlet'), dock.outlet_dialog.accept()))
        dock.edit_selected()
        self.assertEqual('Edited outlet', self.repo.find('cables', cable)['model'])
        self.service.validate()

    def test_m9b_delete_detach_undo_and_all_exports_keep_stable_fibers(self):
        first, unused, housing = self.m9b_housing()
        outlet = self.service.add_outlet(housing, self.m9b_properties(), mode='straight')
        elbow = self.service.add_outlet(housing, self.m9b_properties())
        self.service.validate()
        dock = self.open_dock()
        dock.fit()
        for kind in ('PDF', 'SVG', 'DXF'):
            target = Path(self.directory.name) / ('outlets.' + kind.lower())
            if kind == 'DXF':
                exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), str(target))
            else:
                exports.export_layout(dock.make_layout(), str(target), kind.lower())
            self.assertGreater(target.stat().st_size, 100)
        before = self.repo.snapshot()
        self.service.detach_cable_end(elbow, 'B')
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.service.delete_element(housing)
        self.assertEqual(2, len(self.repo.features('outlets')))
        self.assertTrue(all(optional(o['frame_id']) is None for o in self.repo.features('outlets')))
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.service.delete_cable(outlet)
        self.assertIsNone(self.repo.find('outlets', outlet))
        self.service.validate()
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())

    def test_m9b_v10_copy_migration_retains_multi_cable_graph_and_current_report(self):
        import json
        from fiberq_designer.annotation_layout import table_payload
        first, second, housing = self.m9a_pair()
        self.service.add_housing_cable(housing, second, 'A')
        report = self.service.add_annotation('bom', table_payload(self.service.bill_of_materials()), pt(0, -800))
        before = self.repo.snapshot()
        legacy_state = {k: v for k, v in before.items() if k not in ('fiber_purposes', 'drawing_languages', 'outlets', 'annotations')}
        old_hash = hashlib.sha256(json.dumps(legacy_state, sort_keys=True, default=str).encode('utf-8')).hexdigest()
        payload = json.loads(self.repo.find('annotations', report)['payload'])
        payload['source_hash'] = old_hash
        legacy = Path(self.directory.name) / 'v10.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as target:
            source.backup(target)
        dataset = ogr.Open(str(legacy), 1)
        for future in ('fqsd_fiber_purposes', 'fqsd_drawing_languages', 'fqsd_outlets'):
            index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == future)
            self.assertEqual(0, dataset.DeleteLayer(index))
        annotations = dataset.GetLayerByName('fqsd_annotations')
        saved = annotations.GetNextFeature()
        saved.SetField('payload', json.dumps(payload))
        self.assertEqual(0, annotations.SetFeature(saved))
        dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='10' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        reopened = GeoPackageRepository.open(legacy)
        self.assertTrue(reopened.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(before['housing_cables'], reopened.snapshot()['housing_cables'])
        migrated = NetworkService(reopened)
        self.assertFalse(any('STALE REPORT' in f['text'] for f in migrated.fiber_scene().housing_labels.getFeatures()))
        migrated.validate()

    def test_m9b_corrupt_outlet_rejected_before_workspace_switch(self):
        first, unused, housing = self.m9b_housing()
        outlet = self.service.add_outlet(housing, self.m9b_properties(), mode='straight')
        dock = self.open_dock()
        with patch.object(self.repo, 'before_commit', None):
            with self.repo.command('Inject bad outlet'):
                self.repo.update('outlets', self.repo.find('outlets', outlet).id(), {'mode': 'bad'})
        with self.assertRaisesRegex(ValueError, 'outlet record'):
            dock.attach(self.path)
        self.assertIs(self.repo, dock.repository)
        self.repo.undo()
        self.service.validate()


    def m9c_language(self, language):
        values = self.service.settings()
        values['language'] = language
        self.service.save_settings(values)

    def test_m9c_source_names_and_frozen_drawing_captions(self):
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        old_cable = self.service.add_catalog_cable('', CableProperties(), [pt(0, 0), pt(1200, 0)])
        old_house = self.service.add_housing('', HousingProperties(kind='FDH', capacity=24, mounting='indoor', model='Free'), pt(0, -500))
        before = self.repo.snapshot()
        self.m9c_language('sr')
        for key, records in before.items():
            if key != 'workspace_settings':
                self.assertEqual(records, self.repo.snapshot()[key], key)
        new_house = self.service.add_housing('', HousingProperties(kind='FDH', capacity=24, mounting='indoor', model='Free'), pt(1800, -500))
        box = self.service.add_housing('', HousingProperties(kind='FAT', capacity=12, mounting='wall'), pt(2000, -500))
        new_cable = self.service.add_catalog_cable('', CableProperties(), [pt(1800, 0), pt(3000, 0)])
        closure = self.service.add_closure('Working', ClosureProperties(location_number='Free'), new_cable, pt(2300, 0))
        self.assertEqual('FDH 1', self.repo.find('elements', old_house)['name'])
        self.assertEqual('ODO 1', self.repo.find('elements', new_house)['name'])
        self.assertEqual('ZOK 1', self.repo.find('elements', box)['name'])
        scene = self.service.fiber_scene()
        labels = lambda key: [f['text'] for f in scene.housing_labels.getFeatures() if f['obj_id'] == key]
        self.assertIn('indoor', labels(old_house))
        self.assertIn('unutra', labels(new_house))
        self.assertIn('okno Free', labels(closure))
        self.assertIn('Working', labels(closure))
        self.assertIn('1.Blue', labels(old_cable))
        self.assertIn('1.Plava', labels(new_cable))
        self.assertTrue(any('Free' in value for value in labels(new_house)))
        self.m9c_language('en')
        scene = self.service.fiber_scene()
        self.assertIn('unutra', labels(new_house))
        props = odf_properties(self.repo.find('elements', new_house), self.repo.find('housings', new_house))
        self.service.edit_housing(new_house, 'ODO 1', replace(props, model='Working'))
        self.assertEqual('ODO 1', self.repo.find('elements', new_house)['name'])
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(self.repo.snapshot(), reopened.snapshot())
        NetworkService(reopened).validate()

    def test_m9c_live_dialog_translation_keeps_values_and_qgis_scope(self):
        from fiberq_designer.housing_dialog import HousingDialog
        from fiberq_designer.outlet_dialog import OutletDialog
        from fiberq_designer.housing_cable_dialog import AddHousingCableDialog
        from fiberq_designer.cable_catalog import properties_from_record
        from fiberq_designer.operation_dialogs import FiberDataDialog
        from fiberq_designer.closure_layout import ClosureProperties
        from qgis.PyQt.QtWidgets import QLabel
        self.m9c_language('sr')
        first, unused, housing = self.m9a_pair()
        for identity, name in ((first, 'Working'), (unused, 'Warning')):
            record = self.repo.find('cables', identity)
            self.service.edit_catalog_cable(identity, name, properties_from_record(record), record['length_m'])
        closure = self.service.add_closure('Free', ClosureProperties(), first, pt(500, 0))
        dock = self.open_dock()
        host_label = QLabel('Settings', self.iface.window)
        host_label.show()
        before = self.repo.snapshot()
        dialog = HousingDialog(dock, HousingProperties(kind='FDH', capacity=24, mounting='indoor', model='Free', address='Working'))
        dialog.show()
        self.app.processEvents()
        self.assertEqual('unutra', dialog.mounting.currentText())
        self.assertEqual('indoor', dialog.properties().mounting)
        self.assertEqual('Free', dialog.model.text())
        self.assertEqual('Working', dialog.address.text())
        self.assertEqual('(nema upisanih)', dialog.models.currentText())
        self.assertIn('ODO ', dialog.preview.text())
        self.assertIn('Free', dialog.preview.text())
        dialog.mounting.setCurrentIndex(dialog.mounting.findData('outdoor'))
        self.app.processEvents()
        self.assertEqual('outdoor', dialog.properties().mounting)
        self.assertIn('spolja', dialog.preview.text())
        dialog.used.setText('999999')
        dialog.accept()
        self.app.processEvents()
        self.assertIn('Zauzeto', dialog.error.text())
        dialog.reject()
        outlet = OutletDialog(dock, closure)
        outlet.show()
        self.app.processEvents()
        self.assertIn('Free', [label.text() for label in outlet.findChildren(QLabel)])
        self.assertIn('izvod iz rama', outlet.windowTitle())
        outlet.fibers.setValue(8)
        self.app.processEvents()
        self.assertIn('vlak', outlet.summary.text())
        self.assertIn('Ne stvaraju se nove opticke veze.', outlet.summary.text())
        outlet.reject()
        additional = AddHousingCableDialog(dock, housing, unused, 'A')
        additional.show()
        self.app.processEvents()
        self.assertIn('Working: dodatno', additional.summary.text())
        self.assertIn('Warning: dodatno', additional.summary.text())
        additional.reject()
        data = FiberDataDialog(dock)
        data.show()
        self.app.processEvents()
        self.assertEqual('Zadrzi trenutnu namenu', data.purpose.currentText())
        self.assertEqual('Zadrzi trenutnu vrstu', data.fiber_type.currentText())
        self.assertIsNone(data.purpose.currentData())
        self.assertIsNone(data.fiber_type.currentData())
        self.assertTrue(any(label.text().startswith('Izabrano vlakno') for label in data.findChildren(QLabel)))
        data.reject()
        dock.select('fibers', self.repo.features('fibers')[0]['fiber_id'])
        self.assertIn('Slobodan kraj B', dock.details.text())
        self.assertEqual('Settings', host_label.text())
        self.assertEqual(before, self.repo.snapshot())
        self.m9c_language('en')
        dock.apply_workspace_settings()
        self.app.processEvents()
        self.assertEqual('Frame outlet…', dock.command_actions['frame_outlet'].text())
        self.assertEqual('Settings', host_label.text())
        host_label.close()

    def test_m9c_reports_csv_and_annotations_preserve_control_word_user_data(self):
        import csv
        import json
        from fiberq_designer.annotation_layout import table_payload
        from fiberq_designer.cable_catalog import CableProperties
        from fiberq_designer.closure_layout import ClosureProperties
        from fiberq_designer.report_dialogs import ReportDialog, export_report_csv
        self.m9c_language('sr')
        cable = self.service.add_catalog_cable('Working', CableProperties(model='Free'), [pt(0, 0), pt(1200, 0)])
        fiber = next(f for f in self.repo.features('fibers') if f['cable_id'] == cable)
        self.service.set_fiber_data([fiber['fiber_id']], purpose='nmWork', description='Warning {customer}')
        self.service.add_closure('Working', ClosureProperties(), cable, pt(500, 0))
        report = self.service.bill_of_materials()
        payload = table_payload(report, 'sr')
        self.assertTrue(any('Free' in row[1] and 'vlak' in row[1] for row in payload['rows']))
        self.assertTrue(any('Radno' in row[1] and 'Free' in row[1] for row in payload['rows']))
        self.assertTrue(any(row[0] == 'Vlakno' for row in payload['rows']))
        checks = table_payload(self.service.network_checks(), 'sr')
        self.assertTrue(any(row[1] == 'Working' and 'nastavak' in row[2] for row in checks['rows']))
        dock = self.open_dock()
        dialog = ReportDialog(dock, report, 'sr')
        for row, values in enumerate(payload['rows']):
            self.assertEqual(values, [dialog.table.item(row, column).text() for column in range(len(values))])
        target = Path(self.directory.name) / 'serbian.csv'
        export_report_csv(report, target, 'sr')
        with target.open(encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.reader(stream))
        self.assertEqual(payload['rows'], exported[2:2 + len(payload['rows'])])
        ends = self.service.ends_of(cable)
        route = self.service.route_report(ends[0]['end_id'], next(e['end_id'] for e in ends if e['fiber_id'] == ends[0]['fiber_id'] and e['side'] != ends[0]['side']))
        shown = table_payload(route, 'sr')
        self.assertIn('Working', shown['rows'][0][1])
        self.assertIn('Warning {customer}', shown['rows'][0][4])
        annotation = self.service.add_annotation('bom', payload, pt(0, -500))
        saved = self.repo.find('annotations', annotation)['payload']
        self.m9c_language('en')
        self.assertEqual(saved, self.repo.find('annotations', annotation)['payload'])
        self.assertEqual('sr', json.loads(saved)['language'])
        self.service.validate()

    def test_m9c_v11_copy_migration_preserves_names_captions_and_current_reports(self):
        import json
        from fiberq_designer.annotation_layout import table_payload
        housing = self.service.add_housing('', HousingProperties(kind='FDH', capacity=24, mounting='indoor'), pt(0, 0))
        self.m9c_language('sr')
        report = self.service.add_annotation('bom', table_payload(self.service.bill_of_materials(), 'sr'), pt(0, -500))
        before = self.repo.snapshot()
        old_state = {key: value for key, value in before.items() if key not in ('fiber_purposes', 'drawing_languages', 'annotations')}
        payload = json.loads(self.repo.find('annotations', report)['payload'])
        payload['source_hash'] = hashlib.sha256(json.dumps(old_state, sort_keys=True, default=str).encode('utf-8')).hexdigest()
        legacy = Path(self.directory.name) / 'v11.gpkg'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as target:
            source.backup(target)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(legacy), 1)
            for future in ('fqsd_fiber_purposes', 'fqsd_drawing_languages'):
                index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == future)
                self.assertEqual(0, dataset.DeleteLayer(index))
            layer = dataset.GetLayerByName('fqsd_annotations')
            record = layer.GetNextFeature()
            record.SetField('payload', json.dumps(payload))
            self.assertEqual(0, layer.SetFeature(record))
            dataset = None
        with closing(sqlite3.connect(str(legacy))) as db, db:
            db.execute("UPDATE fqsd_metadata SET value='11' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        reopened = GeoPackageRepository.open(legacy)
        self.assertTrue(reopened.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(before, reopened.snapshot())
        migrated = NetworkService(reopened)
        self.assertEqual('en', migrated.drawing_language('housings', housing))
        self.assertEqual('FDH 1', reopened.find('elements', housing)['name'])
        scene = migrated.fiber_scene()
        self.assertIn('indoor', [f['text'] for f in scene.housing_labels.getFeatures()])
        self.assertFalse(any('ZASTAREO' in f['text'] for f in scene.housing_labels.getFeatures()))
        migrated.validate()

    def test_m9c_language_write_failure_delete_undo_and_corruption(self):
        self.m9c_language('sr')
        before = self.repo.snapshot()
        original = self.repo.insert
        def failing(key, *args, **kwargs):
            if key == 'drawing_languages':
                raise RuntimeError('Injected language write failure')
            return original(key, *args, **kwargs)
        with patch.object(self.repo, 'insert', failing):
            with self.assertRaisesRegex(RuntimeError, 'Injected language'):
                self.service.add_housing('', OdfProperties(), pt(0, 0))
        self.assertEqual(before, self.repo.snapshot())
        housing = self.service.add_housing('', OdfProperties(), pt(0, 0))
        before = self.repo.snapshot()
        self.service.delete_element(housing)
        self.assertIsNone(self.repo.find('drawing_languages', housing))
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        dock = self.open_dock()
        with patch.object(self.repo, 'before_commit', None):
            with self.repo.command('Inject bad language'):
                self.repo.update('drawing_languages', self.repo.find('drawing_languages', housing).id(), {'language': 'xx'})
        with self.assertRaisesRegex(ValueError, 'drawing language'):
            dock.attach(self.path)
        self.assertIs(self.repo, dock.repository)
        self.repo.undo()
        self.service.validate()

    def test_m9c_serbian_add_cable_outlets_properties_and_exports(self):
        from fiberq_designer.cable_catalog import properties_from_record
        self.m9c_language('sr')
        first, second, housing = self.m9a_pair()
        self.service.add_housing_cable(housing, second, 'A', additional=10)
        outlet = self.service.add_outlet(housing, self.m9b_properties(), mode='straight')
        props = properties_from_record(self.repo.find('cables', outlet))
        model = r'Working {customer} \site\P1'
        self.service.edit_catalog_cable(outlet, 'Free', replace(props, model=model), 80)
        self.assertEqual('sr', self.service.drawing_language('cables', outlet))
        self.service.validate()
        dock = self.open_dock()
        dock.fit()
        for extension in ('pdf', 'svg'):
            target = Path(self.directory.name) / ('serbian.' + extension)
            exports.export_layout(dock.make_layout(), str(target), extension)
            self.assertGreater(target.stat().st_size, 100)
        target = Path(self.directory.name) / 'serbian.dxf'
        original_text = [feature['text'] for feature in dock.scene.housing_labels.getFeatures()]
        exports.export_dxf(self.iface.canvas, dock.drawing_layers(paper=True), dock.diagram_extent(), str(target))
        self.assertGreater(target.stat().st_size, 100)
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(target))
            labels = [feature.GetField('Text') or '' for feature in dataset.GetLayer(0)]
            self.assertTrue(any(model in label for label in labels))
            dataset = None
        self.assertEqual(original_text, [feature['text'] for feature in dock.scene.housing_labels.getFeatures()])
        before = self.repo.snapshot()
        self.service.delete_cable(outlet)
        self.assertIsNone(self.repo.find('drawing_languages', outlet))
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())


if __name__ == '__main__':
    unittest.main()
