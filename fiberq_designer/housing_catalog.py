"""Housing properties and catalogue rules, independent of Qt and QGIS.

Catalogue and rack rules adapted from Vladimir Vukovic's FiberQ CAD 1.0.0.
See FIBERQ_CAD_LICENSE.txt for the MIT notice.
"""

from dataclasses import dataclass
import math
import re


CONNECTORS = (
    'SC/APC',
    'SC/UPC',
    'LC/APC',
    'LC/UPC',
    'LSH/APC',
    'FC/PC',
    'ST/PC',
)
HOUSINGS = {
    'ODF': (
        'ODF module',
        'OR ',
        (12, 24, 48, 72, 96, 144, 288, 576),
        ('rack 19"',),
    ),
    'PATCH_PANEL': (
        'Patch panel',
        'PP ',
        (12, 24, 48, 72, 96, 144),
        ('rack 19"',),
    ),
    'FDH': (
        'FDH cabinet',
        'FDH ',
        (12, 24, 48, 72, 96),
        ('indoor', 'outdoor', 'free-standing'),
    ),
    'MDU': (
        'MDU cabinet',
        'MDU ',
        (12, 24, 48, 72, 96, 144),
        ('indoor', 'free-standing'),
    ),
    'FAT': (
        'FAT box',
        'FAT ',
        (1, 2, 4, 6, 8, 12, 16, 24),
        ('wall', 'pole', 'indoor'),
    ),
    'OTB': (
        'OTB box',
        'OTB ',
        (1, 2, 4, 6, 8, 12, 16, 24),
        ('wall', 'pole', 'indoor'),
    ),
}
LAYOUT_VERSION = 'fiberq_odf_1'
CAD_SCALE = 10.0
RACK_HOUSINGS = ('ODF', 'PATCH_PANEL')
CABINET_HOUSINGS = ('FDH', 'MDU')
BOX_HOUSINGS = ('FAT', 'OTB')
SPLITTER_HOUSINGS = CABINET_HOUSINGS + BOX_HOUSINGS
SUPPORTED_HOUSINGS = RACK_HOUSINGS + SPLITTER_HOUSINGS
CABINET_LAYOUT_VERSION = 'fiberq_cabinet_1'
BOX_LAYOUT_VERSION = 'fiberq_box_1'
SPLITTER_LOSSES = {2: 3.6, 4: 7.3, 8: 10.5, 16: 13.7, 32: 17.0, 64: 20.5}


def housing_layout_version(kind):
    return (
        BOX_LAYOUT_VERSION
        if kind in BOX_HOUSINGS
        else CABINET_LAYOUT_VERSION
        if kind in CABINET_HOUSINGS
        else LAYOUT_VERSION
    )


def housing_splitter_available(kind, capacity):
    return kind in CABINET_HOUSINGS or kind in BOX_HOUSINGS and capacity >= 12


@dataclass(frozen=True)
class OdfProperties:
    capacity: int = 24
    model: str = ''
    connector: str = 'SC/APC'
    mounting: str = 'rack 19"'
    rack_module: str = ''
    requested_used: int = None
    orientation: int = 0
    status: str = 'designed'
    modules: int = 1
    mirrored: bool = False
    row_positions: tuple = ()
    kind: str = 'ODF'
    address: str = ''
    splitter_ratio: int = 0
    splitter_feed: int = 0
    layout_state: str = ''

    @property
    def total_capacity(self):
        return self.capacity * self.modules

    def validate(self):
        if self.kind not in SUPPORTED_HOUSINGS:
            raise ValueError('Choose a supported housing family.')
        if (
            type(self.capacity) is not int
            or self.capacity not in HOUSINGS[self.kind][2]
        ):
            raise ValueError(
                'Choose a catalogue capacity for '
                + HOUSINGS[self.kind][0]
                + '.'
            )
        if type(self.modules) is not int or not 1 <= self.modules <= 24:
            raise ValueError('A housing must have between 1 and 24 modules.')
        if (
            self.connector not in CONNECTORS
            or self.mounting not in HOUSINGS[self.kind][3]
        ):
            raise ValueError('Invalid housing connector or mounting.')
        if self.kind in SPLITTER_HOUSINGS:
            if self.modules != 1 or self.rack_module:
                raise ValueError(
                    'Cabinets and boxes have one housing and no rack/module '
                    'designation.'
                )
            if type(
                self.splitter_ratio
            ) is not int or self.splitter_ratio not in (0, *SPLITTER_LOSSES):
                raise ValueError('Choose a supported splitter ratio.')
            if type(self.splitter_feed) is not int:
                raise ValueError('Invalid splitter feed connector.')
            if not self.splitter_ratio and self.splitter_feed:
                raise ValueError(
                    'A feed connector requires a contained splitter.'
                )
            if self.splitter_ratio:
                if not housing_splitter_available(self.kind, self.capacity):
                    raise ValueError(
                        'A FAT/OTB box needs a capacity of at least 12 for a '
                        'contained splitter.'
                    )
                feed = self.splitter_feed or self.capacity
                used = (
                    self.requested_used
                    if self.requested_used is not None
                    else 1
                )
                if (
                    not 1 <= used <= feed - self.splitter_ratio
                    or not self.splitter_ratio < feed <= self.capacity
                ):
                    raise ValueError(
                        'The housing needs separate connectors for the feed '
                        'and every splitter output.'
                    )
                if self.kind in BOX_HOUSINGS and used != 1:
                    raise ValueError(
                        'A FAT/OTB box with a splitter uses one incoming '
                        'feed connector.'
                    )
        elif self.address or self.splitter_ratio or self.splitter_feed:
            raise ValueError(
                'Address and contained splitters are available for cabinets '
                'and boxes.'
            )
        if self.requested_used is not None and (
            type(self.requested_used) is not int
            or not 0 <= self.requested_used <= self.total_capacity
        ):
            raise ValueError(
                'Used must be blank or between 0 and the total connector '
                'capacity.'
            )
        if (
            not isinstance(self.orientation, (int, float))
            or not math.isfinite(self.orientation)
            or not 0 <= self.orientation < 360
            or self.status not in ('designed', 'as_built')
        ):
            raise ValueError('Invalid housing orientation or status.')
        if not isinstance(self.mirrored, bool):
            raise ValueError('Invalid housing mirror state.')
        if self.row_positions and (
            len(self.row_positions) > 576
            or abs(self.row_positions[0]) > 1e-6
            or any(not math.isfinite(y) for y in self.row_positions)
            or any(
                a <= b
                for a, b in zip(self.row_positions, self.row_positions[1:])
            )
        ):
            raise ValueError(
                'Cable rows must start at zero and descend without overlaps.'
            )
        for value in (self.model, self.rack_module, self.address):
            if (
                not isinstance(value, str)
                or '\n' in value
                or '\r' in value
                or len(value) > 200
            ):
                raise ValueError(
                    'Model, rack/module and address must be a single line of '
                    'at most 200 characters.'
                )
        from .housing_multi import read_layout_state

        read_layout_state(self.layout_state, self.capacity, self.modules)
        return self


def module_labels(label, count):
    """fq:odflms/fq:lmmod: preserve an entered starting module number."""
    match = re.search(r'[Mm](\d+)$', label)
    if not label:
        return (
            ['M' + str(i) for i in range(1, count + 1)] if count > 1 else ['']
        )
    if match:
        return [
            label[: match.start(1)] + str(int(match.group(1)) + i)
            for i in range(count)
        ]
    return (
        [label + ', M' + str(i) for i in range(1, count + 1)]
        if count > 1
        else [label]
    )


HousingProperties = (
    OdfProperties  # Existing ODF callers keep their public API.
)


def housing_name(label, kind='ODF', existing=(), language='en'):
    if kind not in SUPPORTED_HOUSINGS:
        raise ValueError('Choose a supported housing family.')
    if (
        not isinstance(label, str)
        or '\n' in label
        or '\r' in label
        or '\x00' in label
        or len(label) > 200
    ):
        raise ValueError(
            'Label must be a single line of at most 200 characters.'
        )
    label = label.strip()
    prefix = (
        {'FDH': 'ODO ', 'FAT': 'ZOK '}.get(kind, HOUSINGS[kind][1])
        if language == 'sr'
        else HOUSINGS[kind][1]
    )
    if label:
        alternatives = {
            prefix,
            HOUSINGS[kind][1],
            {'FDH': 'ODO ', 'FAT': 'ZOK '}.get(kind, prefix),
        }
        return (
            label
            if any(label.upper().startswith(p) for p in alternatives)
            else prefix + label
        )
    names = {name.casefold() for name in existing}
    index = 1
    while (prefix + str(index)).casefold() in names:
        index += 1
    return prefix + str(index)


def odf_name(label, existing=()):
    return housing_name(label, 'ODF', existing)


def odf_description(label, properties, language='en'):
    from .i18n import translate, message

    name = housing_name(label or '<auto>', properties.kind, language=language)
    return '{}{}   {}x{}   {}{}'.format(
        name,
        '   ' + properties.model if properties.model else '',
        properties.capacity,
        properties.connector,
        translate(properties.mounting, language),
        message(
            '   {} modules, {} in total',
            properties.modules,
            properties.total_capacity,
            language=language,
        )
        if properties.modules > 1
        else '',
    ) + (
        message(
            '   with splitter 1:{}',
            properties.splitter_ratio,
            language=language,
        )
        if properties.splitter_ratio
        else ''
    )
