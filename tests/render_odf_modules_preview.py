"""Render milestone 1B review files in a fresh temporary directory.

QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 tests/render_odf_modules_preview.py
"""

from collections import Counter
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from osgeo import ogr
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


def render(repo, output, name, size, extent):
    scene = CadView()
    scene.rebuild(repo)
    scene.set_dark(True)
    settings = QgsMapSettings()
    settings.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
    settings.setLayers(scene.layers())
    settings.setBackgroundColor(QColor('#212930'))
    settings.setOutputSize(size)
    settings.setExtent(extent)
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    job.renderedImage().save(str(output / (name + '.png')))
    expected = Counter(f['text'] for f in scene.housing_labels.getFeatures())
    actual = Counter(label.labelText for label in job.takeLabelingResults().allLabels())
    if any(actual[text] < count for text, count in expected.items()):
        raise RuntimeError('Missing housing labels in ' + name)
    iface = TestIface()
    iface.window.show()
    QgsApplication.processEvents()
    layers = scene.paper_layers()
    iface.canvas.setDestinationCrs(settings.destinationCrs())
    iface.canvas.setLayers(layers)
    iface.canvas.setExtent(extent)
    from fiberq_designer.workspace_model import read_settings
    layout = exports.make_layout(QgsProject.instance(), layers, repo.path, extent, read_settings(repo.features('workspace_settings'))['language'])
    for extension in ('pdf', 'svg'):
        exports.export_layout(layout, str(output / (name + '.' + extension)), extension)
    exports.export_dxf(iface.canvas, layers, extent, str(output / (name + '.dxf')))
    with ogr.ExceptionMgr(useExceptions=True) if hasattr(ogr, 'ExceptionMgr') else nullcontext():
        dataset = ogr.Open(str(output / (name + '.dxf')))
        labels = Counter(f.GetField('Text') for f in dataset.GetLayer(0)
                         if f.GetField('Layer').endswith('housing labels'))
        if labels != expected:
            raise RuntimeError('Missing or unexpected housing text entities in ' + name)
        dataset = None
    iface.window.close()
    return dict(expected)


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-m1b-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-m1b-review-'))
    repo = GeoPackageRepository.create(output / 'two-module.gpkg')
    service = NetworkService(repo)
    service.add_odf('1', OdfProperties(modules=2), pt(0, 0))
    service.validate()
    render(repo, output, 'two-module', QSize(480, 1050), QgsRectangle(-166, -450, 66, 101))
    dialog = HousingDialog(properties=OdfProperties(modules=2), can_attach=True)
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
    service.attach_odf(cable, 'B', '2', OdfProperties(modules=2, requested_used=12))
    service.validate()
    render(attached, output, 'attachment', QSize(1800, 900), QgsRectangle(-180, -450, 1380, 110))
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Font: ' + repo.features('housings')[0]['font_family'])
    print('Both scenes and DXFs contain every ODF label, including repeated connector numbers.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
