"""Source-derived rack, cabinet and box primitives in CAD units.

Ported from fq:drawodfn/fq:odfmod/fq:drawodo/fq:drawzok in FiberQ CAD 1.0.0.
Copyright (c) 2026 Vladimir Vukovic. MIT: see FIBERQ_CAD_LICENSE.txt.
"""

from dataclasses import dataclass, replace
import math

from .housing_catalog import (
    CAD_SCALE,
    CABINET_HOUSINGS,
    BOX_HOUSINGS,
    SPLITTER_LOSSES,
    module_labels,
)


@dataclass(frozen=True)
class LayoutText:
    point: tuple
    text: str
    height: float
    bold: bool = False
    align: str = 'Center'
    localizable: bool = False


@dataclass
class ElementLayout:
    frame: list
    ports: dict
    lines: list
    dots: list
    texts: list
    module_frames: list
    visible_ports: set = None
    splitter: object = None


@dataclass
class ContainedSplitterLayout:
    frame: list
    ports: dict
    paths: dict
    texts: list


def housing_layout(properties, name, rack_width=0.0):
    if properties.layout_state:
        from .housing_multi import expand_layout

        base = replace(properties, layout_state='')
        layout = (
            box_layout(base, name)
            if base.kind in BOX_HOUSINGS
            else cabinet_layout(base, name, all_ports=True)
            if base.kind in CABINET_HOUSINGS
            else odf_layout(base, name, rack_width)
        )
        return expand_layout(layout, properties)
    if properties.kind in BOX_HOUSINGS:
        return box_layout(properties, name)
    if properties.kind in CABINET_HOUSINGS:
        return cabinet_layout(properties, name)
    return odf_layout(properties, name, rack_width)


def cabinet_layout(properties, name, all_ports=False):
    """fq:drawodo/drawodospl: first arriving row is the placement anchor.

    Logical connectors retain all physical numbers; only used connectors are
    drawn. Unused logical positions are available for later termination.
    """
    properties.validate()
    capacity, ratio = properties.capacity, properties.splitter_ratio
    used = properties.requested_used
    if used is None:
        used = 1 if ratio else min(6, capacity)
    row_count = max(1, used)
    rows = (
        list(properties.row_positions)
        if properties.row_positions
        else [-0.8 * i - 0.8 * (i // 12) for i in range(row_count)]
    )
    rows.extend(rows[-1] - 0.8 * (i + 1) for i in range(capacity - len(rows)))
    feed = properties.splitter_feed or capacity
    order = list(range(feed, 0, -1)) + list(range(capacity, feed, -1))
    ports = {number: (0.0, rows[i]) for i, number in enumerate(order)}
    visible = set(order if all_ports else order[:used])
    envelope = (
        list(properties.row_positions)
        if properties.row_positions
        else rows[:row_count]
    )
    splitter = None
    if ratio:
        output_rows = [0.8 * (ratio - i) for i in range(ratio)]
        for number, y in enumerate(output_rows, 1):
            ports[number] = (0.0, y)
        visible.update(range(1, ratio + 1))
        visible.add(feed)
        envelope.extend(output_rows)
        splitter = contained_splitter_layout(
            ports[feed][1], output_rows, ratio
        )
    left, right, divider = -11.5, 0.5, -8.7
    top, bottom = max(envelope) + 2.0, min(envelope) - 2.0
    frame = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
        (left, top),
    ]
    texts = [
        LayoutText(
            (-5.5, top + 3.1), '{} ({})'.format(name, capacity), 1.188, True
        )
    ]
    if properties.address:
        texts.append(
            LayoutText((-5.5, top + 1.5), properties.address, 1.188, True)
        )
    texts.extend(
        [
            LayoutText(
                (-10.1, bottom + 1.15),
                '{}-{}'.format(properties.model, capacity)
                if properties.model
                else str(capacity),
                0.378,
            ),
            LayoutText(
                (-10.1, bottom + 0.55),
                properties.mounting,
                0.378,
                localizable=True,
            ),
        ]
    )
    lines, dots = [(0, [(divider, top), (divider, bottom)])], []
    for number in sorted(visible, key=lambda n: -ports[n][1]):
        y = ports[number][1]
        lines.extend(
            [
                (
                    number,
                    [
                        (-1.83, y + 0.377),
                        (-0.92, y + 0.377),
                        (-0.92, y - 0.377),
                        (-1.83, y - 0.377),
                        (-1.83, y + 0.377),
                    ],
                ),
                (number, [(-1.60, y + 0.377), (-1.60, y - 0.377)]),
                (number, [(-1.15, y + 0.377), (-1.15, y - 0.377)]),
                (number, [(-0.95, y + 0.19), (-1.14, y - 0.19)]),
                (number, [(-1.15, y), (0.0, y)]),
            ]
        )
        dots.append((number, (0.0, y), 0.107))
        texts.append(LayoutText((-1.375, y), str(number), 0.3888, True))
    return ElementLayout(
        frame, ports, lines, dots, texts, [frame], visible, splitter
    )


def contained_splitter_layout(
    feed_y, outputs, ratio, connector_left=-1.83, fanout_distance=None
):
    """fq:odospl/fq:zokspl, with family-specific connector and fanout."""
    mid = sum(outputs) / len(outputs)
    dx = (
        min(5.0, max(2.6, 0.096 * (len(outputs) - 1)))
        if fanout_distance is None
        else fanout_distance
    )
    right, left = -dx, -dx - 2.0
    upper, lower = mid + 0.8, mid - 0.8
    frame = [
        (left, upper),
        (right, upper),
        (right, lower),
        (left, lower),
        (left, upper),
    ]
    ports = {0: (left, mid)}
    ports.update({i: (right, mid) for i in range(1, len(outputs) + 1)})
    paths = {
        0: [
            (0.0, feed_y),
            (connector_left, feed_y),
            (left - 1.2, feed_y),
            (left - 1.2, mid),
            (left, mid),
        ]
    }
    paths.update(
        {
            i: [(right, mid), (connector_left, y), (0.0, y)]
            for i, y in enumerate(outputs, 1)
        }
    )
    texts = [
        LayoutText(
            ((left + right) / 2, mid), '1:{}'.format(ratio), 0.558, True
        ),
        LayoutText(
            (left, lower - 0.495),
            '{} dB'.format(SPLITTER_LOSSES[ratio]),
            0.495,
            False,
            'Left',
        ),
    ]
    return ContainedSplitterLayout(frame, ports, paths, texts)


def box_layout(properties, name):
    """fq:drawzok: all physical adapters, highest number on the first fiber."""
    properties.validate()
    capacity, ratio = properties.capacity, properties.splitter_ratio
    if ratio:
        feed = properties.splitter_feed or capacity
        ports = {n: (0.0, 0.8 * (feed - n)) for n in range(1, capacity + 1)}
    else:
        used = (
            capacity
            if properties.requested_used is None
            else max(1, properties.requested_used)
        )
        incoming = (
            list(properties.row_positions)
            if properties.row_positions
            else [-0.8 * i - 0.8 * (i // 12) for i in range(used)]
        )
        rows = incoming[: min(used, capacity)]
        last_used = min(rows)
        rows.extend(
            last_used - 0.8 * (i + 1) for i in range(capacity - len(rows))
        )
        ports = {capacity - i: (0.0, row) for i, row in enumerate(rows)}
    top = max(point[1] for point in ports.values()) + 1.5
    bottom = min(point[1] for point in ports.values()) - 1.5
    right, left = 1.0, 1.0 - (10.8 if ratio else 6.2)
    center = (left + right) / 2
    frame = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
        (left, top),
    ]
    texts = []
    if properties.address:
        texts.append(
            LayoutText((center, top + 5.88), properties.address, 1.4, True)
        )
    description = '{}{}x{}'.format(
        properties.model + '  ' if properties.model else '',
        capacity,
        properties.connector,
    )
    texts.extend(
        [
            LayoutText((center, top + 3.64), name, 1.4, True),
            LayoutText((center, top + 1.54), description, 1.008),
            LayoutText(
                (center, bottom - 1.4),
                properties.mounting,
                1.008,
                localizable=True,
            ),
        ]
    )
    lines, dots = [], []
    s = 0.438
    a, b, c = 2.110 * s, 1.602 * s, 0.508 * s
    for number, (_, y) in ports.items():
        lines.extend(
            [
                (number, [(-a, y - c), (-b, y - c), (-b, y + c), (-a, y + c)]),
                (number, [(-a, y), (0.0, y)]),
                (
                    number,
                    [(-1.729 * s, y + 0.254 * s), (-1.983 * s, y - 0.254 * s)],
                ),
            ]
        )
        dots.append((number, (-0.666 * s, y), 0.286 * s / 2))
        texts.append(LayoutText((-1.5, y), str(number), 0.588, False, 'Right'))
    splitter = (
        contained_splitter_layout(
            ports[properties.splitter_feed or capacity][1],
            [ports[n][1] for n in range(1, ratio + 1)],
            ratio,
            -a,
            2.6,
        )
        if ratio
        else None
    )
    return ElementLayout(
        frame, ports, lines, dots, texts, [frame], set(ports), splitter
    )


def odf_layout(properties, name, rack_width=0.0):
    """Anchor is the first connector, as in FiberQ standalone placement."""
    properties.validate()
    count = properties.total_capacity
    used = (
        max(1, properties.requested_used)
        if properties.requested_used is not None
        else count
    )
    rows = (
        list(properties.row_positions[:count])
        if properties.row_positions
        else [-0.8 * i - 0.8 * (i // 12) for i in range(used)]
    )
    last_used = rows[-1]
    rows.extend(last_used - 0.8 * (i + 1) for i in range(count - len(rows)))
    right, divider = 1.0, -4.8
    left = divider - max(5.8, rack_width + 1.8)
    top, bottom = (
        1.6,
        min(rows[-1], min(properties.row_positions or rows)) - 1.6,
    )
    frame = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
        (left, top),
    ]
    lines = []
    dots, texts = (
        [],
        [LayoutText(((left + right) / 2, top + 6.3), name, 1.7, True)],
    )
    if properties.model:
        texts.append(
            LayoutText(
                ((left + right) / 2, top + 3.6), properties.model, 1.7, True
            )
        )
    frames = []
    for index, label in enumerate(
        module_labels(properties.rack_module, properties.modules)
    ):
        group = rows[
            index * properties.capacity: (index + 1) * properties.capacity
        ]
        upper = max(group) + (1.6 if index == 0 else 0.4)
        lower = bottom if index == properties.modules - 1 else min(group) - 0.4
        frames.append(
            [
                (left, upper),
                (right, upper),
                (right, lower),
                (left, lower),
                (left, upper),
            ]
        )
        lines.append((0, [(divider, upper), (divider, lower)]))
        if label:
            texts.append(
                LayoutText(
                    ((left + divider) / 2, (upper + lower) / 2),
                    label,
                    1.7,
                    True,
                )
            )
    ports = {}
    s = 0.438
    for number, y in enumerate(rows, 1):
        ports[number] = (0.0, y)
        a, b, c = 2.110 * s, 1.602 * s, 0.508 * s
        lines.extend(
            [
                (number, [(-a, y - c), (-b, y - c), (-b, y + c), (-a, y + c)]),
                (number, [(-a, y), (0, y)]),
                (
                    number,
                    [(-1.729 * s, y + 0.254 * s), (-1.983 * s, y - 0.254 * s)],
                ),
            ]
        )
        # fq:Wdot's argument is a diameter.
        dots.append((number, (-0.666 * s, y), 0.286 * s / 2))
        texts.append(
            LayoutText(
                (-1.5, y),
                str((number - 1) % properties.capacity + 1),
                0.42,
                False,
                'Right',
            )
        )
    return ElementLayout(frame, ports, lines, dots, texts, frames)


def transform(point, anchor=(0.0, 0.0), orientation=0, mirrored=False):
    angle = math.radians(orientation)
    x, y = point[0] * CAD_SCALE, point[1] * CAD_SCALE
    if mirrored:
        x = -x
    return (
        anchor[0] + x * math.cos(angle) - y * math.sin(angle),
        anchor[1] + x * math.sin(angle) + y * math.cos(angle),
    )
