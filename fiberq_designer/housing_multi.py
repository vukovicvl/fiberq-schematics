"""Saved multi-cable housing row maps and source frame expansion rules."""

from dataclasses import replace
import json
import math


def read_layout_state(value, capacity, modules):
    if not value:
        return None
    try:
        state = json.loads(value)
        if (
            not isinstance(state, dict)
            or set(state) != {'version', 'rows', 'visible', 'bounds'}
            or state['version'] != 1
        ):
            raise ValueError('Invalid layout fields.')
        if (
            not isinstance(state['rows'], list)
            or len(state['rows']) != modules
        ):
            raise ValueError('Module row count mismatch.')
        for rows in state['rows']:
            if (
                not isinstance(rows, list)
                or len(rows) != capacity
                or any(
                    type(y) not in (int, float) or not math.isfinite(y)
                    for y in rows
                )
            ):
                raise ValueError('Invalid connector rows.')
        if (
            not isinstance(state['bounds'], list)
            or len(state['bounds']) != modules
        ):
            raise ValueError('Module frame count mismatch.')
        for index, bounds in enumerate(state['bounds']):
            if (
                not isinstance(bounds, list)
                or len(bounds) != 2
                or any(
                    type(y) not in (int, float) or not math.isfinite(y)
                    for y in bounds
                )
                or bounds[0] <= bounds[1]
            ):
                raise ValueError('Invalid frame bounds.')
            if index and state['bounds'][index - 1][1] < bounds[0] - 1e-6:
                raise ValueError(
                    'Module frames overlap. Choose the module matching the '
                    'cable rows.'
                )
        visible = state['visible']
        if (
            not isinstance(visible, list)
            or len(visible) != len(set(visible))
            or any(
                type(n) is not int or not 1 <= n <= capacity * modules
                for n in visible
            )
        ):
            raise ValueError('Invalid visible connector numbers.')
        for number in visible:
            module, port = divmod(number - 1, capacity)
            row = state['rows'][module][port]
            top, bottom = state['bounds'][module]
            if not bottom < row < top:
                raise ValueError(
                    'A visible connector lies outside its module frame.'
                )
        positions = sorted(y for rows in state['rows'] for y in rows)
        if any(abs(a - b) < 1e-5 for a, b in zip(positions, positions[1:])):
            raise ValueError('Housing connector rows overlap.')
        return state
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError(
            'Invalid multi-cable housing layout: ' + str(error)
        ) from error


def expand_layout(layout, properties):
    """Move unoccupied connectors independently; retain source glyph
    dimensions."""
    state = read_layout_state(
        properties.layout_state, properties.capacity, properties.modules
    )
    if state is None:
        return layout
    old_ports = dict(layout.ports)
    rows = [y for module in state['rows'] for y in module]
    visible = set(state['visible'])
    layout.ports = {n: (p[0], rows[n - 1]) for n, p in old_ports.items()}
    delta = {n: layout.ports[n][1] - p[1] for n, p in old_ports.items()}
    layout.lines = [
        (n, [(x, y + delta[n]) for x, y in points])
        for n, points in layout.lines
        if n and n in visible
    ]
    layout.dots = [
        (n, (x, y + delta[n]), radius)
        for n, (x, y), radius in layout.dots
        if n in visible
    ]
    old_top, old_bottom = (
        max(y for x, y in layout.frame),
        min(y for x, y in layout.frame),
    )
    left, right = (
        min(x for x, y in layout.frame),
        max(x for x, y in layout.frame),
    )
    top, bottom = state['bounds'][0][0], state['bounds'][-1][1]
    layout.frame = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
        (left, top),
    ]
    layout.module_frames = []
    divider = -4.8 if properties.kind in ('ODF', 'PATCH_PANEL') else -8.7
    for upper, lower in state['bounds']:
        layout.module_frames.append(
            [
                (left, upper),
                (right, upper),
                (right, lower),
                (left, lower),
                (left, upper),
            ]
        )
        if properties.kind in ('ODF', 'PATCH_PANEL', 'FDH', 'MDU'):
            layout.lines.append((0, [(divider, upper), (divider, lower)]))
    texts = []
    port_height = (
        0.42
        if properties.kind in ('ODF', 'PATCH_PANEL')
        else 0.3888
        if properties.kind in ('FDH', 'MDU')
        else 0.588
    )
    for text in layout.texts:
        x, y = text.point
        if abs(text.height - port_height) < 1e-8 and text.text.isdigit():
            candidates = [
                n
                for n, p in old_ports.items()
                if abs(p[1] - y) < 1e-6
                and (n - 1) % properties.capacity + 1 == int(text.text)
            ]
            if candidates:
                number = candidates[0]
                if number in visible:
                    texts.append(replace(text, point=(x, y + delta[number])))
                continue
        # Module labels stay centered in their own expanded module frame.
        if (
            properties.kind in ('ODF', 'PATCH_PANEL')
            and abs(x - (left + divider) / 2) < 1e-6
        ):
            from .housing_catalog import module_labels

            labels = module_labels(properties.rack_module, properties.modules)
            if text.text in labels:
                index = labels.index(text.text)
                upper, lower = state['bounds'][index]
                texts.append(replace(text, point=(x, (upper + lower) / 2)))
                continue
        if y > old_top:
            y += top - old_top
        elif (
            y < old_bottom
            or properties.kind in ('FDH', 'MDU')
            and y < old_bottom + 1.6
        ):
            y += bottom - old_bottom
        texts.append(replace(text, point=(x, y)))
    layout.texts, layout.visible_ports = texts, visible
    if properties.splitter_ratio:
        from .element_layout import contained_splitter_layout

        feed = properties.splitter_feed or properties.capacity
        layout.splitter = contained_splitter_layout(
            layout.ports[feed][1],
            [
                layout.ports[n][1]
                for n in range(1, properties.splitter_ratio + 1)
            ],
            properties.splitter_ratio,
            -0.92418 if properties.kind in ('FAT', 'OTB') else -1.83,
            2.6 if properties.kind in ('FAT', 'OTB') else None,
        )
    return layout
