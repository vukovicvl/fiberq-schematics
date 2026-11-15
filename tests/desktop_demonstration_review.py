"""Review Workspace Network/Splice demo buttons in an isolated QGIS Desktop."""

from collections import Counter
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1] if '__file__' in globals() else Path.cwd()
sys.path.insert(0, str(ROOT))

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.PyQt.QtCore import QTimer, QT_VERSION_STR, PYQT_VERSION_STR
from qgis.PyQt.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QLineEdit
from qgis.utils import iface
from fiberq_designer.app import launch
from fiberq_designer.compat import enum_compat


def main():
    output = Path(os.environ['FQSD_REVIEW_OUTPUT'])
    errors, records = [], []
    dock = None
    status = 'failed'
    try:
        assert not QgsProject.instance().mapLayers()
        dock = launch(iface)
        dock.err = lambda error: errors.append(''.join(traceback.format_exception(error)))
        iface.mainWindow().resize(1600, 1000)
        dock.ribbon_tabs.setCurrentIndex(3)

        def choose(action, filename=None):
            failures, turns = [], []
            chooser, watchdog = QTimer(), QTimer()
            def pick():
                dialog = QApplication.activeModalWidget()
                if not isinstance(dialog, QFileDialog) or not dialog.isVisible():
                    chooser.start(25)
                    return
                turns.append(True)
                assert dialog.testOption(enum_compat(QFileDialog, 'DontUseNativeDialog', 'Option'))
                finish = QTimer(dialog)
                finish.setSingleShot(True)
                def click():
                    turns.append(True)
                    if filename is None:
                        dialog.reject()
                    else:
                        dialog.setDirectory(str(output))
                        dialog.findChild(QLineEdit, 'fileNameEdit').setText(filename)
                        box = dialog.findChild(QDialogButtonBox)
                        next(button for button in box.buttons() if box.buttonRole(button) ==
                             enum_compat(QDialogButtonBox, 'AcceptRole', 'ButtonRole')).click()
                finish.timeout.connect(click)
                finish.start(20)
            def expired():
                failures.append('Demo file-picker watchdog expired.')
                if QApplication.activeModalWidget():
                    QApplication.activeModalWidget().reject()
            chooser.setSingleShot(True)
            chooser.timeout.connect(pick)
            chooser.start(150)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(expired)
            watchdog.start(7000)
            try:
                dock.command_actions[action].trigger()
            finally:
                chooser.stop()
                watchdog.stop()
            assert turns == [True, True] and not failures, str(failures)
            assert not errors, '\n'.join(errors)

        for name, action in (('network-demo', 'basic_demo'), ('splice-demo', 'legacy_demo')):
            if dock.repository is not None:
                before, path = dock.repository.snapshot(), dock.path
                choose(action)
                assert dock.path == path and before == dock.repository.snapshot()
            choose(action, name + '.gpkg')
            counts = dock.service.validate()
            dock.dark_canvas.setChecked(True)
            dock.clear_selection()
            dock.fit()
            QgsApplication.processEvents()
            iface.mapCanvas().refresh()
            iface.mapCanvas().waitWhileRendering()
            QgsApplication.processEvents()
            required = Counter(feature['text'] for feature in dock.scene.housing_labels.getFeatures())
            visible = Counter(label.labelText for label in iface.mapCanvas().labelingResults().allLabels())
            assert all(visible[text] >= count for text, count in required.items())
            assert iface.mainWindow().grab().save(str(output / (name + '-desktop.png')))
            before = dock.repository.snapshot()
            dock.attach(dock.path)
            assert before == dock.repository.snapshot()
            records.append(dict(name=name,counts=counts,labels=sum(required.values()),reopened=True))
            print('PASS:', name, counts, flush=True)
        status = 'passed'
    except BaseException:
        errors.append(traceback.format_exc())
        traceback.print_exc()
    finally:
        result = dict(status=status,qgis=Qgis.QGIS_VERSION,qt=QT_VERSION_STR,pyqt=PYQT_VERSION_STR,
                      interface=type(iface).__name__,platform=QApplication.platformName(),examples=records,errors=errors)
        (output / 'desktop-results.json').write_text(json.dumps(result,indent=2)+'\n')
        if dock:
            dock.dispose()
            dock.close()
        QgsProject.instance().clear()
        QgsProject.instance().setDirty(False)
        QgsApplication.exit(0 if status == 'passed' else 1)


QTimer.singleShot(700, main)
