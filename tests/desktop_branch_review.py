"""Exercise Branch / Restore route with real QGIS Desktop mouse events.

Run with qgis --code and FQSD_REVIEW_OUTPUT pointing to a fresh directory.
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
from fiberq_designer.active_layout import ActiveProperties
from fiberq_designer.app import launch
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.compat import MOUSE_LEFT, MOUSE_RIGHT
from fiberq_designer.geometry import pt
from fiberq_designer.operation_dialogs import BranchDialog
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService


def main():
    output = Path(os.environ['FQSD_REVIEW_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    dock, checks, errors, expected_errors, status = None, [], [], [], 'failed'
    try:
        assert not QgsProject.instance().mapLayers()
        repo = GeoPackageRepository.create(output / 'branch-example.gpkg')
        service = NetworkService(repo)
        cable = service.add_catalog_cable('K1', CableProperties(tubes=2, fibers_per_tube=6),
                                          [pt(0, 0), pt(1600, 0)])
        fibers = [end['fiber_id'] for end in service.ends_of(cable, 'B')]
        service.set_fiber_data(fibers[:6], purpose='nmWork', description='Six diverted fibers')
        original = repo.snapshot()
        dock = launch(iface)
        dock.attach(repo.path, repo)
        dock.err = lambda error: errors.append(str(error))
        dock.dark_canvas.setChecked(True)
        dock.ribbon_tabs.setCurrentIndex(2)
        iface.mainWindow().resize(1500, 950)

        def pump():
            QgsApplication.processEvents()
            iface.mapCanvas().refresh()
            iface.mapCanvas().waitWhileRendering()
            QgsApplication.processEvents()

        def mouse(point, button=None):
            canvas = iface.mapCanvas()
            canvas.setExtent(QgsRectangle(point.x() - 260, point.y() - 150,
                                          point.x() + 260, point.y() + 150))
            pump()
            pixel = canvas.getCoordinateTransform().transform(point)
            position = QPoint(round(pixel.x()), round(pixel.y()))
            if button is None:
                QTest.mouseMove(canvas.viewport(), position + QPoint(20, 20))
                QTest.mouseMove(canvas.viewport(), position)
            else:
                QTest.mouseClick(canvas.viewport(), button, pos=position)
            QgsApplication.processEvents()

        def capture(name):
            iface.mapCanvas().setExtent(QgsRectangle(-120, -180, 1800, 620))
            pump()
            assert iface.mainWindow().grab().save(str(output / (name + '.png')))
            assert iface.mapCanvas().grab().save(str(output / (name + '-canvas.png')))

        def branch_dialog(side='B', image=None):
            seen, failures = [], []
            timer, watchdog = QTimer(), QTimer()

            def choose():
                dialog = QApplication.activeModalWidget()
                if not isinstance(dialog, BranchDialog) or not dialog.isVisible():
                    timer.start(25)
                    return
                try:
                    seen.append(True)
                    dialog.count.setValue(6)
                    dialog.side.setCurrentIndex(dialog.side.findData(side))
                    if image:
                        assert dialog.grab().save(str(output / (image + '.png')))
                    QTimer.singleShot(40, dialog.accept)
                except BaseException:
                    failures.append(traceback.format_exc())
                    dialog.reject()

            def expired():
                failures.append('Branch dialog watchdog expired.')
                if QApplication.activeModalWidget():
                    QApplication.activeModalWidget().reject()

            timer.setSingleShot(True)
            timer.timeout.connect(choose)
            timer.start(150)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(expired)
            watchdog.start(8000)
            try:
                dock.command_actions['branch_fiber'].trigger()
            finally:
                timer.stop()
                watchdog.stop()
            assert seen == [True] and not failures, failures

        capture('01-original-cable')
        branch_dialog(image='02-branch-dialog')
        mouse(pt(1600, 0), MOUSE_LEFT)
        mouse(pt(1800, 450), MOUSE_LEFT)
        assert len(errors) == 1 and 'away from either endpoint' in errors[0], errors
        expected_errors.extend(errors)
        errors.clear()
        assert original == repo.snapshot()
        assert dock.pending is None
        checks.append('Reproduced endpoint-source error; rejection leaves workspace unchanged and clears pending state')

        branch_dialog()
        mouse(pt(900, 0), MOUSE_LEFT)
        mouse(pt(1500, 450))
        assert original == repo.snapshot()
        capture('03-live-preview')
        mouse(pt(1500, 450), MOUSE_RIGHT)
        assert original == repo.snapshot() and dock.pending is None
        checks.append('Real hover preview and right-click cancellation do not save routes')

        branch_dialog()
        mouse(pt(900, 0), MOUSE_LEFT)
        mouse(pt(1500, 450), MOUSE_LEFT)
        assert not errors, errors
        assert len(repo.features('fiber_routes')) == 6
        assert service.ends_of(cable, 'B')[0].geometry().asPoint().distance(pt(1500, 450)) < 1
        branched = repo.snapshot()
        service.validate()
        capture('04-diverted-free-ends')
        checks.append('Two real canvas clicks divert six B ends; other fibers, IDs and data stay intact')

        dock.command_actions['select'].trigger()
        mouse(pt(1200, 450), MOUSE_LEFT)
        assert dock.selected == ('fibers', fibers[0]), dock.selected
        dock.command_actions['restore_branch'].trigger()
        assert len(repo.features('fiber_routes')) == 5
        dock.command_actions['undo'].trigger()
        assert branched == repo.snapshot()
        dock.select('cables', cable)
        dock.command_actions['restore_branch'].trigger()
        assert original == repo.snapshot()
        dock.command_actions['undo'].trigger()
        assert branched == repo.snapshot()
        dock.command_actions['redo'].trigger()
        assert original == repo.snapshot()
        dock.show_purpose_colors()
        capture('05-restored-cable')
        checks.append('Restore selected fiber / cable and actual Undo / Redo actions reproduce saved geometry exactly')

        # A branches toward the drawing start. Joining afterwards must keep
        # the same end identities and trace continuity when restoring its route.
        branch_dialog('A')
        mouse(pt(700, 0), MOUSE_LEFT)
        mouse(pt(-200, 450), MOUSE_LEFT)
        assert not errors, errors
        assert len(repo.features('fiber_routes')) == 6
        fixed = service.ends_of(cable, 'B')[0]['end_id']
        diverted = service.ends_of(cable, 'A')[0]['end_id']
        ont = service.add_active('ONT', ActiveProperties(kind='ONT', count=1), pt(-400, 450))
        port = service.ports_of(ont)[0]['port_id']
        joint = service.join_nodes(diverted, port)
        assert port in service.trace(fixed)
        service.clear_branch(fibers[0])
        assert repo.find('fiber_joints', joint) is not None and port in service.trace(fixed)
        service.validate()
        checks.append('A direction works; Join attaches a diverted end; Restore route preserves the existing joint and optical continuity')

        # Leave the reusable example showing the simple B diversion.
        service.delete_element(ont)
        dock.select('cables', cable)
        dock.reset_selected_branch()
        branch_dialog()
        mouse(pt(900, 0), MOUSE_LEFT)
        mouse(pt(1500, 450), MOUSE_LEFT)
        assert not errors, errors
        saved = repo.snapshot()
        dock.attach(repo.path)
        assert saved == dock.repository.snapshot()
        dock.service.validate()
        dock.show_purpose_colors()
        capture('branch-example')
        checks.append('Reopening the saved example preserves routes, ends, IDs and data')
        status = 'passed'
        print('BRANCH DESKTOP REVIEW PASSED:', checks, flush=True)
    except BaseException:
        errors.append(traceback.format_exc())
        traceback.print_exc()
    finally:
        payload = dict(status=status, qgis=Qgis.QGIS_VERSION, qt=QT_VERSION_STR, pyqt=PYQT_VERSION_STR,
                       interface=type(iface).__name__, platform=QgsApplication.platformName(),
                       checks=checks, errors=errors, expected_errors=expected_errors)
        (output / 'desktop-results.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        if dock:
            dock.dispose()
            dock.close()
        QgsProject.instance().clear()
        QgsProject.instance().setDirty(False)
        QgsApplication.exit(0 if status == 'passed' else 1)


QTimer.singleShot(700, main)
