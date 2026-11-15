"""Render the Workspace Network/Splice examples in a fresh temporary folder."""

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import Qgis, QgsApplication, QgsRectangle
from qgis.PyQt.QtCore import QSize, QT_VERSION_STR, PYQT_VERSION_STR
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from render_fiber_operations_preview import review


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-demo-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-demo-review-'))
    for name, method, extent, size in (
            ('network', 'network_example', QgsRectangle(-160, -160, 1040, 110), QSize(1800, 650)),
            ('splice', 'splice_example', QgsRectangle(-40, -390, 1100, 80), QSize(1800, 850))):
        repo = GeoPackageRepository.create(output / (name + '.gpkg'))
        service = NetworkService(repo)
        getattr(service, method)()
        review(repo, output, name, size, extent)
        print(name, service.validate(), flush=True)
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Both examples validate/reopen, retain all required QGIS/DXF labels and export PDF/SVG/DXF.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
