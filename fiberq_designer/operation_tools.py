"""Canvas commands for optical editing and native active equipment."""

from dataclasses import replace
import math

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle
from qgis.PyQt.QtWidgets import QDialog, QInputDialog

from .i18n import ui_message, translate
from .active_layout import ActiveProperties, active_layout, active_properties
from .compat import enum_compat
from .geometry import odf_properties, pt
from .fiber_model import PURPOSES
from .operation_dialogs import (
    ActiveDialog,
    FiberDataDialog,
    GroupJoinDialog,
    BranchDialog,
    SlackDialog,
)


OPERATION_MODES = {
    'active_place',
    'cut_fiber',
    'cut_window',
    'mark_fiber',
    'mark_window',
    'through_fiber',
    'fiber_data',
    'fiber_purpose',
    'join_fiber',
    'group_joint',
    'branch_fiber',
    'slack_place',
}


class OperationTools:
    def optical_hit(self, click):
        point = QgsGeometry.fromPointXY(click)
        matches = [
            (feature.geometry().distance(point), priority, key, feature)
            for priority, (key, layer) in enumerate(
                (
                    ('fiber_joints', self.scene.optical_lines),
                    ('links', self.scene.links),
                    ('fibers', self.scene.fibers),
                )
            )
            for feature in layer.getFeatures()
        ]
        hit = min(matches, key=lambda item: (item[0], item[1]), default=None)
        if hit is None or hit[0] > self.tolerance(click, 8):
            raise ValueError(
                'Click a visible fiber section or joined connection.'
            )
        return hit[2], hit[3]

    def fiber_hit(self, click):
        point = QgsGeometry.fromPointXY(click)
        matches = [
            (f.geometry().distance(point), f)
            for f in self.scene.fibers.getFeatures()
        ]
        if not matches:
            raise ValueError('Click an individual visible fiber section.')
        distance, feature = min(matches, key=lambda item: item[0])
        if distance > self.tolerance(click, 8):
            raise ValueError('Click closer to an individual fiber line.')
        return feature

    def node_title(self, identity, node):
        if node['kind'] == 'port':
            return (
                self.service.port_address(
                    self.repository.find('ports', identity)
                )
                + ' · port'
            )
        fiber = self.repository.find('fibers', node['fiber_id'])
        cable = self.repository.find('cables', fiber['cable_id'])
        side = node.get('side') or (
            'cut left'
            if any(
                c['left_id'] == identity
                for c in self.repository.features('fiber_cuts')
            )
            else 'cut right'
        )
        return ui_message(
            self,
            '{} / fiber {} · {} end',
            cable['name'],
            fiber['fiber_no'],
            translate(side, self.workspace_values['language']),
        )

    def pick_optical_node(self, click, free=True, ask=True):
        nodes = (
            self.service.free_optical_nodes()
            if free
            else self.service.optical_nodes()
        )
        if (
            free
            and self.mode in ('join_fiber', 'group_joint')
            and isinstance(self.pending, str)
        ):
            nodes.pop(self.pending, None)
        ordered = sorted(
            (
                (n['point'].distance(click), identity, n)
                for identity, n in nodes.items()
            ),
            key=lambda item: item[0],
        )
        settings = getattr(
            self, 'workspace_values', {'snap': True, 'snap_pixels': 10}
        )
        pixels = settings['snap_pixels'] if settings['snap'] else 3
        ordered = [
            item
            for item in ordered
            if item[0] <= self.tolerance(click, pixels)
        ]
        if not ordered:
            raise ValueError(
                'Click a free cable end, cut end or equipment port. Cut a '
                'continuous fiber before connecting its middle.'
            )
        near = [
            item
            for item in ordered
            if item[0] <= ordered[0][0] + self.tolerance(click, 1)
        ]
        if ask and len(near) > 1:
            titles = [
                self.node_title(identity, node)
                for distance, identity, node in near
            ]
            title, ok = QInputDialog.getItem(
                self,
                'Choose optical endpoint',
                'Coincident endpoints',
                titles,
                0,
                False,
            )
            if not ok:
                return None
            return near[titles.index(title)][1]
        return ordered[0][1]

    def preview_endpoint_join(self, click, free=True):
        if self.join_nodes_cache is None:
            self.join_nodes_cache = (
                self.service.free_optical_nodes()
                if free
                else self.service.optical_nodes()
            )
        candidates = [
            (n['point'].distance(click), identity, n)
            for identity, n in self.join_nodes_cache.items()
            if identity != self.pending
        ]
        nearest = min(candidates, key=lambda item: item[0], default=None)
        pixels = (
            self.workspace_values['snap_pixels']
            if self.workspace_values['snap']
            else 3
        )
        target = (
            nearest
            if nearest and nearest[0] <= self.tolerance(click, pixels)
            else None
        )
        paths = []
        marker = target[2]['point'] if target else None
        if (
            isinstance(self.pending, str)
            and self.pending in self.join_nodes_cache
        ):
            first = self.join_nodes_cache[self.pending]['point']
            if target:
                try:
                    geometry, dot, closure = self.service._joint_geometry(
                        first, marker
                    )
                    paths = [geometry.asPolyline()]
                except ValueError:
                    self.click_tool.show_bundle(
                        [[first, marker]], valid=False, snap=marker
                    )
                    return
            else:
                paths = [[first, click]]
        self.click_tool.show_bundle(paths, snap=marker)
        if target:
            message = 'Snap: ' + self.node_title(target[1], target[2])
            self.status.setText(message)
            self.click_tool.snap_marker.setToolTip(message)
        elif free:
            point = QgsGeometry.fromPointXY(click)
            nearby = [
                m
                for m in self.scene.optical_marks.getFeatures()
                if m['record_key'] != 'annotations'
                and m.geometry().distance(point) <= self.tolerance(click, 6)
            ]
            if nearby:
                self.status.setText(
                    'Occupied/continuous splice point. Select and disconnect '
                    'its joint, or Cut the continuous fiber to expose free '
                    'ends.'
                )

    def node_angle(self, identity, node):
        if node['kind'] == 'port':
            element = self.repository.find('elements', node['obj_id'])
            housing = self.repository.find('housings', node['obj_id'])
            active = self.repository.find('active_equipment', node['obj_id'])
            if housing is not None:
                props = odf_properties(element, housing)
                return (
                    props.orientation + (180 if props.mirrored else 0)
                ) % 360
            return float(active['orientation']) if active is not None else 0
        path = self.scene.drawn_fibers[node['fiber_id']]
        context = path.closestSegmentWithContext(node['point'])
        a, b = path.asPolyline()[context[2] - 1: context[2] + 1]
        angle = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))
        reverse = node.get('side') == 'A' or any(
            c['right_id'] == identity
            for c in self.repository.features('fiber_cuts')
        )
        return (angle + (180 if reverse else 0)) % 360

    def create_active(self, kind='SPLITTER', count=None):
        if not self.ensure_ready():
            return
        self.stop_tool()
        saved = self.active_defaults.get(
            kind,
            ActiveProperties(
                kind=kind,
                count=8 if kind == 'SPLITTER' else 16 if kind == 'OLT' else 1,
            ),
        )
        if count is not None:
            saved = replace(saved, count=count)
        dialog = ActiveDialog(self, saved)
        dialog.snap.setChecked(kind != 'OLT' and self.workspace_values['snap'])
        self.active_dialog = dialog
        if dialog.exec() != enum_compat(QDialog, 'Accepted', 'DialogCode'):
            return
        props = dialog.properties()
        self.active_defaults[kind] = props
        self.activate('active_place')
        self.operation_placement = dict(
            properties=props,
            label=dialog.label.text(),
            snap=dialog.snap.isChecked(),
        )
        self.operation_nodes = self.service.free_optical_nodes()
        self.note(
            ui_message(
                self,
                'Place {} by its connector. {}Esc cancels.',
                translate(kind, self.workspace_values['language']),
                translate(
                    'Press I to switch input/first-output anchor. ',
                    self.workspace_values['language'],
                )
                if kind == 'SPLITTER'
                else '',
            )
        )

    def active_candidate(self, click):
        props = self.operation_placement['properties']
        identity, marker = None, None
        role = (
            'input'
            if props.kind == 'ONT'
            or (props.kind == 'SPLITTER' and props.anchor == 'input')
            else 'output'
        )
        if self.operation_placement['snap']:
            matches = [
                (node['point'].distance(click), key, node)
                for key, node in self.operation_nodes.items()
                if not (node['role'] == role and role in ('input', 'output'))
            ]
            if matches:
                distance, key, node = min(matches, key=lambda item: item[0])
                if distance <= self.tolerance(
                    click, self.workspace_values['snap_pixels']
                ):
                    angle = self.node_angle(key, node)
                    if any(
                        abs(angle - supported) < 1e-5
                        for supported in (0, 90, 180, 270, 360)
                    ):
                        props = replace(
                            props,
                            orientation=min(
                                (0, 90, 180, 270),
                                key=lambda v: abs(
                                    (v - angle + 180) % 360 - 180
                                ),
                            ),
                        )
                        click, identity, marker = (
                            node['point'],
                            key,
                            node['point'],
                        )
        return props, click, identity, marker

    def preview_active(self, click):
        props, at, node, marker = self.active_candidate(click)
        layout = active_layout(props, self.operation_placement['label'])
        theta = math.radians(props.orientation)

        def world(p):
            return pt(
                at.x()
                + 10 * (p[0] * math.cos(theta) - p[1] * math.sin(theta)),
                at.y()
                + 10 * (p[0] * math.sin(theta) + p[1] * math.cos(theta)),
            )

        reference = (
            0 if props.kind != 'SPLITTER' or props.anchor == 'input' else 1
        )
        paths = [layout.paths[reference], layout.frame] + [
            p for i, p in enumerate(layout.paths) if i != reference
        ]
        self.click_tool.show_bundle(
            [[world(p) for p in path] for path in paths], snap=marker
        )

    def toggle_active_anchor(self):
        if self.operation_placement and self.mode == 'active_place':
            props = self.operation_placement['properties']
            if props.kind == 'SPLITTER':
                self.operation_placement['properties'] = replace(
                    props,
                    anchor='input' if props.anchor == 'output' else 'output',
                )
                self.note(
                    'Splitter anchor: '
                    + self.operation_placement['properties'].anchor
                )
                if self.operation_last_point is not None:
                    self.preview_active(self.operation_last_point)

    def edit_active_dialog(self, element, record):
        dialog = ActiveDialog(
            self,
            active_properties(element, record),
            element['name'],
            editing=True,
        )
        self.active_dialog = dialog
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.service.edit_active(
                element['obj_id'], dialog.label.text(), dialog.properties()
            )
            self.refresh()
            self.note(
                'Active equipment saved; retained port identities and '
                'connections kept.'
            )

    def selected_fibers(self):
        if self.selected and self.selected[0] == 'fibers':
            return [self.selected[1]]
        if self.selected and self.selected[0] == 'cables':
            return [
                f['fiber_id']
                for f in self.repository.features('fibers')
                if f['cable_id'] == self.selected[1]
            ]
        return []

    def purpose_starts(self):
        if not self.selected:
            return []
        key, identity = self.selected
        if key == 'cables':
            fibers = {
                f['fiber_id']
                for f in self.repository.features('fibers')
                if f['cable_id'] == identity
            }
            return [
                s['a_id']
                for s in self.scene.fiber_sections.values()
                if s['fiber_id'] in fibers
            ]
        if self.selection_node is not None and key in (
            'fibers',
            'fiber_joints',
            'fiber_marks',
            'ports',
            'links',
        ):
            return [self.selection_node]
        if key == 'fibers':
            return [
                next(
                    s['a_id']
                    for s in self.scene.fiber_sections.values()
                    if s['fiber_id'] == identity
                )
            ]
        return []

    def show_purpose_colors(self):
        self.scene.highlight(set())
        for layer in self.scene.layers():
            layer.removeSelection()
        self.canvas.refresh()

    def edit_fiber_data(self):
        if not self.ensure_ready():
            return
        starts = self.purpose_starts()
        if not starts:
            self.activate('fiber_data')
            return
        identities = sorted(
            {s['fiber_id'] for s in self.service.path_sections(starts)}
        )
        self.stop_tool()
        dialog = FiberDataDialog(
            self, ui_message(self, '{} connected fiber(s)', len(identities))
        )
        self.fiber_data_dialog = dialog
        descriptions = {
            self.repository.find('fibers', identity)['description']
            for identity in identities
        }
        if len(descriptions) == 1:
            dialog.description.setText(next(iter(descriptions)))
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            with self.repository.command('Edit connected fiber data'):
                if dialog.purpose.currentData() is not None:
                    self.service.set_path_purpose(
                        starts, dialog.purpose.currentData()
                    )
                if (
                    dialog.fiber_type.currentData() is not None
                    or dialog.replace_description.isChecked()
                ):
                    self.service.set_fiber_data(
                        identities,
                        None,
                        dialog.fiber_type.currentData(),
                        dialog.description.text()
                        if dialog.replace_description.isChecked()
                        else None,
                    )
            self.refresh()
            self.show_purpose_colors()
            self.note(
                'Fiber data saved. Purpose and splice/through continuity '
                'remain separate.'
            )

    def set_purpose(self, code):
        if not self.ensure_ready():
            return
        if self.mode == 'fiber_purpose':
            self.operation_options['purpose'] = code
        starts = self.purpose_starts()
        if starts:
            self.service.set_path_purpose(starts, code)
            self.refresh()
            self.show_purpose_colors()
            self.note('Purpose: ' + PURPOSES[code].title)
        else:
            self.activate('fiber_purpose')
            self.operation_options = {'purpose': code}
            self.note(
                ui_message(
                    self,
                    'Click a fiber or join to set path purpose: {}. '
                    'Esc stops.',
                    translate(
                        PURPOSES[code].title, self.workspace_values['language']
                    ),
                )
            )

    def start_joint(self, kind='splice', grouped=False, manual=False):
        self.activate('group_joint' if grouped else 'join_fiber')
        self.operation_options = {'kind': kind, 'manual': manual}
        self.join_nodes_cache = None

    def start_branch(self):
        if not self.ensure_ready():
            return
        self.stop_tool()
        dialog = BranchDialog(self)
        self.branch_dialog = dialog
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.activate('branch_fiber')
            self.operation_options = dict(
                count=dialog.count.value(), side=dialog.side.currentData()
            )

    def start_slack(self):
        if not self.ensure_ready():
            return
        cable_id = (
            self.selected[1]
            if self.selected and self.selected[0] == 'cables'
            else None
        )
        if self.selected and self.selected[0] == 'fibers':
            cable_id = self.repository.find('fibers', self.selected[1])[
                'cable_id'
            ]
        self.stop_tool()
        dialog = SlackDialog(self)
        self.slack_dialog = dialog
        dialog.associate.setEnabled(cable_id is not None)
        dialog.associate.setChecked(cable_id is not None)
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.activate('slack_place')
            self.operation_options = dict(
                length=dialog.length.value(),
                orientation=dialog.orientation.currentData(),
                cable_id=cable_id if dialog.associate.isChecked() else None,
            )

    def reset_selected_branch(self):
        identities = self.selected_fibers()
        if not identities:
            self.note('Select a fiber or cable to restore its original route.')
            return
        identities = [
            identity
            for identity in identities
            if self.repository.find('fiber_routes', identity) is not None
        ]
        if not identities:
            self.note(
                'The selected fiber or cable already follows its original '
                'route.'
            )
            return
        with self.repository.command('Restore original fiber routes'):
            for identity in identities:
                self.service.clear_branch(identity)
        self.refresh()
        self.note(
            ui_message(
                self,
                'Restored {} diverted fiber(s) to the original cable path.',
                len(identities),
            )
        )

    def node_group(self, identity):
        nodes = self.service.optical_nodes()
        seed = nodes[identity]
        if seed['kind'] == 'port':
            values = [
                p['port_id'] for p in self.service.ports_of(seed['obj_id'])
            ]
        elif seed['kind'] == 'fiber_ends':
            fiber = self.repository.find('fibers', seed['fiber_id'])
            values = [
                e['end_id']
                for e in self.service.ends_of(fiber['cable_id'], seed['side'])
            ]
        else:
            cut = next(
                c
                for c in self.repository.features('fiber_cuts')
                if identity in (c['left_id'], c['right_id'])
            )
            field = 'left_id' if cut['left_id'] == identity else 'right_id'
            fiber = self.repository.find('fibers', seed['fiber_id'])
            fibers = {
                f['fiber_id']: int(f['fiber_no'])
                for f in self.repository.features('fibers')
                if f['cable_id'] == fiber['cable_id']
            }
            theta = math.radians(self.node_angle(identity, seed))

            def along(p):
                return p.x() * math.cos(theta) + p.y() * math.sin(theta)

            values = [
                c[field]
                for c in sorted(
                    self.repository.features('fiber_cuts'),
                    key=lambda c: fibers.get(c['fiber_id'], 100000),
                )
                if c['fiber_id'] in fibers
                and (
                    c['group_id'] == cut['group_id']
                    or abs(
                        along(nodes[c[field]]['point']) - along(seed['point'])
                    )
                    < 1e-4
                )
            ]
        return values, values.index(identity)

    def operation_click(self, click):
        if self.mode == 'active_place':
            props, at, node, marker = self.active_candidate(click)
            identity = self.service.add_active(
                self.operation_placement['label'], props, at, node
            )
            self.stop_tool()
            self.selected = ('elements', identity)
            self.refresh()
            self.note(
                ui_message(
                    self,
                    '{} saved{}.',
                    translate(props.kind, self.workspace_values['language']),
                    translate(
                        ' and connected to the snapped endpoint',
                        self.workspace_values['language'],
                    )
                    if node
                    else '',
                )
            )
        elif self.mode in ('cut_window', 'mark_window'):
            if self.pending is None:
                self.pending = QgsPointXY(click)
                self.note(
                    'Choose the opposite corner of the fiber selection window.'
                )
                return
            rectangle = QgsRectangle(self.pending, click)
            area = QgsGeometry.fromRect(rectangle)
            identities = list(
                dict.fromkeys(
                    f['fiber_id']
                    for f in self.scene.fibers.getFeatures()
                    if f.geometry().intersects(area)
                )
            )
            origin = self.pending
            if self.mode == 'cut_window':
                joints = [
                    j['joint_id']
                    for j in self.scene.optical_lines.getFeatures()
                    if j.geometry().intersects(area)
                ]
                links = [
                    link_record['link_id']
                    for link_record in self.scene.links.getFeatures()
                    if link_record.geometry().intersects(area)
                ]
                count = self.service.cut_optical(
                    identities, origin, joints, links, window=area
                )
            else:
                if not identities:
                    raise ValueError(
                        'No visible fibers intersect this window.'
                    )
                self.service.add_splice_marks(identities, origin)
            self.pending = None
            self.click_tool.clear()
            self.refresh()
            self.note(
                ui_message(
                    self, '{} cut(s) saved as one undoable operation.', count
                )
                if self.mode == 'cut_window'
                else ui_message(
                    self,
                    '{} fiber(s) updated as one undoable operation.',
                    len(identities),
                )
            )
        elif self.mode == 'cut_fiber':
            key, hit = self.optical_hit(click)
            self.service.cut_optical(
                [hit['fiber_id']] if key == 'fibers' else [],
                click,
                [hit['joint_id']] if key == 'fiber_joints' else [],
                [hit['link_id']] if key == 'links' else [],
            )
            self.refresh()
            self.note(
                'Cut saved. Click another fiber or joined connection, or '
                'press Esc.'
            )
        elif self.mode in ('fiber_data', 'fiber_purpose'):
            key, hit = self.optical_hit(click)
            field = {
                'fibers': 'fiber_id',
                'fiber_joints': 'joint_id',
                'links': 'link_id',
            }[key]
            self.select(
                key,
                hit[field],
                start_node=hit['a_node'] if key == 'fibers' else None,
            )
            if self.mode == 'fiber_data':
                self.edit_fiber_data()
                return
            self.service.set_path_purpose(
                self.purpose_starts(), self.operation_options['purpose']
            )
            self.refresh()
            self.show_purpose_colors()
            self.note(
                'Fiber path purpose saved. Continue selecting paths or press '
                'Esc.'
            )
        elif self.mode in ('mark_fiber', 'through_fiber'):
            hit = self.fiber_hit(click)
            identity = hit['fiber_id']
            if self.mode == 'mark_fiber':
                self.service.add_splice_marks([identity], click)
            elif self.mode == 'through_fiber':
                self.service.restore_through(identity, click)
            self.refresh()
            self.note(
                'Fiber operation saved. Continue selecting fibers or press '
                'Esc.'
            )
        elif self.mode in ('join_fiber', 'group_joint'):
            if self.mode == 'join_fiber' and isinstance(self.pending, tuple):
                first, second = self.pending
                self.service.join_nodes(
                    first,
                    second,
                    self.operation_options.get('kind', 'splice'),
                    point=click,
                )
                self.pending = None
                self.refresh()
                self.note(
                    'Connection saved at the chosen column. Choose another '
                    'first endpoint or press Esc.'
                )
                return
            node = self.pick_optical_node(click)
            if node is None:
                return
            if self.pending is None:
                self.pending = node
                self.note('Choose the second free endpoint or port.')
                return
            first = self.pending
            if self.mode == 'group_joint':
                dialog = GroupJoinDialog(self)
                self.group_dialog = dialog
                if dialog.exec() != enum_compat(
                    QDialog, 'Accepted', 'DialogCode'
                ):
                    self.pending = None
                    return
                left, a = self.node_group(first)
                right, b = self.node_group(node)
                count, step = dialog.count.value(), dialog.step.value()
                if a + count > len(left) or any(
                    not 0 <= b + i * step < len(right) for i in range(count)
                ):
                    raise ValueError(
                        'The group exceeds the available source/destination '
                        'numbering range.'
                    )
                self.service.join_group(
                    left[a: a + count],
                    [right[b + i * step] for i in range(count)],
                    dialog.kind.currentData(),
                )
            else:
                if self.operation_options.get('manual'):
                    self.pending = (first, node)
                    self.note(
                        'Click the splice/drop column. Esc cancels without '
                        'saving a connection.'
                    )
                    return
                self.service.join_nodes(
                    first, node, self.operation_options.get('kind', 'splice')
                )
            self.pending = None
            self.refresh()
            self.note(
                'Connection saved. Choose another first endpoint or press Esc.'
            )
        elif self.mode == 'branch_fiber':
            if self.pending is None:
                hit = self.fiber_hit(click)
                fiber = self.repository.find('fibers', hit['fiber_id'])
                count = self.operation_options['count']
                identities = [
                    f['fiber_id']
                    for f in sorted(
                        self.repository.features('fibers'),
                        key=lambda f: int(f['fiber_no']),
                    )
                    if f['cable_id'] == fiber['cable_id']
                    and int(fiber['fiber_no'])
                    <= int(f['fiber_no'])
                    < int(fiber['fiber_no']) + count
                ]
                if len(identities) != count:
                    raise ValueError(
                        'Not enough consecutive fibers from the chosen one.'
                    )
                self.pending = (identities, QgsPointXY(click))
                self.note(
                    'Place the first diverted end ahead in the chosen A/B '
                    'end direction and off to one side of the cable. Esc '
                    'cancels.'
                )
            else:
                identities, origin = self.pending
                self.service.branch_fibers(
                    identities, click, self.operation_options['side'], origin
                )
                self.stop_tool()
                self.refresh()
                self.note(
                    'Fiber ends diverted. Use Join or Group join to connect '
                    'them; Branch > Restore route returns selected fibers to '
                    'the cable path.'
                )
        elif self.mode == 'slack_place':
            options = self.operation_options
            identity = self.service.add_slack(
                click,
                options['length'],
                options['cable_id'],
                options['orientation'],
            )
            self.stop_tool()
            self.selected = ('slack', identity)
            self.refresh()
            self.note(
                'Slack stored in whole metres; requested fractional value '
                'retained.'
            )

    def operation_move(self, click):
        self.operation_last_point = QgsPointXY(click)
        if (
            self.mode == 'active_place'
            and self.operation_placement is not None
        ):
            self.preview_active(click)
        elif (
            self.mode in ('cut_window', 'mark_window')
            and self.pending is not None
        ):
            a = self.pending
            self.click_tool.show_route(
                [
                    a,
                    pt(click.x(), a.y()),
                    click,
                    pt(a.x(), click.y()),
                    a,
                ]
            )
        elif self.mode == 'slack_place':
            theta = math.radians(self.operation_options['orientation'])
            paths = [
                [
                    pt(
                        click.x()
                        + (6 * i + 12 * math.cos(t)) * math.cos(theta)
                        - 7 * math.sin(t) * math.sin(theta),
                        click.y()
                        + (6 * i + 12 * math.cos(t)) * math.sin(theta)
                        + 7 * math.sin(t) * math.cos(theta),
                    )
                    for t in (n * math.tau / 40 for n in range(41))
                ]
                for i in range(3)
            ]
            self.click_tool.show_bundle(paths)
        elif self.mode == 'branch_fiber' and self.pending is not None:
            identities, origin = self.pending
            try:
                paths = self.service.branch_fibers(
                    identities,
                    click,
                    self.operation_options['side'],
                    origin,
                    preview=True,
                    scene=self.scene,
                )
                self.click_tool.show_bundle(
                    [path.asPolyline() for path in paths]
                )
            except ValueError:
                self.click_tool.show_bundle([[origin, click]], valid=False)
        elif self.mode == 'join_fiber' and isinstance(self.pending, tuple):
            nodes = self.service.optical_nodes()
            geometry, marker, closure = self.service._joint_geometry(
                nodes[self.pending[0]]['point'],
                nodes[self.pending[1]]['point'],
                manual=click,
            )
            self.click_tool.show_bundle([geometry.asPolyline()])
        elif self.mode in ('join_fiber', 'group_joint'):
            self.preview_endpoint_join(click)

    def select_operation_at(self, click):
        point = QgsGeometry.fromPointXY(click)
        for layer, field, key in (
            (self.scene.optical_marks, 'record_id', None),
            (self.scene.optical_lines, 'joint_id', 'fiber_joints'),
        ):
            matches = [
                (f.geometry().distance(point), f) for f in layer.getFeatures()
            ]
            if matches:
                distance, feature = min(matches, key=lambda item: item[0])
                if distance <= self.tolerance(click, 6):
                    self.select(key or feature['record_key'], feature[field])
                    return True
        for (key, identity), shape in self.scene.operation_hits.items():
            if shape.distance(point) <= self.tolerance(click, 3):
                self.select(key, identity)
                return True
        return False

    def operation_details(self, key, feature):
        if key == 'slack':
            return ui_message(
                self,
                'Slack: {} m\nRequested: {} m · {}',
                feature['length_m'],
                feature['requested_m'],
                translate(
                    feature['status'], self.workspace_values['language']
                ),
            )
        if key == 'fiber_marks':
            fiber = self.repository.find('fibers', feature['fiber_id'])
            return ui_message(
                self,
                'Fusion splice point · fiber {}\n{} dB\nPurpose is unchanged.',
                fiber['fiber_no'],
                feature['loss_db'],
            )
        nodes = self.service.optical_nodes()
        return ui_message(
            self,
            '{}\n{} ↔ {}\n{} dB',
            translate(
                feature['kind'], self.workspace_values['language']
            ).capitalize(),
            self.node_title(feature['a_id'], nodes[feature['a_id']]),
            self.node_title(feature['b_id'], nodes[feature['b_id']]),
            feature['loss_db'],
        )

    def highlight_optical_path(self, start):
        """Highlight connected visible sections without changing the editing
        target."""
        found = self.service.trace(start)
        spans = [
            s
            for s in self.scene.fiber_sections.values()
            if s['a_id'] in found and s['b_id'] in found
        ]
        identities = {s['fiber_id'] for s in spans}
        links = {
            link_record['link_id']
            for link_record in self.repository.features('links')
            if link_record['from_id'] in found
            and link_record['to_id'] in found
        }
        joints = {
            j['joint_id']
            for j in self.repository.features('fiber_joints')
            if j['a_id'] in found and j['b_id'] in found
        }
        self.scene.highlight(
            identities, links, {s['span_id'] for s in spans}, joints
        )
        # QGIS's local yellow line selection otherwise masks part of the red
        # path.
        self.scene.optical_lines.removeSelection()
        self.scene.links.removeSelection()
        self.scene.ports.selectByIds(
            [
                p.id()
                for p in self.scene.ports.getFeatures()
                if p['port_id'] in found
            ]
        )
        cables = {
            f['cable_id']
            for f in self.repository.features('fibers')
            if f['fiber_id'] in identities
        }
        count = sum(
            p['port_id'] in found for p in self.repository.features('ports')
        )
        summary = ui_message(
            self,
            'Trace: {} ports, {} cable routes, {} continuous sections. '
            'Splitter branches are included.',
            count,
            len(cables),
            len(spans),
        )
        return dict(
            nodes=found,
            links=links,
            joints=joints,
            cables=cables,
            summary=summary,
        )

    def highlight_selected_path(self, key, feature, start=None):
        if key in ('fibers', 'fiber_marks'):
            identity = feature['fiber_id']
            spans = [
                s
                for s in self.scene.fiber_sections.values()
                if s['fiber_id'] == identity
                and (
                    key == 'fibers'
                    or s['start'] <= float(feature['at']) <= s['end']
                )
            ]
            available = {s[field] for s in spans for field in ('a_id', 'b_id')}
            start = start if start in available else spans[0]['a_id']
        elif key == 'fiber_joints':
            start = feature['a_id']
        elif key == 'ports':
            start = feature['port_id']
        elif key == 'links':
            start = feature['from_id']
        else:
            return ''
        self.selection_node = start
        return self.highlight_optical_path(start)['summary']

    def trace_optical_at(self, click):
        try:
            hit = self.fiber_hit(click)
        except ValueError:
            hit = None
        # A port/cut-end click selects that precise optical node; otherwise use
        # the actual visible span, never the hidden far side of a cut fiber.
        nodes = self.service.optical_nodes()
        nearest = min(
            (
                (n['point'].distance(click), identity)
                for identity, n in nodes.items()
            ),
            default=(float('inf'), None),
        )
        line_distance = (
            hit.geometry().distance(QgsGeometry.fromPointXY(click))
            if hit is not None
            else float('inf')
        )
        start = (
            nearest[1]
            if nearest[0]
            <= min(self.tolerance(click, 5), line_distance + 1e-6)
            else (hit['a_node'] if hit is not None else None)
        )
        joint = min(
            (
                (j.geometry().distance(QgsGeometry.fromPointXY(click)), j)
                for j in self.repository.features('fiber_joints')
            ),
            key=lambda item: item[0],
            default=None,
        )
        if joint and joint[0] <= min(
            self.tolerance(click, 6), line_distance + 1e-6, nearest[0] + 1e-6
        ):
            start = joint[1]['a_id']
        if start is None:
            self.note(
                'Click a visible fiber section or an optical endpoint/port.'
            )
            return
        self.clear_selection()
        self.selected = None
        path = self.highlight_optical_path(start)
        self.layers['ports'].selectByIds(
            [
                p.id()
                for p in self.repository.features('ports')
                if p['port_id'] in path['nodes']
            ]
        )
        self.layers['links'].selectByIds(
            [
                link_record.id()
                for link_record in self.repository.features('links')
                if link_record['link_id'] in path['links']
            ]
        )
        self.layers['fiber_joints'].selectByIds(
            [
                j.id()
                for j in self.repository.features('fiber_joints')
                if j['joint_id'] in path['joints']
            ]
        )
        self.layers['cables'].selectByIds(
            [
                c.id()
                for c in self.repository.features('cables')
                if c['cable_id'] in path['cables']
            ]
        )
        self.details.setText(path['summary'])
        self.note('Connected sections highlighted; open cuts stop continuity.')
        self.update_command_state()
