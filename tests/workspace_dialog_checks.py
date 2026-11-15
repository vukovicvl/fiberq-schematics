"""Real New/Open modal dialogs, liveness and localization boundaries."""

from pathlib import Path
from unittest.mock import patch

from qgis.PyQt.QtCore import QEvent, QTimer
from qgis.PyQt.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog,
                                QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout)

from fiberq_designer.compat import enum_compat
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import OdfProperties
from fiberq_designer.i18n import localize_tree
from fiberq_designer.repository import GeoPackageRepository


class WorkspaceDialogChecks:
    """Use the integration suite's single QGIS application lifecycle."""
    def picker_dock(self):
        from fiberq_designer.app import launch
        from test_qgis_integration import TestIface
        self.iface = TestIface()
        self.dock = launch(self.iface)
        self.iface.window.show()
        self.app.processEvents()

    def run_picker(self, action, path=None, cancel=False):
        """Keep the real modal loop; automate only the user's picker buttons."""
        observations, errors, turns = [], [], []

        def choose():
            dialogs = [widget for widget in QApplication.topLevelWidgets()
                       if isinstance(widget, QFileDialog) and widget.isVisible()]
            if not dialogs:
                chooser.start(25)
                return
            dialog = dialogs[0]
            observations.append(dialog.testOption(enum_compat(QFileDialog, 'DontUseNativeDialog', 'Option')))
            turns.append(True)

            def finish():
                turns.append(True)
                if cancel:
                    dialog.reject()
                    return
                try:
                    dialog.setDirectory(str(path.parent))
                    # selectFile() does not replace a visible Qt 6 filename.
                    dialog.findChild(QLineEdit, 'fileNameEdit').setText(path.name)
                    self.assertEqual([str(path)], dialog.selectedFiles())
                    box = dialog.findChild(QDialogButtonBox)
                    button = next(button for button in box.buttons()
                                  if box.buttonRole(button) == enum_compat(QDialogButtonBox, 'AcceptRole', 'ButtonRole'))
                    if not button.isEnabled():
                        raise AssertionError('The picker accept button is disabled.')
                    button.click()
                except Exception as error:
                    errors.append(str(error))
                    dialog.reject()

            # Measure timer progress only after the picker is visible. Periodic
            # timer counts coalesce during construction under CPU load.
            QTimer.singleShot(20, finish)

        def deadline():
            errors.append('The file picker did not finish before the watchdog.')
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QFileDialog):
                    widget.reject()

        chooser, watchdog = QTimer(), QTimer()
        for timer, delay, callback in ((chooser, 150, choose), (watchdog, 4000, deadline)):
            timer.setSingleShot(True)
            timer.timeout.connect(callback)
            timer.start(delay)
        try:
            action()
        finally:
            chooser.stop()
            watchdog.stop()
        self.assertEqual([], errors)
        self.assertEqual([True], observations)
        self.assertEqual(2, len(turns), 'The visible picker must process the next queued timer.')

    def test_workspace_new_save_open_and_cancel_use_real_responsive_pickers(self):
        self.picker_dock()
        path = Path(self.directory.name) / 'new-workspace.gpkg'
        self.run_picker(self.dock.command_actions['new'].trigger, path)
        self.assertTrue(path.is_file())
        self.assertEqual(str(path), self.dock.path)
        self.assertTrue(all(count == 0 for count in self.dock.service.validate().values()))
        identity = self.dock.service.add_odf('Free', OdfProperties(), pt(0, 0))
        before = self.dock.repository.snapshot()
        self.run_picker(self.dock.command_actions['new'].trigger, cancel=True)
        self.assertEqual(before, self.dock.repository.snapshot())
        self.run_picker(self.dock.command_actions['open'].trigger, cancel=True)
        self.assertEqual(before, self.dock.repository.snapshot())
        self.run_picker(self.dock.command_actions['open'].trigger, path)
        self.assertEqual(before, self.dock.repository.snapshot())
        self.assertEqual('OR Free', self.dock.repository.find('elements', identity)['name'])
        self.assertEqual(before, GeoPackageRepository.open(path).snapshot())

    def test_workspace_file_picker_controls_are_excluded_from_translation_and_paint_hooks(self):
        self.picker_dock()
        dialog = QFileDialog(self.dock, 'Settings')
        dialog.setOption(enum_compat(QFileDialog, 'DontUseNativeDialog', 'Option'))
        sentinel = QLabel('Settings', dialog)
        localize_tree(self.dock, 'sr')
        self.assertEqual('Settings', dialog.windowTitle())
        self.assertEqual('Settings', sentinel.text())
        self.assertIsNone(sentinel.property('fqsd_i18n_text'))
        with patch('fiberq_designer.workspace_tools.localize_tree') as localize:
            self.dock.language_filter.eventFilter(dialog, QEvent(enum_compat(QEvent, 'Show', 'Type')))
            self.dock.language_filter.eventFilter(sentinel, QEvent(enum_compat(QEvent, 'Paint', 'Type')))
            localize.assert_not_called()
        dialog.deleteLater()

    def test_workspace_repeated_table_paints_do_not_rewrite_unchanged_headers(self):
        self.picker_dock()
        dialog = QDialog(self.dock)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, 1, dialog)
        table.setHorizontalHeaderItem(0, QTableWidgetItem('Settings'))
        layout.addWidget(table)
        self.dock.workspace_values['language'] = 'sr'
        dialog.show()
        self.app.processEvents()
        self.assertEqual('Podesavanja', table.horizontalHeaderItem(0).text())
        changes = []
        table.model().headerDataChanged.connect(lambda *unused: changes.append(True))
        for unused in range(5):
            self.dock.language_filter.eventFilter(table, QEvent(enum_compat(QEvent, 'Paint', 'Type')))
        self.assertEqual([], changes)
        dialog.close()

    def test_workspace_new_picker_remains_responsive_after_console_reload(self):
        self.picker_dock()
        launcher = Path(__file__).resolve().parents[1] / 'fiberq_schematic_designer.py'
        namespace = {'iface': self.iface, '__file__': str(launcher)}
        source = compile(launcher.read_text(encoding='utf-8'), str(launcher), 'exec')
        for unused in range(2):
            exec(source, namespace)
            self.dock = namespace['FIBERQ_SCHEMATIC_DESIGNER']
            self.app.processEvents()
        self.run_picker(self.dock.command_actions['new'].trigger, cancel=True)
        self.assertIsNone(self.dock.repository)
