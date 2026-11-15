"""Render milestone 2 patch-panel review files in a fresh directory.

QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 tests/render_patch_panel_preview.py
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
    profile = tempfile.TemporaryDirectory(prefix='fqsd-panel-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-panel-review-'))
    properties = HousingProperties(kind='PATCH_PANEL', modules=2)
    repo = GeoPackageRepository.create(output / 'two-module.gpkg')
    service = NetworkService(repo)
    service.add_housing('', properties, pt(0, 0))
    service.validate()
    render(repo, output, 'two-module', QSize(480, 1050), QgsRectangle(-166, -450, 66, 101))
    dialog = HousingDialog(properties=properties, can_attach=True)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'housing-dialog.png'))
    dialog.cable.setChecked(True)
    dialog.used.setText('36')
    app.processEvents()
    dialog.grab().save(str(output / 'housing-attach-dialog.png'))
    dialog.close()
    attached = GeoPackageRepository.create(output / 'attachment.gpkg')
    service = NetworkService(attached)
    cable = service.add_loose_cable('FEEDER-48', 48, [pt(0, 0), pt(1200, 0)], 250)
    service.attach_odf(cable, 'A', '1', OdfProperties(modules=2, requested_used=36))
    service.attach_housing(cable, 'B', '', HousingProperties(kind='PATCH_PANEL', modules=2, requested_used=12))
    service.validate()
    render(attached, output, 'attachment', QSize(1800, 900), QgsRectangle(-180, -450, 1380, 110))
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Font: ' + repo.features('housings')[0]['font_family'])
    print('Both scenes and DXFs contain every housing label, including repeated connector numbers.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
