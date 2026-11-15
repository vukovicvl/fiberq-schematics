"""Render milestone 5 review files in a fresh directory.

QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 tests/render_closure_preview.py
"""

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import Qgis, QgsApplication, QgsRectangle
from qgis.PyQt.QtCore import QSize, QT_VERSION_STR, PYQT_VERSION_STR
from fiberq_designer.closure_dialog import ClosureDialog
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.geometry import pt
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from render_odf_modules_preview import render


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-closure-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-closure-review-'))
    ordinary = GeoPackageRepository.create(output / 'n-ordinary.gpkg')
    service = NetworkService(ordinary)
    cable = service.add_loose_cable('FEEDER-24', 24, [pt(0, 0), pt(1000, 0)], 250)
    service.add_closure('', ClosureProperties(location_number='12'), cable, pt(350, 0))
    service.validate()
    render(ordinary, output, 'n-ordinary', QSize(1400, 865), QgsRectangle(100, -240, 650, 100))
    dialog = ClosureDialog(properties=ClosureProperties(location_number='12'))
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'closure-dialog.png'))
    dialog.close()
    dialog = ClosureDialog(properties=ClosureProperties(closure_type='NL', location='pole', location_number='7'),
                           repeated=True, continue_series=True, same_cable=True, cable_name='FEEDER-12')
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'series-dialog.png'))
    dialog.close()

    latent = GeoPackageRepository.create(output / 'nl-latent.gpkg')
    service = NetworkService(latent)
    cable = service.add_loose_cable('FEEDER-12', 12, [pt(0, 0), pt(1000, 0)], 250)
    service.add_closure('', ClosureProperties(closure_type='NL', location='pole', location_number='7'), cable, pt(650, 0), 12)
    service.validate()
    render(latent, output, 'nl-latent', QSize(1200, 552), QgsRectangle(500, -130, 1000, 100))

    widened = GeoPackageRepository.create(output / 'two-cables.gpkg')
    service = NetworkService(widened)
    first = service.add_loose_cable('FEEDER-A', 12, [pt(0, 0), pt(1000, 0)])
    second = service.add_loose_cable('FEEDER-B', 12, [pt(0, -180), pt(1000, -180)])
    identity = service.add_closure('', ClosureProperties(location='buried', location_number='4'), first, pt(300, 0), 12)
    service.widen_closure(identity, second)
    service.validate()
    render(widened, output, 'two-cables', QSize(1150, 900), QgsRectangle(120, -320, 650, 110))

    bent = GeoPackageRepository.create(output / 'bent-cable.gpkg')
    service = NetworkService(bent)
    cable = service.add_loose_cable('BENT-12', 12, [pt(0, 0), pt(800, 0), pt(800, 700)])
    service.add_closure('', ClosureProperties(location_number='1'), cable, pt(480, 0))
    service.add_closure('', ClosureProperties(closure_type='NL', location='pole', location_number='2'), cable, pt(800, 350), 10)
    service.validate()
    render(bent, output, 'bent-cable', QSize(1440, 1160), QgsRectangle(-60, -145, 1060, 760))

    splice = GeoPackageRepository.create(output / 'existing-splice.gpkg')
    service = NetworkService(splice)
    cable = service.add_loose_cable('THROUGH-1', 1, [pt(0, 0), pt(1000, 0)])
    a = service.add_element('CABLE', 'A', 1, 'left', pt(0, -10))
    b = service.add_element('CABLE', 'B', 1, 'left', pt(800, -10))
    service.add_link(service.ports_of(a)[0], service.ports_of(b)[0])
    service.add_closure('', ClosureProperties(location_number='9'), cable, pt(450, 0))
    service.validate()
    render(splice, output, 'existing-splice', QSize(1200, 520), QgsRectangle(300, -70, 650, 82))
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Font: ' + ordinary.features('closures')[0]['font_family'])
    print('All five scenes and DXFs contain every required closure label.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
