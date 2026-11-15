"""QGIS geometry adapters for the schematic drawing plane."""

import json
import math

from qgis.core import NULL, QgsGeometry, QgsPointXY

from .domain import frame_size
from .element_layout import housing_layout, transform
from .housing_catalog import OdfProperties
from .closure_layout import ClosureProperties, closure_layout


def pt(x, y):
    return QgsPointXY(float(x), float(y))


def optional(value):
    return None if value is None or value == NULL or value == '' else value


def odf_properties(element, housing):
    requested = housing['requested_used']
    return OdfProperties(
        capacity=int(housing['capacity']),
        model=housing['model'],
        connector=housing['connector'],
        mounting=housing['mounting'],
        rack_module=housing['rack_module'],
        requested_used=None
        if requested is None or requested == NULL
        else requested,
        orientation=float(housing['angle']),
        status=housing['status'],
        modules=int(housing['module_count']),
        mirrored=bool(housing['mirrored']),
        row_positions=tuple(json.loads(housing['row_positions'])),
        kind=element['kind'],
        address=housing['address'],
        splitter_ratio=int(housing['splitter_ratio']),
        splitter_feed=int(housing['splitter_feed']),
        layout_state=housing['layout_state'] or '',
    )


def odf_drawing(element, housing):
    return housing_layout(
        odf_properties(element, housing),
        element['name'],
        float(housing['rack_width']),
    )


def odf_point(point, at, orientation, mirrored=False):
    return pt(*transform(point, (at.x(), at.y()), orientation, mirrored))


def odf_frame(layout, at, orientation, mirrored=False):
    return QgsGeometry.fromPolygonXY(
        [[odf_point(p, at, orientation, mirrored) for p in layout.frame]]
    )


def closure_properties(record):
    return ClosureProperties(
        record['closure_type'],
        record['location'],
        record['location_number'],
        record['status'],
    )


def closure_drawing(element, record):
    return closure_layout(
        closure_properties(record),
        element['name'],
        float(record['width']),
        float(record['top']),
        float(record['bottom']),
    )


def closure_frame(element, record):
    return odf_frame(
        closure_drawing(element, record),
        element.geometry().asPoint(),
        float(record['angle']),
    )


def housing_font_metrics(text, family='Arial', bold=True):
    """Convert CAD cap heights to Qt's em-based text sizes."""
    from qgis.PyQt.QtGui import QFont, QFontInfo, QFontMetricsF

    font = QFont(family)
    font.setBold(bold)
    font.setPixelSize(1000)
    metrics = QFontMetricsF(font)
    height = metrics.capHeight()
    width = (
        metrics.horizontalAdvance(text)
        if hasattr(metrics, 'horizontalAdvance')
        else metrics.width(text)
    )
    return QFontInfo(font).family(), width / height, 1000.0 / height


def parallel_path(points, offset):
    normals = []
    for a, b in zip(points, points[1:]):
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy)
        normals.append(
            (-dy / length, dx / length) if length > 1e-8 else (0, 1)
        )
    result = []
    for i, point in enumerate(points):
        if i == 0:
            nx, ny = normals[0]
        elif i == len(points) - 1:
            nx, ny = normals[-1]
        else:
            a, b = normals[i - 1], normals[i]
            denominator = 1 + a[0] * b[0] + a[1] * b[1]
            if denominator > 0.1:
                nx, ny = (
                    (a[0] + b[0]) / denominator,
                    (a[1] + b[1]) / denominator,
                )
                scale = max(1, math.hypot(nx, ny) / 3)
                nx, ny = nx / scale, ny / scale
            else:
                nx, ny = b
        result.append(pt(point.x() + offset * nx, point.y() + offset * ny))
    return result


def validate_row_route(vertices, rows, minimum=0.0):
    from .domain import validate_vertices

    validate_vertices([(p.x(), p.y()) for p in vertices])
    for a, b in zip(vertices, vertices[1:]):
        if abs(a.x() - b.x()) > 1e-6 and abs(a.y() - b.y()) > 1e-6:
            raise ValueError(
                'Unattached cable segments must be horizontal or vertical.'
            )
    for a, b, c in zip(vertices, vertices[1:], vertices[2:]):
        before, after = (
            (b.x() - a.x(), b.y() - a.y()),
            (c.x() - b.x(), c.y() - b.y()),
        )
        if (
            abs(before[0] * after[1] - before[1] * after[0]) < 1e-6
            and before[0] * after[0] + before[1] * after[1] <= 0
        ):
            raise ValueError('A cable cannot reverse direction at a bend.')
    for index, offset in enumerate(rows, 1):
        path = parallel_path(vertices, offset)
        for segment, (a, b, p, q) in enumerate(
            zip(vertices, vertices[1:], path, path[1:]), 1
        ):
            advance = (
                (b.x() - a.x()) * (q.x() - p.x())
                + (b.y() - a.y()) * (q.y() - p.y())
            ) / math.hypot(b.x() - a.x(), b.y() - a.y())
            if advance <= 1e-6 or advance < minimum - 1e-6:
                raise ValueError(
                    'Cable segment {} is too short for fiber {}.'.format(
                        segment, index
                    )
                )


def cable_fiber_path(vertices, offset, outlet_mode=''):
    path = parallel_path(vertices, offset)
    if outlet_mode == 'straight':
        a, b = vertices[:2]
        length = a.distance(b)
        path[0] = pt(
            path[0].x() - offset * (b.x() - a.x()) / length,
            path[0].y() - offset * (b.y() - a.y()) / length,
        )
    return path


def frame_geometry(kind, count, at):
    width, height = frame_size(kind, count)
    x, y = at.x(), at.y()
    hw, hh = width / 2, height / 2
    return QgsGeometry.fromPolygonXY(
        [
            [
                pt(x - hw, y - hh),
                pt(x + hw, y - hh),
                pt(x + hw, y + hh),
                pt(x - hw, y + hh),
                pt(x - hw, y - hh),
            ]
        ]
    )


def link_geometry(a, b):
    a, b = a.geometry().asPoint(), b.geometry().asPoint()
    mid = (a.x() + b.x()) / 2
    points = [a, pt(mid, a.y()), pt(mid, b.y()), b]
    clean = [points[0]]
    for point in points[1:]:
        if point != clean[-1]:
            clean.append(point)
    return QgsGeometry.fromPolylineXY(clean)
