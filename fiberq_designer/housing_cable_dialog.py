"""Review or cancel additional terminations for an existing housing."""

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from .i18n import ui_message
from .compat import PLAIN_TEXT, enum_compat
from .geometry import odf_properties


class AddHousingCableDialog(QDialog):
    def __init__(self, editor, identity, cable_id, side, module_no=1):
        super().__init__(editor)
        self.editor, self.identity, self.cable_id, self.side = (
            editor,
            identity,
            cable_id,
            side,
        )
        self.plan = None
        self.setWindowTitle('Add cable to existing housing')
        self.setMinimumWidth(520)
        layout, form = QVBoxLayout(self), QFormLayout()
        layout.addLayout(form)
        service, repo = editor.service, editor.repository
        element, record = (
            repo.find('elements', identity),
            repo.find('housings', identity),
        )
        props = odf_properties(element, record)
        self.module = QComboBox()
        for module in service.modules_of(identity):
            self.module.addItem(
                module['label']
                or ui_message(self, 'Module {}', module['number']),
                int(module['number']),
            )
        self.module.setCurrentIndex(self.module.findData(module_no))
        self.module.setEnabled(props.modules > 1)
        self.additional = QSpinBox()
        self.additional.setRange(0, props.capacity)
        self.additional.setSpecialValueText('Widen only · no new terminations')
        from .i18n import user_text

        user_text(self.module)
        form.addRow('Housing', user_text(QLabel(element['name'])))
        form.addRow(
            'Cable end',
            user_text(
                QLabel(
                    ui_message(
                        self,
                        '{} · {}',
                        repo.find('cables', cable_id)['name'],
                        side,
                    )
                )
            ),
        )
        form.addRow('Module', self.module)
        form.addRow('Connect additional fibers', self.additional)
        self.summary, self.error = QLabel(), QLabel()
        user_text(self.summary)
        self.error.setStyleSheet('color: #d55040;')
        for widget in (self.summary, self.error):
            widget.setTextFormat(PLAIN_TEXT)
            widget.setWordWrap(True)
            layout.addWidget(widget)
        help_text = QLabel(
            'Existing hanging fibers are offered before the selected new '
            'cable, following FiberQ CAD. '
            'Existing connections are retained. Zero only expands the frame. '
            'Esc/Cancel saves nothing.'
        )
        help_text.setTextFormat(PLAIN_TEXT)
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.buttons = QDialogButtonBox(
            enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.module.currentIndexChanged.connect(
            lambda: self.recompute(reset=True)
        )
        self.additional.valueChanged.connect(lambda: self.recompute())
        self.recompute(reset=True)

    def recompute(self, reset=False):
        try:
            plan = self.editor.service.housing_cable_plan(
                self.identity,
                self.cable_id,
                self.side,
                None if reset else self.additional.value(),
                self.module.currentData(),
            )
            if reset:
                self.additional.blockSignals(True)
                self.additional.setMaximum(plan['limit'])
                self.additional.setValue(plan['additional'])
                self.additional.blockSignals(False)
            self.plan = plan
            names = {
                c['cable_id']: c['name']
                for c in self.editor.repository.features('cables')
            }
            allocations = [
                ui_message(self, '{}: {} additional', names[identity], count)
                for identity, count in plan['per_cable'].items()
                if count
            ]
            self.summary.setText(
                ui_message(
                    self,
                    'Total terminated after adding: {}\n{}',
                    plan['properties'].requested_used,
                    '\n'.join(allocations)
                    if allocations
                    else ui_message(
                        self, 'No existing termination will be changed.'
                    ),
                )
            )
            self.error.clear()
            self.buttons.button(
                enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            ).setEnabled(True)
            self.editor.show_housing_add_preview(self.identity, plan)
        except ValueError as error:
            self.plan = None
            self.error.setText(str(error))
            self.buttons.button(
                enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
            ).setEnabled(False)
            self.editor.click_tool.clear()

    def accept(self):
        self.recompute()
        if self.plan is not None:
            super().accept()
