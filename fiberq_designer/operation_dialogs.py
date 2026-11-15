"""Native forms for fiber operations and active equipment."""

from qgis.PyQt.QtWidgets import (
    QCheckBox,
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
from .active_layout import ActiveProperties
from .fiber_model import PURPOSES
from .compat import PLAIN_TEXT, enum_compat


def dialog_form(dialog, title):
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(430)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)
    return layout, form


def dialog_buttons(dialog, layout):
    buttons = QDialogButtonBox(
        enum_compat(QDialogButtonBox, 'Ok', 'StandardButton')
        | enum_compat(QDialogButtonBox, 'Cancel', 'StandardButton')
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)


class ActiveDialog(QDialog):
    def __init__(self, parent=None, properties=None, label='', editing=False):
        super().__init__(parent)
        self.saved = properties or ActiveProperties()
        layout, form = dialog_form(
            self, ('Edit ' if editing else 'Create ') + self.saved.kind
        )
        self.label = QLineEdit(label)
        self.label.setPlaceholderText('Automatic designation')
        self.count = (
            QComboBox() if self.saved.kind == 'SPLITTER' else QSpinBox()
        )
        if self.saved.kind == 'SPLITTER':
            for value in (2, 4, 8, 16, 32, 64):
                self.count.addItem('1:' + str(value), value)
            self.count.setCurrentIndex(self.count.findData(self.saved.count))
        else:
            self.count.setRange(1, 576 if self.saved.kind == 'OLT' else 1)
            self.count.setValue(self.saved.count)
        self.orientation = QComboBox()
        for title, angle in (
            ('Horizontal', 0),
            ('Vertical up', 90),
            ('Vertical down', 270),
            ('Horizontal left', 180),
        ):
            self.orientation.addItem(title, angle)
        self.orientation.setCurrentIndex(
            self.orientation.findData(self.saved.orientation)
        )
        self.status, self.anchor = QComboBox(), QComboBox()
        self.status.addItem('Designed', 'designed')
        self.status.addItem('As-built', 'as_built')
        self.status.setCurrentIndex(self.status.findData(self.saved.status))
        self.anchor.addItem('First output endpoint', 'output')
        self.anchor.addItem('Input endpoint', 'input')
        self.anchor.setCurrentIndex(self.anchor.findData(self.saved.anchor))
        self.address = QLineEdit(self.saved.address)
        self.snap = QCheckBox(
            'Snap and connect to a free connector or fiber end'
        )
        self.snap.setChecked(self.saved.kind != 'OLT')
        self.snap.setEnabled(self.saved.kind != 'OLT')
        form.addRow('Label', self.label)
        form.addRow(
            'Ratio'
            if self.saved.kind == 'SPLITTER'
            else 'PON ports'
            if self.saved.kind == 'OLT'
            else 'Input',
            self.count,
        )
        form.addRow('Orientation', self.orientation)
        form.addRow('Drawing status', self.status)
        if self.saved.kind == 'SPLITTER' and not editing:
            form.addRow('Placement anchor', self.anchor)
        if self.saved.kind == 'ONT':
            form.addRow('Customer / address', self.address)
        if not editing:
            layout.addWidget(self.snap)
        self.preview, self.error = QLabel(), QLabel()
        for widget in (self.preview, self.error):
            widget.setWordWrap(True)
            widget.setTextFormat(PLAIN_TEXT)
            layout.addWidget(widget)
        if self.saved.kind == 'SPLITTER':
            self.count.currentIndexChanged.connect(self.update_preview)
        else:
            self.count.valueChanged.connect(self.update_preview)
        self.anchor.currentIndexChanged.connect(self.update_preview)
        self.update_preview()
        dialog_buttons(self, layout)

    def properties(self):
        count = (
            self.count.currentData()
            if self.saved.kind == 'SPLITTER'
            else self.count.value()
        )
        return ActiveProperties(
            self.saved.kind,
            count,
            self.orientation.currentData(),
            self.status.currentData(),
            self.address.text(),
            self.anchor.currentData(),
        ).validate()

    def update_preview(self):
        from .active_layout import active_layout

        try:
            p = self.properties()
        except ValueError as error:
            self.preview.setText(str(error))
            return
        text = (
            ui_message(self, '1:{} · {} dB', p.count, active_layout(p).loss)
            if p.kind == 'SPLITTER'
            else ui_message(self, '{} PON ports', p.count)
            if p.kind == 'OLT'
            else 'One optical input and customer designation'
        )
        self.preview.setText(text)

    def accept(self):
        try:
            self.properties()
        except ValueError as error:
            self.error.setText(str(error))
            return
        super().accept()


class FiberDataDialog(QDialog):
    def __init__(self, parent=None, scope='Selected fiber'):
        super().__init__(parent)
        layout, form = dialog_form(self, 'Fiber purpose, type and description')
        label = QLabel(
            ui_message(self, scope)
            + ui_message(
                self,
                ' · purpose follows connected sections; type and description '
                'apply to whole fibers',
            )
        )
        label.setTextFormat(PLAIN_TEXT)
        label.setWordWrap(True)
        layout.insertWidget(0, label)
        self.purpose, self.fiber_type = QComboBox(), QComboBox()
        self.purpose.addItem(ui_message(self, 'Keep current purpose'), None)
        for code, purpose in PURPOSES.items():
            self.purpose.addItem(purpose.title, code)
        for title, code in (
            ('Keep current type', None),
            ('Unknown', ''),
            ('SM · singlemode', 'SM'),
            ('MM · multimode', 'MM'),
        ):
            self.fiber_type.addItem(ui_message(self, title), code)
        self.replace_description = QCheckBox(
            'Replace description (blank clears it)'
        )
        self.description = QLineEdit()
        self.description.setMaxLength(1000)
        form.addRow('Purpose', self.purpose)
        form.addRow('Fiber type', self.fiber_type)
        form.addRow(self.replace_description)
        form.addRow('Description', self.description)
        dialog_buttons(self, layout)


class GroupJoinDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout, form = dialog_form(self, 'Join a group of fibers')
        self.count, self.step = QSpinBox(), QSpinBox()
        self.count.setRange(1, 576)
        self.count.setValue(12)
        self.step.setRange(-576, 576)
        self.step.setValue(1)
        self.kind = QComboBox()
        self.kind.addItem('Fusion splice · show dots', 'splice')
        self.kind.addItem('Through · no dots', 'through')
        self.kind.addItem('Patch', 'patch')
        form.addRow('Number of pairs', self.count)
        form.addRow('Destination numbering step', self.step)
        form.addRow('Connection', self.kind)
        self.error = QLabel()
        layout.addWidget(self.error)
        dialog_buttons(self, layout)

    def accept(self):
        if self.step.value() == 0:
            self.error.setText('The numbering step must be nonzero.')
        else:
            super().accept()


class BranchDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout, form = dialog_form(self, 'Branch / divert fiber ends')
        self.count, self.side = QSpinBox(), QComboBox()
        self.count.setRange(1, 576)
        self.count.setValue(6)
        self.side.addItem('B end', 'B')
        self.side.addItem('A end', 'A')
        form.addRow('Consecutive fibers from the chosen one', self.count)
        form.addRow('Free cable end to divert', self.side)
        label = QLabel(
            '1. Click inside the first fiber where the diversion should '
            'begin, away from its endpoints.\n'
            '2. Move toward the chosen A/B end and off to one side of the '
            'cable. Click to place the first diverted end.\n\n'
            'A = drawing start; B = drawing finish. Ends must be free; '
            'existing cuts must remain on the retained part.\n\n'
            'Use Join / Group join to connect the diverted ends. To return '
            'to the original path, select the fiber or cable '
            'and choose Branch > Restore route. Restore any cuts first.'
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        dialog_buttons(self, layout)


class SlackDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout, form = dialog_form(self, 'Cable slack')
        self.length = QDoubleSpinBox()
        self.length.setRange(0.5, 10000000)
        self.length.setValue(15)
        self.length.setSuffix(' m')
        self.orientation = QComboBox()
        for angle, title in (
            (0, 'Horizontal'),
            (90, 'Vertical up'),
            (270, 'Vertical down'),
        ):
            self.orientation.addItem(title, angle)
        self.associate = QCheckBox('Associate with the selected cable')
        form.addRow('Length', self.length)
        form.addRow('Orientation', self.orientation)
        form.addRow(self.associate)
        label = QLabel(
            'The CAD annotation stores whole metres; fractional values are '
            'rounded, with the requested value retained.'
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        dialog_buttons(self, layout)
