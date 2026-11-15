"""Reports/settings commands, annotation placement and scoped UI language."""

import json
from datetime import datetime, timezone

from qgis.PyQt.QtCore import QObject, QEvent
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QWidget,
    QMenu,
)

from .i18n import ui_message
from .annotation_layout import (
    legend_payload,
    table_payload,
    legend_primitives,
    table_primitives,
)
from .compat import enum_compat
from .geometry import pt
from .i18n import localize_tree, translate
from .report_dialogs import BudgetDialog, ReportDialog, SettingsDialog
from .workspace_model import DEFAULT_SETTINGS


class LanguageFilter(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.localizing = False

    def eventFilter(self, obj, event):
        if self.localizing:
            return False
        shown = event.type() == enum_compat(
            QEvent, 'Show', 'Type'
        ) and isinstance(obj, (QDialog, QMenu))
        painted = event.type() == enum_compat(
            QEvent, 'Paint', 'Type'
        ) and isinstance(obj, QWidget)
        if shown or painted:
            parent = obj
            roots = (self.editor, getattr(self.editor, 'inspector', None))
            while parent is not None and parent not in roots:
                # Qt/native file pickers own their controls and event loops.
                # Their FiberQ parent does not make those internals our UI.
                if isinstance(parent, QFileDialog):
                    return False
                parent = parent.parent()
            if parent is not None:
                self.localizing = True
                try:
                    localize_tree(
                        obj,
                        self.editor.workspace_values['language'],
                        recursive=shown,
                    )
                finally:
                    self.localizing = False
        return False


class WorkspaceTools:
    def setup_workspace_ui(self):
        self.workspace_values = dict(DEFAULT_SETTINGS)
        self.annotation_placement = None
        self.join_nodes_cache = None
        self.language_filter = LanguageFilter(self)
        QApplication.instance().installEventFilter(self.language_filter)

    def apply_workspace_settings(self):
        self.workspace_values = (
            self.service.settings() if self.service else dict(DEFAULT_SETTINGS)
        )
        self.join_nodes_cache = None
        if hasattr(self, 'join_snap_action'):
            self.join_snap_action.blockSignals(True)
            self.join_snap_action.setChecked(self.workspace_values['snap'])
            self.join_snap_action.blockSignals(False)
        localize_tree(self, self.workspace_values['language'])
        localize_tree(self.inspector, self.workspace_values['language'])
        self.catalog_dialog.configure_settings(
            self.workspace_values, reset_defaults=True
        )

    def edit_workspace_settings(self):
        if not self.ensure_ready():
            return
        self.stop_tool()
        dialog = SettingsDialog(self, self.service.settings())
        self.settings_dialog = dialog
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.service.save_settings(dialog.values())
            self.apply_workspace_settings()
            self.catalog_dialog.configure_settings(self.workspace_values)
            self.refresh()
            self.note(
                'Workspace settings saved. Existing drawings retain their '
                'saved language and cable definitions.'
            )

    def toggle_join_snap(self, checked):
        if self.service is not None:
            values = self.service.settings()
            values['snap'] = checked
            self.service.save_settings(values)
            self.apply_workspace_settings()
            self.note(
                'Endpoint snapping enabled.'
                if checked
                else 'Endpoint snapping disabled; click within 3 pixels of '
                'an exact endpoint.'
            )

    def show_report(self, kind, report=None):
        if not self.ensure_ready():
            return
        self.stop_tool()
        if kind == 'bom':
            report = self.service.bill_of_materials()
        elif kind == 'checks':
            report = self.service.network_checks()
        elif kind == 'route' and report is None:
            self.activate('report_route')
            return
        if not report['rows']:
            self.note('No report items in this workspace.')
            return
        dialog = ReportDialog(self, report, self.workspace_values['language'])
        self.report_dialog = dialog
        if (
            dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode')
            and dialog.place
        ):
            self.start_annotation(
                kind, table_payload(report, self.workspace_values['language'])
            )

    def show_budget(self):
        if not self.ensure_ready():
            return
        self.stop_tool()
        start, destination = None, None
        if self.selected and self.selected[0] == 'fibers':
            ends = {
                e['side']: e['end_id']
                for e in self.repository.features('fiber_ends')
                if e['fiber_id'] == self.selected[1]
            }
            start, destination = ends['A'], ends['B']
        dialog = BudgetDialog(self, self.service, start, destination)
        self.budget_dialog = dialog
        if (
            dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode')
            and dialog.place
        ):
            self.start_annotation(
                'budget',
                table_payload(
                    dialog.result, self.workspace_values['language']
                ),
            )

    def start_legend(self):
        if self.ensure_ready():
            self.start_annotation(
                'legend', legend_payload(self.service.settings())
            )

    def start_annotation(self, kind, payload):
        self.activate('annotation_place')
        if kind != 'legend':
            payload = dict(
                payload,
                source_hash=self.service.report_signature(),
                created_at=datetime.now(timezone.utc).strftime(
                    '%Y-%m-%d %H:%M UTC'
                ),
            )
        self.annotation_placement = kind, payload
        self.note(
            'Choose the table/legend insertion point. Esc cancels without '
            'saving.'
        )

    def workspace_click(self, click):
        if self.mode == 'annotation_place':
            kind, payload = self.annotation_placement
            identity = self.service.add_annotation(kind, payload, click)
            self.stop_tool()
            self.selected = ('annotations', identity)
            self.refresh()
            self.note(
                'Drawing annotation saved as a dated design snapshot. '
                'Recreate reports after network changes.'
            )
        elif self.mode == 'report_route':
            try:
                node = self.pick_optical_node(click, free=False)
            except ValueError:
                node = self.fiber_hit(click)['a_node']
            if node is None:
                return
            if self.pending is None:
                self.pending = node
                self.note(
                    'Choose the optical destination. Branches require an '
                    'explicit endpoint.'
                )
            else:
                report = self.service.route_report(self.pending, node)
                self.show_report('route', report)
        elif self.mode == 'annotation_move':
            self.service.move_annotation(self.pending, click)
            self.stop_tool()
            self.refresh()

    def workspace_move(self, click):
        if (
            self.mode == 'annotation_place'
            and self.annotation_placement is not None
        ):
            kind, payload = self.annotation_placement
            lines, texts, dots = (
                legend_primitives(payload)
                if kind == 'legend'
                else table_primitives(payload)
            )
            paths = [
                [pt(click.x() + x, click.y() + y) for x, y in points]
                for points, ink, width in lines
            ]
            if not paths:
                width = max(
                    (
                        len(value) * height * 0.65 + x
                        for x, y, value, height, bold, ink in texts
                    ),
                    default=100,
                )
                bottom = min(
                    (y for x, y, value, height, bold, ink in texts),
                    default=-40,
                )
                paths = [
                    [
                        click,
                        pt(click.x() + width, click.y()),
                        pt(click.x() + width, click.y() + bottom),
                        pt(click.x(), click.y() + bottom),
                        click,
                    ]
                ]
            self.click_tool.show_bundle(paths)
        elif self.mode == 'report_route':
            self.preview_endpoint_join(click, free=False)

    def annotation_details(self, record):
        payload = json.loads(record['payload'])
        stale = (
            payload.get('source_hash')
            and payload['source_hash'] != self.service.report_signature()
        )
        language = self.workspace_values['language']
        return ui_message(
            self,
            '{} · {}\n{}\n{}\n{}',
            translate(record['kind'], language),
            payload['language'].upper(),
            payload.get(
                'title',
                translate('Color code: ', language)
                + payload.get('color_code', ''),
            ),
            payload.get('created_at', ''),
            translate(
                'STALE REPORT — regenerate after design changes.'
                if stale
                else 'Saved drawing snapshot.',
                language,
            ),
        )

    def move_selected_annotation(self):
        if self.selected and self.selected[0] == 'annotations':
            identity = self.selected[1]
            self.activate('annotation_move')
            self.pending = identity
        else:
            self.activate('move')
