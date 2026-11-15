"""Closure rules from fq:drawnast/fq:vopseg/fq:naoz in FiberQ CAD 1.0.0.

Copyright (c) 2026 Vladimir Vukovic. MIT: see FIBERQ_CAD_LICENSE.txt.
Local CAD coordinates are anchored at the left edge of the first fiber.
"""

from dataclasses import dataclass
import math
import re

from .element_layout import ElementLayout, LayoutText


CLOSURE_LAYOUT_VERSION = 'fiberq_closure_1'
DEFAULT_CLOSURE_WIDTH = 8.0
MIN_CLOSURE_WIDTH = 1.6
CLOSURE_MARGIN = 2.0
CLOSURE_LOCATIONS = {
    'manhole': 'manhole',
    'pole': 'pole',
    'buried': 'buried',
    'in_duct': 'in duct',
    'facade': 'on facade',
    'other': 'other',
}


@dataclass(frozen=True)
class ClosureProperties:
    closure_type: str = 'N'
    location: str = 'manhole'
    location_number: str = ''
    status: str = 'designed'

    def validate(self):
        if self.closure_type not in ('N', 'NL') or self.location not in (
            '',
            *CLOSURE_LOCATIONS,
        ):
            raise ValueError('Choose a closure type and location.')
        if self.status not in ('designed', 'as_built'):
            raise ValueError('Choose Designed or As-built.')
        if (
            not isinstance(self.location_number, str)
            or any(c in self.location_number for c in '\n\r\x00')
            or len(self.location_number) > 200
        ):
            raise ValueError(
                (
                    'Location number must be a single line of at most 200 '
                    'characters.'
                )
            )
        return self

    @property
    def caption(self):
        return ' '.join(
            s
            for s in (
                CLOSURE_LOCATIONS.get(self.location, ''),
                self.location_number.strip(),
            )
            if s
        )

    def caption_in(self, language):
        from .i18n import translate

        return ' '.join(
            s
            for s in (
                translate(CLOSURE_LOCATIONS.get(self.location, ''), language),
                self.location_number.strip(),
            )
            if s
        )


def closure_name(label, closure_type='N', existing=()):
    if closure_type not in ('N', 'NL'):
        raise ValueError('Choose N ordinary or NL latent.')
    if (
        not isinstance(label, str)
        or any(c in label for c in '\n\r\x00')
        or len(label) > 200
    ):
        raise ValueError(
            'Label must be a single line of at most 200 characters.'
        )
    label = label.strip()
    prefix = closure_type + ' '
    if label:
        numeric = re.fullmatch(r'[0-9/ .-]+', label) and any(
            c.isdigit() for c in label
        )
        return prefix + label if numeric else label
    names = {n.casefold() for n in existing}
    index = 1
    while (prefix + str(index)).casefold() in names:
        index += 1
    return prefix + str(index)


def validate_closure_bounds(width, top, bottom):
    if not all(
        isinstance(v, (int, float)) and math.isfinite(v)
        for v in (width, top, bottom)
    ):
        raise ValueError('Closure bounds must be finite.')
    if width < MIN_CLOSURE_WIDTH - 1e-8 or top <= bottom:
        raise ValueError(
            (
                'Closure width must be at least 1.60 CAD units and its '
                'height must be positive.'
            )
        )


def closure_layout(properties, name, width, top, bottom, language='en'):
    properties.validate()
    validate_closure_bounds(width, top, bottom)
    frame = [
        (0.0, top),
        (width, top),
        (width, bottom),
        (0.0, bottom),
        (0.0, top),
    ]
    texts = [LayoutText((width / 2, top + 2.6), name, 1.08, True)]
    if properties.caption:
        texts.append(
            LayoutText(
                (width / 2, top + 1.3), properties.caption_in(language), 0.648
            )
        )
    return ElementLayout(frame, {}, [], [], texts, [frame])


def cross_section(paths, coordinate=0.0):
    """Only actual crossings contribute while any fiber crosses this section.

    fq:vopseg falls back to nearby endpoints only if no path crosses at all.
    """
    values = []
    for points in paths:
        for a, b in zip(points, points[1:]):
            if min(a[0], b[0]) - 1e-8 <= coordinate <= max(a[0], b[0]) + 1e-8:
                if abs(b[0] - a[0]) < 1e-8:
                    values.extend((a[1], b[1]))
                else:
                    t = (coordinate - a[0]) / (b[0] - a[0])
                    values.append(a[1] + t * (b[1] - a[1]))
    if not values:
        values = [
            min((points[0], points[-1]), key=lambda p: abs(p[0] - coordinate))[
                1
            ]
            for points in paths
            if points
        ]
    if not values:
        raise ValueError('The cable has no usable fiber geometry.')
    return min(values), max(values)


def local_point(point, anchor, angle, scale=10.0):
    theta = math.radians(angle)
    dx, dy = point[0] - anchor[0], point[1] - anchor[1]
    return (
        (dx * math.cos(theta) + dy * math.sin(theta)) / scale,
        (dy * math.cos(theta) - dx * math.sin(theta)) / scale,
    )


def nearest_segment(points, target):
    candidates = []
    for index, (a, b) in enumerate(zip(points, points[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        if length2 < 1e-12:
            continue
        t = min(
            1.0,
            max(
                0.0,
                ((target[0] - a[0]) * dx + (target[1] - a[1]) * dy) / length2,
            ),
        )
        point = (a[0] + t * dx, a[1] + t * dy)
        distance = math.hypot(point[0] - target[0], point[1] - target[1])
        candidates.append(
            (
                distance,
                index,
                point,
                math.degrees(math.atan2(dy, dx)) % 360,
            )
        )
    if not candidates:
        raise ValueError('The cable has no usable segment.')
    return min(candidates, key=lambda c: c[0])
