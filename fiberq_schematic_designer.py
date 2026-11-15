# -*- coding: utf-8 -*-
"""Launch the independent FiberQ Schematic Designer inside QGIS Desktop.

Preferred Console command (use an absolute path):
    path = '/path/to/fiberq_schematic_designer.py'
    exec(compile(open(path, encoding='utf-8').read(), path, 'exec'))

The legacy exec(open(...).read()) command also works. If the source directory
cannot be determined, choose this launcher once in the file picker.
"""

import inspect
import importlib
import sys
from pathlib import Path


def _designer_directory(iface):
    candidates = [globals().get('_FQSD_SOURCE_DIR'), Path.cwd()]
    for filename in (globals().get('__file__'), inspect.currentframe().f_code.co_filename):
        if filename and not str(filename).startswith('<'):
            candidates.insert(0, Path(filename).resolve().parent)
    for candidate in candidates:
        if candidate and (Path(candidate) / 'fiberq_designer' / 'app.py').is_file():
            return Path(candidate).resolve()
    from qgis.PyQt.QtWidgets import QFileDialog
    filename, _ = QFileDialog.getOpenFileName(
        iface.mainWindow(), 'Locate fiberq_schematic_designer.py', '',
        'FiberQ launcher (fiberq_schematic_designer.py)')
    if filename and (Path(filename).parent / 'fiberq_designer' / 'app.py').is_file():
        return Path(filename).resolve().parent
    raise RuntimeError('Keep the launcher and fiberq_designer folder together, then run again.')


def launch(iface):
    directory = _designer_directory(iface)
    globals()['_FQSD_SOURCE_DIR'] = str(directory)
    loaded = sys.modules.get('fiberq_designer')
    if loaded and Path(loaded.__file__).resolve().parent != directory / 'fiberq_designer':
        raise RuntimeError('A designer from another folder is loaded. Restart QGIS before switching folders.')
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    previous_path = None
    if loaded:
        from qgis.PyQt.QtWidgets import QDockWidget
        existing = iface.mainWindow().findChild(QDockWidget, 'FiberQSchematicDesignerPhase1')
        if existing is not None:
            previous_path = getattr(existing, 'path', None)
            existing.stop_tool()
            iface.removeDockWidget(existing)
            if hasattr(existing, 'dispose'):
                existing.dispose()
            existing.close()
            existing.setObjectName('')
            existing.deleteLater()
        for module in ('compat', 'domain', 'housing_multi', 'housing_catalog', 'cable_catalog', 'outlet_layout', 'workspace_model', 'i18n',
                       'fiber_model', 'active_layout', 'reports', 'annotation_layout',
                       'element_layout', 'closure_layout', 'schema', 'geometry', 'fiber_geometry', 'repository',
                       'active_service', 'fiber_service', 'workspace_service', 'housing_cables', 'outlet_service', 'services', 'housing_dialog', 'housing_cable_dialog', 'outlet_dialog', 'closure_dialog',
                       'cable_dialog', 'operation_dialogs', 'report_dialogs', 'csv_io', 'rendering', 'operation_view',
                       'annotation_view', 'cad_view', 'exports', 'map_tools', 'cable_tools', 'operation_tools',
                       'workspace_tools', 'housing_cable_tools', 'outlet_tools', 'ribbon', 'app'):
            name = 'fiberq_designer.' + module
            if name in sys.modules:
                importlib.reload(sys.modules[name])
    from fiberq_designer.app import launch as launch_dock
    dock = launch_dock(iface)
    if previous_path and Path(previous_path).is_file():
        dock.guard(lambda: dock.attach(previous_path))
    return dock


if 'iface' not in globals():
    raise RuntimeError('Run this launcher from the QGIS Desktop Python Console.')
FIBERQ_SCHEMATIC_DESIGNER = launch(iface)
