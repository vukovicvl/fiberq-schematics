"""Review cutting joined closure fibers and clean cut/connection drawing in QGIS.

Use qgis --code with FQSD_REVIEW_OUTPUT pointing at a fresh temporary folder.
The isolated QGIS process exits after saving its results.
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
from qgis.utils import iface
from fiberq_designer import exports
from fiberq_designer.app import launch
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.compat import MOUSE_LEFT
from fiberq_designer.geometry import pt
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService


def main():
    output = Path(os.environ['FQSD_REVIEW_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    dock, checks, errors, status = None, [], [], 'failed'
    try:
        assert not QgsProject.instance().mapLayers()
        repo = GeoPackageRepository.create(output / 'cut-joined-closures.gpkg')
        service = NetworkService(repo)
        first = service.add_catalog_cable('K1', CableProperties(tubes=2), [pt(0, 0), pt(400, 0)])
        second = service.add_catalog_cable('K2', CableProperties(tubes=2, fibers_per_tube=6), [pt(560, 0), pt(960, 0)])
        service.add_closure('1', ClosureProperties(), first, pt(360, 0), 24)
        a, b = service.ends_of(first, 'B'), service.ends_of(second, 'A')
        service.join_group([end['end_id'] for end in a[:12]], [end['end_id'] for end in b])
        through = service.add_catalog_cable('K3', CableProperties(), [pt(0, -400), pt(960, -400)])
        service.add_closure('2', ClosureProperties(), through, pt(360, -400), 24)
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
            canvas.setExtent(QgsRectangle(point.x() - 260, point.y() - 150, point.x() + 260, point.y() + 150))
            pump()
            pixel = canvas.getCoordinateTransform().transform(point)
            QTest.mouseClick(canvas.viewport(), MOUSE_LEFT, pos=QPoint(round(pixel.x()), round(pixel.y())))
            QgsApplication.processEvents()
            assert not errors, errors

        def capture(name, extent):
            iface.mapCanvas().setExtent(extent)
            pump()
            assert iface.mainWindow().grab().save(str(output / (name + '.png')))

        initial = repo.snapshot()
        dock.command_actions['cut_fiber'].trigger()
        click(pt(480, 0))
        assert len(repo.features('fiber_joints')) == 11
        assert not repo.features('fiber_nodes')
        assert b[0]['end_id'] not in service.trace(a[0]['end_id'])
        capture('single-splice-cut', QgsRectangle(270, -125, 730, 50))
        dock.command_actions['undo'].trigger()
        assert initial == repo.snapshot()
        dock.command_actions['cut_window'].trigger()
        click(pt(440, 2))
        click(pt(520, -43))
        assert len(repo.features('fiber_joints')) == 6
        assert all((b[number]['end_id'] in service.trace(a[number]['end_id'])) == (number >= 6) for number in range(12))
        assert not repo.features('fiber_nodes')
        capture('six-splices-cut', QgsRectangle(270, -125, 730, 50))
        checks.append('Actual single/window Cut opens joined closure fibers, retaining unselected joins and exposing original snap endpoints')

        dock.command_actions['join_fiber'].trigger()
        click(a[0].geometry().asPoint())
        dock.canvas_move(b[0].geometry().asPoint())
        assert dock.click_tool.snap_marker.isVisible()
        click(b[0].geometry().asPoint())
        assert b[0]['end_id'] in service.trace(a[0]['end_id'])
        dock.command_actions['cut_window'].trigger()
        click(pt(440, -398))
        click(pt(520, -419))
        assert len(repo.features('fiber_cuts')) == 3
        assert len(repo.features('fiber_nodes')) == 6
        capture('straight-cuts-without-ticks', QgsRectangle(270, -470, 730, -350))
        cut = repo.features('fiber_cuts')[0]
        nodes = {node['node_id']: node for node in repo.features('fiber_nodes')}
        dock.command_actions['join_fiber'].trigger()
        click(nodes[cut['left_id']].geometry().asPoint())
        click(nodes[cut['right_id']].geometry().asPoint())
        dock.command_actions['select'].trigger()
        dock.clear_selection()
        capture('rejoined-single-stroke', QgsRectangle(270, -465, 730, -350))
        checks.append('Cut ends snap/rejoin; ordinary cuts have open gaps without vertical ticks; rejoined line uses one matching stroke')

        saved = repo.snapshot()
        dock.command_actions['undo'].trigger()
        assert len(repo.features('fiber_joints')) == 7
        dock.command_actions['redo'].trigger()
        assert saved == repo.snapshot()
        dock.clear_selection()
        dock.fit()
        pump()
        for extension in ('pdf', 'svg'):
            exports.export_layout(dock.make_layout(), str(output / ('cut-joined-closures.' + extension)), extension)
        exports.export_dxf(iface.mapCanvas(), dock.drawing_layers(), dock.diagram_extent(), str(output / 'cut-joined-closures.dxf'))
        dock.attach(repo.path)
        assert saved == dock.repository.snapshot()
        dock.service.validate()
        checks.append('Undo/Redo/reopen preserve all records and IDs; PDF/SVG/DXF export succeeds')
        status = 'passed'
        print('CUT DESKTOP REVIEW PASSED:', checks, flush=True)
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
