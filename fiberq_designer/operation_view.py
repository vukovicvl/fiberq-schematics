"""Derive geometry for optical sections, joints and active equipment."""

import math

from qgis.core import QgsFeature, QgsGeometry

from .active_layout import active_layout, active_properties, local_to_world
from .cable_catalog import rows_from_record
from .fiber_geometry import line_slice, point_at
from .fiber_model import PURPOSES, fiber_spans
from .geometry import housing_font_metrics, optional, parallel_path, pt


def pinned_text(
    scene,
    identity,
    point,
    text,
    height,
    angle=0,
    ink=None,
    bold=False,
    align='Left',
    baseline=False,
):
    if not text:
        return
    family, width, cap = housing_font_metrics(text, bold=bold)
    width *= height
    theta = math.radians(angle)
    if baseline:
        point = pt(
            point.x() - math.sin(theta) * height / 2,
            point.y() + math.cos(theta) * height / 2,
        )
    # Center in drawing coordinates: QGIS's left/right label alignment shifts
    # along the screen axis, which displaces vertical CAD text outside its
    # body.
    offset = (
        width / 2 if align == 'Left' else -width / 2 if align == 'Right' else 0
    )
    point = pt(
        point.x() + offset * math.cos(theta),
        point.y() + offset * math.sin(theta),
    )
    if 90 < angle % 360 <= 270:
        angle -= 180
    scene._add(
        scene.housing_labels,
        QgsGeometry.fromPointXY(point),
        obj_id=identity,
        text=text,
        size=height * cap,
        family=family,
        angle=angle,
        bold=int(bold),
        align='Center',
        color=ink,
    )
    left = -width / 2
    theta = math.radians(angle)
    corners = [
        pt(
            point.x() + x * math.cos(theta) - y * math.sin(theta),
            point.y() + x * math.sin(theta) + y * math.cos(theta),
        )
        for x, y in (
            (left, -height / 2),
            (left + width, -height / 2),
            (left + width, height / 2),
            (left, height / 2),
        )
    ]
    scene.text_bounds.combineExtentWith(
        QgsGeometry.fromPolylineXY(corners).boundingBox()
    )


def draw_active(scene, element, record, ports):
    props = active_properties(element, record)
    layout = active_layout(props, element['name'])
    at = element.geometry().asPoint()

    def world(p):
        return pt(
            *local_to_world(
                p, layout.center, (at.x(), at.y()), props.orientation
            )
        )

    shape = QgsGeometry.fromPolygonXY([[world(p) for p in layout.frame]])
    scene.hit_frames[element['obj_id']] = shape
    scene._add(
        scene.frames,
        shape,
        obj_id=element['obj_id'],
        kind=element['kind'],
        color=layout.color,
    )
    for path in layout.paths:
        scene._add(
            scene.details,
            QgsGeometry.fromPolylineXY([world(p) for p in path]),
            color=layout.color,
        )
    for port in ports:
        scene.positions[port['port_id']] = port.geometry().asPoint()
        scene._add(
            scene.ports,
            port.geometry(),
            obj_id=element['obj_id'],
            port_id=port['port_id'],
            label=port['label'],
            cad=1,
        )
    for x, y, text, height, bold, align in layout.texts:
        pinned_text(
            scene,
            element['obj_id'],
            world((x, y)),
            text,
            height * 10,
            props.orientation,
            layout.color,
            bold,
            align,
            baseline=align != 'Center',
        )
    return shape.boundingBox()


def prepare_routes(scene, repository):
    scene.route_overrides = {
        r['fiber_id']: r for r in repository.features('fiber_routes')
    }
    if not scene.route_overrides:
        return
    cables = {c['cable_id']: c for c in repository.features('cables')}
    fibers = {f['fiber_id']: f for f in repository.features('fibers')}
    for identity, record in scene.route_overrides.items():
        fiber = fibers.get(identity)
        if fiber is None:
            raise ValueError('Branch references a missing fiber.')
        cable = cables[fiber['cable_id']]
        if cable['layout_mode'] != 'rows' or record['side'] not in ('A', 'B'):
            raise ValueError('Branch needs a row-based cable and an A/B end.')
        path = parallel_path(
            cable.geometry().asPolyline(),
            rows_from_record(cable)[int(fiber['fiber_no']) - 1],
        )
        scene.end_positions[(identity, record['side'])] = path[
            0 if record['side'] == 'A' else -1
        ]


def apply_operations(scene, repository, operations=True):
    fibers = {f['fiber_id']: f for f in repository.features('fibers')}
    scene.operation_hits = {}
    if operations:
        for identity, record in scene.route_overrides.items():
            scene.drawn_fibers[identity] = record.geometry()
    ends, cuts, nodes = (
        repository.features(k)
        for k in ('fiber_ends', 'fiber_cuts', 'fiber_nodes')
    )
    overrides = {
        p['span_id']: p['purpose']
        for p in repository.features('fiber_purposes')
    }
    sections = {
        identity: fiber_spans(
            identity, ends, cuts if operations else (), nodes
        )
        for identity in fibers
    }
    node_purposes = {}
    batch = []
    for original in scene.batches[scene.fibers.id()]:
        identity = original['fiber_id']
        data = fibers[identity]
        for span in sections[identity]:
            code = overrides.get(span['span_id'], data['purpose'])
            purpose = PURPOSES.get(code)
            node_purposes[span['a_id']] = node_purposes[span['b_id']] = code
            feature = QgsFeature(original)
            feature.setGeometry(
                line_slice(
                    scene.drawn_fibers[identity], span['start'], span['end']
                )
            )
            for key, value in dict(
                span_id=span['span_id'],
                a_node=span['a_id'],
                b_node=span['b_id'],
                purpose=code,
                stroke=purpose.color if purpose else '',
                width=purpose.width if purpose else 0.22,
            ).items():
                feature[key] = value
            batch.append(feature)
        if operations and data['description']:
            path = scene.drawn_fibers[identity]
            point = point_at(path, 0.5)
            context = path.closestSegmentWithContext(point)
            a, b = path.asPolyline()[context[2] - 1: context[2] + 1]
            theta = math.atan2(b.y() - a.y(), b.x() - a.x())
            pinned_text(
                scene,
                identity,
                pt(
                    point.x() - 8 * math.sin(theta),
                    point.y() + 8 * math.cos(theta),
                ),
                data['description'],
                5.4,
                math.degrees(theta),
                baseline=True,
            )
    scene.batches[scene.fibers.id()] = batch
    scene.fiber_sections = {
        s['span_id']: s for spans in sections.values() for s in spans
    }
    if not operations:
        return
    for end in ends:
        if optional(end['port_id']) and end['end_id'] in node_purposes:
            node_purposes[end['port_id']] = node_purposes[end['end_id']]

    def connection_purpose(a, b):
        codes = {node_purposes.get(a), node_purposes.get(b)} - {None, ''}
        return PURPOSES.get(next(iter(codes))) if len(codes) == 1 else None

    links = {link['link_id']: link for link in repository.features('links')}
    for feature in scene.batches[scene.links.id()]:
        record = links[feature['link_id']]
        purpose = connection_purpose(record['from_id'], record['to_id'])
        feature['width'] = purpose.width if purpose else 0.2
        if purpose:
            feature['color'] = purpose.color or '#333333'
    for feature in scene.batches[scene.splices.id()]:
        record = links[feature['link_id']]
        purpose = connection_purpose(record['from_id'], record['to_id'])
        feature['color'] = (
            purpose.color if purpose and purpose.color else '#333333'
        )
    cables = {c['cable_id']: c for c in repository.features('cables')}
    for identity, record in scene.route_overrides.items():
        fiber, path = fibers[identity], record.geometry().asPolyline()
        a, b = (
            (path[-2], path[-1])
            if record['side'] == 'B'
            else (path[1], path[0])
        )
        distance = a.distance(b)
        ux, uy = (b.x() - a.x()) / distance, (b.y() - a.y()) / distance
        cable = cables[fiber['cable_id']]
        ink = fiber['color']
        text = '{} / {}'.format(cable['name'], fiber['fiber_no'])
        pinned_text(
            scene,
            identity,
            pt(b.x() - 6 * ux - 1.44 * uy, b.y() - 6 * uy + 1.44 * ux),
            text,
            5.4,
            math.degrees(math.atan2(uy, ux)),
            ink,
            align='Right',
            baseline=True,
        )
    for joint in repository.features('fiber_joints'):
        purpose = connection_purpose(joint['a_id'], joint['b_id'])
        ink = purpose.color if purpose and purpose.color else '#333333'
        scene._add(
            scene.optical_lines,
            joint.geometry(),
            joint_id=joint['joint_id'],
            color=ink,
            width=purpose.width if purpose else 0.13,
            active=0,
        )
        if joint['kind'] == 'splice':
            vertices = joint.geometry().asPolyline()
            # The source dot is on the first elbow, not halfway up a vertical
            # transition.
            point = (
                vertices[1]
                if len(vertices) > 2
                else point_at(joint.geometry(), 0.5)
            )
            scene._add(
                scene.optical_marks,
                QgsGeometry.fromPointXY(point),
                record_key='fiber_joints',
                record_id=joint['joint_id'],
                color=ink,
            )
    for mark in repository.features('fiber_marks'):
        span = next(
            s
            for s in sections[mark['fiber_id']]
            if s['start'] <= float(mark['at']) <= s['end']
        )
        purpose = PURPOSES.get(
            overrides.get(span['span_id'], fibers[mark['fiber_id']]['purpose'])
        )
        scene._add(
            scene.optical_marks,
            mark.geometry(),
            record_key='fiber_marks',
            record_id=mark['mark_id'],
            color=purpose.color if purpose and purpose.color else '#333333',
        )
    for slack in repository.features('slack'):
        at, theta = (
            slack.geometry().asPoint(),
            math.radians(int(slack['orientation'])),
        )

        def world(x, y):
            return pt(
                at.x() + x * math.cos(theta) - y * math.sin(theta),
                at.y() + x * math.sin(theta) + y * math.cos(theta),
            )

        for i in range(3):
            ring = [
                world(6 * i + 12 * math.cos(t), 7 * math.sin(t))
                for t in (n * math.tau / 40 for n in range(41))
            ]
            scene._add(
                scene.details,
                QgsGeometry.fromPolylineXY(ring),
                color='#ff7f00',
            )
        pinned_text(
            scene,
            slack['slack_id'],
            world(0, 12),
            'R = {} m'.format(int(slack['length_m'])),
            5.4,
            int(slack['orientation']),
            '#ff7f00',
            baseline=True,
        )
        label = 'R = {} m'.format(int(slack['length_m']))
        width = max(24, housing_font_metrics(label, bold=False)[1] * 5.4)
        scene.operation_hits[('slack', slack['slack_id'])] = (
            QgsGeometry.fromPolygonXY(
                [
                    [
                        world(x, y)
                        for x, y in (
                            (-12, -7),
                            (width, -7),
                            (width, 18),
                            (-12, 18),
                            (-12, -7),
                        )
                    ]
                ]
            )
        )
