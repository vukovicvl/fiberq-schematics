"""Render the ODF review case into a fresh directory; run in QGIS Python.

QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 tests/render_odf_preview.py
"""

import os
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsMapRendererParallelJob,
                       QgsMapSettings, QgsProject, QgsRectangle)
from qgis.PyQt.QtCore import QSize, QT_VERSION_STR, PYQT_VERSION_STR
from qgis.PyQt.QtGui import QColor
from fiberq_designer import exports
from fiberq_designer.cad_view import CadView
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import OdfProperties
from fiberq_designer.housing_dialog import HousingDialog
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from test_qgis_integration import TestIface


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-odf-render-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-odf-review-'))
    repo = GeoPackageRepository.create(output / 'odf24.gpkg')
    service = NetworkService(repo)
    service.add_odf('', OdfProperties(), pt(0, 0))
    service.validate()
    scene = CadView()
    scene.rebuild(repo)
    scene.set_dark(True)
    settings = QgsMapSettings()
    settings.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
    settings.setLayers(scene.layers())
    settings.setBackgroundColor(QColor('#212930'))
    settings.setOutputSize(QSize(453, 679))
    # Same framing as supplied Capture6.JPG, for a direct visual comparison.
    settings.setExtent(QgsRectangle(-151.2, -226, 59.8, 101))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    job.renderedImage().save(str(output / 'odf24.png'))
    labels = job.takeLabelingResults().allLabels()
    expected = {'OR 1'} | {str(n) for n in range(1, 25)}
    if {label.labelText for label in labels} != expected:
        raise RuntimeError('Missing or unexpected ODF labels in the render.')
    dialog = HousingDialog()
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'housing-dialog.png'))
    dialog.close()
    iface = TestIface()
    layers = scene.paper_layers()
    iface.window.show()
    app.processEvents()
    iface.canvas.setDestinationCrs(settings.destinationCrs())
    iface.canvas.setLayers(layers)
    iface.canvas.setExtent(settings.extent())
    layout = exports.make_layout(QgsProject.instance(), layers, repo.path, settings.extent())
    for extension in ('pdf', 'svg'):
        exports.export_layout(layout, str(output / ('odf24.' + extension)), extension)
    exports.export_dxf(iface.canvas, layers, settings.extent(), str(output / 'odf24.dxf'))
    from osgeo import ogr
    with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
        dataset = ogr.Open(str(output / 'odf24.dxf'))
        exported_labels = {f.GetField('Text') for f in dataset.GetLayer(0)
                           if f.GetField('Layer').endswith('housing labels')}
        if exported_labels != expected:
            raise RuntimeError('Missing or unexpected ODF labels in the DXF.')
        dataset = None
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Font: ' + repo.features('housings')[0]['font_family'])
    print('ODF render includes all 25 labels.')
    print('Review output: ' + str(output), flush=True)
    iface.window.close()
    return app, profile  # Keep the isolated Qt/QGIS application alive until process exit.


if __name__ == '__main__':
    application, application_profile = main()
