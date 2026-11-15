"""Source fiber purposes and derived spans between stable optical nodes.

FiberQ CAD 1.0.0 fq-sema.lsp sections 10 and 16 (MIT; see license).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Purpose:
    title: str
    color: str
    width: float


PURPOSES = {
    'nmFree': Purpose('Free', '', 0.13),
    'nmWork': Purpose('Working', '#ff0000', 0.30),
    'nmPlan': Purpose('Planned', '#00ffff', 0.30),
    'nmBuild': Purpose('Under construction', '#ffff00', 0.30),
    'nmAcc': Purpose('Accepted', '#0000ff', 0.30),
    'nmRsvd': Purpose('Reserved', '#ff00ff', 0.30),
    'nmSpare': Purpose('Spare', '#a57c00', 0.13),
    'nmThru': Purpose('Through', '#00a57c', 0.13),
    'nmSpl': Purpose('Splitter', '#007fff', 0.13),
    'nmDmg': Purpose('Damaged', '#00ff00', 0.30),
}


def fiber_spans(fiber_id, ends, cuts=(), nodes=()):
    """Cuts remove an interval; endpoint joins add a separate graph edge."""
    by_id = {n['node_id']: n for n in nodes}
    pair = {e['side']: e['end_id'] for e in ends if e['fiber_id'] == fiber_id}
    if set(pair) != {'A', 'B'}:
        raise ValueError('A fiber needs its original A/B endpoint identities.')
    intervals = sorted(
        (
            float(by_id[c['left_id']]['at']),
            float(by_id[c['right_id']]['at']),
            c['left_id'],
            c['right_id'],
        )
        for c in cuts
        if c['fiber_id'] == fiber_id
    )
    result, previous, node, identity = [], 0.0, pair['A'], fiber_id
    for left, right, left_id, right_id in intervals:
        if not previous < left < right < 1:
            raise ValueError(
                'Fiber cuts must be interior, ordered and nonoverlapping.'
            )
        result.append(
            dict(
                span_id=identity,
                fiber_id=fiber_id,
                a_id=node,
                b_id=left_id,
                start=previous,
                end=left,
            )
        )
        previous, node, identity = right, right_id, right_id
    result.append(
        dict(
            span_id=identity,
            fiber_id=fiber_id,
            a_id=node,
            b_id=pair['B'],
            start=previous,
            end=1.0,
        )
    )
    return result


def validate_fiber_data(purpose='', fiber_type='', description=''):
    if not isinstance(purpose, str) or (purpose and purpose not in PURPOSES):
        raise ValueError('Choose one of the ten fiber purposes.')
    if not isinstance(fiber_type, str) or fiber_type not in ('', 'SM', 'MM'):
        raise ValueError('Fiber type must be SM, MM or unknown.')
    if (
        not isinstance(description, str)
        or len(description) > 1000
        or any(ord(c) < 32 for c in description)
    ):
        raise ValueError(
            'Fiber description must be one line, up to 1000 characters.'
        )
