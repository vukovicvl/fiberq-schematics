"""Render the production ribbon in an isolated QGIS map-canvas host.

QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 tests/render_ribbon_preview.py
"""

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
from qgis.PyQt.QtGui import QColor, QPalette
from qgis.PyQt.QtWidgets import QApplication, QStyleFactory
from fiberq_designer.app import launch
from fiberq_designer.compat import enum_compat
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import HousingProperties, OdfProperties
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from test_qgis_integration import TestIface


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-ribbon-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    app.setStyle(QStyleFactory.create('Fusion'))
    output = Path(tempfile.mkdtemp(prefix='fqsd-ribbon-review-'))
    repo = GeoPackageRepository.create(output / 'workspace.gpkg')
    service = NetworkService(repo)
    cable = service.add_loose_cable('FEEDER-24', 24, [pt(0, 0), pt(1000, 0)])
    service.attach_odf(cable, 'A', '', OdfProperties())
    service.attach_housing(cable, 'B', '', HousingProperties(kind='FAT', capacity=24, mounting='pole', splitter_ratio=16))
    service.add_closure('', ClosureProperties(location_number='12'), cable, pt(350, 0), 12)
    service.validate()
    before = repo.snapshot()
    iface = TestIface()
    iface.window.setWindowTitle('FiberQ — QGIS toolbar review')
    iface.window.resize(1440, 900)
    dock = launch(iface)
    iface.window.show()

    def capture(name, widget=None):
        app.processEvents()
        iface.canvas.refresh()
        iface.canvas.waitWhileRendering()
        app.processEvents()
        (widget or iface.window).grab().save(str(output / (name + '.png')))

    capture('empty-workspace')
    dock.attach(repo.path, repo)
    dock.dark_canvas.setChecked(True)
    dock.fit()
    capture('light-workspace')
    capture('draw-ribbon', dock)
    dock.ribbon_tabs.setCurrentIndex(1)
    capture('edit-ribbon', dock)
    dock.ribbon_tabs.setCurrentIndex(3)
    capture('workspace-ribbon', dock)
    dock.ribbon_tabs.setCurrentIndex(0)
    dock.command_actions['inspector'].trigger()
    dock.select('fibers', repo.features('fibers')[0]['fiber_id'])
    capture('inspector-open')
    dock.inspector.hide()
    dock.toggle_ribbon(True)
    capture('collapsed')
    dock.toggle_ribbon(False)
    iface.window.resize(940, 780)
    capture('narrow-workspace')

    palette = QPalette()
    for role, color in (('Window', '#292929'), ('WindowText', '#eeeeee'), ('Base', '#202020'),
            ('AlternateBase', '#323232'), ('Text', '#eeeeee'), ('Button', '#343434'),
            ('ButtonText', '#eeeeee'), ('Highlight', '#286d9d'), ('HighlightedText', '#ffffff')):
        palette.setColor(enum_compat(QPalette, role, 'ColorRole'), QColor(color))
    app.setPalette(palette)
    dock = launch(iface)
    repo = GeoPackageRepository.open(repo.path)
    dock.attach(repo.path, repo)
    dock.dark_canvas.setChecked(True)
    iface.window.resize(1440, 900)
    dock.fit()
    capture('dark-workspace')
    capture('dark-ribbon', dock)
    dock.setFloating(True)
    dock.resize(560, 236)
    capture('floating-ribbon', dock)
    dock.setFloating(False)

    from qgis.PyQt.QtCore import QTimer
    def grab_dialog():
        dialog = QApplication.activeModalWidget()
        dialog.grab().save(str(output / 'cable-dialog.png'))
        dialog.reject()
    QTimer.singleShot(0, grab_dialog)
    dock.configure_parameter('loose_draw')
    font = app.font()
    font.setPointSize(12)
    app.setFont(font)
    dock = launch(iface)
    repo = GeoPackageRepository.open(repo.path)
    dock.attach(repo.path, repo)
    dock.dark_canvas.setChecked(True)
    dock.fit()
    capture('large-font-workspace')
    dock.ribbon_tabs.setCurrentIndex(1)
    capture('large-font-edit', dock)
    dock.ribbon_tabs.setCurrentIndex(3)
    capture('large-font-settings', dock)
    if before != repo.snapshot():
        raise RuntimeError('Ribbon review changed network records.')
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('Ribbon height: {}; inspector is optional; source records unchanged.'.format(dock.height()))
    print('Review output: ' + str(output), flush=True)
    dock.dispose()
    dock.close()
    iface.window.close()
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
