"""Milestone 8 review: actual reports, budgets, settings, legend and snap UI."""

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import Qgis, QgsApplication, QgsRectangle
from qgis.PyQt.QtCore import QSize, QTimer, QT_VERSION_STR, PYQT_VERSION_STR
from fiberq_designer.active_layout import ActiveProperties
from fiberq_designer.annotation_layout import legend_payload, table_payload
from fiberq_designer.app import launch
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.geometry import pt
from fiberq_designer.i18n import localize_tree
from fiberq_designer.report_dialogs import BudgetDialog, ReportDialog, SettingsDialog, export_report_csv
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from fiberq_designer.workspace_model import budget_calculation
from fiberq_designer.reports import budget_report
from render_fiber_operations_preview import review
from test_qgis_integration import TestIface


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-reports-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-reports-review-'))
    for language in ('en', 'sr'):
        repo = GeoPackageRepository.create(output / ('legend-' + language + '.gpkg'))
        service = NetworkService(repo)
        settings = service.settings()
        settings.update(language=language, color_code='IEC 60304')
        service.save_settings(settings)
        service.add_annotation('legend', legend_payload(settings), pt(0, 0))
        review(repo, output, 'legend-' + language, QSize(1100, 1650), QgsRectangle(-20, -760, 500, 50))
    repo = GeoPackageRepository.create(output / 'reports-and-budget.gpkg')
    service = NetworkService(repo)
    cable = service.add_catalog_cable('FEEDER', CableProperties(), [pt(0, 0), pt(1200, 0)], 2000)
    olt = service.add_active('OLT1', ActiveProperties(kind='OLT', count=8), pt(-100, 0))
    splitter = service.add_active('S1', ActiveProperties(anchor='input'), pt(1400, 0))
    ont = service.add_active('ONT1', ActiveProperties(kind='ONT', count=1, address='Customer A'), pt(1800, 0))
    ports = service.ports_of(splitter)
    first, last = service.ports_of(olt)[0]['port_id'], service.ports_of(ont)[0]['port_id']
    service.join_nodes(first, service.ends_of(cable, 'A')[0]['end_id'], 'patch')
    service.join_nodes(service.ends_of(cable, 'B')[0]['end_id'], ports[0]['port_id'])
    service.join_nodes(ports[1]['port_id'], last, 'drop')
    route = service.route_report(first, last)
    budget = budget_report(budget_calculation(2, route['splices'], route['connectors'], route['splitter_losses']))
    bom, checks = service.bill_of_materials(), service.network_checks()
    for kind, report, anchor in (('budget', budget, pt(-250, -250)), ('bom', bom, pt(850, -250))):
        service.add_annotation(kind, table_payload(report), anchor)
    review(repo, output, 'reports-and-budget', QSize(2500, 1100), QgsRectangle(-320, -650, 2180, 100))
    before = repo.snapshot()
    for key, report in (('bom', bom), ('checks', checks), ('path', route)):
        dialog = ReportDialog(None, report)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(output / (key + '-report-dialog.png')))
        export_report_csv(report, output / (key + '.csv'))
        dialog.close()
    dialog = BudgetDialog(None, service, first, last)
    dialog.load_path()
    if not dialog.calculate():
        raise RuntimeError('Reference path budget could not be calculated.')
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'budget-dialog.png'))
    export_report_csv(dialog.result, output / 'budget.csv')
    dialog.close()
    settings = service.settings()
    settings['catalogues'] = [dict(category='kbAerial', name='CUSTOM-24', capacities=[[2, 12], [4, 6]])]
    settings['color_codes'] = [dict(name='SITE-CODE', colors=[6, 2, 0, 8], suffixes=['-p', '-2p'])]
    for language in ('en', 'sr'):
        settings['language'] = language
        dialog = SettingsDialog(None, settings)
        localize_tree(dialog, language)
        dialog.show()
        app.processEvents()
        for index in range(4):
            dialog.findChild(__import__('qgis.PyQt.QtWidgets', fromlist=['QTabWidget']).QTabWidget).setCurrentIndex(index)
            app.processEvents()
            dialog.grab().save(str(output / ('settings-{}-{}.png'.format(language, index))))
        dialog.close()
    if before != repo.snapshot():
        raise RuntimeError('Read-only report/settings review changed the workspace.')

    iface = TestIface()
    iface.window.resize(1550, 940)
    dock = launch(iface)
    dock.attach(repo.path, repo)
    dock.dark_canvas.setChecked(True)
    dock.ribbon_tabs.setCurrentIndex(4)
    iface.window.show()
    dock.fit()
    app.processEvents()
    iface.canvas.refresh()
    iface.canvas.waitWhileRendering()
    app.processEvents()
    iface.window.grab().save(str(output / 'reports-ribbon.png'))
    # A free output is used for the visible endpoint marker; hover writes nothing.
    dock.start_joint()
    dock.canvas_move(ports[2].geometry().asPoint())
    app.processEvents()
    if not dock.click_tool.snap_marker.isVisible():
        raise RuntimeError('Join endpoint snap marker is missing.')
    iface.window.grab().save(str(output / 'join-snap-marker.png'))
    dock.stop_tool()
    if before != repo.snapshot():
        raise RuntimeError('Join snap preview changed the workspace.')
    # Rebuild a saved table after a network change: frozen numbers, visible warning.
    dock.start_annotation('budget', table_payload(budget))
    dock.workspace_click(pt(-250, -800))
    service.set_fiber_data([repo.features('fibers')[0]['fiber_id']], description='Updated route')
    dock.refresh()
    if not any('STALE REPORT' in f['text'] for f in dock.scene.housing_labels.getFeatures()):
        raise RuntimeError('Changed network did not mark the frozen budget stale.')
    repo.undo()
    repo.undo()
    dock.refresh()
    if before != repo.snapshot():
        raise RuntimeError('Review workspace was not restored to its exported design.')
    dock.dispose()
    dock.close()
    iface.window.close()
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('EN/SR legends and report/budget scene validated/reopened and exported PDF/SVG/DXF.')
    print('Every required CAD label is present in scene/DXF; report CSVs and production forms captured.')
    print('Read-only reports/settings/snap previews preserve snapshots; stale budget warning verified.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
