"""Schematic symbols, labels, and derived, selectable splice markers."""

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtGui import QColor, QFont

from .domain import FIBER_COLORS


def label(layer, field, size, bold=False, above_frame=False):
    settings = QgsPalLayerSettings()
    settings.fieldName = field
    if layer.geometryType() == QgsWkbTypes.LineGeometry:
        settings.placement = getattr(
            Qgis, 'LabelPlacement', QgsPalLayerSettings
        ).Line
    if above_frame:
        settings.geometryGeneratorEnabled = True
        settings.geometryGeneratorType = QgsWkbTypes.PointGeometry
        settings.geometryGenerator = (
            'make_point(x($geometry), '
            "y_max(geometry(get_feature('FQSD | frames', 'obj_id', "
            '"obj_id"))) + 7)'
        )
    fmt = QgsTextFormat()
    font = QFont('DejaVu Sans', size)
    font.setBold(bold)
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setColor(QColor('#12213d'))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.7)
    buffer.setColor(QColor('white'))
    fmt.setBuffer(buffer)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def style(key, layer):
    if key == 'frames':
        colors = {
            'ODF': '#dce9ff',
            'CLOSURE': '#d4f3df',
            'CABINET': '#fff0cb',
            'SPLITTER': '#f1ddff',
            'OLT': '#ffdce0',
            'ONT': '#d7f3f5',
            'CABLE': '#eff5ff',
        }
        categories = [
            QgsRendererCategory(
                kind,
                QgsFillSymbol.createSimple(
                    {
                        'color': color,
                        'outline_color': '#475d81',
                        'outline_width': '0.45',
                    }
                ),
                kind,
            )
            for kind, color in colors.items()
        ]
        layer.setRenderer(QgsCategorizedSymbolRenderer('kind', categories))
    elif key == 'elements':
        layer.setRenderer(
            QgsSingleSymbolRenderer(
                QgsMarkerSymbol.createSimple(
                    {
                        'name': 'square',
                        'size': '1',
                        'color': '#1f395b',
                    }
                )
            )
        )
        label(layer, 'name', 10, True, above_frame=True)
    elif key in ('ports', 'links'):
        categories = []
        for color in FIBER_COLORS:
            symbol = (
                QgsMarkerSymbol.createSimple(
                    {
                        'name': 'circle',
                        'size': '2.2',
                        'color': color,
                        'outline_color': '#374150',
                        'outline_width': '0.15',
                    }
                )
                if key == 'ports'
                else QgsLineSymbol.createSimple(
                    {
                        'line_color': color,
                        'line_width': '0.55',
                    }
                )
            )
            categories.append(QgsRendererCategory(color, symbol, color))
        layer.setRenderer(QgsCategorizedSymbolRenderer('color', categories))
        if key == 'ports':
            label(layer, 'label', 7)
    elif key == 'cables':
        layer.setRenderer(
            QgsSingleSymbolRenderer(
                QgsLineSymbol.createSimple(
                    {
                        'line_color': '#244d85',
                        'line_width': '0.85',
                    }
                )
            )
        )
        label(layer, 'name', 8)


def splice_layer():
    layer = QgsVectorLayer(
        'Point?crs=EPSG:3857&field=link_id:string',
        'FQSD | splice points',
        'memory',
    )
    layer.setRenderer(
        QgsSingleSymbolRenderer(
            QgsMarkerSymbol.createSimple(
                {
                    'name': 'diamond',
                    'size': '2.8',
                    'color': '#25a578',
                    'outline_color': '#11583e',
                    'outline_width': '0.3',
                }
            )
        )
    )
    layer.setReadOnly(True)
    return layer


def update_splices(layer, links):
    provider = layer.dataProvider()
    provider.deleteFeatures([f.id() for f in layer.getFeatures()])
    features = []
    for link in links:
        if link['kind'] != 'splice':
            continue
        feature = QgsFeature(layer.fields())
        geometry = link.geometry()
        feature.setGeometry(geometry.interpolate(geometry.length() / 2))
        feature['link_id'] = link['link_id']
        features.append(feature)
    if features:
        ok, _ = provider.addFeatures(features)
        if not ok:
            raise RuntimeError('Could not refresh splice markers.')
    layer.updateExtents(True)
    layer.triggerRepaint()
