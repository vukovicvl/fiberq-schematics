"""Exercise Module 2 cable snapping via real QGIS Desktop dialogs/mouse events.

Run with qgis --code and FQSD_REVIEW_OUTPUT set to a fresh temporary folder.
The disposable QGIS process exits after recording its results.
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
from fiberq_designer.compat import MOUSE_LEFT, MOUSE_RIGHT
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import OdfProperties
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService


def main():
    output = Path(os.environ['FQSD_REVIEW_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    dock, errors, checks = None, [], []
    status, counts = 'failed', {}
    try:
        assert not QgsProject.instance().mapLayers(), 'Use a blank disposable QGIS project.'
        repo = GeoPackageRepository.create(output / 'two-module-odfs.gpkg')
        service = NetworkService(repo)
        first = service.add_catalog_cable('MODULE-1-48F', CableProperties(tubes=4), [pt(0, 0), pt(1200, 0)])
        left = service.attach_odf(first, 'A', '1', OdfProperties(capacity=48, modules=2, requested_used=48))
        right = service.attach_odf(first, 'B', '2', OdfProperties(capacity=48, modules=2, requested_used=48))
        module = service.modules_of(left)[1]['module_id']
        anchor = next(p for p in service.ports_of(left) if p['module_id'] == module).geometry().asPoint()
        before = repo.snapshot()
        original = {p['port_id']: p.geometry().asWkt() for housing in (left, right) for p in service.ports_of(housing)}
        dock = launch(iface)
        dock.err = lambda error: errors.append(''.join(traceback.format_exception(error)))
        dock.attach(repo.path, repo)
        dock.dark_canvas.setChecked(True)
        iface.mainWindow().resize(1600, 1050)

        def pump():
            QgsApplication.processEvents()
            iface.mapCanvas().refresh()
            iface.mapCanvas().waitWhileRendering()
            QgsApplication.processEvents()

        def move_to_anchor():
            canvas = iface.mapCanvas()
            canvas.setExtent(QgsRectangle(anchor.x() - 65, anchor.y() - 55, anchor.x() + 100, anchor.y() + 55))
            pump()
            pixel = canvas.getCoordinateTransform().transform(anchor)
            position = QPoint(round(pixel.x()) + 2, round(pixel.y()) + 1)
            QTest.mouseMove(canvas.viewport(), position + QPoint(25, 25), delay=20)
            QTest.mouseMove(canvas.viewport(), position, delay=20)
            QgsApplication.processEvents()
            return position

        def cable_form():
            failures, seen = [], []
            chooser, watchdog = QTimer(), QTimer()
            def choose():
                dialog = QApplication.activeModalWidget()
                if dialog is not dock.catalog_dialog or not dialog.isVisible():
                    chooser.start(25)
                    return
                seen.append(True)
                dialog.select_capacity(4, 12)
                dialog.length.setValue(120)
                dialog.label.setText('MODULE-2-48F')
                dialog.snap.setChecked(True)
                dialog.widen.setChecked(True)
                QTimer.singleShot(20, dialog.accept)
            def expired():
                failures.append('Cable dialog watchdog expired.')
                if QApplication.activeModalWidget():
                    QApplication.activeModalWidget().reject()
            chooser.setSingleShot(True)
            chooser.timeout.connect(choose)
            chooser.start(150)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(expired)
            watchdog.start(8000)
            try:
                dock.command_actions['loose_draw'].trigger()
            finally:
                chooser.stop()
                watchdog.stop()
            assert seen == [True] and not failures, failures
            assert not errors, errors

        cable_form()
        position = move_to_anchor()
        assert dock.click_tool.snap_marker.isVisible()
        preview = dock.click_tool.preview.asGeometry().asMultiPolyline()
        assert preview[0][0].distance(anchor) < 1e-6
        assert before == repo.snapshot()
        assert iface.mainWindow().grab().save(str(output / 'module-2-snap-preview.png'))
        QTest.mouseClick(iface.mapCanvas().viewport(), MOUSE_RIGHT, pos=position)
        QgsApplication.processEvents()
        assert before == repo.snapshot()
        assert not dock.click_tool.snap_marker.isVisible()
        checks.append('Real cable form, mouse-hover Module 2 marker, exact preview anchor and cancel without writes')

        cable_form()
        position = move_to_anchor()
        assert dock.click_tool.snap_marker.isVisible()
        QTest.mouseClick(iface.mapCanvas().viewport(), MOUSE_LEFT, pos=position)
        QgsApplication.processEvents()
        assert not errors, errors
        cable = repo.features('cables')[-1]
        assert (cable['from_obj'], cable['to_obj']) == (left, right)
        for side, housing in (('A', left), ('B', right)):
            target = service.modules_of(housing)[1]['module_id']
            ends = service.ends_of(cable['cable_id'], side)
            assert len(ends) == 48
            assert {repo.find('ports', e['port_id'])['module_id'] for e in ends} == {target}
        for number in (0, 47):
            assert service.ends_of(cable['cable_id'], 'B')[number]['end_id'] in service.trace(service.ends_of(cable['cable_id'], 'A')[number]['end_id'])
        assert all(repo.find('ports', identity).geometry().asWkt() == geometry for identity, geometry in original.items())
        counts = service.validate()
        checks.append('Real canvas click attaches 48 fibers to Module 2 at both ends and preserves all 192 port positions')
        dock.clear_selection()
        dock.fit()
        pump()
        assert iface.mainWindow().grab().save(str(output / 'module-2-attached-desktop.png'))
        move_to_anchor()
        assert iface.mainWindow().grab().save(str(output / 'module-2-attached-detail.png'))
        dock.fit()
        pump()
        for extension in ('pdf', 'svg'):
            exports.export_layout(dock.make_layout(), str(output / ('two-module-odfs.' + extension)), extension)
        exports.export_dxf(iface.mapCanvas(), dock.drawing_layers(), dock.diagram_extent(), str(output / 'two-module-odfs.dxf'))
        saved = repo.snapshot()
        repo.undo()
        assert before == repo.snapshot()
        repo.redo()
        assert saved == repo.snapshot()
        assert saved == GeoPackageRepository.open(repo.path).snapshot()
        checks.append('One-step Undo/Redo, identical saved IDs on reopen and PDF/SVG/DXF exports')
        status = 'passed'
        print('MODULE SNAP DESKTOP REVIEW PASSED:', checks, flush=True)
    except BaseException:
        errors.append(traceback.format_exc())
        traceback.print_exc()
    finally:
        result = dict(status=status, qgis=Qgis.QGIS_VERSION, qt=QT_VERSION_STR, pyqt=PYQT_VERSION_STR,
                      interface=type(iface).__name__, platform=QApplication.platformName(), checks=checks, counts=counts, errors=errors)
        (output / 'desktop-results.json').write_text(json.dumps(result, indent=2) + '\n')
        if dock:
            dock.dispose()
            dock.close()
        QgsProject.instance().clear()
        QgsProject.instance().setDirty(False)
        QgsApplication.exit(0 if status == 'passed' else 1)


QTimer.singleShot(700, main)
