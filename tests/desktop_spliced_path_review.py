"""Review Group Join and one-click path selection in disposable QGIS Desktop.

Run via qgis --code with FQSD_REVIEW_OUTPUT set to a fresh temporary directory.
"""

import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1] if '__file__' in globals() else Path.cwd()
sys.path.insert(0, str(ROOT))

from qgis.core import Qgis, QgsApplication, QgsProject, QgsRectangle
from qgis.PyQt.QtCore import QPoint, QTimer, QT_VERSION_STR, PYQT_VERSION_STR
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication
from qgis.utils import iface
from fiberq_designer import exports
from fiberq_designer.app import launch
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.compat import MOUSE_LEFT
from fiberq_designer.geometry import pt
from fiberq_designer.operation_dialogs import GroupJoinDialog
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService


def main():
    output = Path(os.environ['FQSD_REVIEW_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    dock, errors, checks, status = None, [], [], 'failed'
    try:
        assert not QgsProject.instance().mapLayers()
        repo = GeoPackageRepository.create(output / 'group-joined-fibers.gpkg')
        service = NetworkService(repo)
        first = service.add_catalog_cable('K1', CableProperties(tubes=2), [pt(0, 0), pt(400, 0)])
        second = service.add_catalog_cable('K2', CableProperties(category='kbDuct', model='TOSM 03', catalog_model='TOSM 03', tubes=2, fibers_per_tube=6),
                                           [pt(640, 0), pt(1040, 0)])
        service.add_closure('1', ClosureProperties(location='pole', location_number='1'), first, pt(360, 0), 28)
        a, b = service.ends_of(first, 'B'), service.ends_of(second, 'A')
        dock = launch(iface)
        dock.err = lambda error: errors.append(''.join(traceback.format_exception(error)))
        dock.attach(repo.path, repo)
        dock.dark_canvas.setChecked(True)
        dock.ribbon_tabs.setCurrentIndex(2)
        iface.mainWindow().resize(1600, 1000)

        def pump():
            QgsApplication.processEvents()
            iface.mapCanvas().refresh()
            iface.mapCanvas().waitWhileRendering()
            QgsApplication.processEvents()

        def click(point):
            canvas = iface.mapCanvas()
            canvas.setExtent(QgsRectangle(point.x() - 220, point.y() - 120, point.x() + 220, point.y() + 120))
            pump()
            pixel = canvas.getCoordinateTransform().transform(point)
            QTest.mouseClick(canvas.viewport(), MOUSE_LEFT, pos=QPoint(round(pixel.x()), round(pixel.y())))
            QgsApplication.processEvents()
            assert not errors, errors

        def capture(name):
            dock.fit()
            pump()
            assert iface.mainWindow().grab().save(str(output / (name + '.png')))

        for index, kind in enumerate(('splice', 'through', 'patch')):
            dock.command_actions['group_joint'].trigger()
            click(a[index * 4].geometry().asPoint())
            seen, failures = [], []
            timer, watchdog = QTimer(), QTimer()
            def choose():
                dialog = QApplication.activeModalWidget()
                if not isinstance(dialog, GroupJoinDialog) or not dialog.isVisible():
                    timer.start(25)
                    return
                seen.append(True)
                dialog.count.setValue(4)
                dialog.kind.setCurrentIndex(dialog.kind.findData(kind))
                if kind == 'splice':
                    assert dialog.grab().save(str(output / 'group-join-dialog.png'))
                QTimer.singleShot(20, dialog.accept)
            def expired():
                failures.append('Group Join modal watchdog expired.')
                if QApplication.activeModalWidget():
                    QApplication.activeModalWidget().reject()
            timer.setSingleShot(True)
            timer.timeout.connect(choose)
            timer.start(150)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(expired)
            watchdog.start(8000)
            try:
                click(b[index * 4].geometry().asPoint())
            finally:
                timer.stop()
                watchdog.stop()
            assert seen == [True] and not failures, failures
        assert len(repo.features('fiber_joints')) == 12
        checks.append('Actual Group Join dialogs create four fusion, four through and four patch connections')

        before = repo.snapshot()
        dock.command_actions['select'].trigger()
        expected = {a[0]['fiber_id'], b[0]['fiber_id']}
        click(pt(820, 0))
        assert dock.selected == ('fibers', b[0]['fiber_id'])
        assert {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']} == expected
        assert sum(j['active'] for j in dock.scene.optical_lines.getFeatures()) == 1
        assert dock.selected_fibers() == [b[0]['fiber_id']]
        capture('fiber-click-whole-path')
        joint = next(j for j in repo.features('fiber_joints') if j['a_id'] == a[0]['end_id'])
        click(joint.geometry().interpolate(joint.geometry().length() / 2).asPoint())
        assert dock.selected == ('fiber_joints', joint['joint_id'])
        assert {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']} == expected
        assert not dock.scene.optical_lines.selectedFeatureIds()
        capture('joint-click-whole-path')
        checks.append('One real fiber or splice click highlights both cable fibers and the complete joint, retaining the clicked editing target')

        dock.command_actions['delete'].trigger()
        assert len(repo.features('fiber_joints')) == 11
        assert len(repo.features('fibers')) == 36
        dock.command_actions['undo'].trigger()
        assert before == repo.snapshot()
        dock.select('fibers', b[0]['fiber_id'])
        dock.fit()
        pump()
        for extension in ('pdf', 'svg'):
            exports.export_layout(dock.make_layout(), str(output / ('group-joined-fibers.' + extension)), extension)
        exports.export_dxf(iface.mapCanvas(), dock.drawing_layers(), dock.diagram_extent(), str(output / 'group-joined-fibers.dxf'))
        assert before == repo.snapshot()
        dock.attach(repo.path)
        dock.select('fibers', b[0]['fiber_id'])
        assert {f['fiber_id'] for f in dock.scene.fibers.getFeatures() if f['active']} == expected
        assert before == dock.repository.snapshot()
        dock.service.validate()
        checks.append('Delete affects one clicked splice; Undo/reopen retain all IDs; path highlighting survives reload and PDF/SVG/DXF exports succeed')
        status = 'passed'
        print('SPLICED PATH DESKTOP REVIEW PASSED:', checks, flush=True)
    except BaseException:
        errors.append(traceback.format_exc())
        traceback.print_exc()
    finally:
        payload = dict(status=status, qgis=Qgis.QGIS_VERSION, qt=QT_VERSION_STR, pyqt=PYQT_VERSION_STR,
                       interface=type(iface).__name__, platform=QApplication.platformName(), checks=checks, errors=errors)
        (output / 'desktop-results.json').write_text(json.dumps(payload, indent=2) + '\n')
        if dock:
            dock.dispose()
            dock.close()
        QgsProject.instance().clear()
        QgsProject.instance().setDirty(False)
        QgsApplication.exit(0 if status == 'passed' else 1)


QTimer.singleShot(700, main)
