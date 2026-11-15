"""Milestone 9A source-layout, production-dialog and export review in isolation."""

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
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import HousingProperties, OdfProperties
from fiberq_designer.housing_cable_dialog import AddHousingCableDialog
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from render_fiber_operations_preview import review
from test_qgis_integration import TestIface


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-add-cable-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-add-cable-review-'))
    repo = GeoPackageRepository.create(output / 'three-cable-odf.gpkg')
    service = NetworkService(repo)
    first = service.add_catalog_cable('K1', CableProperties(), [pt(0, 0), pt(1200, 0)])
    housing = service.attach_odf(first, 'A', '1', OdfProperties(capacity=48, requested_used=4, rack_module='R1, M1'))
    original = {p['port_id']: p.geometry().asWkt() for p in service.ports_of(housing)[:4]}
    for index, y in ((2, -180), (3, -360)):
        cable = service.add_catalog_cable('K' + str(index), CableProperties(), [pt(0, y), pt(1200, y)])
        service.add_housing_cable(housing, cable, 'A')
    if not all(repo.find('ports', key).geometry().asWkt() == value for key, value in original.items()):
        raise RuntimeError('Adding cable moved an existing connected port.')
    review(repo, output, 'three-cable-odf', QSize(1800, 1100), QgsRectangle(-190, -620, 1280, 110))

    cabinet = GeoPackageRepository.create(output / 'cabinet-splitter.gpkg')
    service = NetworkService(cabinet)
    first = service.add_catalog_cable('K1', CableProperties(), [pt(0, 0), pt(1200, 0)])
    housing = service.attach_housing(first, 'A', '1', HousingProperties(kind='FDH', capacity=48,
        mounting='indoor', requested_used=1, splitter_ratio=8, address='Building A'))
    child = service.contained_splitter(housing)['splitter_id']
    original = cabinet.find('frames', child).geometry().asWkt()
    second = service.add_catalog_cable('K2', CableProperties(), [pt(0, -180), pt(1200, -180)])
    service.add_housing_cable(housing, second, 'A')
    if original != cabinet.find('frames', child).geometry().asWkt():
        raise RuntimeError('Add cable moved the existing contained splitter.')
    review(cabinet, output, 'cabinet-splitter', QSize(1800, 850), QgsRectangle(-170, -350, 1280, 170))

    modules = GeoPackageRepository.create(output / 'two-module-panel.gpkg')
    service = NetworkService(modules)
    first = service.add_catalog_cable('K1', CableProperties(tubes=2), [pt(0, 0), pt(1200, 0)])
    housing = service.attach_housing(first, 'A', '1', HousingProperties(kind='PATCH_PANEL', capacity=24,
        modules=2, requested_used=12, rack_module='R1, M1'))
    second = service.add_catalog_cable('K2', CableProperties(), [pt(0, -240), pt(1200, -240)])
    service.add_housing_cable(housing, second, 'A', module_no=2)
    review(modules, output, 'two-module-panel', QSize(1800, 1050), QgsRectangle(-195, -500, 1280, 110))

    box = GeoPackageRepository.create(output / 'two-cable-box.gpkg')
    service = NetworkService(box)
    props = CableProperties(fibers_per_tube=6)
    first = service.add_catalog_cable('K1', props, [pt(0, 0), pt(1000, 0)])
    housing = service.attach_housing(first, 'A', '1', HousingProperties(kind='OTB', capacity=12,
        mounting='wall', requested_used=2, address='Entrance A'))
    second = service.add_catalog_cable('K2', props, [pt(0, -140), pt(1000, -140)])
    service.add_housing_cable(housing, second, 'A')
    review(box, output, 'two-cable-box', QSize(1600, 800), QgsRectangle(-120, -260, 1080, 130))

    pending = GeoPackageRepository.create(output / 'uncommitted-preview.gpkg')
    service = NetworkService(pending)
    first = service.add_catalog_cable('K1', CableProperties(), [pt(0, 0), pt(1200, 0)])
    housing = service.attach_odf(first, 'A', '1', OdfProperties(capacity=48, requested_used=4))
    second = service.add_catalog_cable('K2', CableProperties(), [pt(0, -180), pt(1200, -180)])
    iface = TestIface()
    iface.window.resize(1520, 980)
    dock = launch(iface)
    dock.attach(pending.path, pending)
    dock.dark_canvas.setChecked(True)
    dock.ribbon_tabs.setCurrentIndex(1)
    iface.window.show()
    dock.fit()
    before = pending.snapshot()
    dock.select('elements', housing)
    dock.start_add_housing_cable()
    dock.preview_add_housing_cable(pt(0, -180))
    iface.canvas.refresh()
    iface.canvas.waitWhileRendering()
    app.processEvents()
    iface.window.grab().save(str(output / 'add-cable-hover.png'))
    dialog = AddHousingCableDialog(dock, housing, second, 'A')
    dialog.additional.setValue(10)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'add-cable-dialog.png'))
    dialog.reject()
    dock.stop_tool()
    if before != pending.snapshot():
        raise RuntimeError('Cancelled Add cable preview wrote network records.')
    dock.attach(repo.path, repo)
    dock.inspector.show()
    dock.select('elements', repo.features('elements')[0]['obj_id'])
    dock.fit()
    iface.canvas.refresh()
    iface.canvas.waitWhileRendering()
    app.processEvents()
    iface.window.grab().save(str(output / 'three-cable-inspector.png'))
    dock.dispose()
    dock.close()
    iface.window.close()
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Four multi-cable scenes validate/reopen; every required CAD label is present in QGIS/DXF; PDF/SVG/DXF exported.')
    print('Existing connected ODF ports and FDH splitter frame remain exact; cancelling production previews preserves the complete snapshot.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
