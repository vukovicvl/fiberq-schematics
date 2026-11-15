"""Atomic FQDODAJ-style additions to existing native housings."""

from dataclasses import replace
import json
import math

from qgis.core import QgsGeometry

from .domain import uid, validate_vertices
from .geometry import odf_properties, odf_drawing, odf_point, optional
from .cable_catalog import rows_from_record
from .housing_catalog import CAD_SCALE, RACK_HOUSINGS, BOX_HOUSINGS
from .housing_multi import read_layout_state


class HousingCables:
    def housing_attachments(self, identity):
        return [
            r
            for r in self.repo.features('housing_cables')
            if r['obj_id'] == identity
        ]

    def housing_attachment(self, cable_id, side):
        return next(
            (
                r
                for r in self.repo.features('housing_cables')
                if r['cable_id'] == cable_id and r['side'] == side
            ),
            None,
        )

    def housing_local(self, point, origin, angle):
        theta = math.radians(angle)
        dx, dy = point.x() - origin.x(), point.y() - origin.y()
        return (
            (dx * math.cos(theta) + dy * math.sin(theta)) / CAD_SCALE,
            (-dx * math.sin(theta) + dy * math.cos(theta)) / CAD_SCALE,
        )

    def attachment_endpoint(self, cable_id, side, fallback=None, at=None):
        record = self.housing_attachment(cable_id, side)
        if record is None:
            return fallback
        element = self.repo.find('elements', record['obj_id'])
        housing = self.repo.find('housings', record['obj_id'])
        props = odf_properties(element, housing)
        return odf_point(
            (0, json.loads(record['rows'])[0]),
            at or element.geometry().asPoint(),
            props.orientation,
            props.mirrored,
        )

    def _attachment_data(
        self, identity, cable, side, module_no=0, allow_align=False
    ):
        element, housing = (
            self.repo.find('elements', identity),
            self.repo.find('housings', identity),
        )
        props = odf_properties(element, housing)
        if cable['layout_mode'] != 'rows':
            raise ValueError(
                'Add cable requires a cable drawn with independent tube rows.'
            )
        owner = optional(cable['from_obj' if side == 'A' else 'to_obj'])
        if owner is not None and owner != identity:
            raise ValueError(
                'This cable end belongs to another housing. Detach it first.'
            )
        outlet = self.repo.find('outlets', cable['cable_id'])
        frame_outlet = (
            outlet is not None
            and outlet['mode'] == 'elbow'
            and outlet['frame_id'] == identity
            and side == 'B'
        )
        if not frame_outlet and (side == 'B') != props.mirrored:
            raise ValueError(
                'Choose the cable end facing the connector side of this '
                'housing.'
            )
        points = cable.geometry().asPolyline()
        a, b = (
            (points[0], points[1]) if side == 'A' else (points[-2], points[-1])
        )
        angle = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x())) % 360
        if abs((angle - props.orientation + 180) % 360 - 180) > 1e-5:
            raise ValueError(
                'Cable and housing must have the same orientation. Align the '
                'cable first.'
            )
        ends = self.ends_of(cable['cable_id'], side)
        if any(
            r['side'] == side
            and r['fiber_id'] in {e['fiber_id'] for e in ends}
            for r in self.repo.features('fiber_routes')
        ):
            raise ValueError(
                'Restore the diverted cable ends before adding them to a '
                'housing.'
            )
        origin = element.geometry().asPoint()
        local = [
            self.housing_local(
                e.geometry().asPoint(), origin, props.orientation
            )
            for e in ends
        ]
        columns = [p[0] for p in local]
        if max(columns) - min(columns) > 1e-5:
            raise ValueError(
                'All cable fiber ends must share one connector column.'
            )
        tolerance = 1.0 if allow_align and owner is None else 1e-6
        if abs(columns[0]) > tolerance + 1e-6:
            raise ValueError(
                'The cable must end on the housing connector column (within '
                '1 CAD unit).'
            )
        rows = [p[1] for p in local]
        if any(a - b < 1e-6 for a, b in zip(rows, rows[1:])):
            raise ValueError(
                'Cable fibers must descend in their saved numbering order.'
            )
        # The CAD accepts a near-column end; align only that route endpoint.
        index = 0 if side == 'A' else -1
        points[index] = odf_point(
            (0, rows[0]), origin, props.orientation, props.mirrored
        )
        from .geometry import validate_row_route
        from .cable_catalog import CABLE_VERSION, MIN_SEGMENT, MIN_STRAIGHT

        if cable['cable_version'] == CABLE_VERSION:
            validate_row_route(points, rows_from_record(cable), MIN_SEGMENT)
            if (
                len(points) == 2
                and points[0].distance(points[1]) < MIN_STRAIGHT - 1e-6
            ):
                raise ValueError(
                    'Column alignment would make the catalogue cable too '
                    'short.'
                )
        validate_vertices([(p.x(), p.y()) for p in points])
        return dict(
            attachment_id=uid(),
            obj_id=identity,
            cable_id=cable['cable_id'],
            side=side,
            module_no=module_no,
            rows=json.dumps(rows),
            used_count=0,
        ), points

    def housing_cable_plan(
        self,
        identity,
        cable_id,
        side,
        additional=None,
        module_no=1,
        new_bundle=None,
    ):
        element, housing = (
            self.repo.find('elements', identity),
            self.repo.find('housings', identity),
        )
        cable = (
            new_bundle['cable']
            if new_bundle
            else self.repo.find('cables', cable_id)
        )
        if (
            element is None
            or housing is None
            or cable is None
            or side not in ('A', 'B')
        ):
            raise ValueError(
                'Choose an existing native housing and cable end.'
            )
        props = odf_properties(element, housing)
        if type(module_no) is not int or not 1 <= module_no <= props.modules:
            raise ValueError('Choose an existing module of this housing.')
        layout = odf_drawing(element, housing)
        records = [
            dict(
                (name, record[name])
                for name in (
                    'attachment_id',
                    'obj_id',
                    'cable_id',
                    'side',
                    'module_no',
                    'rows',
                    'used_count',
                )
            )
            for record in self.housing_attachments(identity)
        ]
        known = {(r['cable_id'], r['side']) for r in records}
        # Enroll existing primary cables lazily; old single-cable layouts are
        # unchanged.
        for old in self.repo.features('cables'):
            if old['layout_mode'] != 'rows':
                continue
            for old_side, field in (('A', 'from_obj'), ('B', 'to_obj')):
                if (
                    optional(old[field]) == identity
                    and (old['cable_id'], old_side) not in known
                ):
                    data, unused = self._attachment_data(
                        identity, old, old_side
                    )
                    records.append(data)
                    known.add((old['cable_id'], old_side))
        selected = next(
            (
                r
                for r in records
                if (r['cable_id'], r['side']) == (cable_id, side)
            ),
            None,
        )
        vertices = None
        if selected is None:
            if new_bundle:
                selected = new_bundle['record']
            else:
                selected, vertices = self._attachment_data(
                    identity, cable, side, module_no, allow_align=True
                )
            records.append(selected)
        ports = self.ports_of(identity)
        modules = {
            m['module_id']: int(m['number']) for m in self.modules_of(identity)
        }

        def number(p):
            return (modules[p['module_id']] - 1) * props.capacity + int(
                p['module_port']
            )

        by_number = {number(p): p for p in ports}
        by_id = {p['port_id']: number(p) for p in ports}
        ends = self.repo.features('fiber_ends') + (
            new_bundle['ends'] if new_bundle else []
        )
        bound = {e['port_id'] for e in ends if optional(e['port_id'])}
        occupancy = self.occupied()
        protected = bound | occupancy
        if props.splitter_ratio:
            protected.update(
                p['port_id']
                for p in ports
                if int(p['module_port']) <= props.splitter_ratio
                or int(p['module_port'])
                == (props.splitter_feed or props.capacity)
            )
        free = sorted(
            [
                n
                for n, p in by_number.items()
                if modules[p['module_id']] == module_no
                and p['port_id'] not in protected
            ],
            reverse=props.kind not in RACK_HOUSINGS,
        )
        if props.splitter_ratio:
            free = [
                n
                for n in free
                if props.splitter_ratio
                < n
                <= (props.splitter_feed or props.capacity)
            ]
        fixed_free = [
            n
            for n, p in by_number.items()
            if modules[p['module_id']] == module_no
            and p['port_id'] not in bound
            and p['port_id'] in occupancy
            and (
                not props.splitter_ratio
                or props.splitter_ratio
                < n
                <= (props.splitter_feed or props.capacity)
            )
        ]
        joint_occupied = self.joint_occupied()
        candidates, all_rows = [], []
        seen_rows = []
        saved_layout = read_layout_state(
            props.layout_state, props.capacity, props.modules
        )
        for record in records:
            rows = json.loads(record['rows'])
            values = (
                new_bundle['ends']
                if new_bundle and record['cable_id'] == cable_id
                else self.ends_of(record['cable_id'], record['side'])
            )
            for index, (end, row) in enumerate(zip(values, rows)):
                if any(
                    abs(row - previous) < 0.05
                    and end['fiber_id'] != previous_id
                    for previous, previous_id in seen_rows
                ):
                    raise ValueError(
                        'Cable bundles overlap at this connector column. '
                        'Place the new bundle above or below the existing '
                        'fibers.'
                    )
                seen_rows.append((row, end['fiber_id']))
                inferred_module = int(record['module_no']) or min(
                    index // props.capacity + 1, props.modules
                )
                if not record['module_no'] and saved_layout:
                    # Capacity edits retain frame/row positions; fiber numbers
                    # no
                    # longer identify the primary cable's original module
                    # slots.
                    inferred_module = next(
                        (
                            m
                            for m, (top, bottom) in enumerate(
                                saved_layout['bounds'], 1
                            )
                            if bottom < row < top
                        ),
                        inferred_module,
                    )
                all_rows.append((inferred_module, row))
                if (
                    inferred_module == module_no
                    and not optional(end['port_id'])
                    and end['end_id'] not in joint_occupied
                ):
                    candidates.append((record, end, row))
        rows = {n: p[1] for n, p in layout.ports.items()}
        reserve, remaining = [], list(free)
        if not (props.kind in BOX_HOUSINGS and props.splitter_ratio):
            for candidate in candidates:
                exact = next(
                    (
                        n
                        for n in fixed_free
                        if abs(rows[n] - candidate[2]) < 1e-6
                    ),
                    None,
                )
                if exact is not None:
                    fixed_free.remove(exact)
                    reserve.append((exact, candidate))
                elif remaining:
                    reserve.append((remaining.pop(0), candidate))
        limit = len(reserve)
        if additional is None:
            additional = limit
        if type(additional) is not int or not 0 <= additional <= limit:
            raise ValueError(
                'Only {} additional fibers can use free incoming connectors '
                'in this module.'.format(limit)
            )
        # Reserve rows for unconnected incoming fibers too, as in CAD's full
        # fl2 list.
        assigned = []
        for index, (n, (record, end, row)) in enumerate(reserve):
            rows[n] = row
            if index < additional:
                assigned.append((end, by_number[n], record))
        occupied_rows = [
            (n, rows[n])
            for n, p in by_number.items()
            if p['port_id'] in protected
        ]
        reserved = {n for n, value in reserve}
        for n in reserved:
            if any(
                other != n and abs(rows[n] - row) < 0.05
                for other, row in occupied_rows
            ):
                raise ValueError(
                    'An incoming fiber overlaps an existing connected adapter.'
                )
        # Unreserved free adapters follow the combined bundle, retaining their
        # IDs.
        unused = (
            []
            if props.kind in BOX_HOUSINGS and props.splitter_ratio
            else [n for n in free if n not in reserved]
        )
        lowest = min(
            [row for module, row in all_rows if module == module_no]
            + [
                rows[n]
                for n, p in by_number.items()
                if modules[p['module_id']] == module_no and n not in unused
            ]
        )
        for index, n in enumerate(unused, 1):
            rows[n] = lowest - 0.8 * index
        visible = set(
            layout.visible_ports
            if layout.visible_ports is not None
            else layout.ports
        )
        visible.update(number(port) for end, port, record in assigned)
        bounds = [
            [max(y for x, y in frame), min(y for x, y in frame)]
            for frame in layout.module_frames
        ]
        for module in range(1, props.modules + 1):
            upper_margin = (
                (1.6 if module == 1 else 0.4)
                if props.kind in RACK_HOUSINGS
                else 1.5
                if props.kind in BOX_HOUSINGS
                else 2.0
            )
            lower_margin = (
                (1.6 if module == props.modules else 0.4)
                if props.kind in RACK_HOUSINGS
                else upper_margin
            )
            envelope = [row for m, row in all_rows if m == module] + [
                rows[n]
                for n in visible
                if (n - 1) // props.capacity + 1 == module
            ]
            if envelope:
                bounds[module - 1] = [
                    max(bounds[module - 1][0], max(envelope) + upper_margin),
                    min(bounds[module - 1][1], min(envelope) - lower_margin),
                ]
        state = dict(
            version=1,
            rows=[
                [
                    rows[(m * props.capacity) + n]
                    for n in range(1, props.capacity + 1)
                ]
                for m in range(props.modules)
            ],
            visible=sorted(visible),
            bounds=bounds,
        )
        layout_state = json.dumps(state, allow_nan=False)
        read_layout_state(layout_state, props.capacity, props.modules)
        total = sum(optional(e['port_id']) in by_id for e in ends) + additional
        planned = replace(
            props,
            requested_used=max(1, total) if props.splitter_ratio else total,
            layout_state=layout_state,
        ).validate()
        return dict(
            properties=planned,
            records=records,
            assignments=assigned,
            cable_id=cable_id,
            side=side,
            vertices=vertices,
            limit=limit,
            additional=additional,
            per_cable={
                r['cable_id']: sum(
                    rec['cable_id'] == r['cable_id']
                    for end, port, rec in assigned
                )
                for r in records
            },
        )

    def add_housing_cable(
        self, identity, cable_id, side, additional=None, module_no=1
    ):
        plan = self.housing_cable_plan(
            identity, cable_id, side, additional, module_no
        )
        with self.repo.command('Add cable to existing housing'):
            if plan['vertices'] is not None:
                cable = self.repo.find('cables', cable_id)
                self.repo.update(
                    'cables',
                    cable.id(),
                    {'from_obj' if side == 'A' else 'to_obj': identity},
                    QgsGeometry.fromPolylineXY(plan['vertices']),
                )
            for record in plan['records']:
                saved = self.repo.find(
                    'housing_cables', record['attachment_id']
                )
                if saved is None:
                    self.repo.insert('housing_cables', record)
            fibers = {f['fiber_id']: f for f in self.repo.features('fibers')}
            for end, port, record in plan['assignments']:
                self.repo.update(
                    'fiber_ends', end.id(), {'port_id': port['port_id']}
                )
                self.repo.update(
                    'fibers',
                    fibers[end['fiber_id']].id(),
                    {
                        'a_id' if record['side'] == 'A' else 'b_id': port[
                            'port_id'
                        ]
                    },
                )
            element = self.repo.find('elements', identity)
            self.edit_housing(
                identity,
                element['name'],
                plan['properties'],
                layout_update=True,
            )
            self.refresh_housing_attachment_counts(identity)
            self.validate()
        return identity

    def refresh_housing_attachment_counts(self, identity):
        for record in self.housing_attachments(identity):
            count = sum(
                optional(e['port_id']) is not None
                for e in self.ends_of(record['cable_id'], record['side'])
            )
            if record['used_count'] != count:
                self.repo.update(
                    'housing_cables', record.id(), {'used_count': count}
                )

    def _update_multi_used(self, identity):
        element, housing = (
            self.repo.find('elements', identity),
            self.repo.find('housings', identity),
        )
        if housing is None or not housing['layout_state']:
            return
        ids = {p['port_id'] for p in self.ports_of(identity)}
        count = sum(
            optional(e['port_id']) in ids
            for e in self.repo.features('fiber_ends')
        )
        props = odf_properties(element, housing)
        # Splitter boxes keep the source feed convention even after detachment.
        used = max(1, count) if props.splitter_ratio else count
        self.edit_housing(
            identity,
            element['name'],
            replace(props, requested_used=used),
            layout_update=True,
        )

    def multi_housing_properties(self, identity, properties):
        old = odf_properties(
            self.repo.find('elements', identity),
            self.repo.find('housings', identity),
        )
        if not old.layout_state:
            return properties
        if (
            properties.orientation,
            properties.mirrored,
            properties.splitter_ratio,
            properties.splitter_feed,
        ) != (
            old.orientation,
            old.mirrored,
            old.splitter_ratio,
            old.splitter_feed,
        ):
            raise ValueError(
                'Expanded housings retain their orientation, mirror and '
                'contained splitter configuration.'
            )
        port_ids = {p['port_id'] for p in self.ports_of(identity)}
        total = sum(
            optional(e['port_id']) in port_ids
            for e in self.repo.features('fiber_ends')
        )
        used = max(1, total) if old.splitter_ratio else total
        if properties.requested_used not in (None, used):
            raise ValueError(
                'Used is the total across attached cables. Use Add cable to '
                'terminate more fibers or detach a cable end.'
            )
        state = read_layout_state(old.layout_state, old.capacity, old.modules)
        base = replace(properties, layout_state='', requested_used=used)
        from .element_layout import housing_layout

        layout = housing_layout(base, '')
        new_rows, bounds, visible = [], [], []
        for module in range(properties.modules):
            values = (
                list(state['rows'][module][: properties.capacity])
                if module < old.modules
                else [
                    layout.ports[module * properties.capacity + n][1]
                    for n in range(1, properties.capacity + 1)
                ]
            )
            if len(values) < properties.capacity:
                values += [
                    min(values) - 0.8 * (i + 1)
                    for i in range(properties.capacity - len(values))
                ]
            new_rows.append(values)
            old_bound = (
                state['bounds'][module]
                if module < old.modules
                else [max(values) + 1.6, min(values) - 1.6]
            )
            visible.extend(
                module * properties.capacity + (n - 1) % old.capacity + 1
                for n in state['visible']
                if (n - 1) // old.capacity == module
                and (n - 1) % old.capacity < properties.capacity
            )
            if properties.kind in RACK_HOUSINGS + BOX_HOUSINGS:
                visible.extend(
                    module * properties.capacity + n
                    for n in range(1, properties.capacity + 1)
                )
                upper = (
                    (1.6 if module == 0 else 0.4)
                    if properties.kind in RACK_HOUSINGS
                    else 1.5
                )
                lower = (
                    (1.6 if module == properties.modules - 1 else 0.4)
                    if properties.kind in RACK_HOUSINGS
                    else 1.5
                )
                old_bound = [
                    max(old_bound[0], max(values) + upper),
                    min(old_bound[1], min(values) - lower),
                ]
            bounds.append(old_bound)
        updated = json.dumps(
            dict(
                version=1,
                rows=new_rows,
                visible=sorted(set(visible)),
                bounds=bounds,
            )
        )
        return replace(
            properties, requested_used=used, layout_state=updated
        ).validate()

    def validate_housing_cables(self, data=None):
        data = data or {
            key: self.repo.features(key)
            for key in (
                'housing_cables',
                'housings',
                'elements',
                'cables',
                'fiber_ends',
                'fibers',
                'ports',
                'modules',
            )
        }
        housings = {r['obj_id']: r for r in data['housings']}
        elements = {r['obj_id']: r for r in data['elements']}
        cables = {r['cable_id']: r for r in data['cables']}
        fiber_numbers = {
            r['fiber_id']: (r['cable_id'], r['fiber_no'])
            for r in data['fibers']
        }
        module_numbers = {
            r['module_id']: int(r['number']) for r in data['modules']
        }
        port_modules = {
            p['port_id']: module_numbers.get(p['module_id'])
            for p in data['ports']
        }
        outlets = {o['cable_id']: o for o in self.repo.features('outlets')}
        from collections import defaultdict

        ends_by_side = defaultdict(list)
        for end in data['fiber_ends']:
            if end['fiber_id'] in fiber_numbers:
                ends_by_side[
                    fiber_numbers[end['fiber_id']][0], end['side']
                ].append(end)
        for values in ends_by_side.values():
            values.sort(key=lambda e: fiber_numbers[e['fiber_id']][1])
        seen = set()
        for record in data['housing_cables']:
            key = record['cable_id'], record['side']
            housing = housings.get(record['obj_id'])
            element = elements.get(record['obj_id'])
            cable = cables.get(record['cable_id'])
            if (
                key in seen
                or housing is None
                or element is None
                or cable is None
                or record['side'] not in ('A', 'B')
            ):
                raise ValueError(
                    'Invalid or duplicate housing cable attachment.'
                )
            seen.add(key)
            props = odf_properties(element, housing)
            if (
                not props.layout_state
                or cable['layout_mode'] != 'rows'
                or optional(
                    cable['from_obj' if record['side'] == 'A' else 'to_obj']
                )
                != record['obj_id']
            ):
                raise ValueError(
                    'Housing cable attachment disagrees with its owner/layout.'
                )
            if (
                type(record['module_no']) is not int
                or not 0 <= record['module_no'] <= props.modules
            ):
                raise ValueError(
                    'Housing cable attachment references a missing module.'
                )
            route = cable.geometry().asPolyline()
            if len(route) < 2:
                raise ValueError('Attached cable needs an endpoint segment.')
            a, b = (
                (route[0], route[1])
                if record['side'] == 'A'
                else (route[-2], route[-1])
            )
            angle = (
                math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x())) % 360
            )
            outlet = outlets.get(record['cable_id'])
            frame_outlet = (
                outlet is not None
                and outlet['mode'] == 'elbow'
                and outlet['frame_id'] == record['obj_id']
                and record['side'] == 'B'
            )
            if (
                not frame_outlet and (record['side'] == 'B') != props.mirrored
            ) or abs((angle - props.orientation + 180) % 360 - 180) > 1e-5:
                raise ValueError(
                    'Attached cable no longer faces its housing connector '
                    'column.'
                )
            try:
                rows = json.loads(record['rows'])
            except (ValueError, TypeError) as error:
                raise ValueError('Invalid attached cable rows.') from error
            ends = ends_by_side[key]
            if (
                not isinstance(rows, list)
                or len(rows) != len(ends)
                or any(
                    type(y) not in (int, float) or not math.isfinite(y)
                    for y in rows
                )
                or any(a <= b for a, b in zip(rows, rows[1:]))
            ):
                raise ValueError('Invalid attached cable rows.')
            count = 0
            for end, row in zip(ends, rows):
                expected = odf_point(
                    (0, row),
                    element.geometry().asPoint(),
                    props.orientation,
                    props.mirrored,
                )
                if end.geometry().asPoint().distance(expected) > 1e-6:
                    raise ValueError(
                        'Attached cable fiber does not match its saved '
                        'housing row.'
                    )
                if (
                    optional(end['port_id'])
                    and record['module_no']
                    and port_modules.get(end['port_id']) != record['module_no']
                ):
                    raise ValueError(
                        'Cable termination belongs to a different housing '
                        'module.'
                    )
                count += optional(end['port_id']) is not None
            if count != record['used_count']:
                raise ValueError('Attached cable termination count is stale.')
        for housing in data['housings']:
            if not housing['layout_state']:
                continue
            for cable in data['cables']:
                if cable['layout_mode'] == 'rows':
                    for side, field in (('A', 'from_obj'), ('B', 'to_obj')):
                        if (
                            optional(cable[field]) == housing['obj_id']
                            and (cable['cable_id'], side) not in seen
                        ):
                            raise ValueError(
                                'Missing housing cable attachment record.'
                            )
