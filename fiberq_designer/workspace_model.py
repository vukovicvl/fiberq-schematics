"""Validated workspace settings and source optical-budget defaults."""

from copy import deepcopy
import json
import math

from .cable_catalog import CATALOGUE, COLOR_CODES


ATTENUATION = {
    'SM': {1310: 0.35, 1490: 0.24, 1550: 0.22},
    'MM': {850: 3.0, 1300: 1.0},
}
DEFAULT_SETTINGS = dict(
    language='en',
    snap=True,
    snap_pixels=10,
    color_code='TIA-598',
    splice_db=0.1,
    connector_db=0.3,
    margin_db=3.0,
    attenuation={
        k: {str(w): a for w, a in v.items()} for k, v in ATTENUATION.items()
    },
    catalogues=[],
    color_codes=[],
)


def single_line(value, maximum=200):
    return (
        isinstance(value, str)
        and 0 < len(value.strip()) <= maximum
        and not any(ord(c) < 32 for c in value)
    )


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) != set(
        DEFAULT_SETTINGS
    ):
        raise ValueError('Invalid workspace settings fields.')
    if (
        settings['language'] not in ('en', 'sr')
        or type(settings['snap']) is not bool
        or type(settings['snap_pixels']) is not int
        or not 3 <= settings['snap_pixels'] <= 40
    ):
        raise ValueError(
            'Choose English/Serbian and a snap tolerance between 3 and 40 '
            'pixels.'
        )
    for key in ('splice_db', 'connector_db', 'margin_db'):
        if (
            type(settings[key]) not in (int, float)
            or not math.isfinite(settings[key])
            or not 0 <= settings[key] <= 100
        ):
            raise ValueError(
                'Optical losses and margin must be finite and nonnegative '
                '(maximum 100 dB).'
            )
    attenuation = settings['attenuation']
    if not isinstance(attenuation, dict) or set(attenuation) != set(
        ATTENUATION
    ):
        raise ValueError('Attenuation needs SM and MM tables.')
    for kind, table in ATTENUATION.items():
        if (
            not isinstance(attenuation[kind], dict)
            or set(attenuation[kind]) != {str(w) for w in table}
            or any(
                type(v) not in (int, float)
                or not math.isfinite(v)
                or not 0 <= v <= 100
                for v in attenuation[kind].values()
            )
        ):
            raise ValueError('Invalid wavelength/attenuation table.')
    if (
        not isinstance(settings['catalogues'], list)
        or len(settings['catalogues']) > 100
    ):
        raise ValueError('At most 100 custom cable models are supported.')
    seen = set()
    for model in settings['catalogues']:
        if not isinstance(model, dict) or set(model) != {
            'category',
            'name',
            'capacities',
        }:
            raise ValueError('Invalid cable catalogue entry.')
        if not isinstance(model['category'], str) or not single_line(
            model['name']
        ):
            raise ValueError('Cable category/model must be nonempty text.')
        key = model['category'], model['name']
        if (
            model['category'] not in CATALOGUE
            or not single_line(model['name'])
            or key in seen
            or model['name'] in CATALOGUE[model['category']]
        ):
            raise ValueError(
                'Custom cable models need unique names separate from the '
                'delivered models.'
            )
        seen.add(key)
        capacities = model['capacities']
        if (
            not isinstance(capacities, list)
            or not capacities
            or len(capacities) > 100
        ):
            raise ValueError('Enter one or more tube × fiber capacities.')
        for pair in capacities:
            if (
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or any(type(n) is not int or n < 1 for n in pair)
                or pair[0] * pair[1] > 576
            ):
                raise ValueError(
                    'Capacity pairs must be positive integers, up to 576 '
                    'total fibers.'
                )
    codes = {'TIA-598', 'IEC 60304'}
    if (
        not isinstance(settings['color_codes'], list)
        or len(settings['color_codes']) > 100
    ):
        raise ValueError('At most 100 custom color codes are supported.')
    for code in settings['color_codes']:
        if (
            not isinstance(code, dict)
            or set(code) != {'name', 'colors', 'suffixes'}
            or not single_line(code['name'])
            or code['name'] in codes
        ):
            raise ValueError(
                'Color codes need unique names separate from the delivered '
                'standards.'
            )
        codes.add(code['name'])
        if (
            not isinstance(code['colors'], list)
            or not 1 <= len(code['colors']) <= 48
            or any(
                type(n) is not int or not 0 <= n < 12 for n in code['colors']
            )
        ):
            raise ValueError(
                'A color sequence needs 1–48 source color numbers (1–12 in '
                'the editor).'
            )
        if (
            not isinstance(code['suffixes'], list)
            or not 1 <= len(code['suffixes']) <= 12
            or any(not single_line(s, 20) for s in code['suffixes'])
        ):
            raise ValueError(
                'Enter 1–12 nonempty group suffixes, up to 20 characters each.'
            )
    if (
        not isinstance(settings['color_code'], str)
        or settings['color_code'] not in codes
    ):
        raise ValueError('The default color code no longer exists.')
    return settings


def read_settings(records=()):
    records = list(records)
    if not records:
        return deepcopy(DEFAULT_SETTINGS)
    if len(records) != 1 or records[0]['setting_id'] != 'workspace':
        raise ValueError('A workspace must have at most one settings record.')
    try:
        value = json.loads(records[0]['payload'])
    except (ValueError, TypeError) as error:
        raise ValueError('Invalid workspace settings JSON.') from error
    return validate_settings(value)


def color_definitions(settings):
    result = {
        name: dict(name=name, colors=list(seq), suffixes=['-p', '-2p', '-3p'])
        for name, seq in COLOR_CODES.items()
    }
    result.update({c['name']: deepcopy(c) for c in settings['color_codes']})
    return result


def catalogue_definitions(settings):
    result = {
        category: {name: list(pairs) for name, pairs in models.items()}
        for category, models in CATALOGUE.items()
    }
    for model in settings['catalogues']:
        result[model['category']][model['name']] = model['capacities']
    return result


def budget_calculation(
    km,
    splices,
    connectors,
    splitter_losses=(),
    fiber_type='SM',
    wavelength=1490,
    settings=None,
):
    settings = validate_settings(settings or deepcopy(DEFAULT_SETTINGS))
    if (
        type(km) not in (int, float)
        or not math.isfinite(km)
        or not 0 <= km <= 100000
        or type(splices) is not int
        or type(connectors) is not int
        or min(splices, connectors) < 0
        or max(splices, connectors) > 1000000
    ):
        raise ValueError(
            'Enter nonnegative real kilometres and splice/connector counts.'
        )
    if (
        fiber_type not in ATTENUATION
        or wavelength not in ATTENUATION[fiber_type]
    ):
        raise ValueError(
            'Choose a wavelength supported by the selected SM/MM fiber type.'
        )
    losses = list(splitter_losses)
    if any(
        type(v) not in (int, float)
        or not math.isfinite(v)
        or not 0 <= v <= 100
        for v in losses
    ):
        raise ValueError('Invalid splitter loss.')
    attenuation = settings['attenuation'][fiber_type][str(wavelength)]
    components = dict(
        fiber=km * attenuation,
        splices=splices * settings['splice_db'],
        connectors=connectors * settings['connector_db'],
        splitters=sum(losses),
        margin=settings['margin_db'],
    )
    total = sum(components.values())
    return dict(
        km=km,
        splices=splices,
        connectors=connectors,
        fiber_type=fiber_type,
        wavelength=wavelength,
        attenuation=attenuation,
        splice_db=settings['splice_db'],
        connector_db=settings['connector_db'],
        splitter_losses=losses,
        components=components,
        total_db=total,
        b_plus=total <= 28,
        c_plus=total <= 32,
    )
