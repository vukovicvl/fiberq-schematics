"""FiberQ CAD 1.0.0 cable catalogue and physical tube identities (MIT).

Ported from fq-sema.lsp sections 3 and 9. See FIBERQ_CAD_LICENSE.txt.
Rows and lengths here use the designer's schematic units (10 per CAD unit).
"""

from dataclasses import dataclass
import json
import math

from .domain import fiber_rows


CABLE_VERSION = 'cad-1'
CATEGORIES = {
    'kbAerial': 'Aerial',
    'kbDuct': 'Duct',
    'kbMicro': 'Micro duct',
    'kbIndoor': 'Indoor',
}
COMMON = (
    (1, 2),
    (1, 4),
    (1, 6),
    (1, 8),
    (1, 12),
    (1, 24),
    (2, 6),
    (2, 12),
    (2, 24),
    (3, 4),
    (4, 6),
    (4, 12),
    (6, 6),
    (6, 8),
    (6, 12),
    (8, 6),
    (8, 12),
    (8, 24),
)
CATALOGUE = {
    'kbAerial': {'ADSS 03': COMMON + ((12, 12), (12, 24), (24, 12), (36, 12))},
    'kbDuct': {'TOSM 03': COMMON + ((10, 24), (12, 12), (12, 24), (24, 12))},
    'kbMicro': {
        'TO(M)SM 03': COMMON + ((10, 24), (12, 12), (12, 24), (24, 12))
    },
    'kbIndoor': {
        'BUTTERFLY H': ((1, 1), (1, 2)),
        'TOMM 03': (
            (1, 2),
            (2, 2),
            (2, 6),
            (2, 12),
            (2, 24),
            (4, 6),
            (4, 12),
            (6, 6),
            (6, 12),
            (6, 24),
            (8, 6),
            (8, 12),
        ),
        'RISER H': (
            (2, 6),
            (2, 12),
            (4, 6),
            (4, 12),
            (6, 4),
            (8, 6),
            (8, 12),
            (12, 4),
            (36, 12),
        ),
    },
}
COLOR_NAMES = (
    'Blue',
    'Orange',
    'Green',
    'Brown',
    'Gray',
    'White',
    'Red',
    'Black',
    'Yellow',
    'Violet',
    'Rose',
    'Aqua',
)
# RGB values of the source ACI 5/30/3/34/8/7/1/250/2/6/221/4,
# checked with GDAL's DXF reader. ACI 7 follows the paper/model palette.
CAD_COLORS = (
    '#0000ff',
    '#ff7f00',
    '#00ff00',
    '#7f3f00',
    '#7f7f7f',
    '#ffffff',
    '#ff0000',
    '#545454',
    '#ffff00',
    '#ff00ff',
    '#ff7fdf',
    '#00ffff',
)
COLOR_CODES = {
    'TIA-598': tuple(range(12)),
    'IEC 60304': (6, 2, 0, 8, 5, 4, 3, 9, 11, 7, 1, 10),
}
DEFAULT_LENGTH = 400.0
MIN_STRAIGHT = 120.0
MIN_SEGMENT = 10.0
LEGACY_CABLE = dict(
    cable_version='',
    category='',
    model='',
    catalog_model='',
    tube_count=0,
    color_code='',
    fiber_type='',
    status='',
    row_positions='[]',
    catalogue_data='',
)
LEGACY_FIBER = dict(
    tube_no=0, tube_fiber=0, purpose='', fiber_type='', description=''
)


def color(number, code='TIA-598', definition=None):
    sequence = definition['colors'] if definition else COLOR_CODES[code]
    index = sequence[(number - 1) % len(sequence)]
    group = (number - 1) // len(sequence)
    suffixes = definition['suffixes'] if definition else ['-p', '-2p', '-3p']
    suffix = (
        ''
        if not group
        else suffixes[group - 1]
        if group <= len(suffixes)
        else '-{}p'.format(group)
    )
    return CAD_COLORS[index], COLOR_NAMES[index] + suffix


def cable_rows(count, group_size, inherited=()):
    natural = fiber_rows(count, group_size)
    result = []
    for index in range(count):
        result.append(
            float(inherited[index])
            if index < len(inherited)
            else result[-1] - 8 - (8 if index % group_size == 0 else 0)
            if result
            else 0.0
        )
    if (
        not all(math.isfinite(v) for v in result)
        or abs(result[0]) > 1e-6
        or any(a - b < 1e-6 for a, b in zip(result, result[1:]))
    ):
        raise ValueError(
            'Cable rows must start at zero and descend in fiber order.'
        )
    return result if inherited else natural


@dataclass(frozen=True)
class CableProperties:
    category: str = 'kbAerial'
    model: str = 'ADSS 03'
    catalog_model: str = 'ADSS 03'
    tubes: int = 1
    fibers_per_tube: int = 12
    color_code: str = 'TIA-598'
    fiber_type: str = 'SM'
    status: str = 'designed'
    row_positions: tuple = ()
    definition: str = ''

    def definition_data(self):
        if not self.definition:
            return {}
        try:
            value = json.loads(self.definition)
            if set(value) != {'capacities', 'colors', 'suffixes'}:
                raise ValueError('Invalid fields.')
            from .workspace_model import DEFAULT_SETTINGS, validate_settings
            from copy import deepcopy

            probe = deepcopy(DEFAULT_SETTINGS)
            probe['catalogues'] = [
                dict(
                    category=self.category or 'kbIndoor',
                    name='__validation__',
                    capacities=value['capacities'],
                )
            ]
            probe['color_codes'] = [
                dict(
                    name='__validation__',
                    colors=value['colors'],
                    suffixes=value['suffixes'],
                )
            ]
            validate_settings(probe)
            return value
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(
                'Invalid saved cable catalogue/color definition.'
            ) from error

    def color(self, number):
        return color(number, self.color_code, self.definition_data() or None)

    def color_caption(self, number, language='en'):
        from .i18n import translate

        name = self.color(number)[1]
        base = next(value for value in COLOR_NAMES if name.startswith(value))
        return translate(base, language) + name[len(base):]

    @property
    def count(self):
        return self.tubes * self.fibers_per_tube

    @property
    def description(self):
        return (
            '{} ({}x{})'.format(self.model, self.tubes, self.fibers_per_tube)
            if self.model
            else '{}x{}'.format(self.tubes, self.fibers_per_tube)
        )

    @property
    def rows(self):
        return cable_rows(self.count, self.fibers_per_tube, self.row_positions)

    def validate(self):
        definition = self.definition_data()
        manual = self.category == '' and self.catalog_model == ''
        if not manual and (
            self.category not in CATALOGUE
            or (
                not definition
                and self.catalog_model not in CATALOGUE[self.category]
            )
        ):
            raise ValueError('Choose a cable category and catalogue model.')
        capacities = (
            definition['capacities']
            if definition
            else ((self.tubes, self.fibers_per_tube),)
            if manual
            else CATALOGUE[self.category][self.catalog_model]
        )
        if (
            type(self.tubes) is not int
            or type(self.fibers_per_tube) is not int
            or not 1 <= self.count <= 576
            or min(self.tubes, self.fibers_per_tube) < 1
        ):
            raise ValueError(
                'Cable capacity must contain 1–576 fibers in positive tube '
                'groups.'
            )
        if (
            type(self.tubes) is not int
            or type(self.fibers_per_tube) is not int
            or [self.tubes, self.fibers_per_tube]
            not in [list(p) for p in capacities]
        ):
            raise ValueError(
                'Choose a tube × fiber capacity offered by this model.'
            )
        if (
            (not manual and not self.model.strip())
            or len(self.model) > 200
            or any(ord(c) < 32 for c in self.model)
        ):
            raise ValueError(
                'Cable model needs a nonempty name without control '
                'characters (up to 200 characters).'
            )
        if (
            (not definition and self.color_code not in COLOR_CODES)
            or self.fiber_type not in ('SM', 'MM')
            or self.status not in ('designed', 'as_built')
        ):
            raise ValueError(
                'Invalid cable color code, fiber type or drawing status.'
            )
        if len(self.row_positions) > self.count:
            raise ValueError('Too many inherited cable rows.')
        self.rows
        return self

    def attributes(self):
        self.validate()
        return dict(
            cable_version=CABLE_VERSION,
            category=self.category,
            model=self.model,
            catalog_model=self.catalog_model,
            tube_count=self.tubes,
            color_code=self.color_code,
            fiber_type=self.fiber_type,
            status=self.status,
            row_positions=json.dumps(self.rows),
            catalogue_data=self.definition,
        )


def properties_from_record(cable):
    return CableProperties(
        cable['category'],
        cable['model'],
        cable['catalog_model'],
        int(cable['tube_count']),
        int(cable['group_size']),
        cable['color_code'],
        cable['fiber_type'],
        cable['status'],
        tuple(json.loads(cable['row_positions'])),
        cable['catalogue_data'] or '',
    ).validate()


def rows_from_record(cable):
    return (
        properties_from_record(cable).rows
        if cable['cable_version'] == CABLE_VERSION
        else fiber_rows(int(cable['count']), int(cable['group_size']))
    )
