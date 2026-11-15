"""Render milestone 7 production geometry, forms and exports in isolation."""

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import (Qgis, QgsApplication, QgsMapSettings, QgsMapRendererParallelJob,
                       QgsCoordinateReferenceSystem, QgsRectangle)
from qgis.PyQt.QtCore import QSize, QT_VERSION_STR, PYQT_VERSION_STR, QTimer
from qgis.PyQt.QtGui import QColor
from fiberq_designer.active_layout import ActiveProperties
from fiberq_designer.app import launch
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.cad_view import CadView
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.fiber_model import PURPOSES
from fiberq_designer.geometry import pt
from fiberq_designer.operation_dialogs import (ActiveDialog, BranchDialog, FiberDataDialog,
                                             GroupJoinDialog, SlackDialog)
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from render_odf_modules_preview import render
from test_qgis_integration import TestIface


def review(repo, output, name, size, extent):
    service = NetworkService(repo)
    service.validate()
    render(repo, output, name, size, extent)
    scene = CadView()
    scene.rebuild(repo)
    settings = QgsMapSettings()
    settings.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
    paper_layers = scene.paper_layers()
    settings.setLayers(paper_layers)
    settings.setBackgroundColor(QColor('white'))
    settings.setOutputSize(size)
    settings.setExtent(extent)
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    job.renderedImage().save(str(output / (name + '-paper.png')))
    if not job.takeLabelingResults().allLabels():
        raise RuntimeError('Paper review did not render labels: ' + name)
    reopened = GeoPackageRepository.open(repo.path)
    NetworkService(reopened).validate()
    if repo.snapshot() != reopened.snapshot():
        raise RuntimeError('Reopened review workspace differs: ' + name)


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-optical-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-optical-review-'))
    gallery = GeoPackageRepository.create(output / 'active-equipment.gpkg')
    service = NetworkService(gallery)
    for label, properties, position in (
            ('S1', ActiveProperties(count=8), pt(160, 0)),
            ('S2', ActiveProperties(count=2), pt(160, -200)),
            ('S3', ActiveProperties(count=64), pt(580, 0)),
            ('OLT1', ActiveProperties(kind='OLT', count=8), pt(1000, 0)),
            ('OLT2', ActiveProperties(kind='OLT', count=16), pt(1300, 0)),
            ('ONT1', ActiveProperties(kind='ONT', count=1, address='Customer 12'), pt(1400, 0)),
            ('ONT2', ActiveProperties(kind='ONT', count=1, orientation=90, address='Floor 2'), pt(1460, -300))):
        service.add_active(label, properties, position)
    review(gallery, output, 'active-equipment', QSize(2200, 1000), QgsRectangle(-20, -550, 1580, 90))
    scene = service.fiber_scene()
    for element in gallery.features('elements'):
        if element['kind'] == 'ONT':
            for text in scene.housing_labels.getFeatures():
                if text['obj_id'] == element['obj_id'] and not scene.hit_frames[element['obj_id']].intersects(text.geometry()):
                    raise RuntimeError('An ONT label center lies outside its rotated housing.')

    operations = GeoPackageRepository.create(output / 'cuts-and-purposes.gpkg')
    service = NetworkService(operations)
    cable = service.add_catalog_cable('K1', CableProperties(), [pt(0, 0), pt(1600, 0)])
    service.add_closure('', ClosureProperties(location_number='7'), cable, pt(600, 0), 12)
    identities = [f['fiber_id'] for f in operations.features('fibers')]
    for identity, purpose in zip(identities, PURPOSES):
        service.set_fiber_data([identity], purpose=purpose)
    service.set_fiber_data(identities[:1], description='Feeder / building A')
    service.cut_fibers(identities[:6], pt(650, 0))
    cuts = operations.features('fiber_cuts')
    service.join_group([c['left_id'] for c in cuts[:3]], [cuts[i]['right_id'] for i in (1, 0, 2)])
    service.restore_through(identities[3])
    service.add_splice_marks(identities[6:8], pt(650, 0))
    service.add_slack(pt(1050, -150), 15, cable)
    scene = service.fiber_scene()
    if scene.fibers.featureCount() != 17 or scene.optical_marks.featureCount() != 5:
        raise RuntimeError('Unexpected continuous-section/splice counts in review scene.')
    review(operations, output, 'cuts-and-purposes', QSize(2200, 560), QgsRectangle(-60, -200, 1660, 75))

    network = GeoPackageRepository.create(output / 'branch-and-active-network.gpkg')
    service = NetworkService(network)
    cable = service.add_catalog_cable('FEEDER', CableProperties(), [pt(0, 0), pt(1600, 0)])
    identities = [f['fiber_id'] for f in network.features('fibers')]
    service.branch_fibers(identities[:6], pt(1800, -420), click=pt(1000, 0))
    splitter = service.add_active('S1', ActiveProperties(anchor='input'), pt(1900, -420))
    ports = service.ports_of(splitter)
    service.join_nodes(service.ends_of(cable, 'B')[0]['end_id'], ports[0]['port_id'])
    ont = service.add_active('ONT1', ActiveProperties(kind='ONT', count=1, address='Building A'), pt(2300, -420))
    service.join_nodes(ports[1]['port_id'], service.ports_of(ont)[0]['port_id'], 'drop')
    olt = service.add_active('OLT1', ActiveProperties(kind='OLT', count=8), pt(-100, 0))
    service.join_nodes(service.ports_of(olt)[0]['port_id'], service.ends_of(cable, 'A')[0]['end_id'], 'patch')
    service.add_slack(pt(700, -220), 12.5, cable)
    if service.ports_of(ont)[0]['port_id'] not in service.trace(service.ports_of(olt)[0]['port_id']):
        raise RuntimeError('OLT / branch / splitter / ONT graph is disconnected.')
    review(network, output, 'branch-and-active-network', QSize(2500, 900), QgsRectangle(-320, -550, 2470, 90))

    for name, dialog in (
            ('splitter-dialog', ActiveDialog(properties=ActiveProperties(anchor='input'))),
            ('olt-dialog', ActiveDialog(properties=ActiveProperties(kind='OLT', count=16))),
            ('ont-dialog', ActiveDialog(properties=ActiveProperties(kind='ONT', count=1, address='Building A'))),
            ('fiber-data-dialog', FiberDataDialog(scope='12 selected fibers')),
            ('group-join-dialog', GroupJoinDialog()), ('branch-dialog', BranchDialog()), ('slack-dialog', SlackDialog())):
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(output / (name + '.png')))
        dialog.close()
    iface = TestIface()
    iface.window.resize(1600, 900)
    dock = launch(iface)
    dock.attach(network.path, network)
    dock.dark_canvas.setChecked(True)
    dock.ribbon_tabs.setCurrentIndex(2)
    iface.window.show()
    dock.fit()
    app.processEvents()
    iface.canvas.refresh()
    iface.canvas.waitWhileRendering()
    app.processEvents()
    iface.window.grab().save(str(output / 'fiber-ribbon.png'))
    before = network.snapshot()
    QTimer.singleShot(0, lambda: dock.active_dialog.accept())
    dock.create_active('SPLITTER')
    dock.preview_active(pt(1000, -700))
    app.processEvents()
    iface.window.grab().save(str(output / 'active-placement-preview.png'))
    dock.stop_tool()
    if before != network.snapshot():
        raise RuntimeError('Cancelled active preview changed the optical network.')
    dock.dispose()
    dock.close()
    iface.window.close()
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Three validated/reopened scenes; every CAD label checked in scene and DXF; PDF/SVG/DXF exported.')
    print('Cut scene: 17 continuous sections and 5 splice dots. OLT to ONT graph verified through branch/splitter.')
    print('Production forms and Fibers ribbon captured. Cancelled equipment preview preserves all records.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
