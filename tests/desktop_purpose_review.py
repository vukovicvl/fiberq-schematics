"""Review purpose painting/data across two closures in isolated QGIS Desktop.

Run via qgis --code with FQSD_REVIEW_OUTPUT set to a fresh temporary folder.
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
from fiberq_designer.operation_dialogs import FiberDataDialog
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService


def main():
    output = Path(os.environ['FQSD_REVIEW_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    dock, checks, errors, status = None, [], [], 'failed'
    try:
        assert not QgsProject.instance().mapLayers()
        repo = GeoPackageRepository.create(output / 'connected-purpose.gpkg')
        service = NetworkService(repo)
        first = service.add_catalog_cable('K1', CableProperties(category='kbDuct', model='TOSM 03', catalog_model='TOSM 03', tubes=2),
                                          [pt(0, 0), pt(920, 0)])
        second = service.add_catalog_cable('K2', CableProperties(tubes=2, fibers_per_tube=6), [pt(1080, 0), pt(1480, 0)])
        service.add_closure('1', ClosureProperties(), first, pt(240, 0), 20)
        service.add_closure('2', ClosureProperties(), first, pt(880, 0), 24)
        a, b = service.ends_of(first, 'B'), service.ends_of(second, 'A')
        service.join_group([end['end_id'] for end in a[:12]], [end['end_id'] for end in b])
        service.cut_fibers([end['fiber_id'] for end in a[:4]], pt(340, 0))
        cuts = repo.features('fiber_cuts')
        service.join_group([cut['left_id'] for cut in cuts], [cut['right_id'] for cut in cuts])
        dock = launch(iface)
        dock.err = lambda error: errors.append(''.join(traceback.format_exception(error)))
        dock.attach(repo.path, repo)
        dock.dark_canvas.setChecked(True)
        dock.ribbon_tabs.setCurrentIndex(2)
        iface.mainWindow().resize(1600, 1050)

        def pump():
            QgsApplication.processEvents()
            iface.mapCanvas().refresh()
            iface.mapCanvas().waitWhileRendering()
            QgsApplication.processEvents()

        def click(point):
            canvas = iface.mapCanvas()
            canvas.setExtent(QgsRectangle(point.x() - 250, point.y() - 130, point.x() + 250, point.y() + 130))
            pump()
            pixel = canvas.getCoordinateTransform().transform(point)
            QTest.mouseClick(canvas.viewport(), MOUSE_LEFT, pos=QPoint(round(pixel.x()), round(pixel.y())))
            QgsApplication.processEvents()
            assert not errors, errors

        def deselect():
            dock.command_actions['select'].trigger()
            click(pt(1700, 200))
            assert dock.selected is None

        def paint(code, point):
            deselect()
            dock.command_actions['purpose_' + code].trigger()
            click(point)
            assert not any(feature['active'] for feature in dock.scene.fibers.getFeatures())

        def capture(name, extent=None):
            dock.stop_tool()
            if extent is None:
                dock.fit()
            else:
                iface.mapCanvas().setExtent(extent)
            pump()
            assert iface.mainWindow().grab().save(str(output / (name + '.png')))

        paint('nmWork', pt(1000, 0))
        assert repo.find('fibers', a[0]['fiber_id'])['purpose'] == repo.find('fibers', b[0]['fiber_id'])['purpose'] == 'nmWork'
        deselect()
        dock.command_actions['fiber_data'].trigger()
        seen, failures = [], []
        timer, watchdog = QTimer(), QTimer()
        def choose():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, FiberDataDialog) or not dialog.isVisible():
                timer.start(25)
                return
            seen.append(True)
            dialog.purpose.setCurrentIndex(dialog.purpose.findData('nmAcc'))
            assert dialog.grab().save(str(output / 'connected-fiber-data-dialog.png'))
            QTimer.singleShot(20, dialog.accept)
        def expired():
            failures.append('Fiber Data watchdog expired.')
            if QApplication.activeModalWidget():
                QApplication.activeModalWidget().reject()
        timer.setSingleShot(True)
        timer.timeout.connect(choose)
        timer.start(150)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(expired)
        watchdog.start(8000)
        try:
            click(pt(340, -8))
        finally:
            timer.stop()
            watchdog.stop()
        assert seen == [True] and not failures, failures
        assert repo.find('fibers', a[1]['fiber_id'])['purpose'] == repo.find('fibers', b[1]['fiber_id'])['purpose'] == 'nmAcc'
        paint('nmPlan', pt(1000, -16))
        paint('nmRsvd', pt(1000, -24))
        for number, code in enumerate(('nmWork', 'nmAcc', 'nmPlan', 'nmRsvd')):
            assert all(section['purpose'] == code for section in service.section_purposes()
                       if section['fiber_id'] in (a[number]['fiber_id'], b[number]['fiber_id']))
        checks.append('Real palette and Fiber Data clicks color complete paths across two closures, including joins and splice dots')

        dock.command_actions['cut_fiber'].trigger()
        click(pt(340, -32))
        paint('nmWork', pt(100, -32))
        assert repo.find('fibers', b[4]['fiber_id'])['purpose'] == 'nmFree'
        paint('nmAcc', pt(1000, -32))
        assert {section['purpose'] for section in service.section_purposes() if section['fiber_id'] == a[4]['fiber_id']} == {'nmWork', 'nmAcc'}
        capture('purpose-path-overview')
        capture('purpose-through-splices', QgsRectangle(800, -145, 1220, 60))
        capture('open-cut-purpose-isolation', QgsRectangle(160, -105, 520, 70))
        checks.append('An open cut keeps Working upstream and Accepted downstream independent; chosen purpose colors remain visible without selection masking')

        saved = repo.snapshot()
        dock.command_actions['undo'].trigger()
        dock.command_actions['redo'].trigger()
        assert saved == repo.snapshot()
        dock.show_purpose_colors()
        dock.fit()
        pump()
        for extension in ('pdf', 'svg'):
            exports.export_layout(dock.make_layout(), str(output / ('connected-purpose.' + extension)), extension)
        exports.export_dxf(iface.mapCanvas(), dock.drawing_layers(), dock.diagram_extent(), str(output / 'connected-purpose.dxf'))
        dock.attach(repo.path)
        assert saved == dock.repository.snapshot()
        dock.service.validate()
        checks.append('Undo/Redo/reopen preserve section purposes and IDs; PDF/SVG/DXF exports retain purpose styling')
        status = 'passed'
        print('PURPOSE DESKTOP REVIEW PASSED:', checks, flush=True)
    except BaseException:
        errors.append(traceback.format_exc())
        traceback.print_exc()
    finally:
        payload = dict(status=status, qgis=Qgis.QGIS_VERSION, qt=QT_VERSION_STR, pyqt=PYQT_VERSION_STR,
                       interface=type(iface).__name__, platform=QgsApplication.platformName(), checks=checks, errors=errors)
        (output / 'desktop-results.json').write_text(json.dumps(payload, indent=2) + '\n')
        if dock:
            dock.dispose()
            dock.close()
        QgsProject.instance().clear()
        QgsProject.instance().setDirty(False)
        QgsApplication.exit(0 if status == 'passed' else 1)


QTimer.singleShot(700, main)
