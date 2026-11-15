"""Milestone 9B production wizard, outlet geometry and export review."""

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import Qgis, QgsApplication, QgsRectangle
from qgis.PyQt.QtCore import QSize, QT_VERSION_STR, PYQT_VERSION_STR
from fiberq_designer.app import launch
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import HousingProperties
from fiberq_designer.outlet_dialog import OutletDialog
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from render_fiber_operations_preview import review
from test_qgis_integration import TestIface


def manual(tubes=2, fibers=6):
    return CableProperties(category='', catalog_model='', model='Distribution', tubes=tubes, fibers_per_tube=fibers)


def housing_case(output, name, kind='ODF', capacity=48, fibers=12):
    repo = GeoPackageRepository.create(output / (name + '.gpkg'))
    service = NetworkService(repo)
    first = service.add_catalog_cable('K1', CableProperties(fibers_per_tube=fibers), [pt(0, 0), pt(1200, 0)])
    housing = service.attach_housing(first, 'A', '1', HousingProperties(kind=kind, capacity=capacity,
        requested_used=4 if fibers == 12 else 2, mounting='wall' if kind == 'OTB' else 'rack 19"'))
    return repo, service, housing


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-outlet-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-outlet-review-'))
    repo, service, housing = housing_case(output, 'repeated-elbows')
    first = service.add_outlet(housing, manual(), name='OUT1')
    old = {f['fiber_id']: service.fiber_scene().drawn_fibers[f['fiber_id']].asWkt()
           for f in repo.features('fibers') if f['cable_id'] == first}
    service.add_outlet(housing, manual(1, 4), name='OUT2')
    scene = service.fiber_scene()
    if not all(scene.drawn_fibers[key].asWkt() == value for key, value in old.items()):
        raise RuntimeError('The repeated outlet moved an existing cable fiber.')
    review(repo, output, 'repeated-elbows', QSize(1950, 1250), QgsRectangle(-300, -780, 1270, 110))

    box, service, housing = housing_case(output, 'straight-box', 'OTB', 12, 6)
    frame = box.find('frames', housing).geometry().asWkt()
    service.add_outlet(housing, manual(1, 6), mode='straight', name='OUT1')
    if box.find('frames', housing).geometry().asWkt() != frame:
        raise RuntimeError('The straight outlet changed the housing frame.')
    review(box, output, 'straight-box', QSize(1950, 1000), QgsRectangle(-200, -650, 1270, 130))

    closure = GeoPackageRepository.create(output / 'closure-outlet.gpkg')
    service = NetworkService(closure)
    cable = service.add_catalog_cable('K1', CableProperties(), [pt(0, 0), pt(1600, 0)])
    frame = service.add_closure('1', ClosureProperties(location_number='7'), cable, pt(600, 0))
    outlet = service.add_outlet(frame, manual(), mode='straight', name='OUT1')
    incoming = [f['fiber_id'] for f in closure.features('fibers') if f['cable_id'] == cable][:2]
    service.cut_fibers(incoming, pt(630, 0))
    cuts, ends = closure.features('fiber_cuts'), service.ends_of(outlet, 'A')
    service.join_group([c['left_id'] for c in cuts], [e['end_id'] for e in ends[:2]])
    if service.ends_of(outlet, 'B')[0]['end_id'] not in service.trace(service.ends_of(cable, 'A')[0]['end_id']):
        raise RuntimeError('The explicit closure splice does not trace to the outlet.')
    review(closure, output, 'closure-outlet', QSize(2100, 1100), QgsRectangle(-60, -740, 1660, 100))

    network, service, housing = housing_case(output, 'outlet-far-housing')
    outlet = service.add_outlet(housing, manual(), name='OUT1', length_m=80)
    service.add_housing_cable(housing, outlet, 'B', additional=10)
    service.attach_housing(outlet, 'A', '2', HousingProperties(kind='OTB', capacity=12, mounting='wall'))
    review(network, output, 'outlet-far-housing', QSize(1950, 1350), QgsRectangle(-300, -950, 1270, 110))

    pending, service, housing = housing_case(output, 'uncommitted-preview')
    iface = TestIface()
    iface.window.resize(1600, 1100)
    dock = launch(iface)
    dock.attach(pending.path, pending)
    dock.dark_canvas.setChecked(True)
    iface.window.show()
    dock.fit()
    app.processEvents()
    before = pending.snapshot()
    dock.activate('frame_outlet')
    dialog = OutletDialog(dock, housing)
    dialog.show()
    app.processEvents()
    dialog.recompute()
    iface.canvas.refresh()
    iface.canvas.waitWhileRendering()
    app.processEvents()
    dialog.grab().save(str(output / 'outlet-dialog.png'))
    iface.window.grab().save(str(output / 'outlet-preview.png'))
    dialog.mode.setCurrentIndex(dialog.mode.findData('straight'))
    app.processEvents()
    dialog.grab().save(str(output / 'straight-dialog.png'))
    dialog.reject()
    dock.stop_tool()
    if pending.snapshot() != before:
        raise RuntimeError('Cancelling the production outlet preview wrote network records.')
    dock.attach(network.path, network)
    dock.inspector.show()
    dock.select('cables', outlet)
    dock.fit()
    iface.canvas.refresh()
    iface.canvas.waitWhileRendering()
    app.processEvents()
    iface.window.grab().save(str(output / 'outlet-inspector.png'))
    dock.dispose()
    dock.close()
    iface.window.close()
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Four outlet scenes validate/reopen, preserve required QGIS/DXF labels and export PDF/SVG/DXF.')
    print('Existing elbow paths and straight-box frame are unchanged; closure joins trace to the outlet; cancellation preserves the complete snapshot.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
