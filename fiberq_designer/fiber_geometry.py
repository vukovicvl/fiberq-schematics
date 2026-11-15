"""Geometry for fiber sections and explicit endpoint connections."""

import math

from qgis.core import QgsGeometry

from .geometry import pt


def fraction_on_line(geometry, point):
    length = geometry.length()
    if not length > 1e-8:
        raise ValueError('A fiber needs a nonzero path.')
    distance = geometry.lineLocatePoint(QgsGeometry.fromPointXY(point))
    if distance < 0:
        raise ValueError('Cannot project this point onto the fiber.')
    return min(1.0, max(0.0, distance / length))


def point_at(geometry, fraction):
    if not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Fiber position must be within its path.')
    return geometry.interpolate(geometry.length() * fraction).asPoint()


def line_slice(geometry, start=0.0, end=1.0):
    if not 0 <= start < end <= 1:
        raise ValueError('Fiber section must have ordered bounds.')
    points, total = geometry.asPolyline(), geometry.length()
    out, distance = [point_at(geometry, start)], 0.0
    for a, b in zip(points, points[1:]):
        distance += a.distance(b)
        if start * total + 1e-7 < distance < end * total - 1e-7:
            out.append(b)
    out.append(point_at(geometry, end))
    return QgsGeometry.fromPolylineXY(out)


def joint_path(a, b, column=None, angle=0):
    theta = math.radians(angle)
    cosine, sine = math.cos(theta), math.sin(theta)

    def local(p):
        return (
            p.x() * cosine + p.y() * sine,
            -p.x() * sine + p.y() * cosine,
        )

    ax, ay = local(a)
    bx, by = local(b)
    column = (ax + bx) / 2 if column is None else column
    raw = [
        a,
        pt(column * cosine - ay * sine, column * sine + ay * cosine),
        pt(column * cosine - by * sine, column * sine + by * cosine),
        b,
    ]
    points = []
    for point in raw:
        if not points or point.distance(points[-1]) > 1e-7:
            points.append(point)
    if len(points) == 1:
        # Coincident ports still need a persisted connection geometry.
        points.append(pt(points[0].x() + 0.001, points[0].y()))
        points.append(b)
    return QgsGeometry.fromPolylineXY(points), raw[1]
