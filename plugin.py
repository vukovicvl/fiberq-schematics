"""QGIS menu, toolbar, help and designer lifecycle."""

from html import escape

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
)
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .plugin_info import (
    AUTHOR,
    DONATE,
    EMAIL,
    HOMEPAGE,
    NAME,
    REPOSITORY,
    ROOT,
    TRACKER,
    VERSION,
)
from .fiberq_designer.compat import enum_compat


class HelpDialog(QDialog):
    """Local help with explicit, user-activated external links."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('FiberQSchematicsHelp')
        self.setWindowTitle(NAME + ' — Help / About')
        self.resize(680, 650)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ROOT / 'icon.png')).scaledToWidth(80))
        heading.addWidget(logo)
        details = QLabel(
            '<h2>{}</h2><p>Version {}<br>{}<br>{}</p>'.format(
                escape(NAME), escape(VERSION), escape(AUTHOR), escape(EMAIL)
            )
        )
        details.setWordWrap(True)
        heading.addWidget(details, 1)
        layout.addLayout(heading)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.open_link)
        self.browser.setSource(
            QUrl.fromLocalFile(str(ROOT / 'help/index.html'))
        )
        layout.addWidget(self.browser, 1)
        buttons = QDialogButtonBox(
            enum_compat(QDialogButtonBox, 'Close', 'StandardButton')
        )
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def open_link(self, url):
        """Open documented links only when the user clicks them."""
        permitted = {
            HOMEPAGE,
            REPOSITORY,
            TRACKER,
            DONATE,
            'mailto:' + EMAIL,
        }
        if url.toString() in permitted:
            QDesktopServices.openUrl(url)


class FiberQSchematicsPlugin:
    """Install once, open on demand and remove all owned UI on unload."""

    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.toolbar = None
        self.dock = None
        self.help_dialog = None

    def initGui(self):
        """Register persistent toolbar and Vector menu commands."""
        if self.toolbar is not None:
            return
        self.toolbar = self.iface.addToolBar(NAME)
        self.toolbar.setObjectName('FiberQSchematicsToolbar')
        for title, icon, callback, object_name in (
            (
                NAME,
                QIcon(str(ROOT / 'icon.png')),
                self.run,
                'FiberQSchematicsOpen',
            ),
            (
                'FiberQ Help / About',
                QgsApplication.getThemeIcon('/mActionHelpContents.svg'),
                self.show_help,
                'FiberQSchematicsHelpAction',
            ),
        ):
            action = QAction(icon, title, self.iface.mainWindow())
            action.setObjectName(object_name)
            action.setToolTip(title)
            action.triggered.connect(callback)
            self.toolbar.addAction(action)
            self.iface.addPluginToVectorMenu(NAME, action)
            self.actions.append(action)

    def run(self):
        """Show the existing designer without replacing its workspace."""
        if self.dock is None:
            from .fiberq_designer.app import launch

            self.dock = launch(self.iface)
        self.dock.show()
        self.dock.raise_()
        if not self.dock.inspector.isHidden():
            self.dock.inspector.raise_()

    def show_help(self):
        """Show help without requiring a schematic workspace."""
        if self.help_dialog is None:
            self.help_dialog = HelpDialog(self.iface.mainWindow())
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()

    def unload(self):
        """Release UI/tools while keeping saved work and project layers."""
        if self.help_dialog is not None:
            self.help_dialog.close()
            self.help_dialog.deleteLater()
            self.help_dialog = None
        if self.dock is not None:
            self.dock.dispose()
            self.iface.removeDockWidget(self.dock)
            self.dock.close()
            self.dock.setObjectName('')
            self.dock.deleteLater()
            self.dock = None
        for action in self.actions:
            self.iface.removePluginVectorMenu(NAME, action)
            action.triggered.disconnect()
            action.deleteLater()
        self.actions.clear()
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
