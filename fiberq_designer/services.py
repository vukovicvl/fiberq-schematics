"""Editing operations preserve IDs, topology and persisted geometry."""

import math
import json
from dataclasses import asdict, replace

from qgis.core import QgsGeometry, QgsPointXY

from .domain import (
    FIBER_COLORS,
    PortSpec,
    check_capacity,
    connected_ports,
    port_specs,
    fiber_rows,
    uid,
    validate_connection,
    validate_vertices,
)
from .element_layout import housing_layout, transform
from .geometry import (
    frame_geometry,
    housing_font_metrics,
    link_geometry,
    odf_drawing,
    odf_frame,
    odf_properties,
    pt,
)
from .geometry import (
    optional,
    parallel_path,
    validate_row_route,
    cable_fiber_path,
)
from .geometry import closure_drawing, closure_frame, closure_properties
from .closure_layout import (
    CLOSURE_LAYOUT_VERSION,
    CLOSURE_MARGIN,
    DEFAULT_CLOSURE_WIDTH,
    closure_name,
    cross_section,
    local_point,
    nearest_segment,
    validate_closure_bounds,
)
from .housing_catalog import (
    CAD_SCALE,
    HOUSINGS,
    CABINET_HOUSINGS,
    SPLITTER_HOUSINGS,
    SUPPORTED_HOUSINGS,
    SPLITTER_LOSSES,
    housing_layout_version,
    housing_name,
    module_labels,
)
from .repository import fields_dict
from .schema import ID_FIELDS
from .cable_catalog import (
    CABLE_VERSION,
    MIN_STRAIGHT,
    MIN_SEGMENT,
    CableProperties,
    rows_from_record,
    properties_from_record,
)
from .fiber_service import FiberService
from .active_service import ActiveService
from .workspace_service import WorkspaceService
from .housing_cables import HousingCables
from .outlet_service import OutletService


class NetworkService(
    FiberService, ActiveService, WorkspaceService, HousingCables, OutletService
):
    def __init__(self, repository):
        self.repo = repository
        self.repo.before_commit = self.refresh_network

    def refresh_network(self):
        self.sync_fiber_operations()
        self.refresh_closures()
        if self.repo.features('housing_cables'):
            self.validate_housing_cables()
        if self.repo.features('outlets'):
            self.validate_outlets()

    def _closure_scene(self):
        from .cad_view import CadView

        scene = CadView()
        scene.rebuild(self.repo)
        return scene

    def closure_bundle(self, cable_id, scene=None):
        if self.repo.find('cables', cable_id) is None:
            raise ValueError('Choose an existing cable by clicking a fiber.')
        scene = scene or self._closure_scene()
        fibers = sorted(
            (
                f
                for f in self.repo.features('fibers')
                if f['cable_id'] == cable_id
            ),
            key=lambda f: int(f['fiber_no']),
        )
        paths = {
            f['fiber_id']: [
                (p.x(), p.y())
                for p in scene.drawn_fibers[f['fiber_id']].asPolyline()
            ]
            for f in fibers
        }
        if not paths:
            raise ValueError('The cable has no fibers.')
        return paths

    def closure_plan(
        self, cable_id, target, width=DEFAULT_CLOSURE_WIDTH, bundle=None
    ):
        if not all(math.isfinite(v) for v in (target.x(), target.y())):
            raise ValueError('Closure position must be finite.')
        bundle = bundle or self.closure_bundle(cable_id)
        first = next(iter(bundle.values()))
        _, segment, anchor, angle = nearest_segment(
            first, (target.x(), target.y())
        )
        paths = [
            [local_point(p, anchor, angle, CAD_SCALE) for p in path]
            for path in bundle.values()
        ]
        low, high = cross_section(paths)
        top, bottom = high + CLOSURE_MARGIN, low - CLOSURE_MARGIN
        validate_closure_bounds(width, top, bottom)
        bends = any(
            1e-8 < p[0] < width - 1e-8 for p in paths[0][segment + 1: -1]
        )
        return {
            'anchor': pt(*anchor),
            'angle': angle,
            'width': float(width),
            'top': top,
            'bottom': bottom,
            'crosses_bend': bends,
        }

    def add_closure(
        self, label, properties, cable_id, target, width=DEFAULT_CLOSURE_WIDTH
    ):
        properties.validate()
        name = self._name(
            closure_name(
                label,
                properties.closure_type,
                (e['name'] for e in self.repo.features('elements')),
            ),
            'elements',
        )
        plan = self.closure_plan(cable_id, target, width)
        identity = uid()
        attrs = dict(
            asdict(properties),
            obj_id=identity,
            cable_id=cable_id,
            layout_version=CLOSURE_LAYOUT_VERSION,
            font_family=housing_font_metrics('')[0],
            **{k: plan[k] for k in ('angle', 'width', 'top', 'bottom')},
        )
        layout = closure_drawing({'name': name}, attrs)
        with self.repo.command(
            'Place ' + properties.closure_type + ' closure'
        ):
            self.repo.insert(
                'elements',
                {
                    'obj_id': identity,
                    'kind': 'CLOSURE',
                    'name': name,
                    'count': 0,
                    'side': 'center',
                },
                QgsGeometry.fromPointXY(plan['anchor']),
            )
            self.repo.insert('closures', attrs)
            self.save_drawing_language('closures', identity)
            self.repo.insert(
                'frames',
                {'obj_id': identity, 'kind': 'CLOSURE'},
                odf_frame(layout, plan['anchor'], plan['angle']),
            )
        return identity

    def edit_closure(self, identity, label, properties, width=None):
        properties.validate()
        record = self.repo.find('closures', identity)
        element = self.repo.find('elements', identity)
        if record is None or element is None:
            raise ValueError('Closure no longer exists.')
        name = self._name(
            closure_name(
                label,
                properties.closure_type,
                (
                    e['name']
                    for e in self.repo.features('elements')
                    if e['obj_id'] != identity
                ),
            ),
            'elements',
            identity,
        )
        width = float(record['width']) if width is None else width
        validate_closure_bounds(
            width, float(record['top']), float(record['bottom'])
        )
        with self.repo.command('Edit closure'):
            self.repo.update('elements', element.id(), {'name': name})
            self.repo.update(
                'closures', record.id(), dict(asdict(properties), width=width)
            )
            self._update_closure_frame(identity)

    def move_closure(self, identity, target):
        record = self.repo.find('closures', identity)
        element = self.repo.find('elements', identity)
        if record is None or element is None:
            raise ValueError('Closure no longer exists.')
        if not all(math.isfinite(v) for v in (target.x(), target.y())):
            raise ValueError('Closure position must be finite.')
        cable_id = optional(record['cable_id'])
        plan = (
            self.closure_plan(cable_id, target, float(record['width']))
            if cable_id
            else None
        )
        with self.repo.command('Move closure enclosure'):
            self.repo.update(
                'elements',
                element.id(),
                geometry=QgsGeometry.fromPointXY(
                    plan['anchor'] if plan else target
                ),
            )
            if plan:
                self.repo.update(
                    'closures',
                    record.id(),
                    {k: plan[k] for k in ('angle', 'top', 'bottom')},
                )
            self._update_closure_frame(identity)

    def _update_closure_frame(self, identity):
        record = self.repo.find('closures', identity)
        element = self.repo.find('elements', identity)
        self.repo.update(
            'frames',
            self.repo.find('frames', identity).id(),
            geometry=closure_frame(element, record),
        )

    @staticmethod
    def _closure_members(shape, scene):
        desired = {
            ('fiber', identity)
            for identity, path in scene.drawn_fibers.items()
            if shape.intersects(path)
        }
        desired.update(
            ('link', p['link_id'])
            for p in scene.splices.getFeatures()
            if shape.intersects(p.geometry())
        )
        desired.update(
            (
                'joint' if p['record_key'] == 'fiber_joints' else 'mark',
                p['record_id'],
            )
            for p in scene.optical_marks.getFeatures()
            if shape.intersects(p.geometry())
        )
        return desired

    def widen_closure(self, identity, cable_id):
        record = self.repo.find('closures', identity)
        element = self.repo.find('elements', identity)
        if record is None or element is None:
            raise ValueError('Select a closure enclosure first.')
        anchor = element.geometry().asPoint()
        paths = [
            [
                local_point(
                    p,
                    (anchor.x(), anchor.y()),
                    float(record['angle']),
                    CAD_SCALE,
                )
                for p in path
            ]
            for path in self.closure_bundle(cable_id).values()
        ]
        if not any(
            min(p[0] for p in path) <= float(record['width'])
            and max(p[0] for p in path) >= 0
            for path in paths
        ):
            raise ValueError(
                'The additional cable does not cross this frame. Move or '
                'resize the closure first.'
            )
        low, high = cross_section(paths)
        with self.repo.command('Widen closure to include cable'):
            self.repo.update(
                'closures',
                record.id(),
                {
                    'top': max(float(record['top']), high + CLOSURE_MARGIN),
                    'bottom': min(
                        float(record['bottom']), low - CLOSURE_MARGIN
                    ),
                },
            )
            self._update_closure_frame(identity)

    def refresh_closures(self):
        """Run inside the outer transaction; retain placement and only expand
        bounds."""
        records = self.repo.features('closures')
        if not records:
            return
        scene = self._closure_scene()
        cables = {c['cable_id'] for c in self.repo.features('cables')}
        members = self.repo.features('closure_members')
        for record in records:
            identity = record['obj_id']
            element = self.repo.find('elements', identity)
            cable_id = optional(record['cable_id'])
            if cable_id is not None and cable_id not in cables:
                self.repo.update('closures', record.id(), {'cable_id': None})
                cable_id = None
            shape = closure_frame(element, record)
            if cable_id:
                paths = self.closure_bundle(cable_id, scene)
                for member in members:
                    if (
                        member['closure_id'] == identity
                        and member['kind'] == 'fiber'
                        and member['ref_id'] in scene.drawn_fibers
                    ):
                        paths[member['ref_id']] = [
                            (p.x(), p.y())
                            for p in scene.drawn_fibers[
                                member['ref_id']
                            ].asPolyline()
                        ]
                anchor = element.geometry().asPoint()
                local = [
                    [
                        local_point(
                            p,
                            (anchor.x(), anchor.y()),
                            float(record['angle']),
                            CAD_SCALE,
                        )
                        for p in path
                    ]
                    for path in paths.values()
                ]
                low, high = cross_section(local)
                top, bottom = (
                    max(float(record['top']), high + CLOSURE_MARGIN),
                    min(float(record['bottom']), low - CLOSURE_MARGIN),
                )
                self.repo.update(
                    'closures', record.id(), {'top': top, 'bottom': bottom}
                )
                record = self.repo.find('closures', identity)
                shape = closure_frame(element, record)
                self.repo.update(
                    'frames',
                    self.repo.find('frames', identity).id(),
                    geometry=shape,
                )
            desired = self._closure_members(shape, scene)
            owned = {
                (m['kind'], m['ref_id']): m
                for m in members
                if m['closure_id'] == identity
            }
            self.repo.delete(
                'closure_members',
                [m.id() for pair, m in owned.items() if pair not in desired],
            )
            for kind, ref_id in sorted(desired - set(owned)):
                self.repo.insert(
                    'closure_members',
                    {
                        'member_id': uid(),
                        'closure_id': identity,
                        'kind': kind,
                        'ref_id': ref_id,
                    },
                )

    def rows(self, key):
        return [fields_dict(f) for f in self.repo.features(key)]

    def ports_of(self, obj_id):
        modules = {
            m['module_id']: int(m['number'])
            for m in self.repo.features('modules')
            if m['obj_id'] == obj_id
        }
        return sorted(
            (p for p in self.repo.features('ports') if p['obj_id'] == obj_id),
            key=lambda p: (
                modules.get(optional(p['module_id']), 0),
                int(p['module_port'])
                if optional(p['module_port']) is not None
                else int(p['fiber_no']),
            ),
        )

    def modules_of(self, obj_id):
        return sorted(
            (
                m
                for m in self.repo.features('modules')
                if m['obj_id'] == obj_id
            ),
            key=lambda m: int(m['number']),
        )

    def port_address(self, port):
        element = self.repo.find('elements', port['obj_id'])
        module = self.repo.find('modules', optional(port['module_id']))
        if module is not None:
            return '{}{}/{}'.format(
                element['name'],
                ' ' + module['label'] if module['label'] else '',
                port['module_port'],
            )
        return '{} / {}'.format(element['name'], port['label'])

    def unique_name(self, prefix, key='elements'):
        names = {f['name'] for f in self.repo.features(key)}
        index = 1
        while '{}-{:02d}'.format(prefix, index) in names:
            index += 1
        return '{}-{:02d}'.format(prefix, index)

    def _name(self, name, key, identity=None):
        name = name.strip()
        if not name:
            raise ValueError('Name cannot be empty.')
        if any(
            f['name'] == name and f[ID_FIELDS[key]] != identity
            for f in self.repo.features(key)
        ):
            raise ValueError('An object already has this name: ' + name)
        return name

    def add_element(self, kind, name, count, side, at):
        check_capacity(kind, count)
        name = self._name(name, 'elements')
        if not math.isfinite(at.x()) or not math.isfinite(at.y()):
            raise ValueError('Element position must be finite.')
        obj_id = uid()
        with self.repo.command('Place ' + kind):
            self.repo.insert(
                'frames',
                {'obj_id': obj_id, 'kind': kind},
                frame_geometry(kind, count, at),
            )
            self.repo.insert(
                'elements',
                {
                    'obj_id': obj_id,
                    'kind': kind,
                    'name': name,
                    'count': count,
                    'side': side,
                },
                QgsGeometry.fromPointXY(at),
            )
            for spec in port_specs(kind, count, side):
                self._insert_port(obj_id, spec, at)
        return obj_id

    def _insert_port(self, obj_id, spec, at, module_id=None, module_port=None):
        self.repo.insert(
            'ports',
            {
                'port_id': uid(),
                'obj_id': obj_id,
                'module_id': module_id,
                'module_port': module_port,
                'label': spec.label,
                'fiber_no': spec.number,
                'role': spec.role,
                'dx': spec.dx,
                'dy': spec.dy,
                'color': spec.color,
            },
            QgsGeometry.fromPointXY(pt(at.x() + spec.dx, at.y() + spec.dy)),
        )

    @staticmethod
    def _odf_definition(properties, name):
        properties.validate()
        labels = module_labels(properties.rack_module, properties.modules)
        family = housing_font_metrics('')[0]
        width = max(housing_font_metrics(label, family)[1] for label in labels)
        attrs = asdict(properties)
        attrs.pop(
            'kind'
        )  # Element kind is the authoritative persisted family.
        attrs['module_count'] = attrs.pop('modules')
        attrs['angle'] = attrs['orientation']
        attrs['orientation'] = int(round(properties.orientation)) % 360
        attrs['mirrored'] = int(attrs['mirrored'])
        attrs['row_positions'] = json.dumps(attrs['row_positions'])
        attrs.update(
            layout_version=housing_layout_version(properties.kind),
            font_family=family,
            rack_width=width * 1.7,
        )
        layout = housing_layout(properties, name, attrs['rack_width'])
        specs = []
        for number, point in layout.ports.items():
            dx, dy = transform(
                point,
                orientation=properties.orientation,
                mirrored=properties.mirrored,
            )
            local = (number - 1) % properties.capacity + 1
            label = (
                '{}/{}'.format(
                    labels[(number - 1) // properties.capacity], local
                )
                if properties.modules > 1
                else str(local)
            )
            specs.append(
                PortSpec(
                    label,
                    number,
                    'terminal',
                    dx,
                    dy,
                    FIBER_COLORS[(number - 1) % 12],
                )
            )
        return attrs, layout, specs

    def add_odf(self, label, properties, at):
        if properties.kind != 'ODF':
            raise ValueError('Use add_housing for another equipment family.')
        return self.add_housing(label, properties, at)

    def add_housing(self, label, properties, at):
        properties.validate()
        name = self._name(
            housing_name(
                label,
                properties.kind,
                (e['name'] for e in self.repo.features('elements')),
                self.settings()['language'],
            ),
            'elements',
        )
        if properties.splitter_ratio and not properties.splitter_feed:
            properties = replace(properties, splitter_feed=properties.capacity)
        attrs, layout, specs = self._odf_definition(properties, name)
        if not math.isfinite(at.x()) or not math.isfinite(at.y()):
            raise ValueError('Element position must be finite.')
        identity = uid()
        with self.repo.command('Place ' + HOUSINGS[properties.kind][0]):
            self.repo.insert(
                'elements',
                {
                    'obj_id': identity,
                    'kind': properties.kind,
                    'name': name,
                    'count': properties.total_capacity,
                    'side': 'center',
                },
                QgsGeometry.fromPointXY(at),
            )
            modules = []
            for number, label in enumerate(
                module_labels(properties.rack_module, properties.modules), 1
            ):
                module_id = uid()
                self.repo.insert(
                    'modules',
                    {
                        'module_id': module_id,
                        'obj_id': identity,
                        'number': number,
                        'label': label,
                        'capacity': properties.capacity,
                    },
                )
                modules.append(module_id)
            self.repo.insert(
                'housings', dict(attrs, obj_id=identity, module_id=modules[0])
            )
            self.save_drawing_language('housings', identity)
            self.repo.insert(
                'frames',
                {'obj_id': identity, 'kind': properties.kind},
                odf_frame(
                    layout, at, properties.orientation, properties.mirrored
                ),
            )
            for spec in specs:
                self._insert_port(
                    identity,
                    spec,
                    at,
                    modules[(spec.number - 1) // properties.capacity],
                    (spec.number - 1) % properties.capacity + 1,
                )
            self._sync_contained_splitter(identity)
        return identity

    def edit_odf(self, identity, label, properties):
        if properties.kind != 'ODF':
            raise ValueError('Use edit_housing for another equipment family.')
        return self.edit_housing(identity, label, properties)

    def edit_housing(self, identity, label, properties, layout_update=False):
        element, housing = (
            self.repo.find('elements', identity),
            self.repo.find('housings', identity),
        )
        if element is None or housing is None:
            raise ValueError('Housing no longer exists.')
        if properties.kind != element['kind']:
            raise ValueError('The type of a saved housing cannot be changed.')
        if not layout_update:
            properties = self.multi_housing_properties(identity, properties)
        if properties.splitter_ratio and not properties.splitter_feed:
            properties = replace(properties, splitter_feed=properties.capacity)
        name = self._name(
            housing_name(
                label,
                properties.kind,
                language=self.drawing_language('housings', identity),
            ),
            'elements',
            identity,
        )
        attrs, layout, specs = self._odf_definition(properties, name)
        old_modules = self.modules_of(identity)
        retained = {
            m['module_id']
            for m in old_modules
            if int(m['number']) <= properties.modules
        }
        owned = self.ports_of(identity)
        existing = {(p['module_id'], int(p['module_port'])): p for p in owned}
        removed = [
            p
            for p in owned
            if p['module_id'] not in retained
            or int(p['module_port']) > properties.capacity
        ]
        occupied = self.occupied() | {
            f[k]
            for f in self.repo.features('fibers')
            for k in ('a_id', 'b_id')
        }
        if any(p['port_id'] in occupied for p in removed):
            raise ValueError(
                'Disconnect occupied ports before reducing capacity.'
            )
        at = element.geometry().asPoint()
        old_used = optional(housing['requested_used'])
        with self.repo.command('Edit ' + HOUSINGS[properties.kind][0]):
            self.repo.update(
                'elements',
                element.id(),
                {'name': name, 'count': properties.total_capacity},
            )
            self.repo.update('housings', housing.id(), attrs)
            self.repo.update(
                'frames',
                self.repo.find('frames', identity).id(),
                geometry=odf_frame(
                    layout, at, properties.orientation, properties.mirrored
                ),
            )
            self.repo.delete('ports', [p.id() for p in removed])
            modules = []
            for index, label in enumerate(
                module_labels(properties.rack_module, properties.modules)
            ):
                data = {
                    'obj_id': identity,
                    'number': index + 1,
                    'label': label,
                    'capacity': properties.capacity,
                }
                if index < len(old_modules):
                    module = old_modules[index]
                    self.repo.update('modules', module.id(), data)
                    modules.append(module['module_id'])
                else:
                    module_id = uid()
                    self.repo.insert(
                        'modules', dict(data, module_id=module_id)
                    )
                    modules.append(module_id)
            self.repo.delete(
                'modules',
                [
                    m.id()
                    for m in old_modules
                    if m['module_id'] not in retained
                ],
            )
            serial = max((int(p['fiber_no']) for p in owned), default=0)
            for spec in specs:
                module_id = modules[(spec.number - 1) // properties.capacity]
                local = (spec.number - 1) % properties.capacity + 1
                port = existing.get((module_id, local))
                if port is None:
                    serial += 1
                    self._insert_port(
                        identity,
                        replace(spec, number=serial),
                        at,
                        module_id,
                        local,
                    )
                else:
                    self.repo.update(
                        'ports',
                        port.id(),
                        {'label': spec.label, 'dx': spec.dx, 'dy': spec.dy},
                        QgsGeometry.fromPointXY(
                            pt(at.x() + spec.dx, at.y() + spec.dy)
                        ),
                    )
            self._update_connections(identity)
            self._sync_contained_splitter(identity)
            self._sync_attached_ends(identity)
            if (
                properties.requested_used != old_used
                and not self.housing_attachments(identity)
            ):
                for cable in self.repo.features('cables'):
                    if cable['layout_mode'] != 'rows':
                        continue
                    for field, side in (('from_obj', 'A'), ('to_obj', 'B')):
                        if optional(cable[field]) == identity:
                            used = (
                                (
                                    1
                                    if properties.splitter_ratio
                                    else min(
                                        int(cable['count']),
                                        properties.total_capacity,
                                    )
                                )
                                if properties.requested_used is None
                                else properties.requested_used
                            )
                            self._set_termination_count(
                                cable['cable_id'], side, identity, used
                            )

    def contained_splitter(self, parent_id):
        return next(
            (
                s
                for s in self.repo.features('contained_splitters')
                if s['parent_id'] == parent_id
            ),
            None,
        )

    def _sync_contained_splitter(self, identity):
        """Persist a child, its optical ports, loss and patch edges."""
        element, housing = (
            self.repo.find('elements', identity),
            self.repo.find('housings', identity),
        )
        if housing is None:
            return
        props = odf_properties(element, housing)
        record = self.contained_splitter(identity)
        if not props.splitter_ratio:
            if record is not None:
                self.repo.delete('contained_splitters', [record.id()])
                self.delete_element(record['splitter_id'])
            return
        layout = odf_drawing(element, housing).splitter
        at = element.geometry().asPoint()

        def position(p):
            return pt(
                *transform(
                    p, (at.x(), at.y()), props.orientation, props.mirrored
                )
            )

        corners = [position(p) for p in layout.frame]
        center = pt(
            sum(p.x() for p in corners[:4]) / 4,
            sum(p.y() for p in corners[:4]) / 4,
        )
        if record is None:
            child_id = self.add_element(
                'SPLITTER',
                element['name'] + '-SP',
                props.splitter_ratio,
                'center',
                center,
            )
        else:
            child_id = record['splitter_id']
            child = self.repo.find('elements', child_id)
            self.repo.update(
                'elements',
                child.id(),
                {
                    'name': self._name(
                        element['name'] + '-SP', 'elements', child_id
                    ),
                    'count': props.splitter_ratio,
                },
                QgsGeometry.fromPointXY(center),
            )
        self.repo.update(
            'frames',
            self.repo.find('frames', child_id).id(),
            geometry=QgsGeometry.fromPolygonXY([corners]),
        )
        child_ports = {int(p['fiber_no']): p for p in self.ports_of(child_id)}
        old_ids = {p['port_id'] for p in child_ports.values()}
        parent_ports = {
            int(p['module_port']): p for p in self.ports_of(identity)
        }
        expected = [(parent_ports[props.splitter_feed], 0)] + [
            (parent_ports[n], n) for n in range(1, props.splitter_ratio + 1)
        ]
        desired_pairs = {
            (p['port_id'], child_ports[n]['port_id'])
            for p, n in expected
            if n in child_ports
        }
        for link in self.repo.features('links'):
            if (
                old_ids.intersection((link['from_id'], link['to_id']))
                and (link['from_id'], link['to_id']) not in desired_pairs
            ):
                self.repo.delete('links', [link.id()])
        self.repo.delete(
            'ports',
            [p.id() for n, p in child_ports.items() if n not in layout.ports],
        )
        for n, local in layout.ports.items():
            point = position(local)
            data = {'dx': point.x() - center.x(), 'dy': point.y() - center.y()}
            if n in child_ports:
                self.repo.update(
                    'ports',
                    child_ports[n].id(),
                    data,
                    QgsGeometry.fromPointXY(point),
                )
            else:
                self._insert_port(
                    child_id,
                    PortSpec(
                        'O{}'.format(n),
                        n,
                        'output',
                        data['dx'],
                        data['dy'],
                        FIBER_COLORS[(n - 1) % 12],
                    ),
                    center,
                )
        child_ports = {int(p['fiber_no']): p for p in self.ports_of(child_id)}
        for parent_port, n in expected:
            child_port = child_ports[n]
            link = next(
                (
                    link_record
                    for link_record in self.repo.features('links')
                    if link_record['from_id'] == parent_port['port_id']
                    and link_record['to_id'] == child_port['port_id']
                ),
                None,
            )
            link_id = (
                link['link_id']
                if link is not None
                else self.add_link(parent_port, child_port, 'patch')
            )
            link = self.repo.find('links', link_id)
            path = (
                layout.paths[n] if n == 0 else list(reversed(layout.paths[n]))
            )
            self.repo.update(
                'links',
                link.id(),
                {'color': '#0000ff', 'loss_db': 0.0},
                QgsGeometry.fromPolylineXY([position(p) for p in path]),
            )
        data = {
            'parent_id': identity,
            'feed_port_id': parent_ports[props.splitter_feed]['port_id'],
            'loss_db': SPLITTER_LOSSES[props.splitter_ratio],
        }
        if record is None:
            self.repo.insert(
                'contained_splitters', dict(data, splitter_id=child_id)
            )
        else:
            self.repo.update('contained_splitters', record.id(), data)

    def occupied(self):
        active = {a['obj_id'] for a in self.repo.features('active_equipment')}
        ports = {
            p['port_id']
            for p in self.repo.features('ports')
            if p['obj_id'] in active
        }
        bound = {
            e['port_id']
            for e in self.repo.features('fiber_ends')
            if optional(e['port_id'])
        }
        return (
            {
                link[key]
                for link in self.repo.features('links')
                for key in ('from_id', 'to_id')
            }
            | self.joint_occupied()
            | ports.intersection(bound)
        )

    def add_link(self, a, b, kind='splice'):
        if kind not in ('splice', 'patch'):
            raise ValueError('Connection type must be splice or patch.')
        a = self.repo.find('ports', a['port_id']) if a is not None else None
        b = self.repo.find('ports', b['port_id']) if b is not None else None
        bound = {
            e['port_id']
            for e in self.repo.features('fiber_ends')
            if optional(e['port_id'])
        }
        if any(
            p is not None
            and p['port_id'] in bound
            and self.repo.find('active_equipment', p['obj_id']) is not None
            for p in (a, b)
        ):
            raise ValueError(
                'An active-equipment port already has a cable fiber.'
            )
        validate_connection(
            a,
            b,
            {f['obj_id']: f['kind'] for f in self.repo.features('elements')},
            self.occupied(),
        )
        identity = uid()
        with self.repo.command('Create ' + kind):
            self.repo.insert(
                'links',
                {
                    'link_id': identity,
                    'from_id': a['port_id'],
                    'to_id': b['port_id'],
                    'kind': kind,
                    'color': a['color'],
                    'loss_db': 0.1,
                },
                link_geometry(a, b),
            )
        return identity

    def delete_link(self, identity):
        link = self.repo.find('links', identity)
        if link is None:
            raise ValueError('Connection no longer exists.')
        children = {
            s['splitter_id'] for s in self.repo.features('contained_splitters')
        }
        if any(
            self.repo.find('ports', link[field])['obj_id'] in children
            for field in ('from_id', 'to_id')
        ):
            raise ValueError(
                'Edit contained splitter connections through the housing '
                'Properties.'
            )
        with self.repo.command('Delete connection'):
            self.repo.delete('links', [link.id()])

    def auto_connect(self, first, second):
        if first == second:
            raise ValueError('Choose two different cable blocks.')
        if any(
            self.repo.find('elements', identity)['kind'] != 'CABLE'
            for identity in (first, second)
        ):
            raise ValueError('Auto 1:1 requires two legacy cable port blocks.')
        count = 0
        with self.repo.command('Auto 1:1'):
            occupied = self.occupied()
            b = {int(p['fiber_no']): p for p in self.ports_of(second)}
            for p in self.ports_of(first):
                q = b.get(int(p['fiber_no']))
                if (
                    q is not None
                    and p['port_id'] not in occupied
                    and q['port_id'] not in occupied
                ):
                    self.add_link(p, q)
                    occupied.update((p['port_id'], q['port_id']))
                    count += 1
        return count

    def available_cable_ports(self, obj_id):
        used = {
            f[key]
            for f in self.repo.features('fibers')
            for key in ('a_id', 'b_id')
        }
        if self.repo.find('active_equipment', obj_id) is not None:
            used.update(self.occupied())
        ports = [p for p in self.ports_of(obj_id) if p['port_id'] not in used]
        element = self.repo.find('elements', obj_id)
        if (
            element is not None
            and element['kind'] in SPLITTER_HOUSINGS
            and self.repo.find('housings', obj_id) is not None
        ):
            housing = self.repo.find('housings', obj_id)
            feed = int(housing['splitter_feed']) or int(housing['capacity'])
            ratio = int(housing['splitter_ratio'])
            ports.sort(
                key=lambda p: (
                    not ratio < int(p['module_port']) <= feed,
                    -int(p['module_port']),
                )
            )
        return ports

    def ends_of(self, cable_id, side=None):
        fibers = {
            f['fiber_id']: int(f['fiber_no'])
            for f in self.repo.features('fibers')
            if f['cable_id'] == cable_id
        }
        return sorted(
            (
                e
                for e in self.repo.features('fiber_ends')
                if e['fiber_id'] in fibers
                and (side is None or e['side'] == side)
            ),
            key=lambda e: (fibers[e['fiber_id']], e['side']),
        )

    def free_cable_ends(self):
        return [
            (c['cable_id'], side)
            for c in self.repo.features('cables')
            if c['layout_mode'] == 'rows'
            for side, field in (('A', 'from_obj'), ('B', 'to_obj'))
            if optional(c[field]) is None
        ]

    def _sync_cable_ends(self, cable_id):
        cable = self.repo.find('cables', cable_id)
        if cable is None:
            return
        ports = {p['port_id']: p for p in self.repo.features('ports')}
        fibers = {
            f['fiber_id']: f
            for f in self.repo.features('fibers')
            if f['cable_id'] == cable_id
        }
        housing_cache = {}
        attachments = {
            r['side']: r
            for r in self.repo.features('housing_cables')
            if r['cable_id'] == cable_id
        }
        paths = {}
        outlet_mode = self.outlet_mode(cable_id)
        if cable['layout_mode'] == 'rows':
            route = cable.geometry().asPolyline()
            rows = rows_from_record(cable)
            validate_vertices([(p.x(), p.y()) for p in route])
            paths = {
                f['fiber_id']: cable_fiber_path(
                    route, rows[int(f['fiber_no']) - 1], outlet_mode
                )
                for f in fibers.values()
            }
        for end in self.ends_of(cable_id):
            port = ports.get(optional(end['port_id']))
            if port is not None:
                geometry = port.geometry()
            elif paths:
                attachment = attachments.get(end['side'])
                owner = optional(
                    cable['from_obj' if end['side'] == 'A' else 'to_obj']
                )
                if owner not in housing_cache:
                    housing = (
                        self.repo.find('housings', owner)
                        if owner is not None
                        else None
                    )
                    element = (
                        self.repo.find('elements', owner)
                        if housing is not None
                        else None
                    )
                    housing_cache[owner] = (
                        (element, odf_properties(element, housing))
                        if housing is not None
                        else None
                    )
                owner_properties = housing_cache[owner]
                if owner_properties is not None:
                    from .geometry import odf_point

                    element, props = owner_properties
                    number = int(fibers[end['fiber_id']]['fiber_no'])
                    row = (
                        json.loads(attachment['rows'])[number - 1]
                        if attachment is not None
                        else props.row_positions[number - 1]
                        if number <= len(props.row_positions)
                        else rows[number - 1] / CAD_SCALE
                    )
                    point = odf_point(
                        (0, row),
                        element.geometry().asPoint(),
                        props.orientation,
                        props.mirrored,
                    )
                else:
                    point = paths[end['fiber_id']][
                        0 if end['side'] == 'A' else -1
                    ]
                geometry = QgsGeometry.fromPointXY(point)
            else:
                raise ValueError(
                    'An unattached endpoint needs a routed cable.'
                )
            self.repo.update('fiber_ends', end.id(), geometry=geometry)

    def _sync_attached_ends(self, identity):
        for cable in self.repo.features('cables'):
            if identity in (
                optional(cable['from_obj']),
                optional(cable['to_obj']),
            ):
                self._sync_cable_ends(cable['cable_id'])

    def add_loose_cable(
        self,
        name,
        count,
        vertices,
        length_m=0.0,
        group_size=12,
        properties=None,
    ):
        name = self._name(name, 'cables')
        rows = (
            properties.validate().rows
            if properties
            else fiber_rows(count, group_size)
        )
        if properties and (
            count != properties.count
            or group_size != properties.fibers_per_tube
        ):
            raise ValueError(
                'Physical cable capacity disagrees with the route.'
            )
        vertices = [QgsPointXY(p) for p in vertices]
        validate_row_route(vertices, rows, MIN_SEGMENT if properties else 0)
        if (
            properties
            and len(vertices) == 2
            and vertices[0].distance(vertices[1]) < MIN_STRAIGHT - 1e-6
        ):
            raise ValueError(
                'A straight cable must be at least 12 CAD units long.'
            )
        if not math.isfinite(length_m) or length_m < 0:
            raise ValueError('Real cable length must be a nonnegative number.')
        identity = uid()
        with self.repo.command('Draw unattached cable'):
            self.repo.insert(
                'cables',
                {
                    'cable_id': identity,
                    'name': name,
                    'count': count,
                    'length_m': length_m,
                    'layout_mode': 'rows',
                    'group_size': group_size,
                    **(properties.attributes() if properties else {}),
                },
                QgsGeometry.fromPolylineXY(vertices),
            )
            self.save_drawing_language('cables', identity)
            for number, offset in enumerate(rows, 1):
                fiber_id = uid()
                self.repo.insert(
                    'fibers',
                    {
                        'fiber_id': fiber_id,
                        'cable_id': identity,
                        'fiber_no': number,
                        'color': properties.color(
                            (number - 1) % group_size + 1
                        )[0]
                        if properties
                        else FIBER_COLORS[(number - 1) % 12],
                        'tube_no': (number - 1) // group_size + 1
                        if properties
                        else 0,
                        'tube_fiber': (number - 1) % group_size + 1
                        if properties
                        else 0,
                        'purpose': 'nmFree' if properties else '',
                        'fiber_type': properties.fiber_type
                        if properties
                        else '',
                    },
                )
                path = parallel_path(vertices, offset)
                for side, point in (('A', path[0]), ('B', path[-1])):
                    self.repo.insert(
                        'fiber_ends',
                        {'end_id': uid(), 'fiber_id': fiber_id, 'side': side},
                        QgsGeometry.fromPointXY(point),
                    )
        return identity

    def add_catalog_cable(
        self,
        name,
        properties,
        vertices,
        length_m=0,
        endpoints=None,
        widen=True,
        endpoint_modules=None,
    ):
        properties.validate()
        vertices = list(vertices)
        if not name.strip():
            names = {c['name'] for c in self.repo.features('cables')}
            number = 1
            while 'K' + str(number) in names:
                number += 1
            name = 'K' + str(number)
        with self.repo.command('Draw catalogue cable'):
            identity = self.add_loose_cable(
                name,
                properties.count,
                vertices,
                length_m,
                properties.fibers_per_tube,
                properties,
            )
            if widen:
                for side, owner in (endpoints or {}).items():
                    element = self.repo.find('elements', owner)
                    record = self.repo.find('housings', owner)
                    if record is None or element is None:
                        raise ValueError(
                            'Cable snapping requires a housing connector.'
                        )
                    end = self.ends_of(identity, side)[0].geometry().asPoint()
                    module_no = (endpoint_modules or {}).get(side, 1)
                    existing = any(
                        owner
                        in (optional(c['from_obj']), optional(c['to_obj']))
                        for c in self.repo.features('cables')
                    )
                    if side in (endpoint_modules or {}) and (
                        existing or module_no != 1
                    ):
                        self.add_housing_cable(
                            owner, identity, side, module_no=module_no
                        )
                        continue
                    if end.distance(element.geometry().asPoint()) > 1e-6:
                        raise ValueError(
                            'Cable endpoint must meet the first housing '
                            'connector exactly.'
                        )
                    saved = odf_properties(element, record)
                    self.attach_housing(
                        identity, side, element['name'], saved, owner
                    )
                for record in self.repo.features('closures'):
                    shape = closure_frame(
                        self.repo.find('elements', record['obj_id']), record
                    )
                    if any(
                        shape.intersects(QgsGeometry.fromPointXY(p))
                        for p in (vertices[0], vertices[-1])
                    ):
                        self.widen_closure(record['obj_id'], identity)
            return identity

    def edit_catalog_cable(self, identity, name, properties, length_m):
        cable = self.repo.find('cables', identity)
        if cable is None or cable['cable_version'] != CABLE_VERSION:
            raise ValueError('Choose a catalogue cable.')
        properties.validate()
        old = properties_from_record(cable)
        if (
            properties.count != old.count
            or properties.fibers_per_tube != old.fibers_per_tube
            or properties.rows != old.rows
        ):
            raise ValueError(
                'Cable tube structure and inherited rows are fixed after '
                'creation. Draw a new cable to change them.'
            )
        with self.repo.command('Edit catalogue cable'):
            self.edit_cable(identity, name, length_m)
            self.repo.update('cables', cable.id(), properties.attributes())
            for fiber in self.repo.features('fibers'):
                if fiber['cable_id'] == identity:
                    attrs = {
                        'color': properties.color(int(fiber['tube_fiber']))[0]
                    }
                    if fiber['fiber_type'] == old.fiber_type:
                        attrs['fiber_type'] = properties.fiber_type
                    self.repo.update('fibers', fiber.id(), attrs)
            self.validate_fiber_operations()

    def odf_attachment_plan(self, cable_id, side, properties):
        return self.housing_attachment_plan(cable_id, side, properties)

    def housing_attachment_plan(self, cable_id, side, properties):
        properties.validate()
        cable = self.repo.find('cables', cable_id)
        if cable is None or side not in ('A', 'B'):
            raise ValueError('Choose an existing cable end.')
        owner = optional(cable['from_obj' if side == 'A' else 'to_obj'])
        if owner is not None:
            element = self.repo.find('elements', owner)
            raise ValueError(
                'This cable end is already attached to {}. Choose an '
                'unattached end.'.format(element['name'])
            )
        if cable['layout_mode'] != 'rows':
            raise ValueError('Choose the free end of an unattached cable.')
        route = cable.geometry().asPolyline()
        a, b = (route[0], route[1]) if side == 'A' else (route[-2], route[-1])
        angle = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x())) % 360
        ends = self.ends_of(cable_id, side)
        anchor = ends[0].geometry().asPoint()
        used = (
            (
                1
                if properties.splitter_ratio
                else min(len(ends), properties.total_capacity)
            )
            if properties.requested_used is None
            else properties.requested_used
        )
        if used > min(len(ends), properties.total_capacity):
            raise ValueError(
                'Used exceeds the cable fiber count or connector capacity.'
            )
        theta = math.radians(angle)
        rows = []
        for end in ends:
            point = end.geometry().asPoint()
            dx, dy = point.x() - anchor.x(), point.y() - anchor.y()
            if abs(dx * math.cos(theta) + dy * math.sin(theta)) > 1e-6:
                raise ValueError(
                    'The cable fiber ends do not share a connector column.'
                )
            rows.append(
                (dy * math.cos(theta) - dx * math.sin(theta)) / CAD_SCALE
            )
        return replace(
            properties,
            orientation=angle,
            mirrored=side == 'B',
            row_positions=tuple(rows),
            requested_used=used,
        ).validate(), anchor

    def attach_odf(self, cable_id, side, label, properties, identity=None):
        if properties.kind != 'ODF':
            raise ValueError(
                'Use attach_housing for another equipment family.'
            )
        return self.attach_housing(cable_id, side, label, properties, identity)

    def attach_housing(self, cable_id, side, label, properties, identity=None):
        properties, anchor = self.housing_attachment_plan(
            cable_id, side, properties
        )
        if identity is not None:
            if any(
                identity in (optional(c['from_obj']), optional(c['to_obj']))
                for c in self.repo.features('cables')
            ):
                raise ValueError(
                    'This housing already has a cable. Use Edit → Add cable '
                    'for additional cables.'
                )
        with self.repo.command(
            'Attach ' + HOUSINGS[properties.kind][0] + ' to cable'
        ):
            if identity is None:
                identity = self.add_housing(label, properties, anchor)
            else:
                self.edit_housing(identity, label, properties)
                self.move_element(identity, anchor)
            cable = self.repo.find('cables', cable_id)
            self.repo.update(
                'cables',
                cable.id(),
                {'from_obj' if side == 'A' else 'to_obj': identity},
            )
            self._set_termination_count(
                cable_id, side, identity, properties.requested_used
            )
        return identity

    def _set_termination_count(self, cable_id, side, identity, used):
        ends = self.ends_of(cable_id, side)
        bound = [e for e in ends if optional(e['port_id']) is not None]
        if not 0 <= used <= min(len(ends), len(self.ports_of(identity))):
            raise ValueError(
                'Used exceeds the cable fiber count or connector capacity.'
            )
        fibers = {f['fiber_id']: f for f in self.repo.features('fibers')}
        field = 'a_id' if side == 'A' else 'b_id'
        for end in reversed(bound[used:]):
            self.repo.update('fiber_ends', end.id(), {'port_id': None})
            self.repo.update(
                'fibers', fibers[end['fiber_id']].id(), {field: None}
            )
        bound = bound[:used]
        free_ports = iter(self.available_cable_ports(identity))
        pending = used - len(bound)
        bound_ids = {e['end_id'] for e in bound}
        for end in ends:
            if pending and end['end_id'] not in bound_ids:
                port = next(free_ports, None)
                if port is None:
                    raise ValueError(
                        'The housing has too few free connectors.'
                    )
                self.repo.update(
                    'fiber_ends', end.id(), {'port_id': port['port_id']}
                )
                self.repo.update(
                    'fibers',
                    fibers[end['fiber_id']].id(),
                    {field: port['port_id']},
                )
                pending -= 1
        self._sync_cable_ends(cable_id)

    def detach_cable_end(self, cable_id, side):
        cable = self.repo.find('cables', cable_id)
        if (
            cable is None
            or cable['layout_mode'] != 'rows'
            or side not in ('A', 'B')
        ):
            raise ValueError(
                'Choose an end of a cable drawn with unattached endpoints.'
            )
        with self.repo.command('Detach cable end'):
            owner = optional(cable['from_obj' if side == 'A' else 'to_obj'])
            if owner is not None:
                self._set_termination_count(cable_id, side, owner, 0)
            self.repo.update(
                'cables',
                cable.id(),
                {'from_obj' if side == 'A' else 'to_obj': None},
            )
            attachment = self.housing_attachment(cable_id, side)
            if attachment is not None:
                self.repo.delete('housing_cables', [attachment.id()])
                self.refresh_housing_attachment_counts(owner)
                self._update_multi_used(owner)
            self._sync_cable_ends(cable_id)

    def add_cable(self, name, count, first, second, vertices, length_m=0.0):
        name = self._name(name, 'cables')
        if first == second:
            raise ValueError('A cable must connect two different elements.')
        if not isinstance(count, int) or not 1 <= count <= 576:
            raise ValueError('Fiber count must be between 1 and 576.')
        if not math.isfinite(length_m) or length_m < 0:
            raise ValueError('Real cable length must be a nonnegative number.')
        a, b = (
            self.repo.find('elements', identity)
            for identity in (first, second)
        )
        if a is None or b is None:
            raise ValueError('Cable endpoint equipment no longer exists.')
        if any(
            self.repo.find('closures', identity) is not None
            for identity in (first, second)
        ):
            raise ValueError(
                'A closure encloses an existing cable and has no adapters. '
                'Draw the cable first.'
            )
        if any(
            s['splitter_id'] in (first, second)
            for s in self.repo.features('contained_splitters')
        ):
            raise ValueError(
                'Connect cables to the housing connectors, rather than its '
                'contained splitter.'
            )
        vertices = [QgsPointXY(p) for p in vertices]
        if len(vertices) < 2:
            raise ValueError('A cable needs a start and end.')
        vertices[0], vertices[-1] = (
            a.geometry().asPoint(),
            b.geometry().asPoint(),
        )
        validate_vertices([(p.x(), p.y()) for p in vertices])
        left, right = (
            self.available_cable_ports(first),
            self.available_cable_ports(second),
        )
        splitter = self.contained_splitter(first)
        if splitter is not None:
            ratio = int(
                self.repo.find('elements', splitter['splitter_id'])['count']
            )
            left.sort(
                key=lambda p: (
                    int(p['module_port']) > ratio,
                    int(p['module_port']),
                )
            )
        for equipment, neighbor, available in ((a, b, left), (b, a, right)):
            if equipment['kind'] == 'CLOSURE':
                toward_right = (
                    neighbor.geometry().asPoint().x()
                    > equipment.geometry().asPoint().x()
                )
                available.sort(
                    key=lambda p: (
                        bool(p['dx'] > 0) != toward_right,
                        int(p['fiber_no']),
                    )
                )
        if min(len(left), len(right)) < count:
            raise ValueError(
                'Not enough unused cable ports: {} has {}, {} has {}; cable '
                'needs {}.'.format(
                    a['name'], len(left), b['name'], len(right), count
                )
            )
        for p, q in zip(left[:count], right[:count]):
            if p['role'] == q['role'] and p['role'] in ('input', 'output'):
                raise ValueError(
                    'Cable cannot join two optical inputs or two optical '
                    'outputs.'
                )
        cable_id = uid()
        with self.repo.command('Draw cable'):
            self.repo.insert(
                'cables',
                {
                    'cable_id': cable_id,
                    'name': name,
                    'count': count,
                    'from_obj': first,
                    'to_obj': second,
                    'length_m': length_m,
                    'layout_mode': 'legacy',
                    'group_size': 12,
                },
                QgsGeometry.fromPolylineXY(vertices),
            )
            self.save_drawing_language('cables', cable_id)
            for index, (p, q) in enumerate(
                zip(left[:count], right[:count]), 1
            ):
                fiber_id = uid()
                self.repo.insert(
                    'fibers',
                    {
                        'fiber_id': fiber_id,
                        'cable_id': cable_id,
                        'fiber_no': index,
                        'a_id': p['port_id'],
                        'b_id': q['port_id'],
                        'color': FIBER_COLORS[(index - 1) % 12],
                    },
                )
                for port, side in ((p, 'A'), (q, 'B')):
                    self.repo.insert(
                        'fiber_ends',
                        {
                            'end_id': uid(),
                            'fiber_id': fiber_id,
                            'side': side,
                            'port_id': port['port_id'],
                        },
                        port.geometry(),
                    )
            for element in (a, b):
                housing = self.repo.find('housings', element['obj_id'])
                if (
                    housing is not None
                    and element['kind'] in CABINET_HOUSINGS
                    and not int(housing['splitter_ratio'])
                ):
                    props = odf_properties(element, housing)
                    occupied = {
                        f[k]
                        for f in self.repo.features('fibers')
                        for k in ('a_id', 'b_id')
                    }
                    visible = max(
                        (
                            props.capacity - int(p['module_port']) + 1
                            for p in self.ports_of(element['obj_id'])
                            if p['port_id'] in occupied
                        ),
                        default=0,
                    )
                    if visible > (
                        props.requested_used
                        if props.requested_used is not None
                        else min(6, props.capacity)
                    ):
                        self.edit_housing(
                            element['obj_id'],
                            element['name'],
                            replace(props, requested_used=visible),
                        )
        return cable_id

    def _update_connections(self, obj_id):
        ports = {p['port_id']: p for p in self.repo.features('ports')}
        changed = {
            p['port_id'] for p in ports.values() if p['obj_id'] == obj_id
        }
        for link in self.repo.features('links'):
            if link['from_id'] in changed or link['to_id'] in changed:
                self.repo.update(
                    'links',
                    link.id(),
                    geometry=link_geometry(
                        ports[link['from_id']], ports[link['to_id']]
                    ),
                )

    def move_element(self, identity, target):
        element = self.repo.find('elements', identity)
        if element is None:
            raise ValueError('Element no longer exists.')
        if self.repo.find('active_equipment', identity) is not None:
            return self.move_active(identity, target)
        if self.repo.find('closures', identity) is not None:
            return self.move_closure(identity, target)
        if self.repo.find('contained_splitters', identity) is not None:
            raise ValueError(
                'Move the parent housing to move its contained splitter.'
            )
        if not math.isfinite(target.x()) or not math.isfinite(target.y()):
            raise ValueError('Element position must be finite.')
        with self.repo.command('Move element'):
            self.repo.update(
                'elements',
                element.id(),
                geometry=QgsGeometry.fromPointXY(target),
            )
            frame = self.repo.find('frames', identity)
            housing = self.repo.find('housings', identity)
            properties = (
                odf_properties(element, housing)
                if housing is not None
                else None
            )
            shape = (
                odf_frame(
                    odf_drawing(element, housing),
                    target,
                    properties.orientation,
                    properties.mirrored,
                )
                if housing is not None
                else frame_geometry(
                    element['kind'], int(element['count']), target
                )
            )
            self.repo.update('frames', frame.id(), geometry=shape)
            for p in self.ports_of(identity):
                self.repo.update(
                    'ports',
                    p.id(),
                    geometry=QgsGeometry.fromPointXY(
                        pt(target.x() + p['dx'], target.y() + p['dy'])
                    ),
                )
            self._update_connections(identity)
            self._sync_contained_splitter(identity)
            for cable in self.repo.features('cables'):
                if identity in (cable['from_obj'], cable['to_obj']):
                    vertices = cable.geometry().asPolyline()
                    index = 0 if cable['from_obj'] == identity else -1
                    original = list(vertices)
                    end_target = self.attachment_endpoint(
                        cable['cable_id'],
                        'A' if index == 0 else 'B',
                        target,
                        target,
                    )
                    vertices[index] = QgsPointXY(end_target)
                    if cable['cable_version'] == CABLE_VERSION:
                        horizontal = (
                            abs(
                                original[index].y()
                                - original[1 if index == 0 else -2].y()
                            )
                            < 1e-6
                        )
                        if self.outlet_mode(cable['cable_id']) == 'elbow':
                            vertices = self.moved_outlet_elbow(
                                original, index, end_target
                            )
                        elif (
                            len(vertices) == 2
                            and abs(vertices[0].x() - vertices[-1].x()) > 1e-6
                            and abs(vertices[0].y() - vertices[-1].y()) > 1e-6
                        ):
                            a, b = vertices
                            middle = (
                                (a.x() + b.x()) / 2
                                if horizontal
                                else (a.y() + b.y()) / 2
                            )
                            vertices = (
                                [a, pt(middle, a.y()), pt(middle, b.y()), b]
                                if horizontal
                                else [
                                    a,
                                    pt(a.x(), middle),
                                    pt(b.x(), middle),
                                    b,
                                ]
                            )
                        elif len(vertices) > 2:
                            adjacent = 1 if index == 0 else -2
                            point = vertices[adjacent]
                            vertices[adjacent] = (
                                pt(point.x(), end_target.y())
                                if horizontal
                                else pt(end_target.x(), point.y())
                            )
                        validate_row_route(
                            vertices, rows_from_record(cable), MIN_SEGMENT
                        )
                        if (
                            len(vertices) == 2
                            and vertices[0].distance(vertices[1])
                            < MIN_STRAIGHT - 1e-6
                        ):
                            raise ValueError(
                                'Moving this housing would make the straight '
                                'cable shorter than 12 CAD units.'
                            )
                    validate_vertices([(p.x(), p.y()) for p in vertices])
                    self.repo.update(
                        'cables',
                        cable.id(),
                        geometry=QgsGeometry.fromPolylineXY(vertices),
                    )
                    self._sync_cable_ends(cable['cable_id'])

    def edit_element(self, identity, name, count):
        element = self.repo.find('elements', identity)
        if element is None:
            raise ValueError('Element no longer exists.')
        active = self.repo.find('active_equipment', identity)
        if active is not None:
            from .active_layout import active_properties

            return self.edit_active(
                identity,
                name,
                replace(active_properties(element, active), count=count),
            )
        if self.repo.find('closures', identity) is not None:
            raise ValueError(
                'Edit the closure using its N/NL Properties form; it has no '
                'port capacity.'
            )
        if self.repo.find('contained_splitters', identity) is not None:
            raise ValueError(
                'Edit the contained splitter through its housing Properties.'
            )
        housing = self.repo.find('housings', identity)
        if housing is not None:
            return self.edit_housing(
                identity,
                name,
                replace(odf_properties(element, housing), capacity=count),
            )
        check_capacity(element['kind'], count)
        name = self._name(name, 'elements', identity)
        specs = port_specs(element['kind'], count, element['side'])
        existing = {int(p['fiber_no']): p for p in self.ports_of(identity)}
        desired = {p.number for p in specs}
        attached = {
            f[k]
            for f in self.repo.features('fibers')
            for k in ('a_id', 'b_id')
        }
        occupied = self.occupied() | attached
        removed = [p for n, p in existing.items() if n not in desired]
        if any(p['port_id'] in occupied for p in removed):
            raise ValueError(
                'Disconnect occupied ports before reducing capacity.'
            )
        center = element.geometry().asPoint()
        with self.repo.command('Edit element'):
            self.repo.update(
                'elements', element.id(), {'name': name, 'count': count}
            )
            self.repo.update(
                'frames',
                self.repo.find('frames', identity).id(),
                geometry=frame_geometry(element['kind'], count, center),
            )
            self.repo.delete('ports', [p.id() for p in removed])
            for spec in specs:
                if spec.number in existing:
                    self.repo.update(
                        'ports',
                        existing[spec.number].id(),
                        {
                            'label': spec.label,
                            'role': spec.role,
                            'dx': spec.dx,
                            'dy': spec.dy,
                            'color': spec.color,
                        },
                        QgsGeometry.fromPointXY(
                            pt(center.x() + spec.dx, center.y() + spec.dy)
                        ),
                    )
                else:
                    self._insert_port(identity, spec, center)
            self._update_connections(identity)
            self._sync_attached_ends(identity)

    def edit_cable(self, identity, name, length_m):
        name = self._name(name, 'cables', identity)
        if not math.isfinite(length_m) or length_m < 0:
            raise ValueError('Real cable length must be nonnegative.')
        cable = self.repo.find('cables', identity)
        if cable is None:
            raise ValueError('Cable no longer exists.')
        with self.repo.command('Edit cable properties'):
            self.repo.update(
                'cables', cable.id(), {'name': name, 'length_m': length_m}
            )

    def edit_vertices(self, identity, vertices):
        cable = self.repo.find('cables', identity)
        if cable is None:
            raise ValueError('Cable no longer exists.')
        vertices = list(vertices)
        if len(vertices) < 2:
            raise ValueError('A route needs two endpoints.')
        if cable['layout_mode'] == 'rows':
            for index, field in ((0, 'from_obj'), (-1, 'to_obj')):
                owner = self.repo.find('elements', optional(cable[field]))
                if owner is not None:
                    vertices[index] = owner.geometry().asPoint()
            validate_row_route(
                vertices,
                rows_from_record(cable),
                MIN_SEGMENT if cable['cable_version'] == CABLE_VERSION else 0,
            )
            if (
                cable['cable_version'] == CABLE_VERSION
                and len(vertices) == 2
                and vertices[0].distance(vertices[1]) < MIN_STRAIGHT - 1e-6
            ):
                raise ValueError(
                    'A straight cable must be at least 12 CAD units long.'
                )
        else:
            vertices[0] = (
                self.repo.find('elements', cable['from_obj'])
                .geometry()
                .asPoint()
            )
            vertices[-1] = (
                self.repo.find('elements', cable['to_obj'])
                .geometry()
                .asPoint()
            )
        validate_vertices([(p.x(), p.y()) for p in vertices])
        with self.repo.command('Edit cable bend'):
            self.repo.update(
                'cables',
                cable.id(),
                geometry=QgsGeometry.fromPolylineXY(vertices),
            )
            self._sync_cable_ends(identity)

    def _delete_cable(self, identity):
        cable = self.repo.find('cables', identity)
        if cable is None:
            raise ValueError('Cable no longer exists.')
        fibers = [
            f
            for f in self.repo.features('fibers')
            if f['cable_id'] == identity
        ]
        self.delete_drawing_language(identity)
        self.repo.delete(
            'outlets',
            [
                r.id()
                for r in self.repo.features('outlets')
                if r['cable_id'] == identity
            ],
        )
        attached_housings = {
            r['obj_id']
            for r in self.repo.features('housing_cables')
            if r['cable_id'] == identity
        }
        self.repo.delete(
            'housing_cables',
            [
                r.id()
                for r in self.repo.features('housing_cables')
                if r['cable_id'] == identity
            ],
        )
        self.delete_fiber_operations(f['fiber_id'] for f in fibers)
        self.repo.delete(
            'slack',
            [
                s.id()
                for s in self.repo.features('slack')
                if s['cable_id'] == identity
            ],
        )
        ends = {f[k] for f in fibers for k in ('a_id', 'b_id')}
        child_ports = {
            p['port_id']
            for s in self.repo.features('contained_splitters')
            for p in self.ports_of(s['splitter_id'])
        }
        self.repo.delete(
            'links',
            [
                f.id()
                for f in self.repo.features('links')
                if (f['from_id'] in ends or f['to_id'] in ends)
                and f['from_id'] not in child_ports
                and f['to_id'] not in child_ports
            ],
        )
        self.repo.delete('fibers', [f.id() for f in fibers])
        fiber_ids = {f['fiber_id'] for f in fibers}
        self.repo.delete(
            'fiber_ends',
            [
                e.id()
                for e in self.repo.features('fiber_ends')
                if e['fiber_id'] in fiber_ids
            ],
        )
        self.repo.delete('cables', [cable.id()])
        for owner in attached_housings:
            self._update_multi_used(owner)

    def delete_cable(self, identity):
        with self.repo.command('Delete cable and attached splices'):
            self._delete_cable(identity)

    def delete_element(self, identity):
        element = self.repo.find('elements', identity)
        if element is None:
            raise ValueError('Element no longer exists.')
        if self.repo.find('contained_splitters', identity) is not None:
            raise ValueError(
                'Remove the contained splitter through its housing Properties.'
            )
        with self.repo.command('Delete element and attachments'):
            self.delete_drawing_language(identity)
            for outlet in self.repo.features('outlets'):
                if optional(outlet['frame_id']) == identity:
                    self.repo.update(
                        'outlets',
                        outlet.id(),
                        {'frame_id': None, 'module_no': 0},
                    )
            self.repo.delete(
                'housing_cables',
                [r.id() for r in self.housing_attachments(identity)],
            )
            closure = self.repo.find('closures', identity)
            if closure is not None:
                self.repo.delete(
                    'closure_members',
                    [
                        m.id()
                        for m in self.repo.features('closure_members')
                        if m['closure_id'] == identity
                    ],
                )
                self.repo.delete('closures', [closure.id()])
            contained = self.contained_splitter(identity)
            if contained is not None:
                self.repo.delete('contained_splitters', [contained.id()])
                self.delete_element(contained['splitter_id'])
            for cable in self.repo.features('cables'):
                if identity in (cable['from_obj'], cable['to_obj']):
                    if cable['layout_mode'] == 'rows':
                        for field, side in (
                            ('from_obj', 'A'),
                            ('to_obj', 'B'),
                        ):
                            if optional(cable[field]) == identity:
                                self.detach_cable_end(cable['cable_id'], side)
                    else:
                        self._delete_cable(cable['cable_id'])
            ports = self.ports_of(identity)
            identities = {p['port_id'] for p in ports}
            self.delete_node_joints(identities)
            self.repo.delete(
                'links',
                [
                    f.id()
                    for f in self.repo.features('links')
                    if f['from_id'] in identities or f['to_id'] in identities
                ],
            )
            self.repo.delete('ports', [p.id() for p in ports])
            active = self.repo.find('active_equipment', identity)
            if active is not None:
                self.repo.delete('active_equipment', [active.id()])
            housing = self.repo.find('housings', identity)
            if housing is not None:
                self.repo.delete(
                    'modules', [m.id() for m in self.modules_of(identity)]
                )
                self.repo.delete('housings', [housing.id()])
            self.repo.delete(
                'frames', [self.repo.find('frames', identity).id()]
            )
            self.repo.delete('elements', [element.id()])

    def trace(self, identity):
        if (
            self.repo.find('ports', identity) is None
            and self.repo.find('fiber_ends', identity) is None
            and self.repo.find('fiber_nodes', identity) is None
        ):
            raise ValueError('Select an existing port or cable fiber end.')
        return connected_ports(
            identity,
            self.rows('ports'),
            self.rows('elements'),
            self.rows('links'),
            self.rows('fibers'),
            self.rows('fiber_ends'),
            self.rows('fiber_cuts'),
            self.rows('fiber_nodes'),
            self.rows('fiber_joints'),
        )

    def validate(self):
        """Reject broken workspaces before replacing the current canvas
        layers."""
        self.validate_workspace()
        data = {k: self.repo.features(k) for k in self.repo.layers}
        self.validate_housing_cables(data)
        self.validate_outlets(data)
        indexes = {}
        for key, records in data.items():
            ids = [f[ID_FIELDS[key]] for f in records]
            if len(ids) != len(set(ids)) or any(
                not identity for identity in ids
            ):
                raise ValueError('Missing or duplicate IDs in ' + key)
            indexes[key] = dict(zip(ids, records))
        elements, ports = indexes['elements'], indexes['ports']
        from .geometry import odf_point

        attachment_points = {}
        for record in data['housing_cables']:
            props = odf_properties(
                elements[record['obj_id']],
                indexes['housings'][record['obj_id']],
            )
            attachment_points[record['cable_id'], record['side']] = odf_point(
                (0, json.loads(record['rows'])[0]),
                elements[record['obj_id']].geometry().asPoint(),
                props.orientation,
                props.mirrored,
            )
        if set(indexes['frames']) != set(elements):
            raise ValueError('Every element must have exactly one frame.')
        owned_ports, owned_modules = {}, {}
        for port in ports.values():
            owned_ports.setdefault(port['obj_id'], []).append(port)
        for module in data['modules']:
            if module['obj_id'] not in indexes['housings']:
                raise ValueError('Orphaned housing module.')
            owned_modules.setdefault(module['obj_id'], []).append(module)
        for e in elements.values():
            if not e.hasGeometry() or e.geometry().isEmpty():
                raise ValueError('Element has no position.')
            center = e.geometry().asPoint()
            if not math.isfinite(center.x()) or not math.isfinite(center.y()):
                raise ValueError('Element has an invalid position.')
            frame = indexes['frames'][e['obj_id']].geometry()
            if frame.isEmpty() or not frame.intersects(e.geometry()):
                raise ValueError('Element frame does not contain its center.')
            owned = owned_ports.get(e['obj_id'], [])
            serials = [int(p['fiber_no']) for p in owned]
            if len(serials) != len(set(serials)) or any(
                n < 0 for n in serials
            ):
                raise ValueError('Duplicate or invalid port ordinals.')
            active = indexes['active_equipment'].get(e['obj_id'])
            if active is not None:
                if (
                    e['obj_id'] in indexes['housings']
                    or e['obj_id'] in indexes['contained_splitters']
                ):
                    raise ValueError(
                        'Standalone active equipment cannot also be a '
                        'housing or contained child.'
                    )
                self.validate_active(e, active, owned, frame)
                continue
            closure = indexes['closures'].get(e['obj_id'])
            if closure is not None:
                if (
                    e['kind'] != 'CLOSURE'
                    or indexes['frames'][e['obj_id']]['kind'] != 'CLOSURE'
                    or int(e['count']) != 0
                    or owned
                    or e['obj_id'] in indexes['housings']
                ):
                    raise ValueError(
                        'A closure enclosure has no physical adapter ports.'
                    )
                closure_properties(closure).validate()
                validate_closure_bounds(
                    float(closure['width']),
                    float(closure['top']),
                    float(closure['bottom']),
                )
                if (
                    closure['layout_version'] != CLOSURE_LAYOUT_VERSION
                    or not closure['font_family']
                    or not math.isfinite(float(closure['angle']))
                    or not 0 <= float(closure['angle']) < 360
                ):
                    raise ValueError('Invalid closure layout or font.')
                if (
                    optional(closure['cable_id']) is not None
                    and closure['cable_id'] not in indexes['cables']
                ):
                    raise ValueError('Closure references a missing cable.')
                if not frame.isGeosEqual(closure_frame(e, closure)):
                    raise ValueError(
                        'Closure frame does not match its saved bounds.'
                    )
                continue
            housing = indexes['housings'].get(e['obj_id'])
            if housing is None:
                check_capacity(e['kind'], int(e['count']))
                specs = port_specs(e['kind'], int(e['count']), e['side'])
                if sorted(serials) != sorted(s.number for s in specs):
                    raise ValueError('Port capacity mismatch on ' + e['name'])
                continue
            version = housing_layout_version(e['kind'])
            if (
                e['kind'] not in SUPPORTED_HOUSINGS
                or housing['layout_version'] != version
            ):
                raise ValueError('Invalid housing owner or layout version.')
            props = odf_properties(e, housing).validate()
            if int(e['count']) != props.total_capacity:
                raise ValueError(
                    'Housing total capacity does not match its modules.'
                )
            modules = sorted(
                owned_modules.get(e['obj_id'], []),
                key=lambda m: int(m['number']),
            )
            if [int(m['number']) for m in modules] != list(
                range(1, props.modules + 1)
            ):
                raise ValueError(
                    'Housing module sequence is incomplete or duplicated.'
                )
            if housing['module_id'] != modules[0]['module_id']:
                raise ValueError(
                    'Housing first-module identity does not match.'
                )
            if [m['label'] for m in modules] != module_labels(
                props.rack_module, props.modules
            ) or any(int(m['capacity']) != props.capacity for m in modules):
                raise ValueError(
                    'Housing module labels or capacities do not match.'
                )
            if (
                not housing['font_family']
                or not math.isfinite(float(housing['rack_width']))
                or housing['rack_width'] < 0
            ):
                raise ValueError('Invalid housing font or rack width.')
            layout = odf_drawing(e, housing)
            expected_pairs = {
                (m['module_id'], n)
                for m in modules
                for n in range(1, props.capacity + 1)
            }
            pairs = [(p['module_id'], int(p['module_port'])) for p in owned]
            if len(pairs) != len(set(pairs)) or set(pairs) != expected_pairs:
                raise ValueError(
                    'Housing module connector sequence is invalid.'
                )
            numbers = {m['module_id']: int(m['number']) for m in modules}
            for port in owned:
                slot = (numbers[port['module_id']] - 1) * props.capacity + int(
                    port['module_port']
                )
                dx, dy = transform(
                    layout.ports[slot],
                    orientation=props.orientation,
                    mirrored=props.mirrored,
                )
                if math.hypot(dx - port['dx'], dy - port['dy']) > 1e-6:
                    raise ValueError(
                        'Port does not match its saved housing layout.'
                    )
            expected = odf_frame(
                layout, center, props.orientation, props.mirrored
            )
            if not frame.isGeosEqual(expected):
                raise ValueError(
                    'Frame does not match its saved housing layout.'
                )
        if any(h['obj_id'] not in elements for h in data['housings']):
            raise ValueError('Orphaned housing properties.')
        for p in ports.values():
            if p['obj_id'] not in elements or not p.hasGeometry():
                raise ValueError('Orphaned or invalid port.')
            if optional(p['module_id']) is not None:
                module = indexes['modules'].get(p['module_id'])
                if module is None or module['obj_id'] != p['obj_id']:
                    raise ValueError('Port references an invalid module.')
            elif optional(p['module_port']) is not None:
                raise ValueError('Module port has no module identity.')
            center = elements[p['obj_id']].geometry().asPoint()
            point = p.geometry().asPoint()
            if (
                not all(
                    math.isfinite(v)
                    for v in (point.x(), point.y(), p['dx'], p['dy'])
                )
                or math.hypot(
                    point.x() - center.x() - p['dx'],
                    point.y() - center.y() - p['dy'],
                )
                > 1e-6
            ):
                raise ValueError(
                    'Port position does not match its element: ' + p['label']
                )
        parents = set()
        for record in data['contained_splitters']:
            parent = elements.get(record['parent_id'])
            child = elements.get(record['splitter_id'])
            housing = indexes['housings'].get(record['parent_id'])
            if (
                parent is None
                or parent['kind'] not in SPLITTER_HOUSINGS
                or housing is None
                or record['parent_id'] in parents
            ):
                raise ValueError(
                    'A contained splitter needs one valid housing parent.'
                )
            parents.add(record['parent_id'])
            props = odf_properties(parent, housing)
            if (
                child is None
                or child['kind'] != 'SPLITTER'
                or int(child['count']) != props.splitter_ratio
                or not props.splitter_ratio
            ):
                raise ValueError(
                    'Contained splitter ratio does not match its housing.'
                )
            if record['loss_db'] != SPLITTER_LOSSES[props.splitter_ratio]:
                raise ValueError(
                    'Contained splitter has an invalid optical loss.'
                )
            parent_ports = {
                int(p['module_port']): p for p in owned_ports[parent['obj_id']]
            }
            if (
                props.splitter_feed not in parent_ports
                or record['feed_port_id']
                != parent_ports[props.splitter_feed]['port_id']
            ):
                raise ValueError(
                    'Contained splitter feed does not match its connector.'
                )
            split_layout = odf_drawing(parent, housing).splitter
            origin = parent.geometry().asPoint()

            def position(point):
                return pt(
                    *transform(
                        point,
                        (origin.x(), origin.y()),
                        props.orientation,
                        props.mirrored,
                    )
                )

            expected_frame = QgsGeometry.fromPolygonXY(
                [[position(p) for p in split_layout.frame]]
            )
            if (
                not indexes['frames'][child['obj_id']]
                .geometry()
                .isGeosEqual(expected_frame)
            ):
                raise ValueError(
                    'Contained splitter frame is detached from its housing.'
                )
            child_ports = {
                int(p['fiber_no']): p for p in owned_ports[child['obj_id']]
            }
            for n, point in split_layout.ports.items():
                if (
                    child_ports[n]
                    .geometry()
                    .distance(QgsGeometry.fromPointXY(position(point)))
                    > 1e-6
                ):
                    raise ValueError(
                        'Contained splitter port does not match its housing.'
                    )
                parent_port = parent_ports[
                    props.splitter_feed if n == 0 else n
                ]
                matches = [
                    link_record
                    for link_record in data['links']
                    if link_record['from_id'] == parent_port['port_id']
                    and link_record['to_id'] == child_ports[n]['port_id']
                    and link_record['kind'] == 'patch'
                ]
                if len(matches) != 1:
                    raise ValueError(
                        'Contained splitter patch connections are incomplete.'
                    )
        if any(
            int(h['splitter_ratio']) and h['obj_id'] not in parents
            for h in data['housings']
        ):
            raise ValueError(
                'Housing splitter has no saved containment relationship.'
            )
        occupied = set()
        for link in data['links']:
            a, b = ports.get(link['from_id']), ports.get(link['to_id'])
            validate_connection(
                a, b, {k: e['kind'] for k, e in elements.items()}, occupied
            )
            occupied.update((link['from_id'], link['to_id']))
            route = link.geometry().asPolyline()
            if (
                len(route) < 2
                or route[0] != a.geometry().asPoint()
                or route[-1] != b.geometry().asPoint()
            ):
                raise ValueError(
                    'Connection geometry does not match its ports.'
                )
        cable_fibers = {}
        for fiber in data['fibers']:
            if fiber['cable_id'] not in indexes['cables']:
                raise ValueError('Orphaned cable fiber.')
            cable_fibers.setdefault(fiber['cable_id'], []).append(fiber)
        ends = {}
        for end in data['fiber_ends']:
            key = (end['fiber_id'], end['side'])
            if (
                end['fiber_id'] not in indexes['fibers']
                or end['side'] not in ('A', 'B')
                or key in ends
            ):
                raise ValueError('Orphaned, duplicate or invalid fiber end.')
            if not end.hasGeometry() or end.geometry().isEmpty():
                raise ValueError('Fiber end has no position.')
            point = end.geometry().asPoint()
            if not math.isfinite(point.x()) or not math.isfinite(point.y()):
                raise ValueError('Fiber end has an invalid position.')
            ends[key] = end
        if len(ends) != 2 * len(data['fibers']):
            raise ValueError(
                'Each cable fiber must have two independent ends.'
            )
        used = set()
        for cable in data['cables']:
            if not 1 <= int(cable['count']) <= 576:
                raise ValueError('Invalid cable fiber count.')
            if (
                not math.isfinite(float(cable['length_m']))
                or cable['length_m'] < 0
            ):
                raise ValueError('Invalid real cable length.')
            owners = [optional(cable[k]) for k in ('from_obj', 'to_obj')]
            if any(
                owner is not None and owner not in elements for owner in owners
            ):
                raise ValueError('Cable references missing equipment.')
            if owners[0] is not None and owners[0] == owners[1]:
                raise ValueError(
                    'Cable endpoints must be different equipment.'
                )
            if cable['layout_mode'] not in ('legacy', 'rows') or (
                cable['layout_mode'] == 'legacy' and None in owners
            ):
                raise ValueError(
                    'Invalid cable layout or missing legacy endpoint.'
                )
            fiber_rows(int(cable['count']), int(cable['group_size']))
            native = cable['cable_version'] == CABLE_VERSION
            if native:
                props = properties_from_record(cable)
                if (
                    props.count != int(cable['count'])
                    or cable['layout_mode'] != 'rows'
                ):
                    raise ValueError(
                        'Physical tube count disagrees with cable capacity.'
                    )
            elif optional(cable['cable_version']) is not None:
                raise ValueError('Unsupported cable layout version.')
            route = cable.geometry().asPolyline()
            validate_vertices([(p.x(), p.y()) for p in route])
            if native:
                validate_row_route(route, props.rows, MIN_SEGMENT)
                if (
                    len(route) == 2
                    and route[0].distance(route[1]) < MIN_STRAIGHT - 1e-6
                ):
                    raise ValueError('Straight catalogue cable is too short.')
            for index, owner in ((0, owners[0]), (-1, owners[1])):
                expected_anchor = (
                    attachment_points.get(
                        (cable['cable_id'], 'A' if index == 0 else 'B'),
                        elements[owner].geometry().asPoint(),
                    )
                    if owner is not None
                    else None
                )
                if (
                    owner is not None
                    and route[index].distance(expected_anchor) > 1e-6
                ):
                    raise ValueError('Cable endpoint geometry is detached.')
            fibers = cable_fibers.get(cable['cable_id'], [])
            if sorted(int(f['fiber_no']) for f in fibers) != list(
                range(1, int(cable['count']) + 1)
            ):
                raise ValueError('Cable fiber count mismatch.')
            for f in fibers:
                if native and (
                    int(f['tube_no'])
                    != (int(f['fiber_no']) - 1) // props.fibers_per_tube + 1
                    or int(f['tube_fiber'])
                    != (int(f['fiber_no']) - 1) % props.fibers_per_tube + 1
                    or f['color'] != props.color(int(f['tube_fiber']))[0]
                ):
                    raise ValueError(
                        'Invalid physical tube/fiber identity or color.'
                    )
                for field, owner in (('a_id', 'from_obj'), ('b_id', 'to_obj')):
                    end = ends[
                        (f['fiber_id'], 'A' if field == 'a_id' else 'B')
                    ]
                    port_id = optional(end['port_id'])
                    if optional(f[field]) != port_id:
                        raise ValueError(
                            'Fiber endpoint and compatibility port '
                            'references disagree.'
                        )
                    if port_id is None:
                        if cable['layout_mode'] == 'legacy':
                            raise ValueError(
                                'A legacy cable has an unattached fiber end.'
                            )
                        if (
                            native
                            and optional(cable[owner]) is None
                            and f['fiber_id'] not in indexes['fiber_routes']
                        ):
                            path = cable_fiber_path(
                                route,
                                props.rows[int(f['fiber_no']) - 1],
                                indexes['outlets'][cable['cable_id']]['mode']
                                if cable['cable_id'] in indexes['outlets']
                                else '',
                            )
                            expected = path[0 if field == 'a_id' else -1]
                            if (
                                end.geometry().asPoint().distance(expected)
                                > 1e-6
                            ):
                                raise ValueError(
                                    'Free catalogue fiber end is detached '
                                    'from its row.'
                                )
                        continue
                    p = ports.get(port_id)
                    if (
                        p is None
                        or p['obj_id'] != optional(cable[owner])
                        or port_id in used
                    ):
                        raise ValueError(
                            'Invalid or reused cable fiber endpoint.'
                        )
                    if end.geometry().distance(p.geometry()) > 1e-6:
                        raise ValueError(
                            'Cable fiber endpoint is detached from its '
                            'connector.'
                        )
                    used.add(port_id)
        if any(r['obj_id'] not in elements for r in data['closures']):
            raise ValueError('Orphaned closure properties.')
        closure_members = {}
        for member in data['closure_members']:
            if member['closure_id'] not in indexes['closures'] or member[
                'kind'
            ] not in ('fiber', 'link', 'joint', 'mark'):
                raise ValueError('Invalid closure membership.')
            target = indexes[
                {
                    'fiber': 'fibers',
                    'link': 'links',
                    'joint': 'fiber_joints',
                    'mark': 'fiber_marks',
                }[member['kind']]
            ]
            if member['ref_id'] not in target or (
                member['kind'] == 'link'
                and target[member['ref_id']]['kind'] != 'splice'
            ):
                raise ValueError(
                    'Closure member references a missing fiber or splice.'
                )
            pair = (member['kind'], member['ref_id'])
            owned = closure_members.setdefault(member['closure_id'], set())
            if pair in owned:
                raise ValueError('Duplicate closure member.')
            owned.add(pair)
        if data['closures']:
            scene = self._closure_scene()
            for record in data['closures']:
                shape = closure_frame(elements[record['obj_id']], record)
                if self._closure_members(shape, scene) != closure_members.get(
                    record['obj_id'], set()
                ):
                    raise ValueError(
                        'Closure membership does not match its enclosure.'
                    )
        if any(a['obj_id'] not in elements for a in data['active_equipment']):
            raise ValueError('Orphaned active equipment properties.')
        self.validate_fiber_operations()
        return {key: len(records) for key, records in data.items()}

    def demo(self):
        with self.repo.command('Create demo'):
            a = self.add_element(
                'CABLE', 'CABLE-A / 12F', 12, 'left', pt(0, 0)
            )
            b = self.add_element(
                'CABLE', 'CABLE-B / 12F', 12, 'right', pt(235, 0)
            )
            c = self.add_element(
                'CABLE', 'CABLE-C / 12F', 12, 'right', pt(235, 155)
            )
            splitter = self.add_element(
                'SPLITTER', 'SPLITTER-1:8', 8, 'center', pt(115, 155)
            )
            pa, pb, pc, ps = (self.ports_of(e) for e in (a, b, c, splitter))
            for i in range(8):
                self.add_link(pa[i], pb[i])
            self.add_link(pa[8], ps[0])
            for i, port in enumerate(ps[1:]):
                self.add_link(port, pc[i])

    def network_demo(self):
        with self.repo.command('Create network demo'):
            odf = self.add_element('ODF', 'ODF-01', 12, 'center', pt(0, 0))
            closure = self.add_element(
                'CLOSURE', 'CLOSURE-01', 24, 'center', pt(300, 0)
            )
            cabinet = self.add_element(
                'CABINET', 'CABINET-01', 12, 'center', pt(600, 0)
            )
            self.add_cable(
                'FEEDER-12F',
                12,
                odf,
                closure,
                [pt(0, 0), pt(120, 120), pt(210, 120), pt(300, 0)],
                250,
            )
            self.add_cable(
                'DISTRIBUTION-12F',
                12,
                closure,
                cabinet,
                [pt(300, 0), pt(400, -110), pt(500, -110), pt(600, 0)],
                300,
            )
            ports = self.ports_of(closure)
            for i in range(12):
                self.add_link(ports[i], ports[i + 12])

    def network_example(self):
        """Native ODF/closure/FDH example with twelve real splices."""
        from .housing_catalog import OdfProperties
        from .closure_layout import ClosureProperties

        with self.repo.command('Create network demo'):
            feeder = self.add_catalog_cable(
                'FEEDER-12F',
                CableProperties(
                    category='kbDuct', model='TOSM 03', catalog_model='TOSM 03'
                ),
                [pt(0, 0), pt(360, 0)],
                250,
            )
            distribution = self.add_catalog_cable(
                'DISTRIBUTION-12F',
                CableProperties(),
                [pt(520, 0), pt(880, 0)],
                300,
            )
            odf = self.attach_housing(
                feeder, 'A', '1', OdfProperties(capacity=12, requested_used=12)
            )
            cabinet = self.attach_housing(
                distribution,
                'B',
                '1',
                OdfProperties(
                    kind='FDH',
                    capacity=12,
                    mounting='indoor',
                    requested_used=12,
                ),
            )
            self.join_group(
                [end['end_id'] for end in self.ends_of(feeder, 'B')],
                [end['end_id'] for end in self.ends_of(distribution, 'A')],
            )
            closure = self.add_closure(
                '1',
                ClosureProperties(location_number='1'),
                feeder,
                pt(220, 0),
                52,
            )
        return dict(
            odf=odf,
            cabinet=cabinet,
            closure=closure,
            feeder=feeder,
            distribution=distribution,
        )

    def splice_example(self):
        """Eight cable splices and an isolated lower-tier 1:8 fan-out."""
        from .active_layout import ActiveProperties

        with self.repo.command('Create splice demo'):
            first = self.add_catalog_cable(
                'CABLE-A / 12F', CableProperties(), [pt(0, 0), pt(350, 0)], 80
            )
            second = self.add_catalog_cable(
                'CABLE-B / 12F',
                CableProperties(),
                [pt(550, 0), pt(900, 0)],
                120,
            )
            third = self.add_catalog_cable(
                'CABLE-C / 12F',
                CableProperties(),
                [pt(700, -232), pt(1050, -232)],
                150,
            )
            splitter = self.add_active(
                'S1', ActiveProperties(count=8, anchor='input'), pt(450, -260)
            )
            incoming = self.ends_of(first, 'B')
            self.join_group(
                [end['end_id'] for end in incoming[:8]],
                [end['end_id'] for end in self.ends_of(second, 'A')[:8]],
            )
            ports = self.ports_of(splitter)
            self.join_nodes(incoming[8]['end_id'], ports[0]['port_id'])
            self.join_group(
                [port['port_id'] for port in ports[1:]],
                [end['end_id'] for end in self.ends_of(third, 'A')[:8]],
            )
        return dict(first=first, second=second, third=third, splitter=splitter)

    def cad_demo(self):
        """24F feeder, two 12F branches and splitters inside housings."""
        with self.repo.command('Create CAD fiber demo'):
            odf = self.add_element('ODF', 'OR 2', 24, 'center', pt(0, 0))
            closure = self.add_element(
                'CLOSURE', 'N 1', 48, 'center', pt(480, 0)
            )
            fdh = self.add_element('FDH', 'FDH 1', 24, 'center', pt(945, 140))
            fat = self.add_element('FAT', 'FAT 3', 12, 'center', pt(945, -200))
            upper = self.add_element(
                'SPLITTER', 'SPLITTER 1:16', 16, 'center', pt(990, 140)
            )
            lower = self.add_element(
                'SPLITTER', 'SPLITTER 1:8', 8, 'center', pt(990, -200)
            )
            self.add_cable(
                'TOSM 03', 24, odf, closure, [pt(0, 0), pt(480, 0)], 250
            )
            self.add_cable(
                'ADSS 03',
                12,
                closure,
                fdh,
                [pt(480, 0), pt(665, 48), pt(665, 188), pt(945, 140)],
                300,
            )
            self.add_cable(
                'TOSM 04',
                12,
                closure,
                fat,
                [pt(480, 0), pt(630, -48), pt(630, -200), pt(945, -200)],
                200,
            )
            ports = self.ports_of(closure)
            for i in range(24):
                self.add_link(ports[i], ports[i + 24])
            self.add_link(
                self.ports_of(fdh)[0], self.ports_of(upper)[0], 'patch'
            )
            self.add_link(
                self.ports_of(fat)[0], self.ports_of(lower)[0], 'patch'
            )
