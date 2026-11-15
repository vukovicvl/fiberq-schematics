"""Read-only network reports and explicit, branch-specific optical paths."""

from collections import Counter, defaultdict, deque

from .fiber_model import PURPOSES, fiber_spans
from .i18n import Phrase


def text(value):
    return value if isinstance(value, str) else ''


def optical_graph(data):
    graph = defaultdict(list)
    ports = {p['port_id']: p for p in data['ports']}
    fibers = {f['fiber_id']: f for f in data['fibers']}
    elements = {e['obj_id']: e for e in data['elements']}
    ends = data['fiber_ends']
    edges = {}

    def edge(key, a, b, **attrs):
        record = dict(edge_id=key, a_id=a, b_id=b, **attrs)
        edges[key] = record
        graph[a].append((b, key))
        graph[b].append((a, key))

    for fiber in fibers.values():
        for span in fiber_spans(
            fiber['fiber_id'], ends, data['fiber_cuts'], data['fiber_nodes']
        ):
            edge(
                'span:' + span['span_id'],
                span['a_id'],
                span['b_id'],
                kind='fiber',
                fiber_id=fiber['fiber_id'],
                start=span['start'],
                end=span['end'],
            )
    for end in ends:
        if text(end['port_id']):
            edge(
                'end:' + end['end_id'],
                end['end_id'],
                end['port_id'],
                kind='termination',
            )
    for link in data['links']:
        edge(
            'link:' + link['link_id'],
            link['from_id'],
            link['to_id'],
            kind=link['kind'],
            loss_db=link['loss_db'],
        )
    for joint in data['fiber_joints']:
        edge(
            'joint:' + joint['joint_id'],
            joint['a_id'],
            joint['b_id'],
            kind=joint['kind'],
            loss_db=joint['loss_db'],
        )
    splitters = {
        s['splitter_id']: float(s['loss_db'])
        for s in data['contained_splitters']
    }
    splitters.update(
        {
            s['obj_id']: float(s['loss_db'])
            for s in data['active_equipment']
            if elements[s['obj_id']]['kind'] == 'SPLITTER'
        }
    )
    for element in elements.values():
        if element['kind'] != 'SPLITTER':
            continue
        owned = [p for p in ports.values() if p['obj_id'] == element['obj_id']]
        inputs = [p for p in owned if p['role'] == 'input']
        for source in inputs:
            for port in owned:
                if port['role'] == 'output':
                    edge(
                        'splitter:' + port['port_id'],
                        source['port_id'],
                        port['port_id'],
                        kind='splitter',
                        obj_id=element['obj_id'],
                        loss_db=splitters.get(element['obj_id']),
                    )
    return graph, edges


def find_path(graph, edges, start, destination):
    if start == destination or start not in graph or destination not in graph:
        raise ValueError('Choose two different connected optical endpoints.')

    def search(omit=None):
        pending, previous = deque([start]), {start: None}
        while pending:
            node = pending.popleft()
            if node == destination:
                return previous
            for other, identity in graph[node]:
                if identity != omit and other not in previous:
                    previous[other] = node, identity
                    pending.append(other)
        return None

    previous = search()
    if previous is None:
        raise ValueError(
            'No continuous optical path: inspect cuts and missing connections.'
        )
    nodes, path, node = [destination], [], destination
    while previous[node] is not None:
        node, identity = previous[node]
        nodes.append(node)
        path.append(edges[identity])
    nodes.reverse()
    path.reverse()
    if any(search(record['edge_id']) is not None for record in path):
        raise ValueError(
            'More than one optical path connects these endpoints. Resolve '
            'the loop before calculating a path budget.'
        )
    return nodes, path


def node_names(data):
    elements = {e['obj_id']: e for e in data['elements']}
    cables = {c['cable_id']: c for c in data['cables']}
    fibers = {f['fiber_id']: f for f in data['fibers']}
    names = {
        p['port_id']: '{} / {}'.format(
            elements[p['obj_id']]['name'], p['label']
        )
        for p in data['ports']
    }
    for key, identity, suffix in (
        ('fiber_ends', 'end_id', ''),
        ('fiber_nodes', 'node_id', 'cut end'),
    ):
        for end in data[key]:
            fiber = fibers[end['fiber_id']]
            side = (
                end['side']
                if key == 'fiber_ends'
                else 'cut left'
                if any(
                    c['left_id'] == end[identity] for c in data['fiber_cuts']
                )
                else 'cut right'
            )
            names[end[identity]] = Phrase(
                '{} / F{} / {}',
                cables[fiber['cable_id']]['name'],
                fiber['fiber_no'],
                Phrase(side),
            )
    return names


def route_report(data, start, destination):
    graph, edges = optical_graph(data)
    nodes, path = find_path(graph, edges, start, destination)
    names = node_names(data)
    fibers = {f['fiber_id']: f for f in data['fibers']}
    cables = {c['cable_id']: c for c in data['cables']}
    ports = {p['port_id']: p for p in data['ports']}
    elements = {e['obj_id']: e for e in data['elements']}
    path_fibers = {e['fiber_id'] for e in path if e['kind'] == 'fiber'}
    types = {text(fibers[f]['fiber_type']) for f in path_fibers} - {''}
    splices = sum(e['kind'] == 'splice' for e in path)
    marks = [
        m
        for m in data['fiber_marks']
        if any(
            e['kind'] == 'fiber'
            and e['fiber_id'] == m['fiber_id']
            and e['start'] <= float(m['at']) <= e['end']
            for e in path
        )
    ]
    splices += len(marks)
    connectors = [
        p
        for n, p in ports.items()
        if n in nodes and elements[p['obj_id']]['kind'] != 'SPLITTER'
    ]
    splitters = [e for e in path if e['kind'] == 'splitter']
    warnings = []
    if len(types) != 1 or any(
        not text(fibers[f]['fiber_type']) for f in path_fibers
    ):
        warnings.append(
            'Fiber type is unknown or mixed; choose the budget type '
            'explicitly.'
        )
    if any(e['loss_db'] is None for e in splitters):
        warnings.append(
            'A legacy splitter has no saved catalogue loss; enter its loss '
            'manually.'
        )
    known_length = 0
    ends = defaultdict(dict)
    for end in data['fiber_ends']:
        ends[end['fiber_id']][end['side']] = end['end_id']
    for identity in path_fibers:
        cable = cables[fibers[identity]['cable_id']]
        if float(cable['length_m']) <= 0 or not set(
            ends[identity].values()
        ).issubset(nodes):
            known_length = None
            break
        known_length += float(cable['length_m'])
    if known_length is None:
        warnings.append(
            'Real path length is incomplete or a partial fiber is traversed. '
            'Enter surveyed kilometres; schematic distances are not real '
            'lengths.'
        )
    rows = []
    for index, record in enumerate(path):
        label = ''
        if record['kind'] == 'fiber':
            fiber = fibers[record['fiber_id']]
            label = '{} / F{} · {}'.format(
                cables[fiber['cable_id']]['name'],
                fiber['fiber_no'],
                text(fiber['description']),
            )
        elif record['kind'] == 'splitter':
            label = '{} · {} dB'.format(
                elements[record['obj_id']]['name'],
                record['loss_db'] if record['loss_db'] is not None else '?',
            )
        rows.append(
            [
                str(index + 1),
                names[nodes[index]],
                names[nodes[index + 1]],
                Phrase(record['kind']),
                label,
            ]
        )
    return dict(
        title='Optical path',
        columns=['Step', 'From', 'To', 'Connection', 'Details'],
        rows=rows,
        start=start,
        destination=destination,
        nodes=nodes,
        edges=path,
        splices=splices,
        connectors=len(connectors),
        splitter_losses=[s['loss_db'] for s in splitters],
        fiber_type=next(iter(types)) if len(types) == 1 else '',
        known_length_m=known_length,
        warnings=warnings,
        marks=[m['mark_id'] for m in marks],
        text_provenance=True,
    )


def bom_report(data):
    quantities = Counter()
    housings = {h['obj_id']: h for h in data['housings']}
    active = {a['obj_id']: a for a in data['active_equipment']}
    closures = {c['obj_id']: c for c in data['closures']}
    parents = {
        c['splitter_id']: c['parent_id'] for c in data['contained_splitters']
    }
    for element in data['elements']:
        kind, identity = element['kind'], element['obj_id']
        props = housings.get(
            identity, active.get(identity, closures.get(identity, {}))
        )
        spec = (
            Phrase('{} ports', element['count'])
            if kind not in ('SPLITTER', 'CLOSURE')
            else Phrase('1:{}', element['count'])
            if kind == 'SPLITTER'
            else Phrase(text(props.get('closure_type', '')))
        )
        for key in ('model', 'connector', 'mounting'):
            if text(props.get(key)):
                spec += ' · ' + (
                    Phrase(props[key]) if key == 'mounting' else props[key]
                )
        if props.get('module_count', 1) > 1:
            spec += Phrase(' · {} modules', props['module_count'])
        if 'requested_used' in props:
            spec += Phrase(' · {} used', props['requested_used'])
        status = (
            text(props.get('status'))
            or text(housings.get(parents.get(identity), {}).get('status'))
            or 'unknown'
        )
        quantities[(Phrase(kind), spec, Phrase(status), Phrase('each'))] += 1
    for cable in data['cables']:
        spec = Phrase(
            '{} · {} fibers',
            text(cable['model']) or Phrase('Compatibility cable'),
            cable['count'],
        )
        if int(cable['tube_count']) > 0:
            spec += ' · {}x{} · {}'.format(
                cable['tube_count'], cable['group_size'], cable['fiber_type']
            )
        status = text(cable['status']) or 'unknown'
        quantities[
            (Phrase('Cable'), spec, Phrase(status), Phrase('each'))
        ] += 1
        if float(cable['length_m']) > 0:
            quantities[
                (Phrase('Cable length'), spec, Phrase(status), 'm')
            ] += float(cable['length_m'])
    cables = {c['cable_id']: c for c in data['cables']}
    cut = {c['fiber_id'] for c in data['fiber_cuts']}
    overrides = {
        p['span_id']: p['purpose'] for p in data.get('fiber_purposes', [])
    }
    for fiber in data['fibers']:
        codes = {
            overrides.get(s['span_id'], fiber['purpose'])
            for s in fiber_spans(
                fiber['fiber_id'],
                data['fiber_ends'],
                data['fiber_cuts'],
                data['fiber_nodes'],
            )
        }
        purpose = (
            'Mixed purposes'
            if len(codes) > 1
            else PURPOSES[next(iter(codes))].title
            if next(iter(codes)) in PURPOSES
            else 'Unknown'
        )
        spec = Phrase(
            '{} · {} · {} · {}',
            text(cables[fiber['cable_id']]['model']),
            Phrase(text(fiber['fiber_type']) or 'Unknown'),
            Phrase(purpose),
            Phrase('Cut' if fiber['fiber_id'] in cut else 'Through'),
        )
        quantities[
            (
                Phrase('Fiber'),
                spec,
                Phrase(text(cables[fiber['cable_id']]['status']) or 'unknown'),
                Phrase('fiber'),
            )
        ] += 1
    for kind in ('splice', 'patch', 'drop', 'through'):
        count = sum(j['kind'] == kind for j in data['fiber_joints'])
        count += sum(
            j['kind'] == kind and float(j['loss_db']) > 0
            for j in data['links']
        )
        if kind == 'splice':
            count += len(data['fiber_marks'])
        if count:
            quantities[
                (
                    Phrase('Connection'),
                    Phrase(kind),
                    Phrase('unknown'),
                    Phrase('each'),
                )
            ] += count
    for slack in data['slack']:
        quantities[
            (
                Phrase('Slack'),
                '{} m'.format(int(slack['length_m'])),
                Phrase(text(slack['status'])),
                Phrase('each'),
            )
        ] += 1
    return dict(
        title='Bill of materials',
        columns=['Item', 'Specification', 'Status', 'Quantity', 'Unit'],
        rows=[
            [item, spec, status, '{:g}'.format(count), unit]
            for (item, spec, status, unit), count in sorted(quantities.items())
        ],
        warnings=[
            'Fiber quantities count logical fibers once; cut sections are '
            'not extra fibers. Unspecified cable lengths are excluded from '
            'metre totals.'
        ],
        text_provenance=True,
    )


def check_report(data, validation_error=None):
    rows = []
    if validation_error:
        rows.append(
            [
                Phrase('Error'),
                Phrase('Storage / topology'),
                Phrase(str(validation_error)),
            ]
        )
        return dict(
            title='Network checks',
            columns=['Severity', 'Object', 'Finding'],
            rows=rows,
            warnings=[
                'Resolve the storage/topology error before running design '
                'checks. No records were changed.'
            ],
            text_provenance=True,
        )
    closures = {c['obj_id']: c for c in data['closures']}
    elements = {e['obj_id']: e for e in data['elements']}
    for identity, closure in closures.items():
        has_splice = (
            any(m['closure_id'] == identity for m in data['fiber_marks'])
            or any(
                j['closure_id'] == identity and j['kind'] == 'splice'
                for j in data['fiber_joints']
            )
            or any(
                m['closure_id'] == identity and m['kind'] == 'splice'
                for m in data['closure_members']
            )
        )
        if closure['closure_type'] != 'NL' and not has_splice:
            rows.append(
                [
                    Phrase('Warning'),
                    elements[identity]['name'],
                    Phrase(
                        'Ordinary closure has no splice points; latent NL '
                        'closures are excluded.'
                    ),
                ]
            )
    if not validation_error:
        graph, edges = optical_graph(data)
        seen = set()
        overrides = {
            p['span_id']: p['purpose'] for p in data.get('fiber_purposes', [])
        }
        for fiber in data['fibers']:
            for span in fiber_spans(
                fiber['fiber_id'],
                data['fiber_ends'],
                data['fiber_cuts'],
                data['fiber_nodes'],
            ):
                if (
                    overrides.get(span['span_id'], fiber['purpose'])
                    != 'nmWork'
                ):
                    continue
                if span['a_id'] in seen:
                    continue
                pending, component = [span['a_id']], set()
                while pending:
                    node = pending.pop()
                    if node in component:
                        continue
                    component.add(node)
                    pending.extend(
                        n for n, unused in graph[node] if n not in component
                    )
                seen.update(component)
                if not any(p['port_id'] in component for p in data['ports']):
                    rows.append(
                        [
                            Phrase('Warning'),
                            Phrase('Working fiber'),
                            Phrase(
                                'A continuous working section has no '
                                'equipment termination.'
                            ),
                        ]
                    )
        rows.insert(
            0,
            [
                Phrase('OK'),
                Phrase('Storage / topology'),
                Phrase(
                    'IDs, references, geometry, occupancy, fiber types and '
                    'saved layouts are valid.'
                ),
            ],
        )
    return dict(
        title='Network checks',
        columns=['Severity', 'Object', 'Finding'],
        rows=rows,
        warnings=[
            'Checks are read-only. A warning requires design review; it does '
            'not change the network.'
        ],
        text_provenance=True,
    )


def budget_report(result):
    rows = [
        [
            'Fiber',
            '{:.3f} km × {:.3f} dB/km ({} / {} nm)'.format(
                result['km'],
                result['attenuation'],
                result['fiber_type'],
                result['wavelength'],
            ),
            '{:.3f}'.format(result['components']['fiber']),
        ],
        [
            'Splices',
            '{} × {:.3f} dB'.format(result['splices'], result['splice_db']),
            '{:.3f}'.format(result['components']['splices']),
        ],
        [
            'Connectors',
            '{} × {:.3f} dB'.format(
                result['connectors'], result['connector_db']
            ),
            '{:.3f}'.format(result['components']['connectors']),
        ],
    ]
    rows += [
        [
            'Splitter {}'.format(i + 1),
            'Catalogue / entered loss',
            '{:.3f}'.format(loss),
        ]
        for i, loss in enumerate(result['splitter_losses'])
    ]
    rows += [
        [
            'Engineering margin',
            'Included in required budget',
            '{:.3f}'.format(result['components']['margin']),
        ],
        [
            'Total required budget',
            'Sum of losses and margin',
            '{:.3f}'.format(result['total_db']),
        ],
        [
            'B+ reference',
            '28 dB ceiling',
            'PASS' if result['b_plus'] else 'FAIL',
        ],
        [
            'C+ reference',
            '32 dB ceiling',
            'PASS' if result['c_plus'] else 'FAIL',
        ],
    ]
    return dict(
        title='Optical budget',
        columns=['Item', 'Calculation / assumption', 'Loss (dB) / result'],
        rows=[[Phrase(value) for value in row] for row in rows],
        text_provenance=True,
        warnings=[
            'B+/C+ are the CAD reference ceilings, not a transceiver or '
            'receiver-overload certification. Verify equipment '
            'specifications and measured losses.'
        ],
        inputs=result,
    )
