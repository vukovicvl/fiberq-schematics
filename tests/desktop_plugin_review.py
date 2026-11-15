"""Load the actual release ZIP using QGIS's real Python plugin manager."""

import importlib
import json
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1] if '__file__' in globals() else Path.cwd()
sys.path.insert(0, str(ROOT / 'tools'))

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QTimer, QUrl
from qgis.PyQt.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication, QFileDialog, QDockWidget, QLineEdit, QToolBar,
)
from qgis.utils import iface
import qgis.utils

from validate_package import validate


def main():
    output = Path(os.environ['FQSD_REVIEW_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    archive_path = ROOT / 'release/fiberq_schematics-1.0.0.zip'
    checks, errors, status = [], [], 'failed'
    name, plugin = 'fiberq_schematics', None
    try:
        assert not QgsProject.instance().mapLayers()
        validate(archive_path)
        installed = output / 'plugins'
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                destination = installed.joinpath(*member.filename.split('/'))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
        sys.path.insert(0, str(installed))
        qgis.utils.plugin_paths.insert(0, str(installed))
        qgis.utils.updateAvailablePlugins()

        def pump():
            QgsApplication.processEvents()
            iface.mapCanvas().refresh()
            iface.mapCanvas().waitWhileRendering()
            QCoreApplication.sendPostedEvents(
                None, QEvent.Type.DeferredDelete
            )
            QgsApplication.processEvents()

        def load():
            assert qgis.utils.loadPlugin(name)
            assert qgis.utils.startPlugin(name)
            instance = qgis.utils.plugins[name]
            assert instance.dock is None
            assert len(instance.actions) == 2
            assert all(not action.icon().isNull() for action in instance.actions)
            assert len(iface.mainWindow().findChildren(
                QToolBar, 'FiberQSchematicsToolbar'
            )) == 1
            return instance

        plugin = load()
        assert qgis.utils.pluginMetadata(name, 'version') == '1.0.0'
        checks.append('Release ZIP imports/enables through the real QGIS plugin manager; one toolbar, two actions, no eager workspace')
        plugin.actions[1].trigger()
        pump()
        dialog = plugin.help_dialog
        assert dialog.isVisible()
        assert 'Donate / sponsor development' in dialog.browser.toPlainText()
        assert dialog.grab().save(str(output / 'help-about.png'))
        dialog.browser.verticalScrollBar().setValue(
            dialog.browser.verticalScrollBar().maximum()
        )
        pump()
        assert dialog.grab().save(str(output / 'help-contact-donate.png'))
        with patch.object(QDesktopServices, 'openUrl') as opened:
            dialog.browser.anchorClicked.emit(QUrl(
                'https://github.com/sponsors/vukovicvl'
            ))
            opened.assert_called_once()
            assert opened.call_args.args[0].toString().endswith('/vukovicvl')
            dialog.open_link(QUrl('file:///etc/passwd'))
            dialog.open_link(QUrl('https://example.invalid/'))
            opened.assert_called_once()
        dialog.close()
        checks.append('Help/About opens before a workspace; contact/logo/local help render; donation click opens only the intended URL')

        plugin.actions[0].trigger()
        dock = plugin.dock
        dock.err = lambda error: errors.append(str(error))
        iface.mainWindow().resize(1500, 1000)
        workspace = output / 'plugin-example.gpkg'
        failures, seen = [], []
        timer, watchdog = QTimer(), QTimer()

        def choose_new():
            picker = QApplication.activeModalWidget()
            if not isinstance(picker, QFileDialog) or not picker.isVisible():
                timer.start(25)
                return
            seen.append(True)
            picker.setDirectory(str(output))
            picker.selectFile(str(workspace))
            picker.findChild(QLineEdit, 'fileNameEdit').setText(str(workspace))
            QTimer.singleShot(40, picker.accept)

        def expired():
            failures.append('New-workspace picker watchdog expired.')
            if QApplication.activeModalWidget():
                QApplication.activeModalWidget().reject()

        timer.setSingleShot(True)
        timer.timeout.connect(choose_new)
        timer.start(150)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(expired)
        watchdog.start(10000)
        try:
            dock.command_actions['new'].trigger()
        finally:
            timer.stop()
            watchdog.stop()
        assert seen == [True] and not failures and not errors
        assert workspace.exists() and dock.repository is not None
        dock.service.cad_demo()
        dock.refresh()
        dock.dark_canvas.setChecked(True)
        dock.fit()
        pump()
        plugin.actions[0].trigger()
        assert plugin.dock is dock and dock.path == str(workspace)
        assert iface.mainWindow().grab().save(str(output / 'installed-plugin.png'))
        dock.service.validate()
        saved = dock.repository.snapshot()
        layers = set(QgsProject.instance().mapLayers())
        dock.save_project()
        assert workspace.with_suffix('.qgz').exists()
        checks.append('Toolbar opens designer; real New picker creates schema-13 workspace; native demo and assets render; reopening toolbar retains workspace')

        dock.command_actions['cut_fiber'].trigger()
        assert iface.mapCanvas().mapTool() is dock.click_tool
        dock.close()
        pump()
        plugin.actions[0].trigger()
        assert plugin.dock is dock and dock.isVisible()
        dock.command_actions['cut_fiber'].trigger()
        assert qgis.utils.unloadPlugin(name)
        pump()
        assert not iface.mainWindow().findChildren(
            QToolBar, 'FiberQSchematicsToolbar'
        )
        assert not iface.mainWindow().findChildren(
            QDockWidget, 'FiberQSchematicDesignerPhase1'
        )
        assert not iface.mainWindow().findChildren(
            QDockWidget, 'FiberQSchematicDesignerInspector'
        )
        assert set(QgsProject.instance().mapLayers()) == layers
        checks.append('Close/reopen retains workspace; unload while a map tool is active removes toolbar, ribbon, inspector and tools while retaining project layers')

        for cycle in range(2):
            plugin = load()
            plugin.actions[0].trigger()
            plugin.dock.attach(str(workspace))
            assert plugin.dock.repository.snapshot() == saved
            plugin.dock.service.validate()
            assert qgis.utils.unloadPlugin(name)
            pump()
        checks.append('Two more real disable/enable cycles reopen identical saved records without duplicate UI or connections')
        assert not errors, errors
        status = 'passed'
        print('PLUGIN DESKTOP REVIEW PASSED:', checks, flush=True)
    except BaseException:
        errors.append(traceback.format_exc())
        traceback.print_exc()
    finally:
        if name in qgis.utils.plugins:
            qgis.utils.unloadPlugin(name)
        payload = dict(
            status=status, qgis=Qgis.QGIS_VERSION, qt=QT_VERSION_STR,
            pyqt=PYQT_VERSION_STR, interface=type(iface).__name__,
            platform=QgsApplication.platformName(), checks=checks, errors=errors,
        )
        (output / 'plugin-results.json').write_text(
            json.dumps(payload, indent=2) + '\n'
        )
        QgsProject.instance().clear()
        QgsProject.instance().setDirty(False)
        QgsApplication.exit(0 if status == 'passed' else 1)


QTimer.singleShot(700, main)
