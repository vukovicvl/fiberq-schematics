"""FQIZVOD local geometry from FiberQ CAD 1.0.0 (MIT).

Coordinates are CAD units. See FIBERQ_CAD_LICENSE.txt.
"""

import math


def outlet_column(column, leftmost=None):
    return (
        min(column - 2.0, leftmost - 1.6)
        if leftmost is not None
        else column - 2.0
    )


def outlet_layout(
    rows, first_row, bend_column, length, elbow=True, column=0.0
):
    if (
        not rows
        or abs(rows[0]) > 1e-7
        or any(a <= b for a, b in zip(rows, rows[1:]))
        or not all(
            math.isfinite(v)
            for v in (*rows, first_row, bend_column, length, column)
        )
        or length < 12
    ):
        raise ValueError(
            'Outlet rows must descend from zero; length must be at least 12 '
            'CAD units.'
        )
    span = -rows[-1]
    bottom = first_row - span - length
    paths = []
    for offset in rows:
        y = first_row + offset
        x = bend_column - span - offset if elbow else bend_column + offset
        paths.append(
            [(x, bottom), (x, y), (column, y)]
            if elbow
            else [(x, y), (x, bottom)]
        )
    return dict(
        route=paths[0],
        paths=paths,
        bottom=bottom,
        last_row=first_row - span,
        left=min(p[0] for path in paths for p in path),
        frame_side='B' if elbow else 'A',
    )


def occupied_left(columns, left, right, reach=6.0):
    inside = [x for x in columns if left - 0.01 <= x <= right + 0.01]
    if not inside:
        return None
    boundary = min(inside)
    while True:
        nearby = [x for x in columns if boundary - reach <= x < boundary]
        if not nearby:
            return boundary
        boundary = min(nearby)
