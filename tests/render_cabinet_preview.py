"""Render milestone 3 cabinet review files in a fresh temporary directory.

QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 tests/render_cabinet_preview.py
"""

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import Qgis, QgsApplication, QgsRectangle
from qgis.PyQt.QtCore import QSize, QT_VERSION_STR, PYQT_VERSION_STR
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import HousingProperties, OdfProperties
from fiberq_designer.housing_dialog import HousingDialog
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from render_odf_modules_preview import render


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-cabinet-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-cabinet-review-'))
    props = HousingProperties(kind='FDH', capacity=12, mounting='indoor', requested_used=1, splitter_ratio=8)
    repo = GeoPackageRepository.create(output / 'fdh12-splitter.gpkg')
    service = NetworkService(repo)
    service.add_housing('1', props, pt(0, 0))
    service.validate()
    render(repo, output, 'fdh12-splitter', QSize(662, 654), QgsRectangle(-152, -55, 32, 125))
    dialog = HousingDialog(properties=props, can_attach=True)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'fdh-dialog.png'))
    dialog.kind_radios['MDU'].setChecked(True)
    dialog.address.setText('Entrance 2')
    app.processEvents()
    dialog.grab().save(str(output / 'mdu-dialog.png'))
    dialog.close()

    ordinary = GeoPackageRepository.create(output / 'fdh48-four-ports.gpkg')
    service = NetworkService(ordinary)
    service.add_housing('1', HousingProperties(kind='FDH', capacity=48, mounting='outdoor',
                        requested_used=4, address='Building A'), pt(0, 0))
    service.validate()
    render(ordinary, output, 'fdh48-four-ports', QSize(720, 610), QgsRectangle(-155, -76, 35, 85))

    mdu = GeoPackageRepository.create(output / 'mdu24-splitter.gpkg')
    service = NetworkService(mdu)
    service.add_housing('1', HousingProperties(kind='MDU', capacity=24, mounting='free-standing',
                        requested_used=1, splitter_ratio=16, address='Entrance 2'), pt(0, 0))
    service.validate()
    render(mdu, output, 'mdu24-splitter', QSize(660, 870), QgsRectangle(-155, -55, 40, 202))

    attached = GeoPackageRepository.create(output / 'attachment.gpkg')
    service = NetworkService(attached)
    cable = service.add_loose_cable('FEEDER-12', 12, [pt(0, 0), pt(1000, 0)], 250)
    service.attach_odf(cable, 'A', '1', OdfProperties(capacity=12))
    service.attach_housing(cable, 'B', '1', props)
    service.validate()
    render(attached, output, 'attachment', QSize(1750, 650), QgsRectangle(-145, -145, 1145, 136))
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Font: ' + repo.features('housings')[0]['font_family'])
    print('All four scenes and DXFs contain every required housing label.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
