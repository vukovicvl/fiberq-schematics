"""Native counterpart of the source cable catalogue dialog."""

import json

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from .i18n import ui_message
from .cable_catalog import CATEGORIES, CATALOGUE, COLOR_CODES, CableProperties
from .compat import PLAIN_TEXT, enum_compat
from .workspace_model import (
    DEFAULT_SETTINGS,
    catalogue_definitions,
    color_definitions,
)


class CableDialog(QDialog):
    def __init__(
        self,
        parent=None,
        properties=None,
        models=(),
        editing=False,
        settings=None,
    ):
        super().__init__(parent)
        self.setWindowTitle('Cable properties' if editing else 'Create cable')
        self.setMinimumWidth(530)
        self.saved = properties or CableProperties()
        self.catalogue = catalogue_definitions(settings or DEFAULT_SETTINGS)
        self.codes = color_definitions(settings or DEFAULT_SETTINGS)
        if self.saved.definition:
            definition = self.saved.definition_data()
            self.catalogue[self.saved.category][self.saved.catalog_model] = (
                definition['capacities']
            )
            self.codes[self.saved.color_code] = dict(
                name=self.saved.color_code,
                colors=definition['colors'],
                suffixes=definition['suffixes'],
            )
        self.custom_models = list(models)
        self.editing = editing
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)
        self.category, self.model, self.capacity = (
            QComboBox(),
            QComboBox(),
            QComboBox(),
        )
        for code, title in CATEGORIES.items():
            self.category.addItem(title, code)
        self.model.setEditable(True)
        self.model.setInsertPolicy(
            enum_compat(QComboBox, 'NoInsert', 'InsertPolicy')
        )
        self.category.setCurrentIndex(
            self.category.findData(self.saved.category)
        )
        for title, widget in (
            ('Category', self.category),
            ('Cable type', self.model),
            ('Capacity · tubes × fibers', self.capacity),
        ):
            form.addRow(title, widget)
        self.orientation = QComboBox()
        for title, angle in (
            ('Horizontal', 0),
            ('Vertical up', 90),
            ('Vertical down', 270),
        ):
            self.orientation.addItem(title, angle)
        self.color_code, self.fiber_type, self.status = (
            QComboBox(),
            QComboBox(),
            QComboBox(),
        )
        self.color_code.addItems(list(self.codes))
        self.color_code.setCurrentText(
            self.saved.color_code
            if properties is not None
            else (settings or DEFAULT_SETTINGS)['color_code']
        )
        self.fiber_type.addItems(['SM', 'MM'])
        self.fiber_type.setCurrentText(self.saved.fiber_type)
        self.status.addItem('Designed', 'designed')
        self.status.addItem('As-built', 'as_built')
        self.status.setCurrentIndex(self.status.findData(self.saved.status))
        self.label = QLineEdit()
        self.label.setPlaceholderText('Automatic: K1, K2, …')
        self.real_length = QDoubleSpinBox()
        self.real_length.setRange(0, 10000000)
        self.real_length.setSuffix(' m')
        self.real_length.setSpecialValueText('Unspecified')
        for title, widget in (
            ('Orientation', self.orientation),
            ('Color code', self.color_code),
            ('Fiber type', self.fiber_type),
            ('Drawing status', self.status),
            ('Label', self.label),
            ('Real cable length', self.real_length),
        ):
            form.addRow(title, widget)
        group = QGroupBox('Drawing')
        drawing = QVBoxLayout(group)
        length_row = QHBoxLayout()
        length_row.addWidget(QLabel('Schematic length'))
        self.length = QDoubleSpinBox()
        self.length.setRange(12, 100000)
        self.length.setDecimals(2)
        self.length.setSuffix(' CAD units')
        self.length.setValue(40)
        length_row.addWidget(self.length)
        drawing.addLayout(length_row)
        self.drag_length = QCheckBox(
            'Drag the length after the insertion point'
        )
        self.bends = QCheckBox('Cable with bends · click the path')
        self.snap = QCheckBox('Snap to free housing/module connectors')
        self.widen = QCheckBox('Widen and attach housings at cable ends')
        self.inherit_grid = QCheckBox(
            'Inherit the full row grid when aligning'
        )
        for widget in (
            self.drag_length,
            self.bends,
            self.snap,
            self.widen,
            self.inherit_grid,
        ):
            drawing.addWidget(widget)
        self.snap.setChecked(True)
        self.widen.setChecked(True)
        self.inherit_grid.setChecked(True)
        outer.addWidget(group)
        group.setVisible(not editing)
        self.orientation.setEnabled(not editing)
        self.preview, self.error = QLabel(), QLabel()
        for label in (self.preview, self.error):
            label.setWordWrap(True)
            label.setTextFormat(PLAIN_TEXT)
            outer.addWidget(label)
        self.error.setStyleSheet('color: #d55040;')
        buttons = QDialogButtonBox(
            enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.category.currentIndexChanged.connect(self.fill_models)
        self.model.currentIndexChanged.connect(self.fill_capacities)
        self.model.editTextChanged.connect(self.update_preview)
        self.capacity.currentIndexChanged.connect(self.update_preview)
        self.bends.toggled.connect(self.update_drawing)
        self.fill_models()
        self.model.setCurrentText(self.saved.model)
        self.select_capacity(self.saved.tubes, self.saved.fibers_per_tube)
        if editing:
            self.category.setEnabled(False)
            self.capacity.setEnabled(False)

    def fill_models(self):
        self.model.blockSignals(True)
        self.model.clear()
        category = self.category.currentData()
        for name in self.catalogue[category]:
            self.model.addItem(name, name)
        for cat, name, template in self.custom_models:
            if (
                cat == category
                and template in self.catalogue[category]
                and self.model.findText(name) < 0
            ):
                self.model.addItem(name, template)
        if category == self.saved.category:
            if self.model.findText(self.saved.model) < 0:
                self.model.addItem(self.saved.model, self.saved.catalog_model)
            self.model.setCurrentIndex(self.model.findText(self.saved.model))
        self.model.blockSignals(False)
        self.fill_capacities()

    def fill_capacities(self):
        self.capacity.blockSignals(True)
        self.capacity.clear()
        template = self.model.currentData()
        if template not in self.catalogue[self.category.currentData()]:
            template = next(iter(self.catalogue[self.category.currentData()]))
        self.template = template
        tube = None
        for tubes, fibers in self.catalogue[self.category.currentData()][
            template
        ]:
            if tubes != tube:
                tube = tubes
                self.capacity.addItem(
                    ui_message(
                        self,
                        '— {} tube —' if tubes == 1 else '— {} tubes —',
                        tubes,
                    ),
                    None,
                )
                self.capacity.model().item(
                    self.capacity.count() - 1
                ).setEnabled(False)
            self.capacity.addItem(
                ui_message(
                    self, '    {} × {}  ·  {}F', tubes, fibers, tubes * fibers
                ),
                (tubes, fibers),
            )
        self.select_capacity(self.saved.tubes, self.saved.fibers_per_tube)
        self.capacity.blockSignals(False)
        self.update_preview()

    def select_capacity(self, tubes, fibers):
        # Qt's QVariant comparison does not compare Python tuple user-data.
        index = next(
            (
                i
                for i in range(self.capacity.count())
                if self.capacity.itemData(i) == (tubes, fibers)
            ),
            1,
        )
        self.capacity.setCurrentIndex(index)

    def update_drawing(self):
        self.length.setEnabled(not self.bends.isChecked())
        self.drag_length.setEnabled(not self.bends.isChecked())

    def properties(self):
        tubes, fibers = self.capacity.currentData()
        definition = ''
        category, code = (
            self.category.currentData(),
            self.color_code.currentText(),
        )
        if (
            self.template not in CATALOGUE[category]
            or code not in COLOR_CODES
            or self.saved.definition
        ):
            colors = self.codes[code]
            definition = json.dumps(
                dict(
                    capacities=self.catalogue[category][self.template],
                    colors=colors['colors'],
                    suffixes=colors['suffixes'],
                )
            )
        return CableProperties(
            self.category.currentData(),
            self.model.currentText().strip() or self.template,
            self.template,
            tubes,
            fibers,
            self.color_code.currentText(),
            self.fiber_type.currentText(),
            self.status.currentData(),
            self.saved.row_positions if self.editing else (),
            definition,
        ).validate()

    def configure_settings(self, settings, reset_defaults=False):
        self.catalogue = catalogue_definitions(settings)
        self.codes = color_definitions(settings)
        selected = self.color_code.currentText()
        self.color_code.clear()
        self.color_code.addItems(list(self.codes))
        self.color_code.setCurrentText(
            settings['color_code']
            if reset_defaults
            else selected
            if selected in self.codes
            else settings['color_code']
        )
        if reset_defaults:
            self.snap.setChecked(settings['snap'])
        self.fill_models()

    def update_preview(self):
        if not hasattr(self, 'preview') or self.capacity.currentData() is None:
            return
        try:
            properties = self.properties()
            self.preview.setText(
                ui_message(
                    self,
                    '{} · {} fibers',
                    properties.description,
                    properties.count,
                )
            )
        except ValueError as error:
            self.preview.setText(str(error))

    def accept(self):
        try:
            self.properties()
        except (ValueError, TypeError) as error:
            self.error.setText(str(error))
            return
        super().accept()
