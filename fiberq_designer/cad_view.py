"""Fiber-by-fiber CAD drawing derived from persisted network identities.

Route vertices, port offsets and logical connections remain unchanged.
All visible features carry the IDs of their saved network records.
"""

import math

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis,
    QgsFeature,
    QgsGeometry,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProperty,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)

from .compat import enum_compat
from .domain import FIBER_COLORS, fiber_rows
from .geometry import (
    housing_font_metrics,
    odf_drawing,
    odf_frame,
    odf_point,
    odf_properties,
    parallel_path,
    cable_fiber_path,
    pt,
)
from .housing_catalog import CAD_SCALE
from .cable_catalog import CABLE_VERSION, properties_from_record
from .operation_view import prepare_routes, apply_operations, draw_active
from .geometry import closure_drawing, closure_frame
from .i18n import translate


COLOR_NAMES = (
    'Blue',
    'Orange',
    'Green',
    'Brown',
    'Slate',
    'White',
    'Red',
    'Black',
    'Yellow',
    'Violet',
    'Rose',
    'Aqua',
)
SPACING = 8.0
MAP_UNITS = Qgis.RenderUnit.MapUnits


def _layer(name, geometry, fields):
    uri = geometry + '?crs=EPSG:3857' + ''.join('&field=' + f for f in fields)
    layer = QgsVectorLayer(uri, 'FQSD | ' + name, 'memory')
    layer.setReadOnly(True)
    return layer


def _point_geometry(point):
    return QgsGeometry.fromPointXY(point)


def _line(points):
    clean = []
    for p in points:
        if (
            not clean
            or math.hypot(p.x() - clean[-1].x(), p.y() - clean[-1].y()) > 1e-7
        ):
            clean.append(p)
    return (
        QgsGeometry.fromPolylineXY(clean) if len(clean) > 1 else QgsGeometry()
    )


def _rectangle(center, width, height):
    x, y = center.x(), center.y()
    return QgsGeometry.fromPolygonXY(
        [
            [
                pt(x - width / 2, y - height / 2),
                pt(x + width / 2, y - height / 2),
                pt(x + width / 2, y + height / 2),
                pt(x - width / 2, y + height / 2),
                pt(x - width / 2, y - height / 2),
            ]
        ]
    )


def _parallel(points, offset):
    """Offset a polyline with bounded miters, including reverse/vertical
    routes."""
    normals = []
    for a, b in zip(points, points[1:]):
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy)
        normals.append(
            (-dy / length, dx / length) if length > 1e-8 else (0, 1)
        )
    result = []
    for i, point in enumerate(points):
        if i == 0:
            nx, ny = normals[0]
        elif i == len(points) - 1:
            nx, ny = normals[-1]
        else:
            a, b = normals[i - 1], normals[i]
            denominator = 1 + a[0] * b[0] + a[1] * b[1]
            if denominator > 0.1:
                nx, ny = (
                    (a[0] + b[0]) / denominator,
                    (a[1] + b[1]) / denominator,
                )
                scale = max(1, math.hypot(nx, ny) / 3)
                nx, ny = nx / scale, ny / scale
            else:
                nx, ny = b
        result.append(pt(point.x() + offset * nx, point.y() + offset * ny))
    return result


def _pen(layer, color='#333333', width='0.18', field=False, active=False):
    symbol = QgsLineSymbol.createSimple(
        {
            'line_color': color,
            'line_width': width,
            'joinstyle': 'miter',
            'capstyle': 'flat',
        }
    )
    if field:
        symbol.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.Property.StrokeColor,
            QgsProperty.fromExpression(
                'CASE WHEN "active" = 1 THEN \'#ef2020\' ELSE "color" END'
            )
            if active
            else QgsProperty.fromField('color'),
        )
    if active:
        symbol.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.Property.StrokeWidth,
            QgsProperty.fromExpression(
                'CASE WHEN "active" = 1 THEN 0.65 ELSE {} END'.format(width)
            ),
        )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _labels(layer, dark=False):
    symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'size': '0.1'})
    symbol.setOpacity(0)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.setCustomProperty('fqsd/labels_only', True)
    settings = QgsPalLayerSettings()
    settings.fieldName = 'text'
    settings.placement = getattr(
        Qgis, 'LabelPlacement', QgsPalLayerSettings
    ).OverPoint
    settings.quadOffset = Qgis.LabelQuadrantPosition.AboveRight
    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setSizeUnit(MAP_UNITS)
    fmt.setColor(QColor('#222222'))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.15)
    buffer.setColor(QColor('#212930' if dark else '#ffffff'))
    fmt.setBuffer(buffer)
    settings.setFormat(fmt)
    properties = settings.dataDefinedProperties()
    for property_name, field in [
        ('Color', 'color'),
        ('Size', 'size'),
        ('LabelRotation', 'angle'),
        ('Bold', 'bold'),
    ]:
        properties.setProperty(
            enum_compat(QgsPalLayerSettings, property_name, 'Property'),
            QgsProperty.fromField(field),
        )
    if dark:
        properties.setProperty(
            enum_compat(QgsPalLayerSettings, 'Color', 'Property'),
            QgsProperty.fromExpression(
                "CASE WHEN \"color\" IN ('#222222', '#333333') "
                'THEN \'#eeeeee\' ELSE "color" END'
            ),
        )
    properties.setProperty(
        enum_compat(QgsPalLayerSettings, 'AlwaysShow', 'Property'),
        QgsProperty.fromValue(True),
    )
    settings.setDataDefinedProperties(properties)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def _housing_labels(layer, ink='#222222'):
    """Pinned CAD text: explicit insertion point and alignment, no label
    buffer."""
    symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'size': '0.1'})
    symbol.setOpacity(0)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.setCustomProperty('fqsd/labels_only', True)
    settings = QgsPalLayerSettings()
    settings.fieldName = 'text'
    settings.placement = getattr(
        Qgis, 'LabelPlacement', QgsPalLayerSettings
    ).OverPoint
    fmt = QgsTextFormat()
    fmt.setFont(QFont('Arial'))
    fmt.setSizeUnit(MAP_UNITS)
    fmt.setColor(QColor(ink))
    settings.setFormat(fmt)
    properties = settings.dataDefinedProperties()
    for property_name, field in [
        ('Size', 'size'),
        ('Family', 'family'),
        ('Bold', 'bold'),
        ('LabelRotation', 'angle'),
        ('Hali', 'align'),
    ]:
        properties.setProperty(
            enum_compat(QgsPalLayerSettings, property_name, 'Property'),
            QgsProperty.fromField(field),
        )
    properties.setProperty(
        enum_compat(QgsPalLayerSettings, 'Color', 'Property'),
        QgsProperty.fromExpression(
            "CASE WHEN \"color\" = '#ffffff' THEN '{}' "
            "WHEN \"color\" = '#545454' THEN '{}' "
            'WHEN "color" = \'#ffff00\' THEN \'{}\' ELSE coalesce("color", '
            "'{}') END".format(
                '#eeeeee' if ink == '#eeeeee' else '#777777',
                '#aaaaaa' if ink == '#eeeeee' else '#545454',
                '#ffff00' if ink == '#eeeeee' else '#947e00',
                ink,
            )
        ),
    )
    for name, expression in [
        ('PositionX', 'x($geometry)'),
        ('PositionY', 'y($geometry)'),
    ]:
        properties.setProperty(
            enum_compat(QgsPalLayerSettings, name, 'Property'),
            QgsProperty.fromExpression(expression),
        )
    properties.setProperty(
        enum_compat(QgsPalLayerSettings, 'Vali', 'Property'),
        QgsProperty.fromValue('Half'),
    )
    properties.setProperty(
        enum_compat(QgsPalLayerSettings, 'AlwaysShow', 'Property'),
        QgsProperty.fromValue(True),
    )
    settings.setDataDefinedProperties(properties)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


class CadView:
    def __init__(self):
        self.frames = _layer(
            'equipment outlines',
            'Polygon',
            ['obj_id:string', 'kind:string', 'color:string'],
        )
        self.ports = _layer(
            'equipment ports',
            'Point',
            ['port_id:string', 'obj_id:string', 'label:string', 'cad:integer'],
        )
        self.fibers = _layer(
            'fiber lines',
            'LineString',
            [
                'fiber_id:string',
                'cable_id:string',
                'fiber_no:integer',
                'color:string',
                'active:integer',
                'cad:integer',
                'span_id:string',
                'a_node:string',
                'b_node:string',
                'purpose:string',
                'stroke:string',
                'width:double',
            ],
        )
        self.links = _layer(
            'fiber connections',
            'LineString',
            [
                'link_id:string',
                'color:string',
                'width:double',
                'active:integer',
            ],
        )
        self.splices = _layer(
            'splice points', 'Point', ['link_id:string', 'color:string']
        )
        self.optical_lines = _layer(
            'optical joints',
            'LineString',
            [
                'joint_id:string',
                'color:string',
                'width:double',
                'active:integer',
            ],
        )
        self.optical_marks = _layer(
            'optical splice points',
            'Point',
            ['record_key:string', 'record_id:string', 'color:string'],
        )
        self.annotation_lines = _layer(
            'report and legend symbols',
            'LineString',
            ['annotation_id:string', 'color:string', 'width:double'],
        )
        self.details = _layer(
            'tube and housing details', 'LineString', ['color:string']
        )
        self.annotations = _layer(
            'fiber and equipment labels',
            'Point',
            [
                'text:string',
                'color:string',
                'size:double',
                'angle:double',
                'bold:integer',
            ],
        )
        self.housing_lines = _layer(
            'housing connector symbols',
            'LineString',
            ['obj_id:string', 'port_id:string'],
        )
        self.housing_dots = _layer(
            'housing connector dots',
            'Polygon',
            ['obj_id:string', 'port_id:string'],
        )
        self.housing_labels = _layer(
            'housing labels',
            'Point',
            [
                'obj_id:string',
                'text:string',
                'size:double',
                'family:string',
                'angle:double',
                'bold:integer',
                'align:string',
                'color:string',
            ],
        )
        from qgis.core import QgsFillSymbol

        outline = QgsFillSymbol.createSimple(
            {
                'style': 'no',
                'outline_color': '#333333',
                'outline_width': '0.23',
            }
        )
        self.frames.setRenderer(QgsSingleSymbolRenderer(outline))
        pins = QgsMarkerSymbol.createSimple(
            {
                'name': 'square',
                'size': '0.9',
                'color': '#252525',
                'outline_color': '#252525',
                'outline_width': '0.1',
            }
        )
        pins.setSizeUnit(MAP_UNITS)
        pins.setSize(1.6)
        from qgis.core import QgsRuleBasedRenderer

        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(
            QgsRuleBasedRenderer.Rule(pins, filterExp='"cad" = 0')
        )
        self.ports.setRenderer(QgsRuleBasedRenderer(root))
        dots = QgsMarkerSymbol.createSimple(
            {
                'name': 'circle',
                'size': '1.4',
                'color': '#111111',
                'outline_width': '0',
            }
        )
        dots.setSizeUnit(MAP_UNITS)
        dots.setSize(3)
        self.splices.setRenderer(QgsSingleSymbolRenderer(dots))
        optical_dots = dots.clone()
        optical_dots.setSize(4.4)
        self.optical_marks.setRenderer(QgsSingleSymbolRenderer(optical_dots))
        _pen(self.optical_lines, field=True, width='0.13', active=True)
        _pen(self.details, field=True, width='0.13')
        _pen(self.links, field=True, width='0.2', active=True)
        # A dark casing keeps white and yellow fibers visible on a white
        # export.
        symbol = QgsLineSymbol.createSimple(
            {
                'line_color': '#777777',
                'line_width': '0.32',
            }
        )
        symbol.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.Property.LayerEnabled,
            QgsProperty.fromExpression(
                'coalesce("cad",0) != 1 AND coalesce("purpose",\'\') = \'\''
            ),
        )
        colored = QgsLineSymbol.createSimple(
            {
                'line_color': '#1675cf',
                'line_width': '0.22',
            }
        ).takeSymbolLayer(0)
        colored.setDataDefinedProperty(
            QgsSymbolLayer.Property.StrokeColor,
            QgsProperty.fromExpression(
                'CASE WHEN "active" = 1 THEN \'#ef2020\' ELSE "color" END'
            ),
        )
        colored.setDataDefinedProperty(
            QgsSymbolLayer.Property.StrokeWidth,
            QgsProperty.fromExpression(
                'CASE WHEN "active" = 1 THEN 0.65 ELSE 0.22 END'
            ),
        )
        symbol.appendSymbolLayer(colored)
        self.fibers.setRenderer(QgsSingleSymbolRenderer(symbol))
        _labels(self.annotations)
        _pen(self.housing_lines, width='0.13')
        self.housing_dots.setRenderer(
            QgsSingleSymbolRenderer(
                QgsFillSymbol.createSimple(
                    {
                        'color': '#333333',
                        'outline_style': 'no',
                    }
                )
            )
        )
        _housing_labels(self.housing_labels)
        self.dark = False
        self.positions = {}
        self.hit_frames = {}
        self.batches = {}
        self.drawn_fibers = {}
        self.text_bounds = QgsRectangle()

        self.set_dark(False)

    def layers(self):
        return [
            self.housing_labels,
            self.annotations,
            self.splices,
            self.optical_marks,
            self.ports,
            self.optical_lines,
            self.links,
            self.annotation_lines,
            self.housing_lines,
            self.housing_dots,
            self.details,
            self.fibers,
            self.frames,
        ]

    def set_dark(self, enabled):
        self.dark = enabled
        self._theme(self.layers(), enabled)

    def paper_layers(self):
        source_layers = self.layers()
        layers = [layer.clone() for layer in source_layers]
        for source, layer in zip(source_layers, layers):
            layer.removeSelection()
            layer.setCustomProperty('fqsd/source_layer_id', source.id())
        self._theme(layers, False)
        return layers

    @staticmethod
    def _theme(layers, dark):
        from qgis.core import QgsFillSymbol

        ink = '#eeeeee' if dark else '#333333'
        for layer in layers:
            name = layer.name()
            if name.endswith('equipment outlines'):
                layer.setRenderer(
                    QgsSingleSymbolRenderer(
                        QgsFillSymbol.createSimple(
                            {
                                'style': 'no',
                                'outline_color': ink,
                                'outline_width': '0.23',
                            }
                        )
                    )
                )
                layer.renderer().symbol().symbolLayer(
                    0
                ).setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeColor,
                    QgsProperty.fromExpression(
                        "CASE WHEN \"color\" = '#ffff00' THEN '{}' ELSE "
                        'coalesce("color", \'{}\') END'.format(
                            '#ffff00' if dark else '#947e00', ink
                        )
                    ),
                )
            elif name.endswith('housing connector symbols'):
                _pen(layer, color=ink, width='0.13')
            elif name.endswith('housing connector dots'):
                layer.setRenderer(
                    QgsSingleSymbolRenderer(
                        QgsFillSymbol.createSimple(
                            {
                                'color': ink,
                                'outline_style': 'no',
                            }
                        )
                    )
                )
            elif name.endswith('housing labels'):
                _housing_labels(layer, ink)
            elif name.endswith('fiber and equipment labels'):
                _labels(layer, dark)
            elif name.endswith('tube and housing details'):
                _pen(layer, field=True, width='0.13')
                if dark:
                    layer.renderer().symbol().symbolLayer(
                        0
                    ).setDataDefinedProperty(
                        QgsSymbolLayer.Property.StrokeColor,
                        QgsProperty.fromExpression(
                            "CASE WHEN \"color\" = '#333333' THEN '#eeeeee' "
                            'ELSE "color" END'
                        ),
                    )
            elif name.endswith('splice points'):
                layer.renderer().symbol().setColor(QColor(ink))
                layer.renderer().symbol().symbolLayer(
                    0
                ).setDataDefinedProperty(
                    QgsSymbolLayer.Property.FillColor,
                    QgsProperty.fromExpression(
                        'CASE WHEN "color" IS NULL OR "color" = \'\' OR '
                        '"color" = \'#333333\' '
                        'THEN \'{}\' ELSE "color" END'.format(ink)
                    ),
                )
            elif name.endswith('optical joints'):
                _pen(layer, color=ink, width='0.13', field=True, active=True)
                layer.renderer().symbol().symbolLayer(
                    0
                ).setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeColor,
                    QgsProperty.fromExpression(
                        'CASE WHEN "active" = 1 THEN \'#ef2020\' WHEN "color" '
                        "= '#333333' "
                        'THEN \'{}\' ELSE "color" END'.format(ink)
                    ),
                )
                layer.renderer().symbol().symbolLayer(
                    0
                ).setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeWidth,
                    QgsProperty.fromExpression(
                        'CASE WHEN "active" = 1 THEN 0.65 ELSE '
                        'coalesce("width", 0.13) END'
                    ),
                )
            elif name.endswith('fiber connections'):
                _pen(layer, color=ink, width='0.2', field=True, active=True)
                layer.renderer().symbol().symbolLayer(
                    0
                ).setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeColor,
                    QgsProperty.fromExpression(
                        'CASE WHEN "active" = 1 THEN \'#ef2020\' WHEN "color" '
                        "= '#333333' "
                        'THEN \'{}\' ELSE "color" END'.format(ink)
                    ),
                )
                layer.renderer().symbol().symbolLayer(
                    0
                ).setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeWidth,
                    QgsProperty.fromExpression(
                        'CASE WHEN "active" = 1 THEN 0.65 ELSE '
                        'coalesce("width", 0.2) END'
                    ),
                )
            elif name.endswith('report and legend symbols'):
                _pen(layer, field=True, width='0.13')
                symbol = layer.renderer().symbol().symbolLayer(0)
                symbol.setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeWidth,
                    QgsProperty.fromField('width'),
                )
                symbol.setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeColor,
                    QgsProperty.fromExpression(
                        "CASE WHEN \"color\" = '#333333' THEN '{}' "
                        'WHEN "color" = \'#ffff00\' THEN \'{}\' ELSE "color" '
                        'END'.format(ink, '#ffff00' if dark else '#947e00')
                    ),
                )
            elif name.endswith('fiber lines'):
                layer.renderer().symbol().symbolLayer(
                    1
                ).setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeColor,
                    QgsProperty.fromExpression(
                        'CASE WHEN "active" = 1 THEN \'#ef2020\' '
                        'WHEN "stroke" '
                        '!= \'\' THEN "stroke" '
                        'WHEN "purpose" = \'nmFree\' OR "cad" = 1 THEN \'{}\' '
                        'ELSE "color" END'.format(ink)
                    ),
                )
                layer.renderer().symbol().symbolLayer(
                    1
                ).setDataDefinedProperty(
                    QgsSymbolLayer.Property.StrokeWidth,
                    QgsProperty.fromExpression(
                        'CASE WHEN "active" = 1 THEN 0.65 ELSE '
                        'coalesce("width", 0.22) END'
                    ),
                )
            elif name.endswith('equipment ports'):
                for rule in layer.renderer().rootRule().children():
                    rule.symbol().setColor(QColor(ink))
            layer.triggerRepaint()

    def _housing(self, element, housing, owned):
        identity = element['obj_id']
        properties = odf_properties(element, housing)
        language = self.drawing_languages.get(identity, 'en')
        at, orientation, mirrored = (
            element.geometry().asPoint(),
            properties.orientation,
            properties.mirrored,
        )
        layout = odf_drawing(element, housing)
        shape = odf_frame(layout, at, orientation, mirrored)
        bounds = shape.boundingBox()
        self.hit_frames[identity] = shape
        for frame in layout.module_frames:
            self._add(
                self.frames,
                QgsGeometry.fromPolygonXY(
                    [[odf_point(p, at, orientation, mirrored) for p in frame]]
                ),
                obj_id=identity,
                kind=element['kind'],
            )
        port_ids = {
            (self.module_numbers[p['module_id']] - 1) * properties.capacity
            + int(p['module_port']): p['port_id']
            for p in owned
        }
        for number, position in layout.ports.items():
            point = odf_point(position, at, orientation, mirrored)
            self.positions[port_ids[number]] = point
            if layout.visible_ports is None or number in layout.visible_ports:
                self._add(
                    self.ports,
                    _point_geometry(point),
                    obj_id=identity,
                    port_id=port_ids[number],
                    label=str((number - 1) % properties.capacity + 1),
                    cad=1,
                )
        for number, vertices in layout.lines:
            self._add(
                self.housing_lines,
                _line(
                    [odf_point(p, at, orientation, mirrored) for p in vertices]
                ),
                obj_id=identity,
                port_id=port_ids.get(number, ''),
            )
        for number, center, radius in layout.dots:
            ring = [
                odf_point(
                    (
                        center[0] + radius * math.cos(i * math.tau / 32),
                        center[1] + radius * math.sin(i * math.tau / 32),
                    ),
                    at,
                    orientation,
                    mirrored,
                )
                for i in range(33)
            ]
            self._add(
                self.housing_dots,
                QgsGeometry.fromPolygonXY([ring]),
                obj_id=identity,
                port_id=port_ids[number],
            )
        ratios = {
            bold: housing_font_metrics('', housing['font_family'], bold)[2]
            for bold in (False, True)
        }
        if layout.splitter is not None:
            self._add(
                self.details,
                _line(
                    [
                        odf_point(p, at, orientation, mirrored)
                        for p in layout.splitter.frame
                    ]
                ),
                color='#0000ff',
            )
        texts = [(text, None) for text in layout.texts] + (
            [(text, '#0000ff') for text in layout.splitter.texts]
            if layout.splitter
            else []
        )
        for text, color in texts:
            cap_ratio = ratios[text.bold]
            upright = 90 < orientation <= 270
            align = text.align
            if upright != mirrored:
                align = {'Left': 'Right', 'Right': 'Left'}.get(align, align)
            self._add(
                self.housing_labels,
                _point_geometry(
                    odf_point(text.point, at, orientation, mirrored)
                ),
                obj_id=identity,
                text=translate(text.text, language)
                if text.localizable
                else text.text,
                size=text.height * CAD_SCALE * cap_ratio,
                family=housing['font_family'],
                angle=orientation - 180 if upright else orientation,
                bold=int(text.bold),
                align=align,
                color=color,
            )
            if text.height > 0.42:
                _, width, _ = housing_font_metrics(
                    text.text, housing['font_family'], text.bold
                )
                width *= text.height
                x, y = text.point
                left = (
                    x - width
                    if text.align == 'Right'
                    else x
                    if text.align == 'Left'
                    else x - width / 2
                )
                corners = [
                    (left, y - text.height / 2),
                    (left + width, y - text.height / 2),
                    (left + width, y + text.height / 2),
                    (left, y + text.height / 2),
                ]
                shape = _line(
                    [odf_point(p, at, orientation, mirrored) for p in corners]
                )
                self.text_bounds.combineExtentWith(shape.boundingBox())
        return bounds

    def _closure(self, element, record):
        identity, at, angle = (
            element['obj_id'],
            element.geometry().asPoint(),
            float(record['angle']),
        )
        layout, shape = (
            closure_drawing(element, record),
            closure_frame(element, record),
        )
        language = self.drawing_languages.get(identity, 'en')
        if language != 'en':
            from .closure_layout import closure_layout
            from .geometry import closure_properties

            layout = closure_layout(
                closure_properties(record),
                element['name'],
                float(record['width']),
                float(record['top']),
                float(record['bottom']),
                language,
            )
        self.hit_frames[identity] = shape
        self._add(self.frames, shape, obj_id=identity, kind='CLOSURE')
        for text in layout.texts:
            family, width, cap_ratio = housing_font_metrics(
                text.text, record['font_family'], text.bold
            )
            point = odf_point(text.point, at, angle)
            self._add(
                self.housing_labels,
                _point_geometry(point),
                obj_id=identity,
                text=text.text,
                size=text.height * CAD_SCALE * cap_ratio,
                family=family,
                angle=angle - 180 if 90 < angle <= 270 else angle,
                bold=int(text.bold),
                align='Center',
                color='#ff0000' if text.bold else None,
            )
            width *= text.height
            x, y = text.point
            corners = [
                (x - width / 2, y - text.height / 2),
                (x + width / 2, y - text.height / 2),
                (x + width / 2, y + text.height / 2),
                (x - width / 2, y + text.height / 2),
            ]
            bounds = _line(
                [odf_point(p, at, angle) for p in corners]
            ).boundingBox()
            self.text_bounds.combineExtentWith(bounds)
        return shape.boundingBox()

    def _add(self, layer, geometry, **attrs):
        if geometry.isNull() or geometry.isEmpty():
            return
        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        for key, value in attrs.items():
            feature[key] = value
        self.batches[layer.id()].append(feature)

    def _text(self, point, text, color='#222222', size=7, angle=0, bold=False):
        self._add(
            self.annotations,
            _point_geometry(point),
            text=text,
            color=color,
            size=size,
            angle=angle,
            bold=int(bold),
        )

    def rebuild(self, repository, operations=True):
        self.batches = {layer.id(): [] for layer in self.layers()}
        self.positions = {}
        self.hit_frames = {}
        self.drawn_fibers = {}
        self.text_bounds = QgsRectangle()
        elements = {e['obj_id']: e for e in repository.features('elements')}
        ports = repository.features('ports')
        housings = {h['obj_id']: h for h in repository.features('housings')}
        closures = {c['obj_id']: c for c in repository.features('closures')}
        self.outlets = {
            o['cable_id']: o for o in repository.features('outlets')
        }
        self.drawing_languages = {
            r['object_id']: r['language']
            for r in repository.features('drawing_languages')
        }
        active = {
            a['obj_id']: a for a in repository.features('active_equipment')
        }
        contained = {
            s['splitter_id']
            for s in repository.features('contained_splitters')
        }
        contained_ports = {
            p['port_id'] for p in ports if p['obj_id'] in contained
        }
        self.module_numbers = {
            m['module_id']: int(m['number'])
            for m in repository.features('modules')
        }
        self.end_positions = {
            (e['fiber_id'], e['side']): e.geometry().asPoint()
            for e in repository.features('fiber_ends')
        }
        prepare_routes(self, repository)
        bounds = {}
        for identity, element in elements.items():
            kind, count = element['kind'], int(element['count'])
            rows = (count + 1) // 2 if kind == 'CLOSURE' else count
            width = (
                120 if kind == 'CLOSURE' else 70 if kind == 'SPLITTER' else 180
            )
            bounds[identity] = _rectangle(
                element.geometry().asPoint(),
                width,
                max(70, (rows - 1) * SPACING + 28),
            ).boundingBox()
        for identity, element in elements.items():
            owned = sorted(
                (p for p in ports if p['obj_id'] == identity),
                key=lambda p: int(p['fiber_no']),
            )
            if identity in contained:
                for port in owned:
                    self.positions[port['port_id']] = port.geometry().asPoint()
                continue
            if identity in closures:
                bounds[identity] = self._closure(element, closures[identity])
                continue
            if identity in active:
                bounds[identity] = draw_active(
                    self, element, active[identity], owned
                )
                continue
            if identity in housings:
                bounds[identity] = self._housing(
                    element, housings[identity], owned
                )
                continue
            kind, count = element['kind'], int(element['count'])
            center = element.geometry().asPoint()
            two_banks = kind == 'CLOSURE'
            rows = (count + 1) // 2 if two_banks else count
            width = 120 if two_banks else 70 if kind == 'SPLITTER' else 180
            height = max(70, (rows - 1) * SPACING + 28)
            shape = _rectangle(center, width, height)
            bounds[identity] = shape.boundingBox()
            housed = kind == 'SPLITTER' and any(
                other != identity
                and elements[other]['kind'] in ('CABINET', 'FDH', 'FAT', 'ODF')
                and rectangle.contains(center)
                for other, rectangle in bounds.items()
            )
            self.hit_frames[identity] = shape
            if not housed:
                self._add(self.frames, shape, obj_id=identity, kind=kind)
            if not housed:
                self._text(
                    pt(center.x() - width / 2, center.y() + height / 2 + 6),
                    '{} ({})'.format(
                        element['name'],
                        '1:' + str(count) if kind == 'SPLITTER' else count,
                    ),
                    size=16,
                    bold=True,
                )
            outward_right = kind in ('ODF', 'OLT') or (
                kind == 'CABLE' and element['side'] == 'left'
            )
            for i, port in enumerate(owned):
                if kind == 'SPLITTER':
                    x = (
                        center.x() - width / 2
                        if port['role'] == 'input'
                        else center.x() + width / 2
                    )
                    y = (
                        center.y()
                        if port['role'] == 'input'
                        else center.y()
                        + (count - 1) * 4
                        - (int(port['fiber_no']) - 1) * 8
                    )
                else:
                    right = i >= rows if two_banks else outward_right
                    x = center.x() + (width / 2 if right else -width / 2)
                    y = center.y() + (rows - 1) * 4 - (i % rows) * SPACING
                position = pt(x, y)
                self.positions[port['port_id']] = position
                self._add(
                    self.ports,
                    _point_geometry(position),
                    port_id=port['port_id'],
                    obj_id=identity,
                    label=port['label'],
                    cad=0,
                )
                self._text(
                    pt(x + (2 if x < center.x() else -13), y + 1),
                    str(port['label']),
                    size=5.5,
                )
            if kind == 'SPLITTER':
                self._add(
                    self.details,
                    _line(_rectangle(center, 28, 18).asPolygon()[0]),
                    color='#2246cf',
                )
                self._text(
                    pt(center.x() - 10, center.y() - 3),
                    '1:' + str(count),
                    '#2246cf',
                    7,
                )
                for port in owned:
                    position = self.positions[port['port_id']]
                    anchor = pt(
                        center.x() + (-14 if port['role'] == 'input' else 14),
                        center.y(),
                    )
                    self._add(
                        self.details,
                        _line([anchor, position]),
                        color='#2246cf',
                    )
            elif not two_banks:
                strip_x = center.x() + (
                    width / 2 - 22 if outward_right else -width / 2 + 22
                )
                self._add(
                    self.details,
                    _line(
                        [
                            pt(strip_x, center.y() - height / 2),
                            pt(strip_x, center.y() + height / 2),
                        ]
                    ),
                    color='#333333',
                )
        fibers = repository.features('fibers')
        for cable in repository.features('cables'):
            owned = sorted(
                (f for f in fibers if f['cable_id'] == cable['cable_id']),
                key=lambda f: int(f['fiber_no']),
            )
            if owned:
                self._cable(cable, owned, bounds)
        for link in repository.features('links'):
            a, b = (
                self.positions[link['from_id']],
                self.positions[link['to_id']],
            )
            middle = (a.x() + b.x()) / 2
            geometry = _line([a, pt(middle, a.y()), pt(middle, b.y()), b])
            if (
                link['from_id'] in contained_ports
                or link['to_id'] in contained_ports
            ):
                # Cabinet patch records include the connector lead; draw the
                # blue path from its left edge, as in fq:odospl.
                geometry = _line(link.geometry().asPolyline()[1:])
            self._add(
                self.links,
                geometry,
                link_id=link['link_id'],
                color=link['color'],
                active=0,
            )
            if link['kind'] == 'splice' and not geometry.isEmpty():
                self._add(
                    self.splices,
                    geometry.interpolate(geometry.length() / 2),
                    link_id=link['link_id'],
                )
        apply_operations(self, repository, operations)
        if operations:
            from .annotation_view import draw_annotations

            draw_annotations(self, repository)
        for layer in self.layers():
            provider = layer.dataProvider()
            provider.deleteFeatures([f.id() for f in layer.getFeatures()])
            features = self.batches[layer.id()]
            if features and not provider.addFeatures(features)[0]:
                raise RuntimeError('Could not draw ' + layer.name())
            layer.updateExtents(True)
            layer.triggerRepaint()
        self.batches.clear()

    def _cable(self, cable, fibers, bounds):
        language = self.drawing_languages.get(cable['cable_id'], 'en')
        if cable['cable_version'] == CABLE_VERSION:
            self._cad_cable(cable, fibers)
            return
        if cable['layout_mode'] == 'rows':
            self._row_cable(cable, fibers)
            return
        count = len(fibers)
        route = list(cable.geometry().asPolyline())
        means = []
        for endpoint in ('a_id', 'b_id'):
            ends = [self.positions[f[endpoint]] for f in fibers]
            means.append(
                pt(
                    sum(p.x() for p in ends) / count,
                    sum(p.y() for p in ends) / count,
                )
            )
        # Stop the parallel body outside each housing. Short leads meet exact
        # ports.
        for index, adjacent, identity, mean in [
            (0, 1, cable['from_obj'], means[0]),
            (-1, -2, cable['to_obj'], means[1]),
        ]:
            center, neighbor = route[index], route[adjacent]
            rectangle = bounds[identity]
            dx, dy = neighbor.x() - center.x(), neighbor.y() - center.y()
            if abs(dx) >= abs(dy):
                x = (
                    rectangle.xMaximum() + 24
                    if dx >= 0
                    else rectangle.xMinimum() - 24
                )
                route[index] = pt(x, mean.y())
            else:
                y = (
                    rectangle.yMaximum() + 24
                    if dy >= 0
                    else rectangle.yMinimum() - 24
                )
                route[index] = pt(mean.x(), y)
        lengths = [
            math.hypot(b.x() - a.x(), b.y() - a.y())
            for a, b in zip(route, route[1:])
        ]
        segment = max(range(len(lengths)), key=lengths.__getitem__)
        a, b = route[segment], route[segment + 1]
        distance = max(lengths[segment], 1e-6)
        ux, uy = (b.x() - a.x()) / distance, (b.y() - a.y()) / distance
        nx, ny = -uy, ux
        # Keep labels upright for cables drawn from right to left.
        angle = -math.degrees(math.atan2(uy, ux))
        if angle > 90:
            angle -= 180
        elif angle < -90:
            angle += 180
        for index, fiber in enumerate(fibers):
            offset = ((count - 1) / 2 - index) * SPACING
            body = _parallel(route, offset)
            first, last = (
                self.positions[fiber['a_id']],
                self.positions[fiber['b_id']],
            )
            geometry = _line([first] + body + [last])
            self.drawn_fibers[fiber['fiber_id']] = geometry
            self._add(
                self.fibers,
                geometry,
                fiber_id=fiber['fiber_id'],
                cable_id=cable['cable_id'],
                fiber_no=int(fiber['fiber_no']),
                color=fiber['color'],
                active=0,
            )
            color_index = (int(fiber['fiber_no']) - 1) % 12
            ink = (
                '#777777'
                if color_index == 5
                else '#947e00'
                if color_index == 8
                else fiber['color']
            )
            label_point = pt(
                a.x() + ux * distance * 0.45 + nx * (offset + 1.3),
                a.y() + uy * distance * 0.45 + ny * (offset + 1.3),
            )
            self._text(
                label_point,
                '{}.{}'.format(
                    fiber['fiber_no'],
                    translate(COLOR_NAMES[color_index], language),
                ),
                ink,
                6,
                angle,
            )
        top = (count - 1) * SPACING / 2 + 12
        self._text(
            pt(
                a.x() + ux * distance * 0.2 + nx * top,
                a.y() + uy * distance * 0.2 + ny * top,
            ),
            '{} ({}x12 / {}F)'.format(
                cable['name'], (count + 11) // 12, count
            ),
            size=8.5,
            angle=angle,
            bold=True,
        )
        for start in range(0, count, 12):
            end = min(start + 12, count)
            middle = ((count - 1) / 2 - (start + end - 1) / 2) * SPACING
            radius = (end - start - 1) * SPACING / 2 + 5
            for fraction in (0.18, 0.82) if distance > 220 else (0.2,):
                cx = a.x() + ux * distance * fraction + nx * middle
                cy = a.y() + uy * distance * fraction + ny * middle
                points = [
                    pt(
                        cx + ux * 4 * math.cos(t) + nx * radius * math.sin(t),
                        cy + uy * 4 * math.cos(t) + ny * radius * math.sin(t),
                    )
                    for t in [i * math.tau / 40 for i in range(41)]
                ]
                color = FIBER_COLORS[(start // 12) % 12]
                self._add(self.details, _line(points), color=color)
                self._text(
                    pt(cx + ux * 6, cy + uy * 6),
                    str(start // 12 + 1),
                    color,
                    6,
                )

    def _row_cable(self, cable, fibers):
        language = self.drawing_languages.get(cable['cable_id'], 'en')
        route = cable.geometry().asPolyline()
        rows = fiber_rows(len(fibers), int(cable['group_size']))
        lengths = [
            math.hypot(b.x() - a.x(), b.y() - a.y())
            for a, b in zip(route, route[1:])
        ]
        segment = max(range(len(lengths)), key=lengths.__getitem__)
        a, b = route[segment], route[segment + 1]
        length = lengths[segment]
        ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
        nx, ny = -uy, ux
        angle = math.degrees(math.atan2(uy, ux))
        if 90 < angle <= 270:
            angle -= 180
        elif angle < -90:
            angle += 180
        for index, fiber in enumerate(fibers):
            path = parallel_path(route, rows[index])
            first, last = (
                self.end_positions[(fiber['fiber_id'], 'A')],
                self.end_positions[(fiber['fiber_id'], 'B')],
            )
            geometry = _line([first] + path + [last])
            self.drawn_fibers[fiber['fiber_id']] = geometry
            self._add(
                self.fibers,
                geometry,
                fiber_id=fiber['fiber_id'],
                cable_id=cable['cable_id'],
                fiber_no=int(fiber['fiber_no']),
                color=fiber['color'],
                active=0,
            )
            label = pt(
                a.x() + ux * length * 0.45 + nx * (rows[index] + 1.3),
                a.y() + uy * length * 0.45 + ny * (rows[index] + 1.3),
            )
            color_index = (int(fiber['fiber_no']) - 1) % 12
            ink = (
                '#777777'
                if color_index == 5
                else '#947e00'
                if color_index == 8
                else fiber['color']
            )
            self._text(
                label,
                '{}.{}'.format(
                    fiber['fiber_no'],
                    translate(COLOR_NAMES[color_index], language),
                ),
                ink,
                6,
                angle,
            )
        group_size = int(cable['group_size'])
        self._text(
            pt(
                a.x() + ux * length * 0.2 + nx * 12,
                a.y() + uy * length * 0.2 + ny * 12,
            ),
            '{} ({}x{} / {}F)'.format(
                cable['name'],
                math.ceil(len(fibers) / group_size),
                group_size,
                len(fibers),
            ),
            size=8.5,
            angle=angle,
            bold=True,
        )
        for start in range(0, len(fibers), group_size):
            end = min(start + group_size, len(fibers))
            middle = (rows[start] + rows[end - 1]) / 2
            radius = (rows[start] - rows[end - 1]) / 2 + 5
            cx, cy = (
                a.x() + ux * length * 0.2 + nx * middle,
                a.y() + uy * length * 0.2 + ny * middle,
            )
            ring = [
                pt(
                    cx + ux * 4 * math.cos(t) + nx * radius * math.sin(t),
                    cy + uy * 4 * math.cos(t) + ny * radius * math.sin(t),
                )
                for t in [i * math.tau / 40 for i in range(41)]
            ]
            self._add(
                self.details,
                _line(ring),
                color=FIBER_COLORS[(start // group_size) % 12],
            )

    def _cad_cable(self, cable, fibers):
        props = properties_from_record(cable)
        language = self.drawing_languages.get(cable['cable_id'], 'en')
        outlet = self.outlets.get(cable['cable_id'])
        outlet_mode = outlet['mode'] if outlet is not None else ''
        route, rows = cable.geometry().asPolyline(), props.rows
        paths = []
        for index, fiber in enumerate(fibers):
            path = cable_fiber_path(route, rows[index], outlet_mode)
            first, last = (
                self.end_positions[(fiber['fiber_id'], 'A')],
                self.end_positions[(fiber['fiber_id'], 'B')],
            )
            geometry = _line([first] + path + [last])
            self.drawn_fibers[fiber['fiber_id']] = geometry
            self._add(
                self.fibers,
                geometry,
                fiber_id=fiber['fiber_id'],
                cable_id=cable['cable_id'],
                fiber_no=int(fiber['fiber_no']),
                color=fiber['color'],
                active=0,
                cad=1,
            )
            paths.append(path)

        def label(
            point, text, height, angle, ink=None, align='Left', bold=False
        ):
            family, width, cap = housing_font_metrics(text, bold=bold)
            upright = 90 < angle % 360 <= 270
            if upright:
                angle -= 180
                align = {'Left': 'Right', 'Right': 'Left'}.get(align, align)
            self._add(
                self.housing_labels,
                _point_geometry(point),
                obj_id=cable['cable_id'],
                text=text,
                size=height * cap,
                family=family,
                angle=angle,
                bold=int(bold),
                align=align,
                color=ink,
            )
            text_width = height * width
            left = (
                -text_width
                if align == 'Right'
                else -text_width / 2
                if align == 'Center'
                else 0
            )
            theta = math.radians(angle)
            corners = [
                pt(
                    point.x() + x * math.cos(theta) - y * math.sin(theta),
                    point.y() + x * math.sin(theta) + y * math.cos(theta),
                )
                for x, y in (
                    (left, -height / 2),
                    (left + text_width, -height / 2),
                    (left + text_width, height / 2),
                    (left, height / 2),
                )
            ]
            self.text_bounds.combineExtentWith(_line(corners).boundingBox())

        # Source end furniture: 5 CAD-unit inset, .95 longitudinal radius,
        # .55 pitch clearance, physical tube colors/numbers and per-tube
        # labels.
        for side in (0, -1):
            if outlet_mode and side == (-1 if outlet_mode == 'elbow' else 0):
                continue
            a, b = (
                (route[0], route[1]) if side == 0 else (route[-2], route[-1])
            )
            length = a.distance(b)
            ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
            nx, ny, direction = -uy, ux, 1 if side == 0 else -1
            angle = math.degrees(math.atan2(uy, ux))
            shortest = min(
                (
                    path[1].distance(path[0])
                    if side == 0
                    else path[-1].distance(path[-2])
                )
                for path in paths
            )
            longest_label = (
                max(
                    len(str(props.count)) + 1 + len(props.color(n)[1])
                    for n in range(1, props.fibers_per_tube + 1)
                )
                * 0.78
                * 5.4
            )
            show_labels = shortest >= 65.5 + longest_label
            # Straight short routes keep just the starting column; bent short
            # end segments omit furniture rather than overlapping their bends.
            if outlet_mode:
                show_labels = True
            elif len(route) == 2:
                show_labels = side == 0 or shortest - 131 > 2.2 * longest_label
            elif not show_labels:
                continue
            endpoints = [path[side] for path in paths]
            for tube in range(props.tubes):
                start = tube * props.fibers_per_tube
                end = start + props.fibers_per_tube
                if any(
                    fibers[i]['fiber_id'] in self.route_overrides
                    and self.route_overrides[fibers[i]['fiber_id']]['side']
                    == ('A' if side == 0 else 'B')
                    for i in range(start, end)
                ):
                    continue
                center = pt(
                    (endpoints[start].x() + endpoints[end - 1].x()) / 2
                    + ux * 50 * direction,
                    (endpoints[start].y() + endpoints[end - 1].y()) / 2
                    + uy * 50 * direction,
                )
                radius = (
                    endpoints[start].distance(endpoints[end - 1]) / 2 + 4.4
                )
                ink, unused = props.color(tube + 1)
                ring_ink = '#333333' if ink == '#ffffff' else ink
                ring = [
                    pt(
                        center.x()
                        + ux * 9.5 * math.cos(t)
                        + nx * radius * math.sin(t),
                        center.y()
                        + uy * 9.5 * math.cos(t)
                        + ny * radius * math.sin(t),
                    )
                    for t in (i * math.tau / 40 for i in range(41))
                ]
                self._add(self.details, _line(ring), color=ring_ink)
                label(center, str(tube + 1), 7.56, angle, ink, 'Center')
            if show_labels:
                for index, end in enumerate(endpoints):
                    if fibers[index][
                        'fiber_id'
                    ] in self.route_overrides and self.route_overrides[
                        fibers[index]['fiber_id']
                    ]['side'] == ('A' if side == 0 else 'B'):
                        continue
                    ink, name = props.color(index % props.fibers_per_tube + 1)
                    name = props.color_caption(
                        index % props.fibers_per_tube + 1, language
                    )
                    label(
                        pt(
                            end.x() + ux * 65.5 * direction + nx * 4.14,
                            end.y() + uy * 65.5 * direction + ny * 4.14,
                        ),
                        '{}.{}'.format(index + 1, name),
                        5.4,
                        angle,
                        ink,
                        'Left' if side == 0 else 'Right',
                    )
        a, b = (
            (route[-1], route[-2]) if outlet_mode == 'straight' else route[:2]
        )
        length = a.distance(b)
        ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
        caption = cable['name'] + '   ' + props.description
        # Centering the equivalent source text box avoids Qt's rotated Left
        # anchor placing a long vertical caption over the far housing title.
        inset = (
            15 + 7.56 * housing_font_metrics(caption)[1] / 2
            if outlet_mode
            else 15
        )
        # Straight outlets number their columns in the opposite order; keep
        # the caption outside that bundle, clear of its fiber/tube labels.
        clearance = -14.18 if outlet_mode == 'straight' else 14.18
        label(
            pt(
                a.x() + ux * inset - uy * clearance,
                a.y() + uy * inset + ux * clearance,
            ),
            caption,
            7.56,
            math.degrees(math.atan2(uy, ux)),
            align='Center' if outlet_mode else 'Left',
            bold=True,
        )

    def highlight(self, identities, links=(), sections=None, joints=()):
        provider = self.fibers.dataProvider()
        index = self.fibers.fields().indexFromName('active')
        provider.changeAttributeValues(
            {
                f.id(): {
                    index: int(
                        f['span_id'] in sections
                        if sections is not None
                        else f['fiber_id'] in identities
                    )
                }
                for f in self.fibers.getFeatures()
            }
        )
        self.fibers.triggerRepaint()
        index_joint = self.optical_lines.fields().indexFromName('active')
        self.optical_lines.dataProvider().changeAttributeValues(
            {
                f.id(): {index_joint: int(f['joint_id'] in joints)}
                for f in self.optical_lines.getFeatures()
            }
        )
        self.optical_lines.triggerRepaint()
        index = self.links.fields().indexFromName('active')
        self.links.dataProvider().changeAttributeValues(
            {
                f.id(): {index: int(f['link_id'] in links)}
                for f in self.links.getFeatures()
            }
        )
        self.links.triggerRepaint()
