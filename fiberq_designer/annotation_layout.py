"""Portable CAD tables and legends frozen in their insertion language."""

import math

from .cable_catalog import CAD_COLORS, COLOR_NAMES
from .fiber_model import PURPOSES


def table_payload(report, language='en'):
    from .i18n import report_strings

    return dict(
        version=1, language=language, **report_strings(report, language)
    )


def table_primitives(payload):
    import textwrap

    # Source table heights: TXTH .54, title 1.6×, line pitch 1.6×.
    texts = [(0, 0, payload['title'], 8.64, True, '')]
    widths = []
    for column, title in enumerate(payload['columns']):
        longest = max(
            [len(title)] + [len(row[column]) for row in payload['rows']]
        )
        widths.append(max(90, min(800, longest * 3.5 + 18)))
    x = 0
    for title, width in zip(payload['columns'], widths):
        texts.append((x, -17.28, title, 5.4, True, ''))
        x += width
    y = -25.92
    for row in payload['rows']:
        x = 0
        row_height = 1
        for value, width in zip(row, widths):
            wrapped = textwrap.wrap(
                value, max(16, int((width - 18) / 3.5))
            ) or ['']
            row_height = max(row_height, len(wrapped))
            for index, line in enumerate(wrapped):
                texts.append((x, y - index * 8.64, line, 5.4, False, ''))
            x += width
        y -= row_height * 8.64
    for note in payload['notes']:
        # Notes wrap as drawing text instead of running across adjacent
        # columns.
        for line in textwrap.wrap(note, 120):
            texts.append((0, y - 8.64, line, 5.4, False, ''))
            y -= 8.64
    return [], texts, []


def legend_payload(settings):
    from .workspace_model import color_definitions

    return dict(
        version=1,
        language=settings['language'],
        color_code=settings['color_code'],
        definition=color_definitions(settings)[settings['color_code']],
    )


def legend_primitives(payload):
    from .i18n import translate

    language = payload['language']
    lines, texts, dots = [], [], []

    def line(points, ink='', width=0.13):
        lines.append((points, ink, width))

    def label(x, y, value, height=5.4, bold=False, ink='', align='Left'):
        texts.append(
            (
                x,
                y,
                translate(value, language),
                height,
                bold,
                ink,
                align,
            )
        )

    def rect(left, top, right, bottom, ink=''):
        line(
            [
                (left, top),
                (right, top),
                (right, bottom),
                (left, bottom),
                (left, top),
            ],
            ink,
        )

    def ellipse(x, y, rx, ry, ink):
        line(
            [
                (
                    x + rx * math.cos(i * math.tau / 40),
                    y + ry * math.sin(i * math.tau / 40),
                )
                for i in range(41)
            ],
            ink,
        )

    def row(y, title):
        label(140, y - 2.16, title)

    definition = payload['definition']
    first = definition['colors'][0]
    label(0, 20, 'Legend', 9.72, True)
    line([(0, 0), (110, 0)])
    label(
        30,
        2,
        '1.' + translate(COLOR_NAMES[first], language),
        3.78,
        ink=CAD_COLORS[first],
    )
    row(0, 'Fiber')
    y = -32
    for i in range(3):
        line([(0, y - 6 * i), (110, y - 6 * i)])
    ellipse(25, y - 6, 7, 13, CAD_COLORS[first])
    ellipse(85, y - 6, 7, 13, CAD_COLORS[first])
    label(25, y - 6, '1', 4.86, ink=CAD_COLORS[first], align='Center')
    row(y - 6, 'Cable / tube')
    y -= 42
    rect(0, y + 12, 110, y - 16)
    label(55, y + 17, 'N1', 6.48, True, '#ff0000', 'Center')
    row(y, 'Closure')
    y -= 40
    line([(0, y), (110, y)])
    dots.append((55, y, 4.4))
    row(y, 'Fusion splice')
    y -= 38
    rect(0, y + 12, 50, y - 12, '#0000ff')
    label(6, y - 2, '1:K', ink='#0000ff')
    for i in range(3):
        line([(50, y), (110, y + 10 - 10 * i)], '#0000ff')
    row(y, 'Splitter')
    y -= 40
    rect(0, y + 16, 110, y - 16)
    line([(28, y + 16), (28, y - 16)])
    for i in range(2):
        rect(82, y + 9 - 8 * i, 91, y + 2 - 8 * i)
        line([(91, y + 5.5 - 8 * i), (110, y + 5.5 - 8 * i)])
        dots.append((110, y + 5.5 - 8 * i, 2.14))
    row(y, 'FDH / MDU cabinet')
    y -= 40
    rect(0, y + 16, 110, y - 16)
    line([(55, y + 16), (55, y - 16)])
    scale = 0.438 * 10
    a, b, c = 2.110 * scale, 1.602 * scale, 0.508 * scale
    for i in range(3):
        yy = y + 8 - 8 * i
        line(
            [
                (110 - a, yy - c),
                (110 - b, yy - c),
                (110 - b, yy + c),
                (110 - a, yy + c),
            ]
        )
        line([(110 - a, yy), (110, yy)])
        line(
            [
                (110 - 1.729 * scale, yy + 0.254 * scale),
                (110 - 1.983 * scale, yy - 0.254 * scale),
            ]
        )
        dots.append((110 - 0.666 * scale, yy, 0.286 * scale))
        label(95, yy, str(i + 1), 4.2, align='Right')
    row(y, 'ODF / patch panel')
    y -= 40
    rect(0, y + 12, 80, y - 12, '#ff00ff')
    line([(80, y), (110, y)], '#ff00ff')
    label(6, y - 2, 'OLT', ink='#ff00ff')
    row(y, 'OLT')
    y -= 38
    rect(30, y + 10, 110, y - 10, '#ffff00')
    line([(0, y), (30, y)], '#ffff00')
    label(36, y - 2, 'ONT', ink='#ffff00')
    row(y, 'ONT')
    y -= 38
    for i in range(3):
        ellipse(30 + 6 * i, y, 12, 7, '#ff7f00')
    row(y, 'Cable slack')
    y -= 48
    label(0, y, 'Fiber purpose', 6.48, True)
    for purpose in PURPOSES.values():
        y -= 15
        line([(0, y), (110, y)], purpose.color, purpose.width)
        row(y, purpose.title)
    y -= 32
    label(0, y, 'Color code: ' + payload['color_code'], 6.48, True)
    for i, index in enumerate(definition['colors']):
        y -= 12
        line([(0, y), (110, y)], CAD_COLORS[index])
        label(
            140,
            y - 2.16,
            '{}. {}'.format(i + 1, translate(COLOR_NAMES[index], language)),
        )
    return lines, texts, dots


def validate_annotation(kind, payload):
    if (
        not isinstance(payload, dict)
        or type(payload.get('version')) is not int
        or payload.get('version') != 1
        or payload.get('language') not in ('en', 'sr')
    ):
        raise ValueError('Invalid drawing annotation version/language.')
    if 'source_hash' in payload and (
        not isinstance(payload['source_hash'], str)
        or len(payload['source_hash']) != 64
        or any(c not in '0123456789abcdef' for c in payload['source_hash'])
    ):
        raise ValueError('Invalid report source signature.')
    if 'created_at' in payload and (
        not isinstance(payload['created_at'], str)
        or len(payload['created_at']) > 100
        or any(ord(c) < 32 for c in payload['created_at'])
    ):
        raise ValueError('Invalid report creation time.')
    if kind == 'legend':
        from .workspace_model import DEFAULT_SETTINGS, validate_settings
        from copy import deepcopy

        probe = deepcopy(DEFAULT_SETTINGS)
        code = payload.get('definition')
        if (
            not isinstance(code, dict)
            or not isinstance(payload.get('color_code'), str)
            or not 1 <= len(payload['color_code']) <= 200
        ):
            raise ValueError('Legend needs a color definition.')
        # Validate the frozen snapshot independently of current workspace
        # settings.
        probe['color_codes'] = [dict(code, name='__legend__')]
        validate_settings(probe)
    elif kind in ('bom', 'budget', 'route', 'checks'):
        if (
            not isinstance(payload.get('title'), str)
            or not isinstance(payload.get('columns'), list)
            or not 1 <= len(payload['columns']) <= 10
            or not isinstance(payload.get('rows'), list)
            or len(payload['rows']) > 10000
            or not isinstance(payload.get('notes'), list)
            or len(payload['notes']) > 1000
        ):
            raise ValueError('Invalid report table.')
        values = [payload['title']] + payload['columns'] + payload['notes']
        for row in payload['rows']:
            if not isinstance(row, list) or len(row) != len(
                payload['columns']
            ):
                raise ValueError('Report columns and row lengths differ.')
            values.extend(row)
        if any(
            not isinstance(v, str)
            or len(v) > 4000
            or any(ord(c) < 32 for c in v)
            for v in values
        ):
            raise ValueError(
                'Report text must contain single-line strings (up to 4000 '
                'characters).'
            )
    else:
        raise ValueError('Unknown drawing annotation kind.')
    return payload
