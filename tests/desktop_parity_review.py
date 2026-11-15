"""Run via qgis --code, using the real Desktop iface and a disposable profile.

Set FQSD_REVIEW_OUTPUT to a fresh writable directory. This script never opens a
user project and exits the isolated QGIS process after saving its evidence.
"""

from contextlib import nullcontext
from collections import Counter
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1] if '__file__' in globals() else Path('/home/vladimir-vukovic/Documents/FiberQ_Schematic_Designer')
sys.path.insert(0, str(ROOT))

from osgeo import ogr
from qgis.core import Qgis, QgsApplication, QgsProject, QgsRectangle
from qgis.PyQt.QtCore import QPoint, QTimer, QT_VERSION_STR, PYQT_VERSION_STR
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QLineEdit
from qgis.utils import iface

from fiberq_designer.app import launch
from fiberq_designer import exports
from fiberq_designer.compat import MOUSE_LEFT, enum_compat
from fiberq_designer.geometry import pt
from fiberq_designer.housing_dialog import HousingDialog
from fiberq_designer.closure_dialog import ClosureDialog
from fiberq_designer.cable_dialog import CableDialog
from fiberq_designer.housing_cable_dialog import AddHousingCableDialog
from fiberq_designer.outlet_dialog import OutletDialog
from fiberq_designer.operation_dialogs import ActiveDialog, BranchDialog, FiberDataDialog, GroupJoinDialog, SlackDialog
from fiberq_designer.report_dialogs import BudgetDialog, ReportDialog, SettingsDialog, export_report_csv


class DesktopReview:
    def __init__(self):
        self.output = Path(os.environ['FQSD_REVIEW_OUTPUT'])
        self.output.mkdir(parents=True, exist_ok=True)
        self.results, self.errors = [], []
        self.dock = launch(iface)
        self.dock.err = lambda error: self.errors.append(''.join(traceback.format_exception(error)))
        iface.mainWindow().resize(1600, 1100)
        self.write('running')

    def write(self, status):
        payload = dict(status=status, qgis=Qgis.QGIS_VERSION, qt=QT_VERSION_STR, pyqt=PYQT_VERSION_STR,
                       interface=type(iface).__name__, platform=QApplication.platformName(),
                       checks=self.results, errors=self.errors)
        (self.output / 'review-results.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')

    def passed(self, name):
        assert not self.errors, '\n'.join(self.errors)
        if self.dock.service:
            self.dock.service.validate()
        self.results.append(name)
        self.write('running')
        print('PASS: ' + name, flush=True)

    def pump(self):
        QgsApplication.processEvents()
        iface.mapCanvas().refresh()
        iface.mapCanvas().waitWhileRendering()
        QgsApplication.processEvents()

    def capture(self, name):
        self.dock.fit()
        self.pump()
        assert iface.mainWindow().grab().save(str(self.output / (name + '.png')))

    def click(self, point):
        canvas = iface.mapCanvas()
        canvas.setExtent(QgsRectangle(point.x() - 240, point.y() - 180, point.x() + 240, point.y() + 180))
        self.pump()
        pixel = canvas.getCoordinateTransform().transform(point)
        QTest.mouseClick(canvas.viewport(), MOUSE_LEFT, pos=QPoint(round(pixel.x()), round(pixel.y())))
        QgsApplication.processEvents()
        assert not self.errors, '\n'.join(self.errors)

    def modal(self, action, kind, configure=lambda dialog: None, accept=True, image=None):
        failures, seen, finished = [], [], []
        chooser, watchdog = QTimer(), QTimer()

        def choose():
            dialog = QApplication.activeModalWidget()
            try:
                assert isinstance(dialog, kind), 'Unexpected modal: ' + repr(dialog)
                seen.append(True)
                configure(dialog)
                QgsApplication.processEvents()
                if image:
                    assert dialog.grab().save(str(self.output / (image + '.png')))
                def finish():
                    finished.append(True)
                    if not accept:
                        dialog.reject()
                    elif isinstance(dialog, QFileDialog):
                        assert dialog.testOption(enum_compat(QFileDialog, 'DontUseNativeDialog', 'Option'))
                        box = dialog.findChild(QDialogButtonBox)
                        next(button for button in box.buttons() if box.buttonRole(button) ==
                             enum_compat(QDialogButtonBox, 'AcceptRole', 'ButtonRole')).click()
                    else:
                        dialog.accept()
                # Start liveness measurement after potentially expensive form
                # construction, allowing timers to run while the form is open.
                QTimer.singleShot(70, finish)
            except BaseException:
                failures.append(traceback.format_exc())
                if dialog:
                    dialog.reject()

        def expired():
            failures.append('Modal watchdog expired.')
            dialog = QApplication.activeModalWidget()
            if dialog:
                dialog.reject()

        for timer, delay, callback in ((chooser, 180, choose), (watchdog, 6000, expired)):
            timer.setSingleShot(True)
            timer.timeout.connect(callback)
            timer.start(delay)
        try:
            action()
        finally:
            for timer in (chooser, watchdog):
                timer.stop()
        assert seen and not failures, '\n'.join(failures)
        assert finished, 'The visible modal did not process the next queued timer.'
        assert not self.errors, '\n'.join(self.errors)

    def file(self, action, name, image=None):
        target = self.output / name
        def choose(dialog):
            dialog.setDirectory(str(self.output))
            dialog.findChild(QLineEdit, 'fileNameEdit').setText(target.name)
            assert dialog.selectedFiles() == [str(target)]
        self.modal(action, QFileDialog, choose, image=image)
        return target

    def command(self, key):
        self.dock.command_actions[key].trigger()

    def housing(self, kind, point, capacity=12, used='', modules=1, attached=False, splitter=False):
        def choose(dialog):
            dialog.capacity.setCurrentText(str(capacity))
            dialog.modules.setValue(modules)
            dialog.used.setText(str(used))
            dialog.model.setText('Free')
            dialog.cable.setChecked(attached)
            dialog.splitter.setChecked(splitter)
        self.modal(lambda: self.command(kind), HousingDialog, choose, image='housing-' + kind.lower())
        self.click(point)
        return self.dock.selected[1]

    def cable(self, name, point, length=120, bends=False):
        def choose(dialog):
            dialog.label.setText(name)
            dialog.length.setValue(length)
            dialog.real_length.setValue(2000 if name == 'FEEDER' else 100)
            dialog.bends.setChecked(bends)
            dialog.drag_length.setChecked(False)
            dialog.snap.setChecked(False)
        self.modal(lambda: self.command('loose_draw'), CableDialog, choose, image='catalogue-cable')
        self.click(point)
        if bends:
            self.click(pt(point.x() + 400, point.y()))
            self.click(pt(point.x() + 400, point.y() - 350))
            self.click(pt(point.x() + 900, point.y() - 350))
            self.command('finish')
        return self.dock.selected[1]

    def join(self, first, second, kind='splice'):
        self.dock.start_joint(kind)
        self.click(self.dock.service.optical_nodes()[first]['point'])
        self.dock.canvas_move(self.dock.service.optical_nodes()[second]['point'])
        assert self.dock.click_tool.snap_marker.isVisible()
        self.click(self.dock.service.optical_nodes()[second]['point'])
        self.dock.stop_tool()

    def run(self):
        self.file(lambda: self.command('new'), 'equipment-gallery.gpkg', 'new-workspace')
        before = self.dock.repository.snapshot()
        self.modal(lambda: self.command('ODF'), HousingDialog, accept=False)
        assert self.dock.repository.snapshot() == before
        for index, kind in enumerate(('ODF', 'PATCH_PANEL', 'FDH', 'MDU', 'FAT', 'OTB')):
            self.housing(kind, pt(index * 400, 0), capacity=24 if index < 2 else 12,
                         modules=2 if kind == 'ODF' else 1, splitter=kind == 'FDH')
        self.dock.stop_tool()
        self.capture('equipment-gallery')
        self.passed('Real New dialog, housing cancellation and all six equipment creation commands')

        self.file(lambda: self.command('new'), 'integrated-network.gpkg')
        feeder = self.cable('FEEDER', pt(0, 0))
        route = self.dock.repository.find('cables', feeder).geometry().asPolyline()
        origin = route[0]
        odf = self.housing('ODF', origin, 48, 4, attached=True)
        panel = self.housing('PATCH_PANEL', route[-1], 24, 12, attached=True)
        ids = [f['fiber_id'] for f in self.dock.repository.features('fibers') if f['cable_id'] == feeder]
        self.passed('Catalogue cable, physical tubes/fibers and two native endpoint housing attachments')

        auxiliary = self.cable('ADDITIONAL', pt(origin.x(), origin.y() + 150))
        self.dock.select('elements', odf)
        self.command('add_housing_cable')
        self.modal(lambda: self.click(self.dock.service.ends_of(auxiliary, 'A')[0].geometry().asPoint()),
                   AddHousingCableDialog, lambda dialog: dialog.additional.setValue(6), image='add-cable')
        self.dock.select('elements', odf)
        before = self.dock.repository.snapshot()
        self.modal(lambda: self.command('frame_outlet'), OutletDialog, accept=False, image='outlet-preview')
        assert before == self.dock.repository.snapshot()
        self.dock.select('elements', odf)
        self.modal(lambda: self.command('frame_outlet'), OutletDialog,
                   lambda dialog: dialog.label.setText('OUT1'), image='outlet')
        self.passed('Add cable, source elbow outlet, exact preview cancellation and stable existing fibers')

        for latent, x in ((False, 580), (True, 900)):
            def choose(dialog):
                dialog.latent.setChecked(latent)
                dialog.number.setText('7')
                dialog.continue_series.setChecked(False)
            self.modal(lambda: self.command('closure'), ClosureDialog, choose, image='closure-nl' if latent else 'closure-n')
            self.click(pt(origin.x() + x, origin.y()))
            self.click(pt(origin.x() + x, origin.y()))
            self.click(pt(origin.x() + x + 80, origin.y()))
        self.dock.stop_tool()
        self.passed('N/NL closure dialogs and measured two-edge canvas placement')

        self.command('cut_window')
        self.click(pt(origin.x() + 600, origin.y() + 1))
        self.click(pt(origin.x() + 605, origin.y() - 12))
        cuts = list(self.dock.repository.features('fiber_cuts'))
        assert len(cuts) == 2
        self.join(cuts[0]['left_id'], cuts[0]['right_id'])
        self.command('group_joint')
        self.click(self.dock.service.optical_nodes()[cuts[1]['left_id']]['point'])
        self.modal(lambda: self.click(self.dock.service.optical_nodes()[cuts[1]['right_id']]['point']),
                   GroupJoinDialog, lambda dialog: dialog.count.setValue(1), image='group-join')
        self.dock.stop_tool()
        self.command('cut_fiber')
        self.click(pt(origin.x() + 400, origin.y() - 16))
        assert len(self.dock.repository.features('fiber_cuts')) == 3
        self.command('through_fiber')
        self.click(pt(origin.x() + 450, origin.y() - 16))
        assert len(self.dock.repository.features('fiber_cuts')) == 2
        self.command('mark_fiber')
        self.click(pt(origin.x() + 700, origin.y() - 24))
        self.dock.stop_tool()
        assert len(self.dock.repository.features('fiber_marks')) == 1
        self.passed('Window Cut, individual/group Join with snap marker, Through and independent splice marks')

        active = {}
        for key, at in (('OLT', pt(-350, -200)), ('splitter', pt(1450, 0)), ('ONT', pt(1800, 0))):
            def choose(dialog):
                dialog.snap.setChecked(False)
                if key == 'OLT':
                    dialog.count.setValue(8)
                if key == 'splitter':
                    dialog.anchor.setCurrentIndex(dialog.anchor.findData('input'))
            self.modal(lambda key=key: self.command(key), ActiveDialog, choose, image='active-' + key.lower())
            self.click(at)
            active[key] = self.dock.selected[1]
        service = self.dock.service
        first = service.ports_of(active['OLT'])[0]['port_id']
        last = service.ports_of(active['ONT'])[0]['port_id']
        split = service.ports_of(active['splitter'])
        self.join(first, service.ports_of(odf)[0]['port_id'], 'patch')
        self.join(service.ports_of(panel)[0]['port_id'], split[0]['port_id'])
        self.join(split[1]['port_id'], last, 'drop')
        path = service.route_report(first, last)
        assert path['known_length_m'] == 2000 and path['splitter_losses'] == [10.5]
        before = self.dock.repository.snapshot()
        try:
            service.join_nodes(first, service.ports_of(odf)[0]['port_id'])
            raise AssertionError('An occupied optical connection was accepted.')
        except ValueError:
            pass
        assert before == self.dock.repository.snapshot()
        self.passed('Native OLT/splitter/ONT creation, real port joints, occupied rejection and branch-specific tracing')

        self.dock.select('fibers', ids[0])
        def metadata(dialog):
            dialog.purpose.setCurrentIndex(dialog.purpose.findData('nmWork'))
            dialog.replace_description.setChecked(True)
            dialog.description.setText(r'Free {customer} \site\P1')
        self.modal(lambda: self.command('fiber_data'), FiberDataDialog, metadata, image='fiber-data')
        self.modal(lambda: self.command('branch_fiber'), BranchDialog, lambda dialog: dialog.count.setValue(2))
        self.click(pt(origin.x() + 850, origin.y() + 70))
        self.click(pt(origin.x() + 1500, origin.y() + 230))
        assert len(self.dock.repository.features('fiber_routes')) == 2
        self.dock.select('cables', auxiliary)
        self.command('restore_branch')
        assert not self.dock.repository.features('fiber_routes')
        self.modal(lambda: self.command('slack'), SlackDialog, lambda dialog: dialog.length.setValue(25), image='slack')
        self.click(pt(1100, -400))
        self.dock.stop_tool()
        self.passed('Fiber purpose/description, branch/restore and slack placement')

        retained = {p['port_id'] for p in service.ports_of(odf)}
        self.dock.select('elements', odf)
        self.modal(lambda: self.command('properties'), HousingDialog, lambda dialog: dialog.model.setText('Working'))
        assert retained == {p['port_id'] for p in service.ports_of(odf)}
        before = self.dock.repository.snapshot()
        self.command('move')
        self.click(self.dock.repository.find('frames', odf).geometry().centroid().asPoint())
        self.click(pt(origin.x() - 80, origin.y()))
        moved = self.dock.repository.snapshot()
        assert before != moved
        self.command('undo')
        assert self.dock.repository.snapshot() == before
        self.command('redo')
        assert self.dock.repository.snapshot() == moved
        assert last in service.trace(first)
        self.dock.stop_tool()
        self.passed('Housing properties and Move with attached cables/outlet, stable ports, Undo/Redo and retained trace')

        self.cable('BENT', pt(200, -600), bends=True)
        self.dock.stop_tool()
        self.passed('Source orthogonal bent-cable preview, mouse insertion and Finish command')

        for language in ('sr', 'en'):
            def settings(dialog):
                dialog.language.setCurrentIndex(dialog.language.findData(language))
            self.modal(lambda: self.command('settings'), SettingsDialog, settings, image='settings-' + language)
            assert service.settings()['language'] == language
            assert self.dock.repository.find('cables', feeder)['name'] == 'FEEDER'
        self.passed('Settings language round trip with original network names and drawing captions retained')

        for key in ('bom_report', 'check_report'):
            before = self.dock.repository.snapshot()
            self.modal(lambda key=key: self.command(key), ReportDialog, accept=False, image=key)
            assert before == self.dock.repository.snapshot()
        def budget(dialog):
            dialog.mode.setCurrentIndex(dialog.mode.findData('path'))
            dialog.start.setCurrentIndex(dialog.start.findData(first))
            dialog.destination.setCurrentIndex(dialog.destination.findData(last))
            assert dialog.load_path() and dialog.calculate()
            expected = .48 + .1 * dialog.splices.value() + .3 * dialog.connectors.value() + 10.5 + 3
            row = next(row for row in dialog.result['rows'] if row[0] == 'Total required budget')
            assert abs(float(row[2]) - expected) < .0001
            export_report_csv(dialog.result, self.output / 'budget.csv')
            dialog.place = True
        self.modal(lambda: self.command('budget'), BudgetDialog, budget, image='budget')
        self.click(pt(-450, -1350))
        self.modal(lambda: self.command('bom_report'), ReportDialog, lambda dialog: setattr(dialog, 'place', True))
        self.click(pt(900, -1350))
        self.command('legend')
        self.click(pt(2100, -900))
        for name, report in (('bom', service.bill_of_materials()), ('checks', service.network_checks()),
                             ('route', service.route_report(first, last))):
            export_report_csv(report, self.output / (name + '.csv'))
        self.dock.stop_tool()
        self.passed('BOM/check forms, independent path budget arithmetic, CSVs, placed tables and source legend')

        self.dock.clear_selection()
        self.capture('integrated-network-dark')
        self.dock.dark_canvas.setChecked(False)
        self.capture('integrated-network-paper')
        self.dock.dark_canvas.setChecked(True)
        self.dock.fit()
        self.pump()
        for extension in ('pdf', 'svg', 'dxf'):
            target = self.file(lambda extension=extension: self.command(extension), 'integrated-network.' + extension)
            assert target.stat().st_size > 1000
        with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
            dataset = ogr.Open(str(self.output / 'integrated-network.dxf'))
            actual = Counter(f.GetField('Text') for f in dataset.GetLayer(0) if f.GetField('Layer').endswith('housing labels'))
            expected = Counter(f['text'] for f in self.dock.scene.housing_labels.getFeatures())
            assert actual == expected, str((expected - actual, actual - expected))
            dataset = None
        self.passed('Real export choosers, PDF/SVG/DXF and all required DXF labels including literal customer text')

        before = self.dock.repository.snapshot()
        self.command('save')
        assert (self.output / 'integrated-network.qgz').is_file()
        self.file(lambda: self.command('open'), 'integrated-network.gpkg')
        assert before == self.dock.repository.snapshot()
        QgsProject.instance().clear()
        assert QgsProject.instance().read(str(self.output / 'integrated-network.qgz'))
        self.dock.attach(str(self.output / 'integrated-network.gpkg'))
        assert before == self.dock.repository.snapshot()
        saved_layout = QgsProject.instance().layoutManager().layouts()[0]
        exports.export_layout(saved_layout, str(self.output / 'reopened-paper.svg'), 'svg')
        assert '#333333' in (self.output / 'reopened-paper.svg').read_text()
        self.passed('QGIS project Save, actual Open picker and project read/reattach with identical records')

        source = ROOT / 'fiberq_schematic_designer.py'
        namespace = {'iface': iface, '__file__': str(source)}
        exec(compile(source.read_text(encoding='utf-8'), str(source), 'exec'), namespace)
        self.dock = namespace['FIBERQ_SCHEMATIC_DESIGNER']
        self.dock.err = lambda error: self.errors.append(''.join(traceback.format_exception(error)))
        assert before == self.dock.repository.snapshot()
        self.passed('Console launcher reload preserves the current workspace in the actual QGIS Desktop host')

        self.file(lambda: self.command('new'), 'csv-workflow.gpkg')
        service = self.dock.service
        service.add_element('CABLE', 'LEFT_12F', 12, 'left', pt(0, 0))
        service.add_element('CABLE', 'RIGHT_12F', 12, 'right', pt(250, 0))
        self.dock.refresh()
        (self.output / 'connections.csv').write_text((ROOT / 'example_connections.csv').read_text())
        self.file(lambda: self.command('csv'), 'connections.csv')
        assert len(self.dock.repository.features('links')) == 4
        self.passed('Real splice CSV import picker and four saved connections')
        self.dock.stop_tool()
        self.write('passed')


def main():
    review = None
    try:
        assert not QgsProject.instance().mapLayers(), 'Start with an isolated blank project.'
        review = DesktopReview()
        review.run()
        print('DESKTOP REVIEW PASSED: {} phases; output {}'.format(len(review.results), review.output), flush=True)
        code = 0
    except BaseException:
        traceback.print_exc()
        if review:
            review.errors.append(traceback.format_exc())
            review.write('failed')
        code = 1
    finally:
        if review:
            review.dock.dispose()
            review.dock.close()
        QgsProject.instance().clear()
        QgsProject.instance().setDirty(False)
        QgsApplication.exit(code)


QTimer.singleShot(700, main)
