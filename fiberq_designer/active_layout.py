"""Source splitter/OLT/ONT geometry in CAD units, around the actual connector.

Ported from fq-sema.lsp sections 13–15, MIT (FIBERQ_CAD_LICENSE.txt).
"""

from dataclasses import dataclass
import math

from .housing_catalog import SPLITTER_LOSSES


ACTIVE_VERSION = 'fiberq_active_1'


@dataclass(frozen=True)
class ActiveProperties:
    kind: str = 'SPLITTER'
    count: int = 8
    orientation: int = 0
    status: str = 'designed'
    address: str = ''
    anchor: str = 'output'

    def validate(self):
        if (
            self.kind not in ('SPLITTER', 'OLT', 'ONT')
            or type(self.count) is not int
        ):
            raise ValueError('Choose splitter, OLT or ONT.')
        if (
            (self.kind == 'SPLITTER' and self.count not in SPLITTER_LOSSES)
            or (self.kind == 'OLT' and not 1 <= self.count <= 576)
            or (self.kind == 'ONT' and self.count != 1)
        ):
            raise ValueError(
                'Invalid splitter ratio or active-equipment port count.'
            )
        if (
            self.orientation not in (0, 90, 180, 270)
            or self.status not in ('designed', 'as_built')
            or self.anchor not in ('input', 'output')
        ):
            raise ValueError(
                'Invalid active-equipment orientation, status or anchor.'
            )
        if (
            not isinstance(self.address, str)
            or len(self.address) > 200
            or any(ord(c) < 32 for c in self.address)
        ):
            raise ValueError(
                'ONT customer/address must be one line, up to 200 characters.'
            )
        return self


@dataclass
class ActiveLayout:
    frame: list
    paths: list
    ports: dict
    texts: list
    center: tuple
    color: str
    loss: float = 0


def active_layout(properties, name=''):
    p = properties.validate()
    if p.kind == 'SPLITTER':
        distance = max(2.6, 0.12 * 0.8 * (p.count - 1))
        u, top = -1.6, 0.0
        if p.anchor == 'input':
            u, top = distance + 4, 0.4 * (p.count - 1)
        right, middle = u - distance, top - 0.4 * (p.count - 1)
        left = right - 2
        high, low = middle + 0.8, middle - 0.8
        ports = {0: (left - 2, middle)}
        paths = [[ports[0], (left, middle)]]
        for number in range(1, p.count + 1):
            row = top - 0.8 * (number - 1)
            ports[number] = (u + 1.6, row)
            paths.append([(right, middle), (u, row), ports[number]])
        texts = [
            (
                (left + right) / 2,
                middle,
                '1:' + str(p.count),
                0.558,
                True,
                'Center',
            ),
            (
                left,
                low - 0.495,
                '{} dB'.format(SPLITTER_LOSSES[p.count]),
                0.495,
                False,
                'Left',
            ),
            (left, high + 0.27, name, 0.495, False, 'Left'),
        ]
        color, loss = '#0000ff', SPLITTER_LOSSES[p.count]
    elif p.kind == 'OLT':
        left, right, high, low = -18, -4, 1.6, 1.6 - 0.8 * (p.count + 3)
        ports = {n: (0, -0.8 * (n - 1)) for n in range(1, p.count + 1)}
        paths = [[(right, y), (0, y)] for x, y in ports.values()]
        texts = [(left, high + 0.56, name, 0.972, True, 'Left')]
        texts.extend(
            (right + 0.4, y + 0.108, 'PON ' + str(n), 0.54, False, 'Left')
            for n, (x, y) in ports.items()
        )
        color, loss = '#ff00ff', 0
    else:
        left, right, high, low = 4, 13, 1.3, -1.3
        ports, paths = {1: (0, 0)}, [[(0, 0), (left, 0)]]
        texts = [
            (left + 0.5, high - 1.1, name, 0.648, True, 'Left'),
            (left + 0.5, high - 2.2, p.address, 0.54, False, 'Left'),
        ]
        color, loss = '#ffff00', 0
    frame = [
        (left, high),
        (right, high),
        (right, low),
        (left, low),
        (left, high),
    ]
    return ActiveLayout(
        frame,
        paths,
        ports,
        texts,
        ((left + right) / 2, (high + low) / 2),
        color,
        loss,
    )


def local_to_world(point, center, at, angle):
    theta = math.radians(angle)
    x, y = (point[0] - center[0]) * 10, (point[1] - center[1]) * 10
    return at[0] + x * math.cos(theta) - y * math.sin(theta), at[
        1
    ] + x * math.sin(theta) + y * math.cos(theta)


def active_properties(element, record):
    return ActiveProperties(
        element['kind'],
        int(element['count']),
        int(record['orientation']),
        record['status'],
        record['address'],
        record['anchor'],
    ).validate()
