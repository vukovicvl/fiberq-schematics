"""Native Qt version of FiberQ CAD's separate N/NL closure dialog."""

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
    QRadioButton,
    QVBoxLayout,
)

from .i18n import ui_message
from .closure_layout import CLOSURE_LOCATIONS, ClosureProperties, closure_name
from .compat import PLAIN_TEXT, enum_compat


class ClosureDialog(QDialog):
    def __init__(
        self,
        parent=None,
        properties=None,
        label='',
        names=(),
        cable_name='',
        repeated=False,
        continue_series=False,
        same_cable=False,
        width=None,
        language=None,
    ):
        super().__init__(parent)
        from .i18n import ui_language

        self.drawing_language = language or ui_language(self)
        properties = properties or ClosureProperties()
        self.setWindowTitle('FiberQ CAD - closure')
        self.setMinimumWidth(470)
        self.names = tuple(names)
        outer = QVBoxLayout(self)
        types = QGroupBox('Type')
        row = QHBoxLayout(types)
        self.ordinary, self.latent = (
            QRadioButton('N  ordinary'),
            QRadioButton('NL  latent'),
        )
        self.ordinary.setChecked(properties.closure_type == 'N')
        self.latent.setChecked(properties.closure_type == 'NL')
        row.addWidget(self.ordinary)
        row.addWidget(self.latent)
        outer.addWidget(types)
        form = QFormLayout()
        self.label = QLineEdit(label)
        self.label.setMaxLength(200)
        form.addRow('Label   (a number is enough)', self.label)
        outer.addLayout(form)
        where = QGroupBox('Where it sits')
        row = QHBoxLayout(where)
        self.location = QComboBox()
        for key, text in CLOSURE_LOCATIONS.items():
            self.location.addItem(text, key)
        self.location.setCurrentIndex(
            self.location.findData(properties.location)
        )
        self.number = QLineEdit(properties.location_number)
        self.number.setMaxLength(200)
        row.addWidget(self.location, 1)
        row.addWidget(QLabel('No.'))
        row.addWidget(self.number)
        outer.addWidget(where)
        status = QGroupBox('Status')
        row = QHBoxLayout(status)
        self.designed, self.as_built = (
            QRadioButton('Designed'),
            QRadioButton('As-built'),
        )
        self.designed.setChecked(properties.status == 'designed')
        self.as_built.setChecked(properties.status == 'as_built')
        row.addWidget(self.designed)
        row.addWidget(self.as_built)
        outer.addWidget(status)
        self.width = None
        if width is not None:
            self.width = QDoubleSpinBox()
            self.width.setRange(1.6, 1000000)
            self.width.setDecimals(2)
            self.width.setValue(width)
            form = QFormLayout()
            form.addRow('Frame width (CAD units)', self.width)
            outer.addLayout(form)
        self.series = QGroupBox('Series')
        column = QVBoxLayout(self.series)
        self.continue_series = QCheckBox(
            'Continue, ask for the next closure right away'
        )
        self.same_cable = QCheckBox('Same cable, do not pick a fiber again')
        self.continue_series.setChecked(continue_series)
        self.same_cable.setChecked(
            repeated and same_cable and bool(cable_name)
        )
        self.same_cable.setEnabled(repeated and bool(cable_name))
        column.addWidget(self.continue_series)
        column.addWidget(self.same_cable)
        if width is None:
            outer.addWidget(self.series)
        else:
            self.series.hide()
        preview = QGroupBox('Drawn in the drawing')
        column = QVBoxLayout(preview)
        self.preview, self.cable_hint = QLabel(), QLabel()
        from .i18n import user_text

        user_text(self.preview)
        for control in (self.preview, self.cable_hint):
            control.setTextFormat(PLAIN_TEXT)
            control.setWordWrap(True)
            column.addWidget(control)
        outer.addWidget(preview)
        self.cable_name = cable_name
        self.error = QLabel()
        self.error.setTextFormat(PLAIN_TEXT)
        self.error.setStyleSheet('color: #b03020;')
        self.error.setWordWrap(True)
        self.error.hide()
        outer.addWidget(self.error)
        buttons = QDialogButtonBox(
            enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        for control in (self.label, self.number):
            control.textChanged.connect(self.update_preview)
        self.location.currentIndexChanged.connect(self.update_preview)
        self.latent.toggled.connect(self.update_preview)
        self.same_cable.toggled.connect(self.update_preview)
        self.update_preview()
        if repeated:
            self.number.setFocus()
            self.number.selectAll()

    def properties(self):
        return ClosureProperties(
            'NL' if self.latent.isChecked() else 'N',
            self.location.currentData() or '',
            self.number.text().strip(),
            'as_built' if self.as_built.isChecked() else 'designed',
        )

    def update_preview(self, *args):
        properties = self.properties()
        try:
            name = closure_name(
                self.label.text(), properties.closure_type, self.names
            )
        except ValueError:
            name = self.label.text()
        self.preview.setText(
            name
            + (
                '        ' + properties.caption_in(self.drawing_language)
                if properties.caption
                else ''
            )
        )
        self.cable_hint.setText(
            ui_message(
                self, 'cable {}, only the frame is picked', self.cable_name
            )
            if self.same_cable.isChecked()
            else 'the cable is picked by clicking a fiber'
        )

    def accept(self):
        try:
            self.properties().validate()
            closure_name(self.label.text(), self.properties().closure_type)
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        super().accept()
