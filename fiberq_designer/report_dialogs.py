"""Reports, optical-budget inputs and editable workspace settings."""

from copy import deepcopy
import csv

from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .compat import FILE_DIALOG_OPTIONS, PLAIN_TEXT, enum_compat
from .cable_catalog import CATEGORIES, COLOR_NAMES
from .i18n import translate, report_strings, Phrase, user_text, ui_language
from .reports import budget_report, node_names
from .workspace_model import ATTENUATION, budget_calculation, validate_settings


def label(value=''):
    widget = QLabel(value)
    widget.setTextFormat(PLAIN_TEXT)
    widget.setWordWrap(True)
    return widget


def decimal(value, maximum=100):
    widget = QDoubleSpinBox()
    widget.setDecimals(3)
    widget.setRange(0, maximum)
    widget.setValue(value)
    return widget


def export_report_csv(report, path, language='en'):
    def safe(value):
        value = str(value)
        return (
            "'" + value
            if value.startswith(('=', '+', '-', '@', '\t', '\r'))
            else value
        )

    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        localized = report_strings(report, language)
        writer.writerow([localized['title']])
        writer.writerow(localized['columns'])
        writer.writerows([[safe(v) for v in row] for row in localized['rows']])
        for warning in localized['notes']:
            writer.writerow([translate('Note', language), safe(warning)])


class ReportDialog(QDialog):
    def __init__(self, parent, report, language='en'):
        super().__init__(parent)
        self.report, self.language, self.place = report, language, False
        localized = report_strings(report, language)
        self.setWindowTitle(translate(report['title'], language))
        self.resize(1000, 560)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(report['rows']), len(report['columns']))
        self.table.setHorizontalHeaderLabels(
            [translate(c, language) for c in report['columns']]
        )
        self.table.setEditTriggers(
            enum_compat(QAbstractItemView, 'NoEditTriggers', 'EditTrigger')
        )
        for row, values in enumerate(localized['rows']):
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        layout.addWidget(label('\n'.join(localized['notes'])))
        buttons = QHBoxLayout()
        self.csv = QPushButton('Export CSV')
        self.csv.clicked.connect(self.export_csv)
        self.insert = QPushButton('Place in drawing')
        self.insert.clicked.connect(self.place_table)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        for widget in (self.csv, self.insert, close):
            buttons.addWidget(widget)
        layout.addLayout(buttons)
        self.error = label()
        layout.addWidget(self.error)

    def place_table(self):
        self.place = True
        self.accept()

    def export_csv(self):
        path, unused = QFileDialog.getSaveFileName(
            self,
            translate('Export CSV', self.language),
            '',
            'CSV (*.csv)',
            options=FILE_DIALOG_OPTIONS,
        )
        if path:
            try:
                export_report_csv(self.report, path, self.language)
            except OSError as error:
                self.error.setText(str(error))


class BudgetDialog(QDialog):
    def __init__(self, parent, service, start=None, destination=None):
        super().__init__(parent)
        self.service, self.settings = service, service.settings()
        self.result, self.route, self.place = None, None, False
        self.setWindowTitle('Optical budget')
        self.setMinimumWidth(700)
        layout, form = QVBoxLayout(self), QFormLayout()
        layout.addLayout(form)
        self.mode = QComboBox()
        self.mode.addItem('Manual inputs', 'manual')
        self.mode.addItem('Selected optical path', 'path')
        self.start, self.destination = QComboBox(), QComboBox()
        user_text(self.start)
        user_text(self.destination)
        for identity, name in sorted(
            node_names(service.report_data()).items(), key=lambda pair: pair[1]
        ):
            for combo in (self.start, self.destination):
                combo.addItem(
                    name.render(ui_language(self))
                    if isinstance(name, Phrase)
                    else name,
                    identity,
                )
        for combo, identity in (
            (self.start, start),
            (self.destination, destination),
        ):
            if identity is not None:
                combo.setCurrentIndex(combo.findData(identity))
        self.read_path = QPushButton('Read path')
        self.read_path.clicked.connect(self.load_path)
        self.km, self.splices, self.connectors = (
            decimal(2, 100000),
            QSpinBox(),
            QSpinBox(),
        )
        for widget, value in ((self.splices, 8), (self.connectors, 4)):
            widget.setRange(0, 1000000)
            widget.setValue(value)
        self.fiber_type, self.wavelength = QComboBox(), QComboBox()
        self.fiber_type.addItems(['SM', 'MM'])
        self.splitters = QLineEdit('7.3, 10.5')
        for title, widget in (
            ('Source', self.mode),
            ('Start', self.start),
            ('Destination', self.destination),
            ('', self.read_path),
            ('Real path length (km)', self.km),
            ('Splices', self.splices),
            ('Connectors', self.connectors),
            ('Fiber type', self.fiber_type),
            ('Wavelength (nm)', self.wavelength),
            ('Splitter losses (dB, comma separated)', self.splitters),
        ):
            form.addRow(title, widget)
        self.output, self.error = label(), label()
        self.error.setStyleSheet('color: #d55040;')
        layout.addWidget(self.output)
        layout.addWidget(self.error)
        self.fiber_type.currentIndexChanged.connect(self.fill_wavelengths)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.fill_wavelengths()
        self.update_mode()
        buttons = QHBoxLayout()
        for title, callback in (
            ('Calculate', self.calculate),
            ('Export CSV', self.export_csv),
            ('Place in drawing', self.place_result),
            ('Close', self.reject),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        if start and destination:
            self.mode.setCurrentIndex(1)

    def fill_wavelengths(self):
        kind = self.fiber_type.currentText()
        self.wavelength.clear()
        for value in ATTENUATION[kind]:
            self.wavelength.addItem(str(value), value)
        self.wavelength.setCurrentIndex(
            self.wavelength.findData(1490 if kind == 'SM' else 850)
        )

    def update_mode(self):
        selected = self.mode.currentData() == 'path'
        for widget in (self.start, self.destination, self.read_path):
            widget.setEnabled(selected)
        for widget in (self.splices, self.connectors, self.splitters):
            widget.setEnabled(not selected)

    def load_path(self, checked=False, update_length=True):
        try:
            self.route = self.service.route_report(
                self.start.currentData(), self.destination.currentData()
            )
            if any(v is None for v in self.route['splitter_losses']):
                raise ValueError(
                    'A legacy splitter has no saved loss. Use manual inputs '
                    'and enter its verified loss.'
                )
            self.splices.setValue(self.route['splices'])
            self.connectors.setValue(self.route['connectors'])
            self.splitters.setText(
                ', '.join(str(v) for v in self.route['splitter_losses'])
            )
            if self.route['fiber_type']:
                self.fiber_type.setCurrentText(self.route['fiber_type'])
            if update_length and self.route['known_length_m'] is not None:
                self.km.setValue(self.route['known_length_m'] / 1000)
            self.output.setText(
                '\n'.join(self.route['warnings'])
                or 'Real path, counts and saved splitter losses loaded.'
            )
            self.error.clear()
            return True
        except ValueError as error:
            self.error.setText(str(error))
            return False

    def calculate(self):
        try:
            if self.mode.currentData() == 'path' and not self.load_path(
                update_length=False
            ):
                return False
            losses = [
                float(v.strip())
                for v in self.splitters.text().split(',')
                if v.strip()
            ]
            result = budget_calculation(
                self.km.value(),
                self.splices.value(),
                self.connectors.value(),
                losses,
                self.fiber_type.currentText(),
                self.wavelength.currentData(),
                self.service.settings(),
            )
            self.result = budget_report(result)
            if self.mode.currentData() == 'path':
                self.result['warnings'] += self.route['warnings']
                self.result['warnings'].append(
                    Phrase(
                        'Path: {} → {}. Real length is the entered/surveyed '
                        'value.',
                        self.start.currentText(),
                        self.destination.currentText(),
                    )
                )
            language = self.service.settings()['language']
            self.output.setText(
                '\n'.join(
                    '{}: {} = {}'.format(
                        translate(row[0], language),
                        translate(row[1], language),
                        translate(row[2], language),
                    )
                    for row in self.result['rows']
                )
            )
            self.error.clear()
            return True
        except (ValueError, TypeError) as error:
            self.error.setText(str(error))
            return False

    def export_csv(self):
        if self.calculate():
            path, unused = QFileDialog.getSaveFileName(
                self,
                translate('Export CSV', self.settings['language']),
                '',
                'CSV (*.csv)',
                options=FILE_DIALOG_OPTIONS,
            )
            if path:
                try:
                    export_report_csv(
                        self.result, path, self.settings['language']
                    )
                except OSError as error:
                    self.error.setText(str(error))

    def place_result(self):
        if self.calculate():
            self.place = True
            self.accept()


class SettingsDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.saved = deepcopy(settings)
        self.setWindowTitle('Workspace settings')
        self.resize(900, 570)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        general, optical = QWidget(), QWidget()
        tabs.addTab(general, 'General')
        tabs.addTab(optical, 'Optical losses')
        form, losses = QFormLayout(general), QFormLayout(optical)
        self.language, self.default_code = (
            QComboBox(),
            QLineEdit(settings['color_code']),
        )
        self.language.addItem('English', 'en')
        self.language.addItem('Serbian', 'sr')
        self.language.setCurrentIndex(
            self.language.findData(settings['language'])
        )
        self.snap = QCheckBox('Enable endpoint snapping')
        self.snap.setChecked(settings['snap'])
        self.tolerance = QSpinBox()
        self.tolerance.setRange(3, 40)
        self.tolerance.setValue(settings['snap_pixels'])
        for title, widget in (
            ('Language', self.language),
            ('', self.snap),
            ('Snap tolerance (pixels)', self.tolerance),
            ('Default color code', self.default_code),
        ):
            form.addRow(title, widget)
        self.splice, self.connector, self.margin = (
            decimal(settings[k])
            for k in ('splice_db', 'connector_db', 'margin_db')
        )
        for title, widget in (
            ('Splice loss (dB)', self.splice),
            ('Connector loss (dB)', self.connector),
            ('Engineering margin (dB)', self.margin),
        ):
            losses.addRow(title, widget)
        self.attenuation = {}
        for kind, waves in settings['attenuation'].items():
            for wave, value in waves.items():
                control = decimal(value)
                losses.addRow('{} / {} nm · dB/km'.format(kind, wave), control)
                self.attenuation[kind, wave] = control
        self.catalogue = self.editor_tab(
            tabs,
            'Cable catalogue',
            ['Category', 'Name', 'Capacities (e.g. 2x6,4x12)'],
        )
        self.catalogue.setProperty('fqsd_catalogue', True)
        for entry in settings['catalogues']:
            self.append_row(
                self.catalogue,
                [
                    entry['category'],
                    entry['name'],
                    ','.join('{}x{}'.format(*p) for p in entry['capacities']),
                ],
            )
        self.codes = self.editor_tab(
            tabs,
            'Color codes',
            ['Name', 'Colors (source numbers 1–12)', 'Group suffixes'],
        )
        for entry in settings['color_codes']:
            self.append_row(
                self.codes,
                [
                    entry['name'],
                    ','.join(str(c + 1) for c in entry['colors']),
                    ','.join(entry['suffixes']),
                ],
            )
        layout.addWidget(
            label(
                'Delivered catalogues and TIA-598 / IEC 60304 are retained. '
                'Add custom copies here. Changes affect future cables; '
                'existing cable definitions are frozen.\n'
                + '; '.join(
                    '{}={}'.format(
                        i + 1, translate(name, settings['language'])
                    )
                    for i, name in enumerate(COLOR_NAMES)
                )
            )
        )
        self.error = label()
        self.error.setStyleSheet('color: #d55040;')
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def editor_tab(self, tabs, title, columns):
        page, table = QWidget(), QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(page)
        layout.addWidget(table)
        row = QHBoxLayout()
        add, remove = QPushButton('Add row'), QPushButton('Remove row')
        add.clicked.connect(
            lambda: self.append_row(table, [''] * len(columns))
        )
        remove.clicked.connect(
            lambda: (
                table.removeRow(table.currentRow())
                if table.currentRow() >= 0
                else None
            )
        )
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        tabs.addTab(page, title)
        return table

    @staticmethod
    def append_row(table, values):
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            if column == 0 and table.property('fqsd_catalogue'):
                category = QComboBox()
                for key, title in CATEGORIES.items():
                    category.addItem(title, key)
                category.setCurrentIndex(max(0, category.findData(value)))
                table.setCellWidget(row, column, category)
            else:
                table.setItem(row, column, QTableWidgetItem(value))

    @staticmethod
    def rows(table):
        return [
            [
                table.cellWidget(row, col).currentData()
                if table.cellWidget(row, col) is not None
                else table.item(row, col).text().strip()
                if table.item(row, col)
                else ''
                for col in range(table.columnCount())
            ]
            for row in range(table.rowCount())
        ]

    def values(self):
        result = deepcopy(self.saved)
        result.update(
            language=self.language.currentData(),
            snap=self.snap.isChecked(),
            snap_pixels=self.tolerance.value(),
            color_code=self.default_code.text().strip(),
            splice_db=self.splice.value(),
            connector_db=self.connector.value(),
            margin_db=self.margin.value(),
            catalogues=[],
            color_codes=[],
        )
        for (kind, wave), control in self.attenuation.items():
            result['attenuation'][kind][wave] = control.value()
        try:
            for category, name, capacities in self.rows(self.catalogue):
                result['catalogues'].append(
                    dict(
                        category=category,
                        name=name,
                        capacities=[
                            [
                                int(n)
                                for n in pair.lower()
                                .replace('×', 'x')
                                .split('x')
                            ]
                            for pair in capacities.split(',')
                        ],
                    )
                )
            for name, colors, suffixes in self.rows(self.codes):
                result['color_codes'].append(
                    dict(
                        name=name,
                        colors=[int(n) - 1 for n in colors.split(',')],
                        suffixes=[s.strip() for s in suffixes.split(',')],
                    )
                )
        except ValueError as error:
            raise ValueError(
                'Use numeric TxF capacity pairs and color numbers 1–12, '
                'separated by commas.'
            ) from error
        return validate_settings(result)

    def accept(self):
        try:
            self.values()
        except ValueError as error:
            self.error.setText(str(error))
            return
        super().accept()
