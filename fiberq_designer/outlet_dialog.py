"""FQIZVOD parameters and a read-only production preview."""

import json

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from .i18n import ui_message
from .cable_catalog import CableProperties, properties_from_record
from .compat import PLAIN_TEXT, enum_compat
from .workspace_model import color_definitions


class OutletDialog(QDialog):
    def __init__(self, editor, identity=None, module_no=1, cable=None):
        super().__init__(editor)
        self.editor, self.identity, self.editing = (
            editor,
            identity,
            cable is not None,
        )
        self.plan = None
        self.setWindowTitle(
            'Outlet properties' if self.editing else 'Create frame outlet'
        )
        self.setMinimumWidth(540)
        self.contexts = {}
        self.codes = color_definitions(editor.service.settings())
        saved = (
            properties_from_record(cable)
            if self.editing
            else CableProperties(
                category='',
                model='',
                catalog_model='',
                tubes=2,
                fibers_per_tube=6,
            )
        )
        if saved.definition:
            definition = saved.definition_data()
            self.codes[saved.color_code] = dict(
                colors=definition['colors'], suffixes=definition['suffixes']
            )
        outer, form = QVBoxLayout(self), QFormLayout()
        outer.addLayout(form)
        self.module, self.mode = QComboBox(), QComboBox()
        if not self.editing:
            element = editor.repository.find('elements', identity)
            label = QLabel(element['name'])
            label.setTextFormat(PLAIN_TEXT)
            from .i18n import user_text

            form.addRow('Frame', user_text(label))
            housing = editor.repository.find('housings', identity)
            for module in editor.service.modules_of(identity):
                self.module.addItem(
                    module['label']
                    or ui_message(self, 'Module {}', module['number']),
                    int(module['number']),
                )
            if housing is None:
                self.module.addItem(ui_message(self, 'Closure'), 0)
            self.module.setCurrentIndex(
                max(0, self.module.findData(module_no))
            )
            self.module.setEnabled(self.module.count() > 1)
            if housing is not None:
                self.mode.addItem(
                    'Elbow · reach the connector column', 'elbow'
                )
            self.mode.addItem('Straight · free ends for splicing', 'straight')
            form.addRow('Module', self.module)
            form.addRow('Fiber route', self.mode)
        self.tubes, self.fibers = QSpinBox(), QSpinBox()
        from .i18n import user_text

        user_text(self.module)
        for widget, value in (
            (self.tubes, saved.tubes),
            (self.fibers, saved.fibers_per_tube),
        ):
            widget.setRange(1, 576)
            widget.setValue(value)
            widget.setEnabled(not self.editing)
        self.length, self.real_length = QDoubleSpinBox(), QDoubleSpinBox()
        self.length.setRange(12, 100000)
        self.length.setValue(40)
        self.length.setSuffix(' CAD units')
        self.real_length.setRange(0, 10000000)
        self.real_length.setSuffix(' m')
        self.real_length.setSpecialValueText('Unspecified')
        self.real_length.setValue(
            float(cable['length_m']) if self.editing else 0
        )
        self.label, self.model = (
            QLineEdit(cable['name'] if self.editing else ''),
            QLineEdit(saved.model),
        )
        self.label.setPlaceholderText('Automatic: K1, K2, …')
        self.color_code, self.fiber_type, self.status = (
            QComboBox(),
            QComboBox(),
            QComboBox(),
        )
        self.color_code.addItems(list(self.codes))
        self.color_code.setCurrentText(
            saved.color_code
            if self.editing
            else editor.service.settings()['color_code']
        )
        self.fiber_type.addItems(['SM', 'MM'])
        self.fiber_type.setCurrentText(saved.fiber_type)
        for title, code in (
            ('Designed', 'designed'),
            ('As-built', 'as_built'),
        ):
            self.status.addItem(title, code)
        self.status.setCurrentIndex(self.status.findData(saved.status))
        for title, widget in (
            ('Tubes', self.tubes),
            ('Fibers per tube', self.fibers),
            ('Cable type', self.model),
            ('Label', self.label),
            ('Color code', self.color_code),
            ('Fiber type', self.fiber_type),
            ('Drawing status', self.status),
            ('Real cable length', self.real_length),
        ):
            form.addRow(title, widget)
        if not self.editing:
            form.addRow('Schematic length', self.length)
        self.summary, self.error = QLabel(), QLabel()
        self.error.setStyleSheet('color: #d55040;')
        for widget in (self.summary, self.error):
            widget.setTextFormat(PLAIN_TEXT)
            widget.setWordWrap(True)
            outer.addWidget(widget)
        help_text = QLabel(
            'Creates a new outlet cable. Incoming fibers remain continuous. '
            'Use Join / Group join for splices, or Edit → Add cable for '
            'adapter terminations. Esc/Cancel saves nothing.'
        )
        help_text.setTextFormat(PLAIN_TEXT)
        help_text.setWordWrap(True)
        outer.addWidget(help_text)
        self.buttons = QDialogButtonBox(
            enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.scene = editor.service.fiber_scene() if not self.editing else None
        for widget in (
            self.module,
            self.mode,
            self.color_code,
            self.fiber_type,
            self.status,
        ):
            widget.currentIndexChanged.connect(self.recompute)
        for widget in (self.tubes, self.fibers, self.length):
            widget.valueChanged.connect(self.recompute)
        self.model.textChanged.connect(self.recompute)
        self.recompute()

    def properties(self):
        code = self.codes[self.color_code.currentText()]
        definition = json.dumps(
            dict(
                capacities=[[self.tubes.value(), self.fibers.value()]],
                colors=code['colors'],
                suffixes=code['suffixes'],
            )
        )
        return CableProperties(
            category='',
            catalog_model='',
            model=self.model.text().strip(),
            tubes=self.tubes.value(),
            fibers_per_tube=self.fibers.value(),
            color_code=self.color_code.currentText(),
            fiber_type=self.fiber_type.currentText(),
            status=self.status.currentData(),
            definition=definition,
        ).validate()

    def recompute(self, *unused):
        try:
            props = self.properties()
            self.plan = None
            message = ui_message(
                self,
                '{} tubes × {} fibers = {} fibers.',
                props.tubes,
                props.fibers_per_tube,
                props.count,
            )
            if not self.editing:
                module = self.module.currentData()
                if module not in self.contexts:
                    self.contexts[module] = self.editor.service.outlet_context(
                        self.identity, module, self.scene
                    )
                self.plan = self.editor.service.outlet_plan(
                    self.identity,
                    props,
                    self.length.value(),
                    self.mode.currentData(),
                    module,
                    self.contexts[module],
                )
                context = self.plan['context']
                message += (
                    ui_message(
                        self,
                        '\nPrevious outlet detected; new columns go to its '
                        'left.',
                    )
                    if context['repeated']
                    else ''
                )
                if (
                    context['housing']
                    and self.plan['drawing']['left'] < context['left']
                ):
                    message += ui_message(
                        self,
                        '\nThe bundle extends to the left of the housing '
                        'frame, as in CAD.',
                    )
                message += ui_message(
                    self, '\nNo new optical connections are created.'
                )
                self.editor.preview_outlet(self.plan)
            self.summary.setText(message)
            self.error.clear()
            self.buttons.button(
                enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            ).setEnabled(True)
        except ValueError as error:
            self.plan = None
            self.error.setText(str(error))
            self.buttons.button(
                enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            ).setEnabled(False)
            self.editor.click_tool.clear()

    def accept(self):
        self.recompute()
        if not self.error.text():
            super().accept()
