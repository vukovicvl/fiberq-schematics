"""Persistent standalone active equipment, using real optical ports."""

from dataclasses import asdict
import math

from qgis.core import QgsGeometry

from .active_layout import (
    ACTIVE_VERSION,
    active_layout,
    active_properties,
    local_to_world,
)
from .domain import FIBER_COLORS, uid, validate_vertices
from .geometry import pt


class ActiveService:
    def _active_attributes(self, properties, identity):
        attrs = asdict(properties)
        attrs.pop('kind')
        attrs.pop('count')
        attrs.update(
            obj_id=identity,
            layout_version=ACTIVE_VERSION,
            loss_db=active_layout(properties).loss,
        )
        return attrs

    def _active_points(self, properties, name, center):
        layout = active_layout(properties, name)

        def world(p):
            return pt(
                *local_to_world(
                    p,
                    layout.center,
                    (center.x(), center.y()),
                    properties.orientation,
                )
            )

        frame = QgsGeometry.fromPolygonXY([[world(p) for p in layout.frame]])
        return layout, world, frame

    def active_name(self, kind, label):
        if label.strip():
            return self._name(label, 'elements')
        prefix = 'S' if kind == 'SPLITTER' else kind
        names = {e['name'] for e in self.repo.features('elements')}
        number = 1
        while prefix + str(number) in names:
            number += 1
        return prefix + str(number)

    def add_active(self, label, properties, anchor, connect_to=None):
        properties.validate()
        name = self.active_name(properties.kind, label)
        layout = active_layout(properties, name)
        theta = math.radians(properties.orientation)
        x, y = layout.center
        center = pt(
            anchor.x() + 10 * (x * math.cos(theta) - y * math.sin(theta)),
            anchor.y() + 10 * (x * math.sin(theta) + y * math.cos(theta)),
        )
        if not all(math.isfinite(v) for v in (center.x(), center.y())):
            raise ValueError('Active equipment position must be finite.')
        identity = uid()
        with self.repo.command('Place ' + properties.kind):
            self.repo.insert(
                'elements',
                dict(
                    obj_id=identity,
                    kind=properties.kind,
                    name=name,
                    count=properties.count,
                    side='center',
                ),
                QgsGeometry.fromPointXY(center),
            )
            self.repo.insert(
                'active_equipment',
                self._active_attributes(properties, identity),
            )
            self.save_drawing_language('active_equipment', identity)
            self._write_active(identity, properties, name, center)
            if connect_to:
                reference = (
                    0
                    if properties.kind == 'SPLITTER'
                    and properties.anchor == 'input'
                    else 1
                )
                port = next(
                    p
                    for p in self.ports_of(identity)
                    if int(p['fiber_no']) == reference
                )
                self.join_nodes(connect_to, port['port_id'], 'patch')
        return identity

    def _write_active(self, identity, properties, name, center):
        layout, world, frame = self._active_points(properties, name, center)
        existing = {int(p['fiber_no']): p for p in self.ports_of(identity)}
        removed = [p for n, p in existing.items() if n not in layout.ports]
        used = self.occupied() | {
            f[k]
            for f in self.repo.features('fibers')
            for k in ('a_id', 'b_id')
        }
        if any(p['port_id'] in used for p in removed):
            raise ValueError(
                'Disconnect occupied active-equipment ports before reducing '
                'capacity.'
            )
        self.repo.delete('ports', [p.id() for p in removed])
        saved_frame = self.repo.find('frames', identity)
        if saved_frame is None:
            self.repo.insert(
                'frames', {'obj_id': identity, 'kind': properties.kind}, frame
            )
        else:
            self.repo.update('frames', saved_frame.id(), geometry=frame)
        for number, point in layout.ports.items():
            position = world(point)
            role = (
                'input'
                if properties.kind == 'ONT' or number == 0
                else 'output'
            )
            label = (
                'IN'
                if number == 0
                else 'PON ' + str(number)
                if properties.kind == 'OLT'
                else 'DROP'
                if properties.kind == 'ONT'
                else 'OUT ' + str(number)
            )
            attrs = dict(
                obj_id=identity,
                label=label,
                fiber_no=number,
                role=role,
                dx=position.x() - center.x(),
                dy=position.y() - center.y(),
                color=FIBER_COLORS[max(0, number - 1) % 12],
            )
            if number in existing:
                self.repo.update(
                    'ports',
                    existing[number].id(),
                    attrs,
                    QgsGeometry.fromPointXY(position),
                )
            else:
                self.repo.insert(
                    'ports',
                    dict(attrs, port_id=uid()),
                    QgsGeometry.fromPointXY(position),
                )

    def edit_active(self, identity, label, properties):
        element = self.repo.find('elements', identity)
        if (
            element is None
            or self.repo.find('active_equipment', identity) is None
            or properties.kind != element['kind']
        ):
            raise ValueError(
                'Choose native active equipment of the same kind.'
            )
        properties.validate()
        name = self._name(label, 'elements', identity)
        with self.repo.command('Edit ' + properties.kind):
            self.repo.update(
                'elements',
                element.id(),
                {'name': name, 'count': properties.count},
            )
            record = self.repo.find('active_equipment', identity)
            self.repo.update(
                'active_equipment',
                record.id(),
                self._active_attributes(properties, identity),
            )
            self._write_active(
                identity, properties, name, element.geometry().asPoint()
            )
            self._update_connections(identity)
            self._sync_attached_ends(identity)

    def move_active(self, identity, target):
        element = self.repo.find('elements', identity)
        record = self.repo.find('active_equipment', identity)
        if not all(math.isfinite(v) for v in (target.x(), target.y())):
            raise ValueError('Element position must be finite.')
        with self.repo.command('Move active equipment'):
            self.repo.update(
                'elements',
                element.id(),
                geometry=QgsGeometry.fromPointXY(target),
            )
            self._write_active(
                identity,
                active_properties(element, record),
                element['name'],
                target,
            )
            self._update_connections(identity)
            for cable in self.repo.features('cables'):
                if identity in (cable['from_obj'], cable['to_obj']):
                    vertices = cable.geometry().asPolyline()
                    vertices[0 if cable['from_obj'] == identity else -1] = (
                        target
                    )
                    validate_vertices([(p.x(), p.y()) for p in vertices])
                    self.repo.update(
                        'cables',
                        cable.id(),
                        geometry=QgsGeometry.fromPolylineXY(vertices),
                    )
            self._sync_attached_ends(identity)

    def validate_active(self, element, record, owned, frame):
        properties = active_properties(element, record)
        if record['layout_version'] != ACTIVE_VERSION:
            raise ValueError('Unsupported active-equipment layout version.')
        layout, world, expected = self._active_points(
            properties, element['name'], element.geometry().asPoint()
        )
        if abs(
            float(record['loss_db']) - layout.loss
        ) > 1e-9 or not frame.isGeosEqual(expected):
            raise ValueError(
                'Active equipment frame/loss does not match its source layout.'
            )
        ports = {int(p['fiber_no']): p for p in owned}
        if set(ports) != set(layout.ports):
            raise ValueError('Active equipment port count mismatch.')
        for number, point in layout.ports.items():
            expected_role = (
                'input'
                if properties.kind == 'ONT' or number == 0
                else 'output'
            )
            if (
                ports[number]['role'] != expected_role
                or ports[number].geometry().asPoint().distance(world(point))
                > 1e-6
            ):
                raise ValueError(
                    'Active equipment connector is detached or has the wrong '
                    'optical role.'
                )
