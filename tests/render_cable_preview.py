"""Render milestone 6 geometry, dialogs, exports and bundle preview in isolation."""

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import Qgis, QgsApplication, QgsRectangle
from qgis.PyQt.QtCore import QSize, QT_VERSION_STR, PYQT_VERSION_STR, QTimer
from fiberq_designer.app import launch
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.cable_dialog import CableDialog
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import HousingProperties, OdfProperties
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from render_odf_modules_preview import render
from test_qgis_integration import TestIface


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-cable-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-cable-review-'))
    repo = GeoPackageRepository.create(output / 'tube-structures.gpkg')
    service = NetworkService(repo)
    service.add_catalog_cable('K1', CableProperties(tubes=2, fibers_per_tube=6), [pt(0, 0), pt(1000, 0)])
    service.add_catalog_cable('K2', CableProperties(), [pt(0, -220), pt(1000, -220)])
    service.validate()
    render(repo, output, 'tube-structures', QSize(1700, 850), QgsRectangle(-60, -360, 1060, 65))
    for category, model in (('kbAerial', 'ADSS 03'), ('kbIndoor', 'RISER H')):
        props = CableProperties(category, model, model, 2, 6)
        dialog = CableDialog(properties=props)
        dialog.bends.setChecked(category == 'kbIndoor')
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(output / ('cable-' + category + '-dialog.png')))
        dialog.close()

    bent = GeoPackageRepository.create(output / 'bent-and-attached.gpkg')
    service = NetworkService(bent)
    identity = service.add_catalog_cable('K3', CableProperties('kbDuct', 'TOSM 03', 'TOSM 03', 2, 12),
        [pt(0, 0), pt(900, 0), pt(900, 800), pt(1800, 800)], 1200)
    service.attach_odf(identity, 'A', '', OdfProperties(capacity=24))
    service.attach_housing(identity, 'B', '', HousingProperties(kind='FAT', capacity=24, mounting='pole', splitter_ratio=16))
    service.add_closure('', ClosureProperties(location_number='12'), identity, pt(350, 0), 12)
    service.validate()
    render(bent, output, 'bent-and-attached', QSize(1900, 1250), QgsRectangle(-170, -285, 1980, 1130))

    snapped = GeoPackageRepository.create(output / 'housing-first.gpkg')
    service = NetworkService(snapped)
    a = service.add_odf('', OdfProperties(capacity=24), pt(0, 0))
    b = service.add_housing('', HousingProperties(kind='FDH', capacity=48, mounting='outdoor', requested_used=4), pt(1300, 0))
    service.add_catalog_cable('K4', CableProperties('kbMicro', 'TO(M)SM 03', 'TO(M)SM 03', 2, 6),
        [pt(0, 0), pt(1300, 0)], endpoints={'A': a, 'B': b})
    service.validate()
    render(snapped, output, 'housing-first', QSize(1750, 850), QgsRectangle(-175, -280, 1475, 130))

    iface = TestIface()
    iface.window.resize(1440, 900)
    dock = launch(iface)
    dock.attach(bent.path, bent)
    dock.dark_canvas.setChecked(True)
    iface.window.show()
    dock.fit()
    app.processEvents()
    iface.canvas.refresh()
    iface.canvas.waitWhileRendering()
    app.processEvents()
    iface.window.grab().save(str(output / 'ribbon-workspace.png'))
    before = bent.snapshot()
    dialog = dock.catalog_dialog
    dialog.bends.setChecked(True)
    dialog.snap.setChecked(False)
    QTimer.singleShot(0, dialog.accept)
    dock.create_catalog_cable('kbAerial')
    dock.catalog_cable_click(pt(200, 400))
    dock.catalog_cable_click(pt(700, 400))
    dock.preview_catalog_cable(pt(700, 950))
    app.processEvents()
    iface.window.grab().save(str(output / 'bundle-preview.png'))
    dock.stop_tool()
    if before != bent.snapshot():
        raise RuntimeError('Cancelled bundle preview changed network records.')
    dock.dispose()
    dock.close()
    iface.window.close()
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Three scenes validated; every CAD label is present in scene and DXF; PDF/SVG/DXF exported.')
    print('Cancelled preview preserves the complete network snapshot.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
