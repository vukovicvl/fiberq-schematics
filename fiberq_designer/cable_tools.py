"""Catalogue cable placement, snapping and one-shot row alignment."""

import math
from bisect import bisect_left
from dataclasses import dataclass, replace

from qgis.core import QgsPointXY
from qgis.PyQt.QtWidgets import QDialog

from .cable_catalog import (
    CABLE_VERSION,
    cable_rows,
    rows_from_record,
    properties_from_record,
)
from .cable_dialog import CableDialog
from .compat import enum_compat
from .i18n import translate
from .geometry import (
    odf_properties,
    optional,
    parallel_path,
    pt,
    validate_row_route,
)
from .housing_catalog import CAD_SCALE


@dataclass(frozen=True)
class HousingCableTarget:
    point: QgsPointXY
    angle: float
    owner: str
    module_no: int
    rows: tuple
    mirrored: bool
    primary: bool


class CableTools:
    def cable_models(self):
        saved = getattr(self, 'custom_cable_models', [])
        return list(
            dict.fromkeys(
                saved
                + [
                    (c['category'], c['model'], c['catalog_model'])
                    for c in self.repository.features('cables')
                    if c['cable_version'] == CABLE_VERSION
                ]
            )
        )

    def create_catalog_cable(self, category=None):
        if not self.ensure_ready():
            return
        self.stop_tool()
        dialog = self.catalog_dialog
        dialog.configure_settings(self.service.settings())
        dialog.custom_models = self.cable_models()
        if category is not None:
            dialog.category.setCurrentIndex(dialog.category.findData(category))
        dialog.fill_models()
        if dialog.exec() != enum_compat(QDialog, 'Accepted', 'DialogCode'):
            return
        properties = dialog.properties()
        self.custom_cable_models = self.cable_models() + [
            (properties.category, properties.model, properties.catalog_model)
        ]
        self.activate('cad_cable')
        self.catalog_placement = dict(
            properties=properties,
            angle=float(dialog.orientation.currentData()),
            length=dialog.length.value() * 10,
            drag=dialog.drag_length.isChecked(),
            bends=dialog.bends.isChecked(),
            snap=dialog.snap.isChecked(),
            widen=dialog.widen.isChecked(),
            grid=dialog.inherit_grid.isChecked(),
            name=dialog.label.text(),
            real_length=dialog.real_length.value(),
            endpoints={},
            modules={},
            reverse=False,
        )
        self.cable_targets = self.housing_cable_targets()
        language = self.workspace_values['language']
        self.note(
            translate(
                'Place the first fiber. Preview follows the mouse; Esc '
                'cancels.',
                language,
            )
            + (
                translate(' Alignment is active.', language)
                if self.cable_alignment
                else ''
            )
        )

    def housing_cable_targets(self):
        cables = {c['cable_id']: c for c in self.repository.features('cables')}
        fibers = {f['fiber_id']: f for f in self.repository.features('fibers')}
        incoming = {}
        for end in self.repository.features('fiber_ends'):
            cable = cables[fibers[end['fiber_id']]['cable_id']]
            owner = optional(
                cable['from_obj' if end['side'] == 'A' else 'to_obj']
            )
            if owner:
                incoming.setdefault(owner, []).append(end.geometry().asPoint())
        modules = {
            m['module_id']: int(m['number'])
            for m in self.repository.features('modules')
        }
        ports = self.repository.features('ports')
        elements = {
            e['obj_id']: e for e in self.repository.features('elements')
        }
        result = []
        for record in self.repository.features('housings'):
            identity = record['obj_id']
            element = elements[identity]
            props = odf_properties(element, record)
            origin = element.geometry().asPoint()
            ordered = sorted(
                (
                    (
                        p,
                        self.service.housing_local(
                            p.geometry().asPoint(), origin, props.orientation
                        )[1],
                    )
                    for p in ports
                    if p['obj_id'] == identity
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            incoming_rows = sorted(
                y
                for x, y in (
                    self.service.housing_local(
                        point, origin, props.orientation
                    )
                    for point in incoming.get(identity, [])
                )
                if abs(x) < 0.05
            )
            selected = set()
            for index, (port, row) in enumerate(ordered):
                module_no = modules[port['module_id']]
                if (
                    module_no in selected
                    or props.splitter_ratio
                    and int(port['module_port']) <= props.splitter_ratio
                ):
                    continue
                position = bisect_left(incoming_rows, row)
                if any(
                    abs(row - incoming_rows[i]) < 0.05
                    for i in (position - 1, position)
                    if 0 <= i < len(incoming_rows)
                ):
                    continue
                selected.add(module_no)
                point = port.geometry().asPoint()
                rows = tuple(
                    y * CAD_SCALE - row * CAD_SCALE for p, y in ordered[index:]
                )
                result.append(
                    HousingCableTarget(
                        point,
                        props.orientation + (180 if props.mirrored else 0),
                        identity,
                        module_no,
                        rows,
                        props.mirrored,
                        identity not in incoming
                        and point.distance(origin) < 1e-6,
                    )
                )
        return result

    def toggle_cable_alignment(self):
        if self.cable_alignment:
            self.cable_alignment = None
            self.stop_tool()
            self.note('Cable alignment cleared.')
        else:
            self.activate('align_cable')

    def align_cable_at(self, click):
        targets = self.housing_cable_targets()
        target = min(
            targets, key=lambda t: t.point.distance(click), default=None
        )
        if target and target.point.distance(click) <= self.tolerance(click):
            state = dict(
                point=target.point,
                angle=target.angle,
                rows=target.rows,
                owner=target.owner,
                module_no=target.module_no,
                reverse=target.mirrored,
            )
        else:
            fiber = self.nearest('fibers', click)
            if fiber is None:
                raise ValueError(
                    'Click a fiber or a free housing/module connector.'
                )
            cable = self.repository.find('cables', fiber['cable_id'])
            if cable['layout_mode'] != 'rows':
                raise ValueError(
                    'Choose a cable drawn with tube rows, or a housing '
                    'connector.'
                )
            geometry = self.scene.drawn_fibers[fiber['fiber_id']]
            context = geometry.closestSegmentWithContext(click)
            points = geometry.asPolyline()
            a, b = points[context[2] - 1: context[2] + 1]
            angle = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))
            rows = rows_from_record(cable)
            number = int(fiber['fiber_no']) - 1
            state = dict(
                point=context[1],
                angle=angle,
                rows=tuple(v - rows[number] for v in rows[number:]),
                owner=None,
            )
        self.stop_tool()
        self.cable_alignment = state
        self.note(
            'Next cable aligns to this row and direction. The cable form '
            'selects full grid or first row only. Click Align again to clear.'
        )

    def native_candidate(self, click):
        state = self.catalog_placement
        alignment = self.cable_alignment
        props = state['properties']
        angle = state['angle']
        endpoints = dict(state['endpoints'])
        modules = dict(state['modules'])
        reverse = state['reverse']
        target = (
            min(
                self.cable_targets,
                key=lambda t: t.point.distance(click),
                default=None,
            )
            if state['snap']
            else None
        )
        target = (
            target
            if target and target.point.distance(click) <= self.tolerance(click)
            else None
        )
        marker = None
        if self.route is None:
            start = QgsPointXY(click)
            if alignment:
                angle = alignment['angle']
                base = alignment['point']
                theta = math.radians(angle)
                along = (click.x() - base.x()) * math.cos(theta) + (
                    click.y() - base.y()
                ) * math.sin(theta)
                start = (
                    base
                    if alignment['owner']
                    else pt(
                        base.x() + along * math.cos(theta),
                        base.y() + along * math.sin(theta),
                    )
                )
                if alignment['owner']:
                    endpoints['A'] = alignment['owner']
                    modules['A'] = alignment['module_no']
                    reverse = alignment['reverse']
                    marker = start
                inherited = (
                    alignment['rows'][: props.count] if state['grid'] else ()
                )
                props = replace(
                    props,
                    row_positions=tuple(
                        cable_rows(
                            props.count, props.fibers_per_tube, inherited
                        )
                    ),
                )
            elif target:
                start, angle = target.point, target.angle
                endpoints['A'], modules['A'] = target.owner, target.module_no
                reverse = target.mirrored
                if not target.primary and state['grid']:
                    props = replace(
                        props,
                        row_positions=tuple(
                            cable_rows(
                                props.count,
                                props.fibers_per_tube,
                                target.rows[: props.count],
                            )
                        ),
                    )
                marker = start
            theta = math.radians(angle)
            points = [
                start,
                pt(
                    start.x() + math.cos(theta) * state['length'],
                    start.y() + math.sin(theta) * state['length'],
                ),
            ]
        else:
            points = list(self.route['vertices'])
            start = points[-1]
            if len(points) > 1:
                a, b = points[-2:]
                angle = (
                    math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x())) + 90
                )
            else:
                angle = state['angle']
            theta = math.radians(angle)
            along = (click.x() - start.x()) * math.cos(theta) + (
                click.y() - start.y()
            ) * math.sin(theta)
            if len(points) == 1 and state['bends']:
                along = max(0, along)
            if not state['bends']:
                along = max(120, along)
            end = pt(
                start.x() + along * math.cos(theta),
                start.y() + along * math.sin(theta),
            )
            endpoints.pop('B', None)
            modules.pop('B', None)
            on_axis = (
                target
                and abs(
                    (target.point.x() - start.x()) * math.sin(theta)
                    - (target.point.y() - start.y()) * math.cos(theta)
                )
                < 1e-6
            )
            if (
                on_axis
                and target.point.distance(end) <= self.tolerance(click)
                and target.owner != endpoints.get('A')
                and self.target_accepts_end(target, angle, reverse)
            ):
                end, marker, endpoints['B'], modules['B'] = (
                    target.point,
                    target.point,
                    target.owner,
                    target.module_no,
                )
            points.append(end)
        # Fixed-length insertion can meet a second housing without a second
        # click.
        if self.route is None and state['snap'] and state['widen']:
            for candidate in self.cable_targets:
                if (
                    candidate.point.distance(points[-1]) < 1e-6
                    and candidate.owner != endpoints.get('A')
                    and self.target_accepts_end(candidate, angle, reverse)
                ):
                    endpoints['B'], modules['B'] = (
                        candidate.owner,
                        candidate.module_no,
                    )
        return dict(
            points=points,
            properties=props,
            angle=angle,
            endpoints=endpoints,
            modules=modules,
            marker=marker,
            reverse=reverse,
        )

    @staticmethod
    def target_accepts_end(target, angle, reverse):
        return target.primary or (
            target.mirrored != reverse
            and abs((angle - target.angle) % 360 - 180) < 1e-6
        )

    def preview_catalog_cable(self, click):
        candidate = self.native_candidate(click)
        points, props = candidate['points'], candidate['properties']
        if candidate['reverse']:
            points = list(reversed(points))
        valid = True
        try:
            validate_row_route(points, props.rows, 10)
        except ValueError:
            valid = False
        indices = (
            range(props.count)
            if props.count <= 240
            else [
                i
                for i in range(props.count)
                if i % props.fibers_per_tube in (0, props.fibers_per_tube - 1)
            ]
        )
        self.click_tool.show_bundle(
            [parallel_path(points, props.rows[i]) for i in indices],
            valid,
            candidate['marker'],
        )

    def catalog_cable_click(self, click):
        state = self.catalog_placement
        candidate = self.native_candidate(click)
        points = candidate['points']
        state.update(
            (key, candidate[key])
            for key in ('properties', 'endpoints', 'modules', 'reverse')
        )
        if self.route is None:
            state['angle'] = candidate['angle']
            if not state['bends'] and not state['drag']:
                self.route = {'vertices': points}
                self.finish_catalog_cable()
                return
            self.route = {'vertices': points[:1]}
            self.note(
                'Click bends, then Enter or Finish. Backspace removes the '
                'last bend.'
                if state['bends']
                else 'Drag the cable length and click. Enter or Finish uses '
                'the configured length.'
            )
        elif state['bends']:
            if points[-1].distance(points[-2]) > 1e-6:
                self.route['vertices'] = points
            self.note(
                'Continue with the next bend; Enter or Finish saves the '
                'cable. Red preview means a segment is too short.'
            )
        else:
            self.route['vertices'] = points
            self.finish_catalog_cable()

    def finish_catalog_cable(self):
        if not self.route or not self.catalog_placement:
            self.note('Place the start of the cable first.')
            return
        state = self.catalog_placement
        points = self.route['vertices']
        if len(points) == 1 and not state['bends']:
            theta = math.radians(state['angle'])
            points = points + [
                pt(
                    points[0].x() + math.cos(theta) * state['length'],
                    points[0].y() + math.sin(theta) * state['length'],
                )
            ]
        endpoints, modules = state['endpoints'], state['modules']
        if state['reverse']:
            points = list(reversed(points))
            endpoints = {
                'B' if side == 'A' else 'A': owner
                for side, owner in endpoints.items()
            }
            modules = {
                'B' if side == 'A' else 'A': number
                for side, number in modules.items()
            }
        identity = self.service.add_catalog_cable(
            state['name'],
            state['properties'],
            points,
            state['real_length'],
            endpoints,
            state['widen'],
            endpoint_modules=modules,
        )
        self.cable_alignment = None
        self.stop_tool()
        self.selected = ('cables', identity)
        self.refresh()
        self.fit()
        self.note(
            'Cable saved with physical tube/fiber identities. Free ends can '
            'be terminated with the housing commands.'
        )

    def edit_catalog_selected(self, cable):
        dialog = CableDialog(
            self,
            properties_from_record(cable),
            self.cable_models(),
            editing=True,
            settings=self.service.settings(),
        )
        dialog.label.setText(cable['name'])
        dialog.real_length.setValue(float(cable['length_m']))
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.service.edit_catalog_cable(
                cable['cable_id'],
                dialog.label.text(),
                dialog.properties(),
                dialog.real_length.value(),
            )
            self.refresh()
            self.note(
                'Cable properties saved; tube, fiber and endpoint identities '
                'retained.'
            )
