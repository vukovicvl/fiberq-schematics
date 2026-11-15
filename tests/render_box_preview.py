"""Render milestone 4 FAT box review files in a fresh temporary directory.

QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 tests/render_box_preview.py
"""

from dataclasses import replace
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
    profile = tempfile.TemporaryDirectory(prefix='fqsd-box-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-box-review-'))
    props = HousingProperties(kind='FAT', capacity=12, mounting='wall', address='Entrance 2')
    repo = GeoPackageRepository.create(output / 'fat12.gpkg')
    service = NetworkService(repo)
    service.add_housing('1', props, pt(0, 0))
    service.validate()
    render(repo, output, 'fat12', QSize(650, 1000), QgsRectangle(-110, -145, 40, 92))
    dialog = HousingDialog(properties=props, can_attach=True)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'box-dialog.png'))
    dialog.splitter.setChecked(True)
    app.processEvents()
    dialog.grab().save(str(output / 'splitter-dialog.png'))
    dialog.kind_radios['OTB'].setChecked(True)
    app.processEvents()
    dialog.grab().save(str(output / 'otb-dialog.png'))
    dialog.close()

    split = GeoPackageRepository.create(output / 'fat12-splitter.gpkg')
    service = NetworkService(split)
    service.add_housing('1', HousingProperties(kind='FAT', capacity=12, mounting='pole',
                        splitter_ratio=8, address='Entrance 2'), pt(0, 0))
    service.validate()
    render(split, output, 'fat12-splitter', QSize(700, 960), QgsRectangle(-142, -50, 35, 194))

    otb = GeoPackageRepository.create(output / 'otb12-splitter.gpkg')
    service = NetworkService(otb)
    service.add_housing('', replace(props, kind='OTB', mounting='pole', splitter_ratio=8), pt(0, 0))
    service.validate()
    render(otb, output, 'otb12-splitter', QSize(700, 960), QgsRectangle(-142, -50, 35, 194))

    capacities = GeoPackageRepository.create(output / 'all-capacities.gpkg')
    service = NetworkService(capacities)
    for index, count in enumerate((1, 2, 4, 6, 8, 12, 16, 24)):
        service.add_housing(str(count), HousingProperties(kind='FAT', capacity=count, mounting='wall'), pt(index * 150, 0))
    service.validate()
    render(capacities, output, 'all-capacities', QSize(1900, 650), QgsRectangle(-95, -246, 1110, 90))

    attached = GeoPackageRepository.create(output / 'attachment.gpkg')
    service = NetworkService(attached)
    cable = service.add_loose_cable('FEEDER-12', 12, [pt(0, 0), pt(800, 0)], 250)
    service.attach_odf(cable, 'A', '1', OdfProperties(capacity=12))
    service.attach_housing(cable, 'B', '1', HousingProperties(kind='FAT', capacity=12, mounting='pole', splitter_ratio=8))
    service.validate()
    render(attached, output, 'attachment', QSize(1750, 700), QgsRectangle(-145, -145, 950, 160))
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Font: ' + repo.features('housings')[0]['font_family'])
    print('All five scenes and DXFs contain every required housing label.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
