"""Atomic fiber cuts, joints, marks, branches, slack and optical validation."""

import math

from qgis.core import QgsGeometry

from .domain import uid, validate_vertices
from .fiber_model import PURPOSES, fiber_spans, validate_fiber_data
from .fiber_geometry import fraction_on_line, point_at, line_slice, joint_path
from .geometry import closure_frame, optional, pt, validate_row_route


class FiberService:
    def fiber_scene(self, operations=True):
        from .cad_view import CadView

        scene = CadView()
        scene.rebuild(self.repo, operations=operations)
        return scene

    def optical_nodes(self):
        result = {}
        for port in self.repo.features('ports'):
            result[port['port_id']] = dict(
                point=port.geometry().asPoint(),
                role=port['role'],
                kind='port',
                obj_id=port['obj_id'],
                fiber_id=None,
                fiber_type='',
                port_id=port['port_id'],
            )
        fibers = {f['fiber_id']: f for f in self.repo.features('fibers')}
        for key, field in (
            ('fiber_ends', 'end_id'),
            ('fiber_nodes', 'node_id'),
        ):
            for record in self.repo.features(key):
                fiber = fibers.get(record['fiber_id'])
                if fiber is None:
                    raise ValueError(
                        'An optical node references a missing fiber.'
                    )
                if record[field] in result:
                    raise ValueError(
                        'Optical node identities must be unique across ports '
                        'and fiber ends.'
                    )
                if (
                    not record.hasGeometry()
                    or record.geometry().isEmpty()
                    or not all(
                        math.isfinite(v)
                        for v in (
                            record.geometry().asPoint().x(),
                            record.geometry().asPoint().y(),
                        )
                    )
                ):
                    raise ValueError(
                        'Optical endpoints must have a finite position.'
                    )
                result[record[field]] = dict(
                    point=record.geometry().asPoint(),
                    role='fiber',
                    kind=key,
                    fiber_id=record['fiber_id'],
                    fiber_type=fiber['fiber_type'],
                    side=record['side'] if key == 'fiber_ends' else '',
                    port_id=optional(record['port_id'])
                    if key == 'fiber_ends'
                    else None,
                )
        return result

    def fiber_sections(self):
        ends, cuts, nodes = (
            self.repo.features(key)
            for key in ('fiber_ends', 'fiber_cuts', 'fiber_nodes')
        )
        return [
            span
            for fiber in self.repo.features('fibers')
            for span in fiber_spans(fiber['fiber_id'], ends, cuts, nodes)
        ]

    def section_purposes(self):
        fibers = {f['fiber_id']: f for f in self.repo.features('fibers')}
        overrides = {
            p['span_id']: p['purpose']
            for p in self.repo.features('fiber_purposes')
        }
        return [
            dict(
                span,
                purpose=overrides.get(
                    span['span_id'], fibers[span['fiber_id']]['purpose']
                ),
            )
            for span in self.fiber_sections()
        ]

    def _save_section_purposes(self, identity, values):
        fiber = self.repo.find('fibers', identity)
        self.repo.delete(
            'fiber_purposes',
            [
                p.id()
                for p in self.repo.features('fiber_purposes')
                if p['fiber_id'] == identity
            ],
        )
        codes = set(values.values())
        if len(codes) == 1:
            self.repo.update(
                'fibers', fiber.id(), {'purpose': next(iter(codes))}
            )
        else:
            for span, code in values.items():
                if code != fiber['purpose']:
                    self.repo.insert(
                        'fiber_purposes',
                        dict(span_id=span, fiber_id=identity, purpose=code),
                    )

    def _remap_section_purposes(self, before):
        current = self.fiber_sections()
        for identity in {span['fiber_id'] for span in before}:
            previous = [
                span for span in before if span['fiber_id'] == identity
            ]
            values = {}
            for span in current:
                if span['fiber_id'] == identity:
                    # New cut sides inherit their original section. Restoring
                    # Through keeps the upstream section's purpose on a merge.
                    original = next(
                        p
                        for p in previous
                        if p['start'] - 1e-9
                        <= span['start']
                        <= p['end'] + 1e-9
                    )
                    values[span['span_id']] = original['purpose']
            self._save_section_purposes(identity, values)

    def path_sections(self, starts):
        from .reports import optical_graph

        starts = list(dict.fromkeys(starts))
        if not starts:
            raise ValueError('Select a connected fiber path.')
        nodes = self.optical_nodes()
        if any(start not in nodes for start in starts):
            raise ValueError('Select an existing port or cable fiber end.')
        graph, edges = optical_graph(self.report_data())
        found, pending = set(), list(starts)
        while pending:
            node = pending.pop()
            if node not in found:
                found.add(node)
                pending.extend(
                    other for other, edge in graph[node] if other not in found
                )
        return [
            s
            for s in self.section_purposes()
            if s['a_id'] in found and s['b_id'] in found
        ]

    def set_path_purpose(self, starts, purpose):
        if purpose not in PURPOSES:
            raise ValueError('Choose one of the ten fiber purposes.')
        chosen = {s['span_id'] for s in self.path_sections(starts)}
        sections = self.section_purposes()
        affected = {s['fiber_id'] for s in sections if s['span_id'] in chosen}
        if not affected:
            raise ValueError(
                'The selected connection has no cable fiber sections.'
            )
        with self.repo.command('Set connected fiber path purpose'):
            for identity in affected:
                values = {
                    s['span_id']: purpose
                    if s['span_id'] in chosen
                    else s['purpose']
                    for s in sections
                    if s['fiber_id'] == identity
                }
                self._save_section_purposes(identity, values)
        return affected

    def joint_occupied(self):
        return {
            j[key]
            for j in self.repo.features('fiber_joints')
            for key in ('a_id', 'b_id')
        }

    def free_optical_nodes(self):
        used = self.occupied()
        return {
            identity: node
            for identity, node in self.optical_nodes().items()
            if identity not in used
            and (node['kind'] == 'port' or not node.get('port_id'))
        }

    def _fiber_at(self, identity, click, scene=None):
        scene = scene or self.fiber_scene()
        path = scene.drawn_fibers.get(identity)
        if path is None:
            raise ValueError('Choose an existing cable fiber.')
        return path, fraction_on_line(path, click)

    def _closure_at(self, point):
        candidates = []
        for record in self.repo.features('closures'):
            shape = closure_frame(
                self.repo.find('elements', record['obj_id']), record
            )
            if shape.intersects(QgsGeometry.fromPointXY(point)):
                candidates.append((shape.area(), record))
        return (
            min(candidates, key=lambda item: item[0])[1]
            if candidates
            else None
        )

    def cut_fibers(self, identities, click):
        identities = list(dict.fromkeys(identities))
        if not identities:
            raise ValueError('Choose at least one fiber to cut.')
        scene = self.fiber_scene()
        previous_purposes = [
            s for s in self.section_purposes() if s['fiber_id'] in identities
        ]
        ends, cuts, nodes = (
            self.repo.features(k)
            for k in ('fiber_ends', 'fiber_cuts', 'fiber_nodes')
        )
        group = uid()
        plans, recuts = [], []
        for identity in identities:
            path, position = self._fiber_at(identity, click, scene)
            where = point_at(path, position)
            closure = self._closure_at(where)
            if closure is not None:
                origin = (
                    self.repo.find('elements', closure['obj_id'])
                    .geometry()
                    .asPoint()
                )
                theta = math.radians(float(closure['angle']))

                def along(x):
                    return pt(
                        origin.x() + x * math.cos(theta),
                        origin.y() + x * math.sin(theta),
                    )

                width = float(closure['width']) * 10
                inset = min(15, 0.5 * (width - min(12, 0.5 * width)))
                left = fraction_on_line(path, along(inset))
                right = fraction_on_line(path, along(width - inset))
                left, right = sorted((left, right))
            else:
                left, right = (
                    position - 6 / path.length(),
                    position + 6 / path.length(),
                )
            spans = fiber_spans(identity, ends, cuts, nodes)
            by_node = {n['node_id']: n for n in nodes}
            matching = next(
                (
                    c
                    for c in cuts
                    if c['fiber_id'] == identity
                    and abs(float(by_node[c['left_id']]['at']) - left) < 1e-6
                    and abs(float(by_node[c['right_id']]['at']) - right) < 1e-6
                ),
                None,
            )
            if matching is not None:
                recuts.append(matching)
                continue
            if not any(
                s['start'] + 1 / path.length()
                < left
                < right
                < s['end'] - 1 / path.length()
                for s in spans
            ):
                raise ValueError(
                    'The cut must fit inside one continuous fiber section, '
                    'away from its ends and other cuts.'
                )
            plans.append((identity, path, left, right))
        with self.repo.command('Cut {} fiber(s)'.format(len(plans))):
            for cut in recuts:
                self.delete_node_joints({cut['left_id'], cut['right_id']})
                self.repo.update('fiber_cuts', cut.id(), {'group_id': group})
            for identity, path, left, right in plans:
                left_id, right_id = uid(), uid()
                for node, fraction in ((left_id, left), (right_id, right)):
                    self.repo.insert(
                        'fiber_nodes',
                        dict(node_id=node, fiber_id=identity, at=fraction),
                        QgsGeometry.fromPointXY(point_at(path, fraction)),
                    )
                self.repo.insert(
                    'fiber_cuts',
                    dict(
                        cut_id=uid(),
                        fiber_id=identity,
                        left_id=left_id,
                        right_id=right_id,
                        group_id=group,
                    ),
                )
                # A splice in the removed gap no longer represents a physical
                # joint.
                self.repo.delete(
                    'fiber_marks',
                    [
                        m.id()
                        for m in self.repo.features('fiber_marks')
                        if m['fiber_id'] == identity
                        and left <= float(m['at']) <= right
                    ],
                )
            self._remap_section_purposes(previous_purposes)
        return group

    def cut_optical(
        self, identities, click, joint_ids=(), link_ids=(), window=None
    ):
        """Cut continuous sections or disconnect existing joins as one
        command."""
        identities = list(dict.fromkeys(identities))
        joints = [
            self.repo.find('fiber_joints', identity)
            for identity in dict.fromkeys(joint_ids)
        ]
        links = [
            self.repo.find('links', identity)
            for identity in dict.fromkeys(link_ids)
        ]
        if any(record is None for record in joints + links):
            raise ValueError('Choose existing optical connections to cut.')
        nodes = self.optical_nodes()
        if identities:
            # Clicking an incoming stub within a closure cuts that fiber's
            # existing splice, just like clicking its central joining segment.
            scene = self.fiber_scene()
            known = {joint['joint_id'] for joint in joints}
            saved = self.repo.features('fiber_joints')
            for identity in identities:
                path, position = self._fiber_at(identity, click, scene)
                point = point_at(path, position)
                closure = self._closure_at(point)
                candidates = [
                    joint
                    for joint in saved
                    if closure is not None
                    and joint['closure_id'] == closure['obj_id']
                    and any(
                        nodes[joint[field]]['fiber_id'] == identity
                        for field in ('a_id', 'b_id')
                    )
                ]
                joint = min(
                    candidates,
                    key=lambda j: j.geometry().distance(
                        QgsGeometry.fromPointXY(point)
                    ),
                    default=None,
                )
                if joint is not None and joint['joint_id'] not in known:
                    joints.append(joint)
                    known.add(joint['joint_id'])
        adjacent = {
            nodes[joint[field]]['fiber_id']
            for joint in joints
            for field in ('a_id', 'b_id')
        }
        ports = {
            link[field] for link in links for field in ('from_id', 'to_id')
        }
        adjacent.update(
            end['fiber_id']
            for end in self.repo.features('fiber_ends')
            if optional(end['port_id']) in ports
        )
        if window is not None:
            # A window around a closure also includes already-open incoming
            # stubs. Their free ends need no additional cut within the
            # enclosure.
            adjacent.update(
                node['fiber_id']
                for node in self.free_optical_nodes().values()
                if node['fiber_id']
                and window.intersects(QgsGeometry.fromPointXY(node['point']))
            )
        identities = [
            identity for identity in identities if identity not in adjacent
        ]
        count = len(identities) + len(joints) + len(links)
        if not count:
            raise ValueError(
                'No continuous fiber sections or joined connections to cut '
                'at this position.'
            )
        with self.repo.command('Cut fibers and joined connections'):
            self.repo.delete('fiber_joints', [joint.id() for joint in joints])
            for link in links:
                self.delete_link(link['link_id'])
            if identities:
                self.cut_fibers(identities, click)
        return count

    def _joint_geometry(
        self, a, b, closure_id=None, exclude=None, manual=None
    ):
        record = (
            self.repo.find('closures', closure_id)
            if closure_id
            else self._closure_at(a)
        )
        if record is not None:
            shape = closure_frame(
                self.repo.find('elements', record['obj_id']), record
            )
            if not shape.intersects(QgsGeometry.fromPointXY(b)):
                record = None
        angle, column, saved_closure = 0, None, ''
        if record is not None:
            angle, saved_closure = float(record['angle']), record['obj_id']
            origin = (
                self.repo.find('elements', saved_closure).geometry().asPoint()
            )
            theta = math.radians(angle)
            center = (
                origin.x() * math.cos(theta)
                + origin.y() * math.sin(theta)
                + float(record['width']) * 5
            )
            ay = -a.x() * math.sin(theta) + a.y() * math.cos(theta)
            by = -b.x() * math.sin(theta) + b.y() * math.cos(theta)
            limit = max(0, float(record['width']) * 5 - 15)
            column = center
            if abs(ay - by) > 1e-6 and manual is None:
                occupied = []
                for joint in self.repo.features('fiber_joints'):
                    if (
                        joint['closure_id'] != saved_closure
                        or joint['joint_id'] == exclude
                    ):
                        continue
                    vertices = joint.geometry().asPolyline()
                    for p, q in zip(vertices, vertices[1:]):
                        px, qx = (
                            v.x() * math.cos(theta) + v.y() * math.sin(theta)
                            for v in (p, q)
                        )
                        py, qy = (
                            -v.x() * math.sin(theta) + v.y() * math.cos(theta)
                            for v in (p, q)
                        )
                        if abs(px - qx) < 1e-6 and abs(py - qy) > 1e-6:
                            occupied.append((px, min(py, qy), max(py, qy)))
                columns = [center] + [
                    center + sign * step * 5
                    for step in range(1, int(limit / 5) + 1)
                    for sign in (1, -1)
                ]
                column = next(
                    (
                        c
                        for c in columns
                        if not any(
                            abs(c - x) < 2.5
                            and min(ay, by) <= high + 0.05
                            and max(ay, by) >= low - 0.05
                            for x, low, high in occupied
                        )
                    ),
                    None,
                )
                if column is None:
                    raise ValueError(
                        'No free splice column in this closure. Widen the '
                        'closure or choose another location.'
                    )
        if manual is not None:
            theta = math.radians(angle)
            column = manual.x() * math.cos(theta) + manual.y() * math.sin(
                theta
            )
        geometry, marker = joint_path(a, b, column, angle)
        if saved_closure and not closure_frame(
            self.repo.find('elements', saved_closure), record
        ).intersects(QgsGeometry.fromPointXY(marker)):
            saved_closure = ''
        return geometry, marker, saved_closure

    def join_nodes(
        self, first, second, kind='splice', loss_db=0.1, point=None
    ):
        if (
            kind not in ('splice', 'through', 'patch', 'drop')
            or not math.isfinite(loss_db)
            or loss_db < 0
        ):
            raise ValueError(
                'Choose a valid connection kind and nonnegative loss.'
            )
        if point is not None and not all(
            math.isfinite(v) for v in (point.x(), point.y())
        ):
            raise ValueError('Joint position must be finite.')
        nodes = self.optical_nodes()
        if first == second or first not in nodes or second not in nodes:
            raise ValueError(
                'Choose two different existing optical endpoints or ports.'
            )
        a, b = nodes[first], nodes[second]
        occupied = self.occupied()
        if any(
            identity in occupied
            or (node['kind'] == 'fiber_ends' and node['port_id'])
            for identity, node in ((first, a), (second, b))
        ):
            raise ValueError(
                'An endpoint is occupied. Disconnect its existing connection '
                'first.'
            )
        if a['kind'] == b['kind'] == 'port':
            if a['obj_id'] == b['obj_id'] or (
                a['role'] == b['role'] and a['role'] in ('input', 'output')
            ):
                raise ValueError('Cannot connect these optical ports.')
        bound = {
            e['port_id']
            for e in self.repo.features('fiber_ends')
            if optional(e['port_id'])
        }
        if any(
            node['kind'] == 'port'
            and identity in bound
            and self.repo.find('active_equipment', node['obj_id']) is not None
            for identity, node in ((first, a), (second, b))
        ):
            raise ValueError(
                'This active-equipment port already has a cable fiber.'
            )
        if (
            a['fiber_type']
            and b['fiber_type']
            and a['fiber_type'] != b['fiber_type']
        ):
            raise ValueError('Cannot join known SM and MM fibers.')
        if a['fiber_id'] and a['fiber_id'] == b['fiber_id']:
            same_cut = any(
                {c['left_id'], c['right_id']} == {first, second}
                for c in self.repo.features('fiber_cuts')
            )
            if not same_cut:
                raise ValueError(
                    'Choose different fibers or the two sides of one cut.'
                )
            if kind == 'through':
                self.restore_through(a['fiber_id'], a['point'])
                return a['fiber_id']
        geometry, marker, closure_id = self._joint_geometry(
            a['point'], b['point'], manual=point
        )
        identity = uid()
        with self.repo.command('Create fiber ' + kind):
            self.repo.insert(
                'fiber_joints',
                dict(
                    joint_id=identity,
                    a_id=first,
                    b_id=second,
                    kind=kind,
                    loss_db=0.0 if kind == 'through' else loss_db,
                    closure_id=closure_id,
                    manual_x=point.x() if point is not None else None,
                    manual_y=point.y() if point is not None else None,
                ),
                geometry,
            )
        return identity

    def join_group(self, first_nodes, second_nodes, kind='splice'):
        first_nodes, second_nodes = list(first_nodes), list(second_nodes)
        if (
            not first_nodes
            or len(first_nodes) != len(second_nodes)
            or len(set(first_nodes + second_nodes)) != 2 * len(first_nodes)
        ):
            raise ValueError(
                'Group joining needs equally sized, distinct endpoint lists.'
            )
        with self.repo.command('Join {} fiber pairs'.format(len(first_nodes))):
            return [
                self.join_nodes(a, b, kind)
                for a, b in zip(first_nodes, second_nodes)
            ]

    def add_splice_marks(self, identities, click):
        scene, marks = self.fiber_scene(), self.repo.features('fiber_marks')
        with self.repo.command('Place fiber splice point(s)'):
            for identity in dict.fromkeys(identities):
                path, position = self._fiber_at(identity, click, scene)
                closure = self._closure_at(point_at(path, position))
                if closure is not None:
                    origin = (
                        self.repo.find('elements', closure['obj_id'])
                        .geometry()
                        .asPoint()
                    )
                    theta = math.radians(float(closure['angle']))
                    point = pt(
                        origin.x()
                        + float(closure['width']) * 5 * math.cos(theta),
                        origin.y()
                        + float(closure['width']) * 5 * math.sin(theta),
                    )
                    position = fraction_on_line(path, point)
                spans = [
                    s
                    for s in self.fiber_sections()
                    if s['fiber_id'] == identity
                ]
                if not any(s['start'] <= position <= s['end'] for s in spans):
                    raise ValueError(
                        'Cannot place a splice point in a cut gap.'
                    )
                if any(
                    m['fiber_id'] == identity
                    and abs(float(m['at']) - position) * path.length() < 0.1
                    for m in marks
                ):
                    continue
                self.repo.insert(
                    'fiber_marks',
                    dict(
                        mark_id=uid(),
                        fiber_id=identity,
                        at=position,
                        closure_id=closure['obj_id']
                        if closure is not None
                        else '',
                        loss_db=0.1,
                    ),
                    QgsGeometry.fromPointXY(point_at(path, position)),
                )

    def restore_through(self, identity, click=None):
        cuts = [
            c
            for c in self.repo.features('fiber_cuts')
            if c['fiber_id'] == identity
        ]
        if self.repo.find('fibers', identity) is None:
            raise ValueError('Choose an existing fiber.')
        if click is not None and cuts:
            nodes = {
                n['node_id']: n for n in self.repo.features('fiber_nodes')
            }
            cuts = [
                min(
                    cuts,
                    key=lambda c: (
                        nodes[c['left_id']]
                        .geometry()
                        .asPoint()
                        .distance(click)
                    ),
                )
            ]
        remove_nodes = {c[k] for c in cuts for k in ('left_id', 'right_id')}
        joints = [
            j
            for j in self.repo.features('fiber_joints')
            if remove_nodes.intersection((j['a_id'], j['b_id']))
        ]
        if any(
            j['a_id'] not in remove_nodes or j['b_id'] not in remove_nodes
            for j in joints
        ):
            raise ValueError(
                'Disconnect external fibers from these cut ends before '
                'restoring through continuity.'
            )
        previous = [
            span
            for span in self.section_purposes()
            if span['fiber_id'] == identity
        ]
        with self.repo.command('Restore unspliced through fiber'):
            self.repo.delete('fiber_joints', [j.id() for j in joints])
            self.repo.delete('fiber_cuts', [c.id() for c in cuts])
            self.repo.delete(
                'fiber_nodes',
                [
                    n.id()
                    for n in self.repo.features('fiber_nodes')
                    if n['node_id'] in remove_nodes
                ],
            )
            self.repo.delete(
                'fiber_marks',
                [
                    m.id()
                    for m in self.repo.features('fiber_marks')
                    if m['fiber_id'] == identity
                ],
            )
            self._remap_section_purposes(previous)

    def set_fiber_data(
        self, identities, purpose=None, fiber_type=None, description=None
    ):
        identities = list(dict.fromkeys(identities))
        if not identities:
            raise ValueError('Select one or more fibers.')
        with self.repo.command('Edit fiber purpose/type/description'):
            for identity in identities:
                fiber = self.repo.find('fibers', identity)
                if fiber is None:
                    raise ValueError('A selected fiber no longer exists.')
                values = dict(
                    purpose=fiber['purpose'],
                    fiber_type=fiber['fiber_type'],
                    description=fiber['description'],
                )
                values.update(
                    {
                        k: v
                        for k, v in (
                            ('purpose', purpose),
                            ('fiber_type', fiber_type),
                            ('description', description),
                        )
                        if v is not None
                    }
                )
                validate_fiber_data(**values)
                self.repo.update('fibers', fiber.id(), values)
                if purpose is not None:
                    self.repo.delete(
                        'fiber_purposes',
                        [
                            p.id()
                            for p in self.repo.features('fiber_purposes')
                            if p['fiber_id'] == identity
                        ],
                    )
            self.validate_fiber_operations()

    def add_slack(
        self,
        point,
        length_m=15,
        cable_id=None,
        orientation=0,
        status='designed',
    ):
        if (
            not math.isfinite(length_m)
            or not 0.5 <= length_m <= 10000000
            or orientation not in (0, 90, 270)
            or status not in ('designed', 'as_built')
            or not all(math.isfinite(v) for v in (point.x(), point.y()))
        ):
            raise ValueError(
                'Enter positive slack (at least 0.5 m), a valid orientation '
                'and drawing status.'
            )
        if cable_id is not None and self.repo.find('cables', cable_id) is None:
            raise ValueError('Slack cable no longer exists.')
        identity = uid()
        with self.repo.command('Place cable slack'):
            self.repo.insert(
                'slack',
                dict(
                    slack_id=identity,
                    cable_id=cable_id,
                    length_m=math.floor(length_m + 0.5),
                    requested_m=length_m,
                    orientation=orientation,
                    status=status,
                ),
                QgsGeometry.fromPointXY(point),
            )
        return identity

    def branch_fibers(
        self,
        identities,
        target,
        side='B',
        click=None,
        preview=False,
        scene=None,
    ):
        identities = list(dict.fromkeys(identities))
        if not identities or side not in ('A', 'B'):
            raise ValueError('Choose fibers and an A/B cable end.')
        scene = scene or self.fiber_scene()
        plans = []
        first_row = None
        for index, identity in enumerate(identities):
            fiber = self.repo.find('fibers', identity)
            cable = (
                self.repo.find('cables', fiber['cable_id'])
                if fiber is not None
                else None
            )
            if cable is None or cable['layout_mode'] != 'rows':
                raise ValueError(
                    'Branching requires a cable drawn with tube rows.'
                )
            end = next(
                e
                for e in self.ends_of(fiber['cable_id'], side)
                if e['fiber_id'] == identity
            )
            if (
                optional(end['port_id'])
                or end['end_id'] in self.joint_occupied()
            ):
                raise ValueError(
                    'Disconnect the branch end before rerouting it.'
                )
            original = scene.drawn_fibers[identity]
            vertices = original.asPolyline()
            if side == 'A':
                vertices.reverse()
            path = QgsGeometry.fromPolylineXY(vertices)
            position = (
                fraction_on_line(path, click) if click is not None else 0.8
            )
            if not 0.01 < position < 0.99:
                raise ValueError(
                    'Click farther inside the fiber to start the branch, '
                    'away from either endpoint.'
                )
            prefix = line_slice(path, 0, position).asPolyline()
            a, origin = prefix[-2:]
            length = a.distance(origin)
            ux, uy = (
                (origin.x() - a.x()) / length,
                (origin.y() - a.y()) / length,
            )

            def local(p):
                return (
                    p.x() * ux + p.y() * uy,
                    -p.x() * uy + p.y() * ux,
                )

            ox, oy = local(origin)
            tx, ty = local(target)
            if first_row is None:
                first_row = oy
            row = ty + oy - first_row
            down = row < oy
            column = (
                ox + 10 + 8 * (len(identities) - 1 - index if down else index)
            )
            if tx < column + 10 or abs(row - oy) < 10:
                raise ValueError(
                    'The branch needs room for its staggered bends and an '
                    'end stub.'
                )

            def world(x, y):
                return pt(x * ux - y * uy, x * uy + y * ux)

            points = prefix + [
                world(column, oy),
                world(column, row),
                world(tx, row),
            ]
            if side == 'A':
                points.reverse()
            validate_row_route(points, [0], 1)
            geometry = QgsGeometry.fromPolylineXY(points)
            if not geometry.isSimple():
                raise ValueError('The branch would cross its own fiber path.')
            nodes = [
                n
                for n in self.repo.features('fiber_nodes')
                if n['fiber_id'] == identity
            ]
            for node in nodes:
                if geometry.distance(node.geometry()) > 1e-6:
                    raise ValueError(
                        'Keep existing cuts on the retained part of the '
                        'branch, or restore them first.'
                    )
            plans.append((identity, geometry, nodes))
        if preview:
            return [geometry for identity, geometry, nodes in plans]
        with self.repo.command('Branch {} fiber(s)'.format(len(plans))):
            for identity, geometry, nodes in plans:
                existing = self.repo.find('fiber_routes', identity)
                if existing is None:
                    self.repo.insert(
                        'fiber_routes',
                        dict(fiber_id=identity, side=side),
                        geometry,
                    )
                else:
                    self.repo.update(
                        'fiber_routes', existing.id(), {'side': side}, geometry
                    )
                for node in nodes:
                    self.repo.update(
                        'fiber_nodes',
                        node.id(),
                        {
                            'at': fraction_on_line(
                                geometry, node.geometry().asPoint()
                            )
                        },
                    )

    def clear_branch(self, identity):
        record = self.repo.find('fiber_routes', identity)
        if record is None:
            return
        if any(
            n['fiber_id'] == identity
            for n in self.repo.features('fiber_nodes')
        ):
            raise ValueError(
                'Restore cut sections before resetting this branch route.'
            )
        with self.repo.command('Restore original cable fiber route'):
            self.repo.delete('fiber_routes', [record.id()])
            fiber = self.repo.find('fibers', identity)
            self._sync_cable_ends(fiber['cable_id'])

    def delete_optical(self, key, identity):
        if key not in ('fiber_joints', 'fiber_marks', 'slack'):
            raise ValueError(
                'Choose a fiber joint, splice point or slack symbol.'
            )
        record = self.repo.find(key, identity)
        if record is not None:
            with self.repo.command('Delete ' + key.replace('_', ' ')):
                self.repo.delete(key, [record.id()])

    def delete_fiber_operations(self, identities):
        identities = set(identities)
        nodes = {
            n['node_id']
            for n in self.repo.features('fiber_nodes')
            if n['fiber_id'] in identities
        }
        nodes.update(
            e['end_id']
            for e in self.repo.features('fiber_ends')
            if e['fiber_id'] in identities
        )
        self.delete_node_joints(nodes)
        for key in (
            'fiber_purposes',
            'fiber_cuts',
            'fiber_marks',
            'fiber_nodes',
            'fiber_routes',
        ):
            self.repo.delete(
                key,
                [
                    r.id()
                    for r in self.repo.features(key)
                    if r['fiber_id'] in identities
                ],
            )

    def delete_node_joints(self, identities):
        self.repo.delete(
            'fiber_joints',
            [
                j.id()
                for j in self.repo.features('fiber_joints')
                if j['a_id'] in identities or j['b_id'] in identities
            ],
        )

    def sync_fiber_operations(self):
        routes, nodes, marks, joints = (
            self.repo.features(k)
            for k in (
                'fiber_routes',
                'fiber_nodes',
                'fiber_marks',
                'fiber_joints',
            )
        )
        if not any((routes, nodes, marks, joints)):
            return
        raw = self.fiber_scene(operations=False)
        paths = dict(raw.drawn_fibers)
        {f['fiber_id']: f for f in self.repo.features('fibers')}
        ends = {
            (e['fiber_id'], e['side']): e
            for e in self.repo.features('fiber_ends')
        }
        for route in routes:
            identity, side = route['fiber_id'], route['side']
            if identity not in paths or (identity, side) not in ends:
                raise ValueError(
                    'Branch route references a missing fiber end.'
                )
            if optional(ends[(identity, side)]['port_id']):
                raise ValueError(
                    'Restore this branch route before assigning its end to a '
                    'housing connector.'
                )
            points = route.geometry().asPolyline()
            fixed = 0 if side == 'B' else -1
            adjacent = 1 if side == 'B' else -2
            target = paths[identity].asPolyline()[fixed]
            horizontal = abs(points[fixed].y() - points[adjacent].y()) < 1e-6
            points[fixed] = target
            point = points[adjacent]
            points[adjacent] = (
                pt(point.x(), target.y())
                if horizontal
                else pt(target.x(), point.y())
            )
            validate_row_route(points, [0], 1)
            geometry = QgsGeometry.fromPolylineXY(points)
            if not geometry.isSimple():
                raise ValueError(
                    'Moving this connection would make its branch cross '
                    'itself.'
                )
            self.repo.update('fiber_routes', route.id(), geometry=geometry)
            end = ends[(identity, side)]
            self.repo.update(
                'fiber_ends',
                end.id(),
                geometry=QgsGeometry.fromPointXY(
                    points[-1 if side == 'B' else 0]
                ),
            )
            paths[identity] = geometry
        for node in nodes:
            if node['fiber_id'] not in paths:
                raise ValueError('Cut node references a missing fiber.')
            self.repo.update(
                'fiber_nodes',
                node.id(),
                geometry=QgsGeometry.fromPointXY(
                    point_at(paths[node['fiber_id']], float(node['at']))
                ),
            )
        for mark in marks:
            if mark['fiber_id'] not in paths:
                raise ValueError('Splice mark references a missing fiber.')
            closure_id = (
                mark['closure_id']
                if self.repo.find('closures', mark['closure_id']) is not None
                else ''
            )
            self.repo.update(
                'fiber_marks',
                mark.id(),
                {'closure_id': closure_id},
                QgsGeometry.fromPointXY(
                    point_at(paths[mark['fiber_id']], float(mark['at']))
                ),
            )
        optical = self.optical_nodes()
        for joint in joints:
            if joint['a_id'] not in optical or joint['b_id'] not in optical:
                raise ValueError(
                    'Fiber joint references a missing optical node.'
                )
            manual = (
                pt(joint['manual_x'], joint['manual_y'])
                if optional(joint['manual_x']) is not None
                and optional(joint['manual_y']) is not None
                else None
            )
            geometry, marker, closure_id = self._joint_geometry(
                optical[joint['a_id']]['point'],
                optical[joint['b_id']]['point'],
                joint['closure_id'],
                joint['joint_id'],
                manual,
            )
            self.repo.update(
                'fiber_joints',
                joint.id(),
                {'closure_id': closure_id},
                geometry,
            )

    def validate_fiber_operations(self):
        fibers = {f['fiber_id']: f for f in self.repo.features('fibers')}
        for fiber in fibers.values():
            validate_fiber_data(
                fiber['purpose'], fiber['fiber_type'], fiber['description']
            )
        optical = self.optical_nodes()
        nodes = {n['node_id']: n for n in self.repo.features('fiber_nodes')}
        cuts, marks, routes = (
            self.repo.features(k)
            for k in ('fiber_cuts', 'fiber_marks', 'fiber_routes')
        )
        owned = set()
        for cut in cuts:
            if (
                cut['fiber_id'] not in fibers
                or cut['left_id'] not in nodes
                or cut['right_id'] not in nodes
                or not cut['group_id']
            ):
                raise ValueError('Invalid fiber cut references.')
            for field in ('left_id', 'right_id'):
                node = nodes[cut[field]]
                if (
                    node['fiber_id'] != cut['fiber_id']
                    or node['node_id'] in owned
                ):
                    raise ValueError(
                        'Cut endpoints must belong to one fiber and one cut.'
                    )
                owned.add(node['node_id'])
        if owned != set(nodes):
            raise ValueError('Orphaned cut endpoint.')
        spans = self.fiber_sections()
        span_owners = {span['span_id']: span['fiber_id'] for span in spans}
        seen = set()
        for record in self.repo.features('fiber_purposes'):
            if (
                record['span_id'] in seen
                or span_owners.get(record['span_id']) != record['fiber_id']
                or record['purpose'] not in PURPOSES
            ):
                raise ValueError('Invalid saved fiber section purpose.')
            seen.add(record['span_id'])
        if any((nodes, marks, routes)):
            scene = self.fiber_scene()
            for node in nodes.values():
                value = float(node['at'])
                if (
                    not math.isfinite(value)
                    or not 0 < value < 1
                    or node.geometry()
                    .asPoint()
                    .distance(
                        point_at(scene.drawn_fibers[node['fiber_id']], value)
                    )
                    > 1e-6
                ):
                    raise ValueError(
                        'Cut endpoint does not match its fiber position.'
                    )
            for mark in marks:
                if (
                    mark['fiber_id'] not in fibers
                    or not math.isfinite(float(mark['loss_db']))
                    or mark['loss_db'] < 0
                ):
                    raise ValueError('Invalid fiber splice point.')
                if not any(
                    s['fiber_id'] == mark['fiber_id']
                    and s['start'] <= float(mark['at']) <= s['end']
                    for s in spans
                ):
                    raise ValueError(
                        'Splice point is outside a continuous fiber section.'
                    )
                if (
                    mark.geometry()
                    .asPoint()
                    .distance(
                        point_at(
                            scene.drawn_fibers[mark['fiber_id']],
                            float(mark['at']),
                        )
                    )
                    > 1e-6
                ):
                    raise ValueError(
                        'Splice point is detached from its fiber.'
                    )
                if (
                    optional(mark['closure_id'])
                    and self.repo.find('closures', mark['closure_id']) is None
                ):
                    raise ValueError(
                        'Splice point references a missing closure.'
                    )
            for route in routes:
                if route['fiber_id'] not in fibers or route['side'] not in (
                    'A',
                    'B',
                ):
                    raise ValueError('Invalid branch route.')
                points = route.geometry().asPolyline()
                validate_row_route(points, [0], 1)
                if not route.geometry().isSimple():
                    raise ValueError('A branch route crosses itself.')
                ends = {
                    e['side']: e
                    for e in self.repo.features('fiber_ends')
                    if e['fiber_id'] == route['fiber_id']
                }
                if optional(ends[route['side']]['port_id']) or any(
                    points[index].distance(ends[side].geometry().asPoint())
                    > 1e-6
                    for side, index in (('A', 0), ('B', -1))
                ):
                    raise ValueError(
                        'Branch geometry is detached from its fiber ends.'
                    )
        occupied = {
            link_record[k]
            for link_record in self.repo.features('links')
            for k in ('from_id', 'to_id')
        }
        bound = {
            e['port_id']
            for e in self.repo.features('fiber_ends')
            if optional(e['port_id'])
        }
        active_ports = {
            p['port_id']
            for a in self.repo.features('active_equipment')
            for p in self.ports_of(a['obj_id'])
        }
        if bound.intersection(occupied).intersection(active_ports):
            raise ValueError(
                'An active-equipment port has both a cable and a separate '
                'external connection.'
            )
        for joint in self.repo.features('fiber_joints'):
            a, b = optical.get(joint['a_id']), optical.get(joint['b_id'])
            if (
                a is None
                or b is None
                or joint['a_id'] == joint['b_id']
                or joint['kind'] not in ('splice', 'through', 'patch', 'drop')
            ):
                raise ValueError('Invalid optical joint references or kind.')
            if (
                not math.isfinite(float(joint['loss_db']))
                or joint['loss_db'] < 0
                or (joint['kind'] == 'through' and joint['loss_db'] != 0)
            ):
                raise ValueError('Invalid optical joint loss.')
            if (optional(joint['manual_x']) is None) != (
                optional(joint['manual_y']) is None
            ) or (
                optional(joint['manual_x']) is not None
                and not all(
                    math.isfinite(float(joint[k]))
                    for k in ('manual_x', 'manual_y')
                )
            ):
                raise ValueError('Invalid manual joint position.')
            for identity, node in ((joint['a_id'], a), (joint['b_id'], b)):
                if identity in occupied or (
                    node['kind'] == 'fiber_ends' and node['port_id']
                ):
                    raise ValueError(
                        'An optical endpoint has more than one external '
                        'connection.'
                    )
                occupied.add(identity)
                if identity in active_ports and identity in bound:
                    raise ValueError(
                        'An active-equipment port has more than one optical '
                        'attachment.'
                    )
            if a['role'] == b['role'] and a['role'] in ('input', 'output'):
                raise ValueError(
                    'Invalid input/input or output/output optical connection.'
                )
            if (
                a['fiber_type']
                and b['fiber_type']
                and a['fiber_type'] != b['fiber_type']
            ):
                raise ValueError(
                    'An optical joint connects incompatible known fiber types.'
                )
            points = joint.geometry().asPolyline()
            validate_vertices([(p.x(), p.y()) for p in points])
            if (
                len(points) < 2
                or points[0].distance(a['point']) > 1e-6
                or points[-1].distance(b['point']) > 1e-6
            ):
                raise ValueError(
                    'Optical joint geometry is detached from its endpoints.'
                )
            if (
                optional(joint['closure_id'])
                and self.repo.find('closures', joint['closure_id']) is None
            ):
                raise ValueError('Optical joint references a missing closure.')
        for slack in self.repo.features('slack'):
            if (
                optional(slack['cable_id'])
                and self.repo.find('cables', slack['cable_id']) is None
            ):
                raise ValueError('Slack references a missing cable.')
            if (
                not math.isfinite(float(slack['length_m']))
                or slack['length_m'] < 1
                or not math.isfinite(float(slack['requested_m']))
                or slack['requested_m'] < 0.5
                or slack['length_m'] != math.floor(slack['requested_m'] + 0.5)
                or slack['orientation'] not in (0, 90, 270)
                or slack['status'] not in ('designed', 'as_built')
            ):
                raise ValueError('Invalid cable slack properties.')
            if not slack.hasGeometry() or slack.geometry().isEmpty():
                raise ValueError('Slack needs a position.')
            point = slack.geometry().asPoint()
            if not all(math.isfinite(v) for v in (point.x(), point.y())):
                raise ValueError('Slack position must be finite.')
