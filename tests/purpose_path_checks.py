"""Connected-purpose behavior, cut isolation, migration and rendering checks."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from osgeo import ogr
from qgis.core import QgsGeometry
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QApplication

from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.compat import MOUSE_LEFT
from fiberq_designer.fiber_model import PURPOSES
from fiberq_designer.geometry import pt
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService


class PurposePathChecks:
    def purpose_path_fixture(self):
        a = self.service.add_catalog_cable('PURPOSE-A', CableProperties(), [pt(0, 0), pt(400, 0)])
        b = self.service.add_catalog_cable('PURPOSE-B', CableProperties(tubes=2, fibers_per_tube=6), [pt(560, 0), pt(960, 0)])
        c = self.service.add_catalog_cable('PURPOSE-C', CableProperties(), [pt(1120, 0), pt(1520, 0)])
        self.service.add_closure('1', ClosureProperties(), a, pt(360, 0), 24)
        incoming = self.service.ends_of(a, 'B')
        middle = self.service.ends_of(b, 'A')
        self.service.join_group([e['end_id'] for e in incoming], [e['end_id'] for e in middle])
        onward = self.service.ends_of(b, 'B')
        outgoing = self.service.ends_of(c, 'A')
        self.service.join_group([e['end_id'] for e in onward], [e['end_id'] for e in outgoing], 'through')
        return a, b, c, incoming, middle, outgoing

    def test_purpose_path_all_styles_cover_cables_joins_and_dots(self):
        a, b, c, incoming, middle, outgoing = self.purpose_path_fixture()
        chosen = {incoming[0]['fiber_id'], middle[0]['fiber_id'], outgoing[0]['fiber_id']}
        dock = self.open_dock()
        dock.select('fibers', incoming[0]['fiber_id'])
        original = {key: self.repo.snapshot()[key] for key in ('fiber_ends', 'fiber_nodes', 'fiber_joints', 'cables')}
        for code, purpose in PURPOSES.items():
            with self.subTest(purpose=code):
                dock.command_actions['purpose_' + code].trigger()
                self.assertTrue(all(self.repo.find('fibers', identity)['purpose'] == code for identity in chosen))
                self.assertTrue(all(f['purpose'] == 'nmFree' for f in self.repo.features('fibers') if f['fiber_id'] not in chosen))
                self.assertFalse(any(f['active'] for f in dock.scene.fibers.getFeatures()), 'Purpose must be visible without red selection masking it.')
                for feature in dock.scene.fibers.getFeatures():
                    if feature['fiber_id'] in chosen:
                        self.assertEqual(purpose.color, feature['stroke'])
                        self.assertAlmostEqual(purpose.width, feature['width'])
                for dark in (False, True):
                    dock.scene.set_dark(dark)
                    for layers in (dock.scene.layers(), dock.scene.paper_layers()):
                        lines = next(layer for layer in layers if layer.name().endswith('optical joints'))
                        selected = [j for j in self.repo.features('fiber_joints') if j['a_id'] in self.service.trace(incoming[0]['end_id'])]
                        for feature in lines.getFeatures():
                            if feature['joint_id'] in {j['joint_id'] for j in selected}:
                                self.assertEqual(purpose.color or '#333333', feature['color'])
                                self.assertAlmostEqual(purpose.width, feature['width'])
                dot = next(d for d in dock.scene.optical_marks.getFeatures()
                           if d['record_id'] in {j['joint_id'] for j in self.repo.features('fiber_joints') if j['a_id'] == incoming[0]['end_id']})
                self.assertEqual(purpose.color or '#333333', dot['color'])
        for key, records in original.items():
            self.assertEqual(records, self.repo.snapshot()[key])
        self.service.validate()

    def test_purpose_path_paint_join_and_change_active_palette(self):
        a, b, c, incoming, middle, outgoing = self.purpose_path_fixture()
        dock = self.open_dock()
        dock.command_actions['purpose_nmWork'].trigger()
        dock.canvas_click(pt(480, 0), MOUSE_LEFT)
        self.assertEqual('fiber_joints', dock.selected[0])
        self.assertEqual({'nmWork'}, {self.repo.find('fibers', end['fiber_id'])['purpose'] for end in (incoming[0], middle[0], outgoing[0])})
        dock.command_actions['purpose_nmAcc'].trigger()
        dock.canvas_click(pt(800, -8), MOUSE_LEFT)
        self.assertEqual({'nmAcc'}, {self.repo.find('fibers', end['fiber_id'])['purpose'] for end in (incoming[1], middle[1], outgoing[1])})
        self.assertEqual('nmFree', self.repo.find('fibers', incoming[2]['fiber_id'])['purpose'])

    def test_purpose_path_open_cut_isolation_split_inheritance_and_reopen(self):
        a = self.service.add_catalog_cable('A', CableProperties(), [pt(0, 0), pt(1600, 0)])
        b = self.service.add_catalog_cable('B', CableProperties(), [pt(1800, 0), pt(2200, 0)])
        fiber = self.service.ends_of(a, 'A')[0]['fiber_id']
        self.service.join_nodes(self.service.ends_of(a, 'B')[0]['end_id'], self.service.ends_of(b, 'A')[0]['end_id'])
        self.service.cut_fibers([fiber], pt(800, 0))
        dock = self.open_dock()
        dock.select_at(pt(400, 0))
        dock.command_actions['purpose_nmWork'].trigger()
        codes = {s['span_id']: s['purpose'] for s in self.service.section_purposes()}
        cut = self.repo.features('fiber_cuts')[0]
        self.assertEqual('nmWork', codes[fiber])
        self.assertEqual('nmFree', codes[cut['right_id']])
        self.assertEqual('nmFree', self.repo.find('fibers', self.service.ends_of(b, 'A')[0]['fiber_id'])['purpose'])
        self.service.cut_fibers([fiber], pt(600, 0))
        self.assertEqual(2, sum(s['purpose'] == 'nmWork' for s in self.service.section_purposes() if s['fiber_id'] == fiber))
        dock.refresh()
        dock.select_at(pt(700, 0))
        dock.command_actions['purpose_nmAcc'].trigger()
        self.assertIn('Accepted', dock.details.text())
        self.assertEqual({'nmWork', 'nmAcc', 'nmFree'}, {s['purpose'] for s in self.service.section_purposes() if s['fiber_id'] == fiber})
        self.assertTrue(any('Mixed purposes' in str(value) for row in self.service.bill_of_materials()['rows'] for value in row))
        saved = self.repo.snapshot()
        self.repo.undo()
        self.repo.redo()
        self.assertEqual(saved, self.repo.snapshot())
        reopened = GeoPackageRepository.open(self.path)
        self.assertEqual(saved, reopened.snapshot())
        NetworkService(reopened).validate()

    def test_purpose_path_data_dialog_cancel_full_path_and_atomic_failure(self):
        a, b, c, incoming, middle, outgoing = self.purpose_path_fixture()
        dock = self.open_dock()
        dock.select('fibers', middle[0]['fiber_id'])
        before = self.repo.snapshot()
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        dock.edit_fiber_data()
        self.assertEqual(before, self.repo.snapshot())
        def accept():
            dialog = QApplication.activeModalWidget()
            dialog.purpose.setCurrentIndex(dialog.purpose.findData('nmAcc'))
            dialog.fiber_type.setCurrentIndex(dialog.fiber_type.findData('MM'))
            dialog.replace_description.setChecked(True)
            dialog.description.setText('Free {customer}')
            dialog.accept()
        QTimer.singleShot(0, accept)
        dock.edit_fiber_data()
        for end in (incoming[0], middle[0], outgoing[0]):
            fiber = self.repo.find('fibers', end['fiber_id'])
            self.assertEqual(('nmAcc', 'MM', 'Free {customer}'), (fiber['purpose'], fiber['fiber_type'], fiber['description']))
        before, history = self.repo.snapshot(), list(self.repo.history)
        update, writes = self.repo.update, []
        def fail(key, *args, **kwargs):
            if key == 'fibers':
                writes.append(True)
                if len(writes) == 2:
                    raise RuntimeError('Injected path purpose failure')
            return update(key, *args, **kwargs)
        with patch.object(self.repo, 'update', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected path purpose'):
                self.service.set_path_purpose([incoming[0]['end_id']], 'nmWork')
        self.assertEqual(before, self.repo.snapshot())
        self.assertEqual(history, self.repo.history)

    def test_purpose_path_through_and_cable_delete_preserve_section_state(self):
        cable = self.service.add_catalog_cable('A', CableProperties(), [pt(0, 0), pt(1200, 0)])
        fiber = self.service.ends_of(cable, 'A')[0]['fiber_id']
        self.service.cut_fibers([fiber], pt(600, 0))
        cut = self.repo.features('fiber_cuts')[0]
        self.service.set_path_purpose([cut['left_id']], 'nmWork')
        self.service.set_path_purpose([cut['right_id']], 'nmAcc')
        before = self.repo.snapshot()
        self.service.delete_cable(cable)
        self.assertFalse(self.repo.features('fiber_purposes'))
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())
        self.service.restore_through(fiber)
        self.assertFalse(self.repo.features('fiber_purposes'))
        self.assertEqual('nmWork', self.repo.find('fibers', fiber)['purpose'])
        self.repo.undo()
        self.assertEqual(before, self.repo.snapshot())

    def test_purpose_path_legacy_links_and_port_colors(self):
        self.service.network_demo()
        dock = self.open_dock()
        first = self.repo.features('fibers')[0]
        dock.select('ports', first['a_id'])
        dock.command_actions['purpose_nmRsvd'].trigger()
        found = self.service.trace(first['a_id'])
        expected = {f['fiber_id'] for f in self.repo.features('fibers') if f['a_id'] in found and f['b_id'] in found}
        self.assertEqual(2, len(expected))
        self.assertTrue(all(self.repo.find('fibers', identity)['purpose'] == 'nmRsvd' for identity in expected))
        links = {l['link_id'] for l in self.repo.features('links') if l['from_id'] in found}
        self.assertTrue(all(l['color'] == '#ff00ff' and abs(l['width'] - .30) < 1e-9
                            for l in dock.scene.links.getFeatures() if l['link_id'] in links))
        self.assertTrue(all(dot['color'] == '#ff00ff' for dot in dock.scene.splices.getFeatures() if dot['link_id'] in links))

    def test_purpose_path_v12_copy_migration_and_invalid_override_rejection(self):
        from fiberq_designer.annotation_layout import table_payload
        self.service.add_catalog_cable('A', CableProperties(), [pt(0, 0), pt(1200, 0)])
        annotation = self.service.add_annotation('bom', table_payload(self.service.bill_of_materials()), pt(0, -300))
        before = self.repo.snapshot()
        legacy = Path(self.directory.name) / 'v12.gpkg'
        old = {key: value for key, value in before.items() if key not in ('fiber_purposes', 'annotations')}
        payload = json.loads(self.repo.find('annotations', annotation)['payload'])
        payload['source_hash'] = hashlib.sha256(json.dumps(old, sort_keys=True, default=str).encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(str(legacy))) as destination:
            source.backup(destination)
        dataset = ogr.Open(str(legacy), 1)
        index = next(i for i in range(dataset.GetLayerCount()) if dataset.GetLayer(i).GetName() == 'fqsd_fiber_purposes')
        self.assertEqual(0, dataset.DeleteLayer(index))
        layer = dataset.GetLayerByName('fqsd_annotations')
        record = layer.GetNextFeature()
        record.SetField('payload', json.dumps(payload))
        self.assertEqual(0, layer.SetFeature(record))
        dataset = None
        with closing(sqlite3.connect(str(legacy))) as connection, connection:
            connection.execute("UPDATE fqsd_metadata SET value='12' WHERE key='schema_version'")
        digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        reopened = GeoPackageRepository.open(legacy)
        self.assertTrue(reopened.path.endswith('_v13.gpkg'))
        self.assertEqual(digest, hashlib.sha256(legacy.read_bytes()).hexdigest())
        self.assertEqual(before, reopened.snapshot())
        NetworkService(reopened).validate()
        with patch.object(self.repo, 'before_commit', None):
            with self.repo.command('Inject invalid section purpose'):
                self.repo.insert('fiber_purposes', dict(span_id='missing', fiber_id='missing', purpose='nmWork'))
        with self.assertRaisesRegex(ValueError, 'section purpose'):
            self.service.validate()
        self.repo.undo()
        self.service.validate()
