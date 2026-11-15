"""FiberQ housing form for rack equipment, cabinets and the FAT/OTB box
family."""

from qgis.PyQt.QtGui import QIntValidator
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from .compat import PLAIN_TEXT, enum_compat
from .housing_catalog import (
    CONNECTORS,
    HOUSINGS,
    RACK_HOUSINGS,
    BOX_HOUSINGS,
    SUPPORTED_HOUSINGS,
    SPLITTER_LOSSES,
    OdfProperties,
    housing_splitter_available,
    odf_description,
)


class HousingDialog(QDialog):
    def __init__(
        self,
        parent=None,
        properties=None,
        label='',
        models=(),
        can_attach=False,
        attached=False,
        lock_kind=False,
        language=None,
    ):
        super().__init__(parent)
        from .i18n import ui_language, user_text

        self.drawing_language = language or ui_language(self)
        self.setWindowTitle('FiberQ CAD - housing')
        self.setMinimumWidth(475)
        properties = properties or OdfProperties()
        self.initial_properties = properties
        self.kind = properties.kind
        self.kind_radios = {}
        self.model_history = (
            {key: list(values) for key, values in models.items()}
            if isinstance(models, dict)
            else {self.kind: list(models)}
        )
        self.model_texts = {self.kind: properties.model}
        self.attached = attached
        self.editing = lock_kind
        self.mounting_texts = {self.kind: properties.mounting}
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        types = QGroupBox('Type')
        type_layout = QVBoxLayout(types)
        for key, data in HOUSINGS.items():
            radio = QRadioButton(data[0])
            radio.setChecked(key == self.kind)
            radio.setEnabled(
                key in SUPPORTED_HOUSINGS
                and (not lock_kind or key == self.kind)
            )
            if key not in SUPPORTED_HOUSINGS:
                radio.setToolTip(
                    'This housing family is planned for a later milestone.'
                )
            elif lock_kind and key != self.kind:
                radio.setToolTip('The type is fixed for an existing element.')
            self.kind_radios[key] = radio
            type_layout.addWidget(radio)
        type_layout.addStretch()
        top.addWidget(types)
        fields = QVBoxLayout()
        model_box = QGroupBox('Model (maker and type)')
        model_layout = QHBoxLayout(model_box)
        self.model = QLineEdit(properties.model)
        self.model.setMaxLength(200)
        self.models = QComboBox()
        self.models.setMaximumWidth(135)
        self.fill_models()
        self.models.activated.connect(
            lambda index: self.model.setText(self.models.itemText(index))
        )
        model_layout.addWidget(self.model, 1)
        model_layout.addWidget(self.models)
        fields.addWidget(model_box)
        grid = QGridLayout()
        self.capacity = QComboBox()
        self.capacity.addItems([str(n) for n in HOUSINGS[self.kind][2]])
        self.capacity.setCurrentText(str(properties.capacity))
        self.connector = QComboBox()
        self.connector.addItems(CONNECTORS)
        self.connector.setCurrentText(properties.connector)
        self.mounting = QComboBox()
        for code in HOUSINGS[self.kind][3]:
            self.mounting.addItem(code, code)
        self.mounting.setCurrentText(properties.mounting)
        for column, (title, control) in enumerate(
            (
                ('Capacity', self.capacity),
                ('Connector type', self.connector),
                ('Mounting', self.mounting),
            )
        ):
            grid.addWidget(QLabel(title), 0, column)
            grid.addWidget(control, 1, column)
        fields.addLayout(grid)
        grid = QGridLayout()
        self.label = QLineEdit(label)
        self.label.setMaxLength(200)
        self.used = QLineEdit(
            ''
            if properties.requested_used is None
            else str(properties.requested_used)
        )
        self.used.setMaximumWidth(65)
        self.used.setToolTip(
            'Number of cable fibers to terminate. Blank uses the available '
            'capacity; zero leaves the fibers unconnected.'
        )
        self.rack_module = QLineEdit(properties.rack_module)
        self.rack_module.setMaxLength(200)
        self.modules = QSpinBox()
        self.modules.setRange(1, 24)
        self.modules.setValue(properties.modules)
        self.modules.setToolTip(
            'Large placement previews sample connector rows; the saved '
            'drawing contains every connector.'
        )
        self.capacity.setToolTip('Connector capacity of each module.')
        grid.addWidget(QLabel('Label'), 0, 0)
        grid.addWidget(self.label, 0, 1)
        grid.addWidget(QLabel('Used'), 0, 2)
        grid.addWidget(self.used, 0, 3)
        self.rack_label = QLabel('Rack and module')
        self.modules_label = QLabel('Modules')
        grid.addWidget(self.rack_label, 1, 0)
        grid.addWidget(self.rack_module, 1, 1)
        grid.addWidget(self.modules_label, 1, 2)
        grid.addWidget(self.modules, 1, 3)
        address_label, address = self.address_label, self.address = (
            QLabel('Address or location'),
            QLineEdit(properties.address),
        )
        address.setMaxLength(200)
        grid.addWidget(address_label, 2, 0)
        grid.addWidget(address, 2, 1, 1, 3)
        splitter = self.splitter = QCheckBox('Splitter inside the box')
        splitter.setChecked(bool(properties.splitter_ratio))
        ratio = self.ratio = QComboBox()
        ratio.addItems(['1:{}'.format(n) for n in SPLITTER_LOSSES])
        ratio.setCurrentText('1:{}'.format(properties.splitter_ratio or 8))
        grid.addWidget(splitter, 3, 0, 1, 2)
        grid.addWidget(ratio, 3, 2, 1, 2)
        fields.addLayout(grid)
        top.addLayout(fields, 1)
        outer.addLayout(top)
        orientation = self.orientation_group = QGroupBox('Orientation')
        row = QHBoxLayout(orientation)
        self.orientations = QButtonGroup(self)
        for value, title in (
            (0, 'Horizontal'),
            (90, 'Vertical up'),
            (270, 'Vertical down'),
        ):
            radio = QRadioButton(title)
            radio.setChecked(value == properties.orientation)
            self.orientations.addButton(radio, value)
            row.addWidget(radio)
        outer.addWidget(orientation)
        row = QHBoxLayout()
        attachment = QGroupBox('Attaches to')
        attach_layout = QHBoxLayout(attachment)
        self.cable = QRadioButton('A cable')
        self.cable.setEnabled(can_attach and not attached)
        self.cable.setToolTip(
            'Click a fiber near a free cable end. Draw Unattached Cable '
            'creates a cable before equipment.'
        )
        standalone = self.standalone = QRadioButton('Nothing')
        standalone.setChecked(not attached)
        self.cable.setChecked(attached)
        standalone.setEnabled(not attached)
        attach_layout.addWidget(self.cable)
        attach_layout.addWidget(standalone)
        row.addWidget(attachment)
        status = QGroupBox('Status')
        status_layout = QHBoxLayout(status)
        self.designed = QRadioButton('Designed')
        self.as_built = QRadioButton('As-built')
        self.designed.setChecked(properties.status == 'designed')
        self.as_built.setChecked(properties.status == 'as_built')
        status_layout.addWidget(self.designed)
        status_layout.addWidget(self.as_built)
        row.addWidget(status)
        outer.addLayout(row)
        preview = QGroupBox('Drawn in the drawing')
        preview_layout = QVBoxLayout(preview)
        self.preview = QLabel()
        user_text(self.preview)
        self.preview.setTextFormat(PLAIN_TEXT)
        self.preview.setWordWrap(True)
        self.preview.setMinimumWidth(420)
        preview_layout.addWidget(self.preview)
        outer.addWidget(preview)
        self.error = QLabel()
        self.error.setTextFormat(PLAIN_TEXT)
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #b03020;')
        self.error.hide()
        outer.addWidget(self.error)
        buttons = QDialogButtonBox(
            enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        for control in (self.label, self.model, self.rack_module):
            control.textChanged.connect(self.update_preview)
        self.connector.currentIndexChanged.connect(self.update_preview)
        self.mounting.currentIndexChanged.connect(self.update_preview)
        self.ratio.currentIndexChanged.connect(self.update_preview)
        self.splitter.toggled.connect(self.update_family_fields)
        self.capacity.currentIndexChanged.connect(self.update_capacity)
        self.modules.valueChanged.connect(self.update_capacity)
        self.cable.toggled.connect(
            lambda checked: self.orientation_group.setEnabled(
                not checked and not self.attached
            )
        )
        self.orientation_group.setEnabled(not attached)
        for kind, radio in self.kind_radios.items():
            radio.toggled.connect(
                lambda checked, key=kind: (
                    self.change_kind(key) if checked else None
                )
            )
        self.update_capacity()
        self.update_family_fields()

    def fill_models(self):
        models = list(
            dict.fromkeys(
                m for m in self.model_history.get(self.kind, ()) if m
            )
        )
        self.models.clear()
        if models:
            self.models.addItems(models)
        else:
            self.models.addItem('(none entered)', '__none')
        self.models.setEnabled(bool(models))

    def change_kind(self, kind):
        if kind == self.kind:
            return
        self.model_texts[self.kind] = self.model.text()
        self.mounting_texts[self.kind] = self.mounting.currentData()
        capacity = int(self.capacity.currentText())
        self.kind = kind
        previous = self.capacity.blockSignals(True)
        self.capacity.clear()
        capacities = HOUSINGS[kind][2]
        self.capacity.addItems([str(n) for n in capacities])
        self.capacity.setCurrentText(
            str(capacity if capacity in capacities else capacities[0])
        )
        self.capacity.blockSignals(previous)
        previous = self.mounting.blockSignals(True)
        self.mounting.clear()
        for code in HOUSINGS[kind][3]:
            self.mounting.addItem(code, code)
        self.mounting.setCurrentIndex(
            self.mounting.findData(
                self.mounting_texts.get(kind, HOUSINGS[kind][3][0])
            )
        )
        self.mounting.blockSignals(previous)
        self.fill_models()
        self.model.setText(self.model_texts.get(kind, ''))
        self.update_capacity()
        self.update_family_fields()

    def update_family_fields(self, *args):
        rack = self.kind in RACK_HOUSINGS
        for control in (
            self.rack_label,
            self.rack_module,
            self.modules_label,
            self.modules,
        ):
            control.setEnabled(rack)
        for control in (self.address_label, self.address):
            control.setEnabled(not rack)
        self.splitter.setEnabled(
            housing_splitter_available(
                self.kind, int(self.capacity.currentText())
            )
        )
        splitting = self.splitter.isEnabled() and self.splitter.isChecked()
        self.ratio.setEnabled(splitting)
        self.used.setEnabled(not splitting)
        if self.initial_properties.layout_state:
            self.cable.setEnabled(False)
            self.used.setReadOnly(True)
            self.used.setToolTip(
                'Total across attached cables. Use Add cable for additional '
                'terminations or Detach cable end.'
            )
            self.splitter.setEnabled(False)
            self.ratio.setEnabled(False)
        self.update_preview()

    def update_capacity(self, *args):
        modules = self.modules.value() if self.kind in RACK_HOUSINGS else 1
        self.used.setValidator(
            QIntValidator(
                0, int(self.capacity.currentText()) * modules, self.used
            )
        )
        self.update_family_fields()

    def properties(self):
        value = self.used.text().strip()
        rack = self.kind in RACK_HOUSINGS
        ratio = (
            self.initial_properties.splitter_ratio
            if self.initial_properties.layout_state
            else int(self.ratio.currentText().split(':')[1])
            if self.splitter.isEnabled() and self.splitter.isChecked()
            else 0
        )
        used = (
            (self.initial_properties.requested_used or 1)
            if ratio and self.editing and self.kind not in BOX_HOUSINGS
            else 1
            if ratio
            else int(value)
            if value
            else None
        )
        return OdfProperties(
            capacity=int(self.capacity.currentText()),
            model=self.model.text().strip(),
            connector=self.connector.currentText(),
            mounting=self.mounting.currentData(),
            rack_module=self.rack_module.text().strip() if rack else '',
            requested_used=used,
            orientation=self.initial_properties.orientation
            if self.attached or self.cable.isChecked()
            else self.orientations.checkedId()
            if self.orientations.checkedId() >= 0
            else self.initial_properties.orientation,
            status='as_built' if self.as_built.isChecked() else 'designed',
            modules=self.modules.value() if rack else 1,
            mirrored=self.initial_properties.mirrored,
            row_positions=self.initial_properties.row_positions,
            kind=self.kind,
            address=self.address.text().strip() if not rack else '',
            splitter_ratio=ratio,
            splitter_feed=self.initial_properties.splitter_feed
            if ratio
            else 0,
            layout_state=self.initial_properties.layout_state
            if int(self.capacity.currentText())
            == self.initial_properties.capacity
            and (self.modules.value() if rack else 1)
            == self.initial_properties.modules
            else '',
        )

    def attach_to_cable(self):
        return self.cable.isChecked() and not self.attached

    def update_preview(self, *args):
        properties = OdfProperties(
            capacity=int(self.capacity.currentText()),
            model=self.model.text().strip(),
            connector=self.connector.currentText(),
            mounting=self.mounting.currentData(),
            modules=self.modules.value() if self.kind in RACK_HOUSINGS else 1,
            kind=self.kind,
            splitter_ratio=int(self.ratio.currentText().split(':')[1])
            if self.splitter.isEnabled() and self.splitter.isChecked()
            else 0,
        )
        self.preview.setText(
            odf_description(
                self.label.text(), properties, self.drawing_language
            )
        )

    def accept(self):
        try:
            self.properties().validate()
            if '\n' in self.label.text() or '\r' in self.label.text():
                raise ValueError('Label must be a single line.')
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        super().accept()
