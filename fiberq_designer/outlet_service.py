"""Atomic frame outlets; Join/Group join handles optical connections."""

import json

from qgis.core import QgsGeometry

from .cable_catalog import CABLE_VERSION, rows_from_record
from .closure_layout import CLOSURE_MARGIN, local_point
from .geometry import (
    cable_fiber_path,
    odf_drawing,
    odf_point,
    odf_properties,
    optional,
    pt,
)
from .outlet_layout import occupied_left, outlet_column, outlet_layout


class OutletService:
    def outlet_context(self, identity, module_no=1, scene=None):
        element = self.repo.find('elements', identity)
        housing, closure = (
            self.repo.find('housings', identity),
            self.repo.find('closures', identity),
        )
        if element is None or (housing is None and closure is None):
            raise ValueError('Choose a native housing or N/NL closure frame.')
        origin = element.geometry().asPoint()
        if housing is not None:
            props = odf_properties(element, housing)
            if props.mirrored:
                raise ValueError(
                    'A frame outlet would enter this mirrored housing. Use '
                    'Branch or a cable with bends, as in FiberQ CAD.'
                )
            if props.splitter_ratio:
                raise ValueError(
                    'FiberQ CAD does not create frame outlets from housings '
                    'with a contained splitter. Use its free optical outputs '
                    'with Join/Drop.'
                )
            if (
                type(module_no) is not int
                or not 1 <= module_no <= props.modules
            ):
                raise ValueError('Choose an existing housing module.')
            layout = odf_drawing(element, housing)
            frame = layout.module_frames[module_no - 1]
            angle = props.orientation
            left, right = min(x for x, y in frame), max(x for x, y in frame)
            top, bottom = max(y for x, y in frame), min(y for x, y in frame)
            margin = (
                1.6
                if props.kind in ('ODF', 'PATCH_PANEL')
                else 1.5
                if props.kind in ('FAT', 'OTB')
                else 2.0
            )
            column = 0.0
        else:
            angle = float(closure['angle'])
            left, right = 0.0, float(closure['width'])
            top, bottom = float(closure['top']), float(closure['bottom'])
            column, margin, module_no = (
                right - CLOSURE_MARGIN,
                CLOSURE_MARGIN,
                0,
            )
        scene = scene or self.fiber_scene()
        paths = [
            [
                local_point((p.x(), p.y()), (origin.x(), origin.y()), angle)
                for p in geometry.asPolyline()
            ]
            for geometry in scene.drawn_fibers.values()
        ]
        occupied, columns = [], []
        for path in paths:
            if housing is not None:
                occupied.extend(
                    y
                    for x, y in (path[0], path[-1])
                    if abs(x - column) < 0.01
                    and bottom - 0.01 <= y <= top + 0.01
                )
            else:
                middle = (left + right) / 2
                for a, b in zip(path, path[1:]):
                    if abs(b[0] - a[0]) > 1e-7 and min(
                        a[0], b[0]
                    ) <= middle <= max(a[0], b[0]):
                        y = a[1] + (b[1] - a[1]) * (middle - a[0]) / (
                            b[0] - a[0]
                        )
                        if bottom - 0.01 <= y <= top + 0.01:
                            occupied.append(y)
            columns.extend(
                a[0]
                for a, b in zip(path, path[1:])
                if abs(a[0] - b[0]) < 0.01
                and abs(a[1] - b[1]) > 0.01
                and max(a[1], b[1]) <= top + 0.01
                and a[0] < column - 0.5
            )
        leftmost = occupied_left(columns, left, right)
        return dict(
            identity=identity,
            origin=origin,
            housing=housing is not None,
            module_no=module_no,
            angle=angle,
            left=left,
            right=right,
            top=top,
            bottom=bottom,
            column=column,
            first_row=min(occupied) - 1.6 if occupied else top - margin,
            bend_column=outlet_column(column, leftmost),
            repeated=leftmost is not None,
        )

    def outlet_plan(
        self,
        identity,
        properties,
        length=40.0,
        mode='elbow',
        module_no=1,
        context=None,
    ):
        properties.validate()
        if properties.row_positions:
            raise ValueError('A new outlet uses the standard tube grid.')
        context = context or self.outlet_context(identity, module_no)
        if mode not in ('elbow', 'straight') or (
            not context['housing'] and mode != 'straight'
        ):
            raise ValueError(
                'Closures use straight outlet fibers; housings offer elbow '
                'or straight.'
            )
        drawing = outlet_layout(
            [row / 10 for row in properties.rows],
            context['first_row'],
            context['bend_column'],
            length,
            mode == 'elbow',
            context['column'],
        )

        def world(path):
            return [
                odf_point(p, context['origin'], context['angle']) for p in path
            ]

        plan = dict(
            context=context,
            mode=mode,
            properties=properties,
            vertices=world(drawing['route']),
            paths=[world(path) for path in drawing['paths']],
            drawing=drawing,
            housing_plan=None,
        )
        if context['housing'] and mode == 'elbow':
            rows = [context['first_row'] + row / 10 for row in properties.rows]
            virtual_id = '__outlet_preview__'
            bundle = dict(
                cable={'cable_id': virtual_id},
                record=dict(
                    attachment_id='__outlet_attachment__',
                    obj_id=identity,
                    cable_id=virtual_id,
                    side='B',
                    module_no=module_no,
                    rows=json.dumps(rows),
                    used_count=0,
                ),
                ends=[
                    dict(
                        end_id='__outlet_end_{}'.format(i),
                        fiber_id='__outlet_fiber_{}'.format(i),
                        port_id=None,
                    )
                    for i in range(properties.count)
                ],
            )
            plan['housing_plan'] = self.housing_cable_plan(
                identity,
                virtual_id,
                'B',
                additional=0,
                module_no=module_no,
                new_bundle=bundle,
            )
        return plan

    def add_outlet(
        self,
        identity,
        properties,
        length=40.0,
        mode='elbow',
        module_no=1,
        name='',
        length_m=0.0,
    ):
        plan = self.outlet_plan(identity, properties, length, mode, module_no)
        context = plan['context']
        with self.repo.command('Create frame outlet'):
            cable_id = self.add_catalog_cable(
                name, properties, plan['vertices'], length_m, widen=False
            )
            self.repo.insert(
                'outlets',
                dict(
                    cable_id=cable_id,
                    frame_id=identity,
                    module_no=context['module_no'],
                    mode=mode,
                ),
            )
            self._sync_cable_ends(cable_id)
            if context['housing'] and mode == 'elbow':
                self.add_housing_cable(
                    identity, cable_id, 'B', additional=0, module_no=module_no
                )
            elif not context['housing']:
                record, element = (
                    self.repo.find('closures', identity),
                    self.repo.find('elements', identity),
                )
                left = min(0.0, plan['drawing']['left'] - CLOSURE_MARGIN)
                if left < 0:
                    # Growing the enclosure must not recenter existing splices.
                    # Retain their actual column when the left edge moves.
                    for joint in self.repo.features('fiber_joints'):
                        if (
                            joint['closure_id'] == identity
                            and optional(joint['manual_x']) is None
                        ):
                            points = joint.geometry().asPolyline()
                            column = (
                                points[1]
                                if len(points) > 2
                                else joint.geometry()
                                .interpolate(joint.geometry().length() / 2)
                                .asPoint()
                            )
                            self.repo.update(
                                'fiber_joints',
                                joint.id(),
                                {
                                    'manual_x': column.x(),
                                    'manual_y': column.y(),
                                },
                            )
                origin = odf_point(
                    (left, 0),
                    element.geometry().asPoint(),
                    float(record['angle']),
                )
                self.repo.update(
                    'elements',
                    element.id(),
                    geometry=QgsGeometry.fromPointXY(origin),
                )
                self.repo.update(
                    'closures',
                    record.id(),
                    dict(
                        width=float(record['width']) - left,
                        bottom=min(
                            float(record['bottom']),
                            plan['drawing']['last_row'] - CLOSURE_MARGIN,
                        ),
                    ),
                )
                self._update_closure_frame(identity)
            self.validate_outlets()
        return cable_id

    def outlet_mode(self, cable_id):
        record = self.repo.find('outlets', cable_id)
        return record['mode'] if record is not None else ''

    def moved_outlet_elbow(self, original, index, target):
        first, corner, end = original[0], original[-2], original[-1]
        horizontal = abs(corner.y() - end.y()) < 1e-6
        if index == -1:
            corner = pt(
                corner.x() + target.x() - end.x(),
                corner.y() + target.y() - end.y(),
            )
            end = target
        else:
            first = target
        if horizontal:
            if abs(first.x() - corner.x()) < 1e-6:
                return [first, corner, end]
            middle = (first.y() + corner.y()) / 2
            return [
                first,
                pt(first.x(), middle),
                pt(corner.x(), middle),
                corner,
                end,
            ]
        if abs(first.y() - corner.y()) < 1e-6:
            return [first, corner, end]
        middle = (first.x() + corner.x()) / 2
        return [
            first,
            pt(middle, first.y()),
            pt(middle, corner.y()),
            corner,
            end,
        ]

    def validate_outlets(self, data=None):
        data = data or {
            key: self.repo.features(key)
            for key in ('outlets', 'cables', 'housings', 'closures')
        }
        cables = {c['cable_id']: c for c in data['cables']}
        housings = {h['obj_id']: h for h in data['housings']}
        closures = {c['obj_id']: c for c in data['closures']}
        seen = set()
        for record in data['outlets']:
            cable = cables.get(record['cable_id'])
            if (
                record['cable_id'] in seen
                or cable is None
                or record['mode'] not in ('elbow', 'straight')
                or cable['cable_version'] != CABLE_VERSION
            ):
                raise ValueError('Invalid or duplicate frame outlet record.')
            seen.add(record['cable_id'])
            frame = optional(record['frame_id'])
            if frame is not None:
                if frame in housings:
                    if (
                        type(record['module_no']) is not int
                        or not 1
                        <= record['module_no']
                        <= housings[frame]['module_count']
                    ):
                        raise ValueError(
                            'Outlet references a missing housing module.'
                        )
                elif (
                    frame not in closures
                    or record['module_no'] != 0
                    or record['mode'] != 'straight'
                ):
                    raise ValueError(
                        'Outlet references a missing frame or invalid '
                        'closure mode.'
                    )
            route, rows = (
                cable.geometry().asPolyline(),
                rows_from_record(cable),
            )
            if len(route) < (3 if record['mode'] == 'elbow' else 2):
                raise ValueError(
                    'Outlet is missing its source bend/vertical segment.'
                )
            for row in rows:
                path = cable_fiber_path(route, row, record['mode'])
                if (
                    any(
                        a.distance(b) < 10 - 1e-6
                        for a, b in zip(path, path[1:])
                    )
                    or not QgsGeometry.fromPolylineXY(path).isSimple()
                ):
                    raise ValueError(
                        'Outlet length/bends cannot contain the complete '
                        'staggered bundle.'
                    )
        if any(
            c['cable_version'] == CABLE_VERSION
            and not c['category']
            and c['cable_id'] not in seen
            for c in data['cables']
        ):
            raise ValueError(
                'A manual native cable needs its frame outlet record.'
            )
