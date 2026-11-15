"""Milestone 9C language pairs, frozen captions and protected user text."""

import csv
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import Qgis, QgsApplication, QgsRectangle
from qgis.PyQt.QtCore import QSize, QT_VERSION_STR, PYQT_VERSION_STR
from fiberq_designer.app import launch
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import HousingProperties
from fiberq_designer.housing_dialog import HousingDialog
from fiberq_designer.housing_cable_dialog import AddHousingCableDialog
from fiberq_designer.outlet_dialog import OutletDialog
from fiberq_designer.closure_dialog import ClosureDialog
from fiberq_designer.cable_dialog import CableDialog
from fiberq_designer.operation_dialogs import ActiveDialog, FiberDataDialog, GroupJoinDialog, BranchDialog
from fiberq_designer.active_layout import ActiveProperties
from fiberq_designer.report_dialogs import BudgetDialog, ReportDialog, SettingsDialog, export_report_csv
from fiberq_designer.annotation_layout import legend_payload, table_payload
from fiberq_designer.i18n import SR
from fiberq_designer.repository import GeoPackageRepository
from fiberq_designer.services import NetworkService
from render_fiber_operations_preview import review
from test_qgis_integration import TestIface
from translation_inventory import inventory


def language(service, code):
    settings = service.settings()
    settings['language'] = code
    service.save_settings(settings)


def show_dialog(app, output, name, dialog):
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / (name + '.png')))
    dialog.reject()
    app.processEvents()


def main():
    profile = tempfile.TemporaryDirectory(prefix='fqsd-language-profile-')
    app = QgsApplication([], True, profile.name)
    app.initQgis()
    output = Path(tempfile.mkdtemp(prefix='fqsd-language-review-'))
    repo = GeoPackageRepository.create(output / 'serbian-equipment.gpkg')
    service = NetworkService(repo)
    language(service, 'sr')
    cable = service.add_catalog_cable('Working', CableProperties(model='Free'), [pt(0, 0), pt(1200, 0)])
    odf = service.attach_housing(cable, 'A', '', HousingProperties(capacity=48, requested_used=4))
    for kind, x, y in (('FDH', -400, 0), ('MDU', -400, -270), ('FAT', -400, -510), ('OTB', -700, -510), ('PATCH_PANEL', -700, 0)):
        service.add_housing('', HousingProperties(kind=kind, capacity=12,
            mounting='rack 19"' if kind == 'PATCH_PANEL' else 'wall' if kind in ('FAT', 'OTB') else 'indoor',
            model='Free', address='' if kind == 'PATCH_PANEL' else 'Working', rack_module='Free' if kind == 'PATCH_PANEL' else ''), pt(x, y))
    service.add_closure('Free', ClosureProperties(location_number='Working'), cable, pt(500, 0))
    service.add_outlet(odf, CableProperties(category='', catalog_model='', model='Working', tubes=2, fibers_per_tube=6), name='Free')
    review(repo, output, 'serbian-equipment', QSize(2400, 1400), QgsRectangle(-900, -850, 1280, 150))

    mixed = GeoPackageRepository.create(output / 'mixed-languages.gpkg')
    service = NetworkService(mixed)
    english = service.add_catalog_cable('English / Free', CableProperties(model='Working'), [pt(0, 0), pt(1200, 0)])
    first = service.attach_housing(english, 'A', '', HousingProperties(kind='FDH', capacity=12, requested_used=2, mounting='indoor'))
    language(service, 'sr')
    serbian = service.add_catalog_cable('Serbian / Free', CableProperties(model='Working'), [pt(0, -350), pt(1200, -350)])
    second = service.attach_housing(serbian, 'A', '', HousingProperties(kind='FDH', capacity=12, requested_used=2, mounting='outdoor'))
    language(service, 'en')
    scene = service.fiber_scene()
    texts = lambda key: [f['text'] for f in scene.housing_labels.getFeatures() if f['obj_id'] == key]
    if 'indoor' not in texts(first) or 'spolja' not in texts(second) or '1.Blue' not in texts(english) or '1.Plava' not in texts(serbian):
        raise RuntimeError('Workspace language switch relabeled an existing drawing.')
    review(mixed, output, 'mixed-languages', QSize(2200, 1100), QgsRectangle(-170, -570, 1280, 100))

    reports = GeoPackageRepository.create(output / 'serbian-reports.gpkg')
    service = NetworkService(reports)
    language(service, 'sr')
    cable = service.add_catalog_cable('Working', CableProperties(model='Free', fibers_per_tube=2), [pt(0, 0), pt(800, 0)])
    service.add_closure('Warning', ClosureProperties(location_number='Free'), cable, pt(300, 0))
    service.set_fiber_data([reports.features('fibers')[0]['fiber_id']], purpose='nmWork', description='Free {customer}')
    bom, checks = service.bill_of_materials(), service.network_checks()
    service.add_annotation('bom', table_payload(bom, 'sr'), pt(0, -180))
    service.add_annotation('legend', legend_payload(service.settings()), pt(1700, -80))
    export_report_csv(bom, output / 'bom-sr.csv', 'sr')
    export_report_csv(checks, output / 'checks-sr.csv', 'sr')
    review(reports, output, 'serbian-reports', QSize(2500, 1200), QgsRectangle(-80, -850, 2250, 80))

    pending = GeoPackageRepository.create(output / 'dialog-preview.gpkg')
    service = NetworkService(pending)
    language(service, 'sr')
    cable = service.add_catalog_cable('Working', CableProperties(model='Free'), [pt(0, 0), pt(1200, 0)])
    housing = service.attach_housing(cable, 'A', '', HousingProperties(capacity=48, requested_used=4))
    free = service.add_catalog_cable('Warning', CableProperties(), [pt(0, -180), pt(1200, -180)])
    iface = TestIface()
    iface.window.resize(1600, 1050)
    dock = launch(iface)
    dock.attach(pending.path, pending)
    dock.dark_canvas.setChecked(True)
    iface.window.show()
    dock.fit()
    app.processEvents()
    for code in ('en', 'sr'):
        language(dock.service, code)
        dock.apply_workspace_settings()
        before = pending.snapshot()
        budget = BudgetDialog(dock, dock.service)
        if not budget.calculate():
            raise RuntimeError('Language review optical budget failed.')
        dialogs = (
            ('housing', HousingDialog(dock, HousingProperties(kind='FDH', capacity=24, mounting='indoor', model='Free', address='Working'))),
            ('closure', ClosureDialog(dock, ClosureProperties(location_number='Working'), 'Free')),
            ('cable', CableDialog(dock, CableProperties(model='Free'), settings=dock.service.settings())),
            ('splitter', ActiveDialog(dock, ActiveProperties())),
            ('fiber-data', FiberDataDialog(dock)),
            ('group-join', GroupJoinDialog(dock)),
            ('branch', BranchDialog(dock)),
            ('add-cable', AddHousingCableDialog(dock, housing, free, 'A')),
            ('outlet', OutletDialog(dock, housing)),
            ('bom', ReportDialog(dock, bom, code)),
            ('budget', budget),
            ('settings', SettingsDialog(dock, dock.service.settings())),
        )
        for name, dialog in dialogs:
            show_dialog(app, output, name + '-' + code, dialog)
        error = HousingDialog(dock)
        error.used.setText('999999')
        error.show()
        error.accept()
        app.processEvents()
        error.grab().save(str(output / ('validation-error-' + code + '.png')))
        error.reject()
        dock.stop_tool()
        if pending.snapshot() != before:
            raise RuntimeError('Language/dialog review changed the optical workspace.')
        dock.select('fibers', pending.features('fibers')[0]['fiber_id'])
        dock.inspector.show()
        dock.fit()
        iface.canvas.refresh()
        iface.canvas.waitWhileRendering()
        app.processEvents()
        iface.window.grab().save(str(output / ('ribbon-inspector-' + code + '.png')))
    dock.dispose()
    dock.close()
    iface.window.close()
    rows = inventory()
    with (output / 'translation-inventory.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['File', 'Line', 'Source', 'Classification', 'Serbian'])
        writer.writerows(rows)
    if any(row[3] == 'missing' for row in rows):
        raise RuntimeError('A controlled message is missing its Serbian translation.')
    print('QGIS {}; Qt {}; PyQt {}'.format(Qgis.QGIS_VERSION, QT_VERSION_STR, PYQT_VERSION_STR))
    print('{} controlled-message occurrences audited; no missing translations; {} merged phrase keys.'.format(len(rows), len(SR)))
    print('Three language scenes validate/reopen and retain all required labels in QGIS/DXF; PDF/SVG/DXF exported.')
    print('Twelve production dialog pairs, validation errors and inspectors captured. Preview/cancellation preserves complete snapshots.')
    print('English/Serbian mixed captions keep their original language after switching; Free/Working/Warning user text is retained.')
    print('Review output: ' + str(output), flush=True)
    return app, profile


if __name__ == '__main__':
    application, application_profile = main()
