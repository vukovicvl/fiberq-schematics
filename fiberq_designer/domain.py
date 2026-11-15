"""Network layout and connectivity rules without Qt or QGIS dependencies."""

from collections import defaultdict
from dataclasses import dataclass
import math
import uuid


FIBER_COLORS = [
    '#1675cf',
    '#f5841f',
    '#129449',
    '#8b5a38',
    '#8a8d98',
    '#ffffff',
    '#e32636',
    '#151515',
    '#eac523',
    '#8525bd',
    '#ed71b7',
    '#14acb0',
]
EQUIPMENT = {
    'ODF': 'ODF',
    'CLOSURE': 'Splice closure',
    'CABINET': 'Cabinet',
    'FDH': 'FDH cabinet',
    'MDU': 'MDU cabinet',
    'FAT': 'FAT box',
    'OTB': 'OTB box',
    'PATCH_PANEL': 'Patch panel',
    'OLT': 'OLT',
    'ONT': 'ONT',
}
KINDS = set(EQUIPMENT) | {'CABLE', 'SPLITTER'}


def uid():
    return uuid.uuid4().hex


@dataclass(frozen=True)
class PortSpec:
    label: str
    number: int
    role: str
    dx: float
    dy: float
    color: str


def check_capacity(kind, count):
    if kind not in KINDS:
        raise ValueError('Unknown equipment type: ' + str(kind))
    if not isinstance(count, int) or not 1 <= count <= 576:
        raise ValueError('Capacity must be between 1 and 576.')
    if kind == 'SPLITTER' and count not in (2, 4, 8, 16, 32, 64):
        raise ValueError('Splitter outputs must be 2, 4, 8, 16, 32 or 64.')


def port_specs(kind, count, side='left'):
    check_capacity(kind, count)
    if kind == 'SPLITTER':
        result = [PortSpec('IN', 0, 'input', -30, 0, FIBER_COLORS[0])]
        result.extend(
            PortSpec(
                'O{}'.format(i + 1),
                i + 1,
                'output',
                30,
                (count - 1) * 4 - i * 8,
                FIBER_COLORS[i % 12],
            )
            for i in range(count)
        )
        return result
    if kind == 'CABLE':
        return [
            PortSpec(
                '{:02d}'.format(i + 1),
                i + 1,
                'fiber',
                41 if side == 'left' else -41,
                (count - 1) * 4 - i * 8,
                FIBER_COLORS[i % 12],
            )
            for i in range(count)
        ]
    rows = (count + 1) // 2
    role = {'CLOSURE': 'splice', 'OLT': 'output', 'ONT': 'input'}.get(
        kind, 'terminal'
    )
    return [
        PortSpec(
            '{:02d}'.format(i + 1),
            i + 1,
            role,
            -40 if i < rows else 40,
            (rows - 1) * 4 - (i % rows) * 8,
            FIBER_COLORS[i % 12],
        )
        for i in range(count)
    ]


def frame_size(kind, count):
    if kind == 'CABLE':
        return 82, max(33, (count - 1) * 8 + 24)
    if kind == 'SPLITTER':
        return 60, max(34, (count - 1) * 8 + 22)
    return 80, max(40, ((count + 1) // 2 - 1) * 8 + 24)


def validate_connection(a, b, element_kinds, occupied):
    if a is None or b is None:
        raise ValueError('A selected port no longer exists.')
    if a['port_id'] == b['port_id']:
        raise ValueError('Cannot connect a port to itself.')
    if a['obj_id'] == b['obj_id'] and element_kinds[a['obj_id']] != 'CLOSURE':
        raise ValueError(
            'Internal splices are supported inside splice closures.'
        )
    if a['port_id'] in occupied or b['port_id'] in occupied:
        raise ValueError(
            'One or both ports already have a splice or patch connection.'
        )
    if a['role'] == b['role'] and a['role'] in ('input', 'output'):
        raise ValueError(
            'Cannot connect two optical inputs or two optical outputs.'
        )


def validate_vertices(vertices):
    if len(vertices) < 2 or any(
        not math.isfinite(v) for p in vertices for v in p
    ):
        raise ValueError('A cable needs at least two finite points.')
    if any(a == b for a, b in zip(vertices, vertices[1:])):
        raise ValueError('Consecutive cable vertices must be different.')


def connected_ports(
    start,
    ports,
    elements,
    links,
    fibers,
    fiber_ends=None,
    cuts=(),
    nodes=(),
    joints=(),
):
    """Trace physical continuity, including cable fibers and splitter
    branches."""
    graph = defaultdict(set)
    edges = []
    if fiber_ends is None:
        edges.extend((f['a_id'], f['b_id']) for f in fibers)
    else:
        ends = defaultdict(dict)
        for end in fiber_ends:
            ends[end['fiber_id']][end['side']] = end['end_id']
            if isinstance(end['port_id'], str) and end['port_id']:
                edges.append((end['end_id'], end['port_id']))
        if cuts:
            from .fiber_model import fiber_spans

            for fiber in fibers:
                edges.extend(
                    (s['a_id'], s['b_id'])
                    for s in fiber_spans(
                        fiber['fiber_id'], fiber_ends, cuts, nodes
                    )
                )
        else:
            edges.extend((pair['A'], pair['B']) for pair in ends.values())
    edges.extend((link['from_id'], link['to_id']) for link in links)
    edges.extend((joint['a_id'], joint['b_id']) for joint in joints)
    for a, b in edges:
        graph[a].add(b)
        graph[b].add(a)
    for element in elements:
        if element['kind'] == 'SPLITTER':
            owned = [p for p in ports if p['obj_id'] == element['obj_id']]
            inputs = [p['port_id'] for p in owned if p['role'] == 'input']
            for a in inputs:
                for p in owned:
                    if p['role'] == 'output':
                        graph[a].add(p['port_id'])
                        graph[p['port_id']].add(a)
    seen, pending = set(), [start]
    while pending:
        current = pending.pop()
        if current not in seen:
            seen.add(current)
            pending.extend(graph[current] - seen)
    return seen


def fiber_rows(count, group_size=12):
    if (
        type(count) is not int
        or not 1 <= count <= 576
        or type(group_size) is not int
        or not 1 <= group_size <= 576
    ):
        raise ValueError(
            'Fiber count and fibers per tube must be between 1 and 576.'
        )
    return [-8.0 * i - 8.0 * (i // group_size) for i in range(count)]
