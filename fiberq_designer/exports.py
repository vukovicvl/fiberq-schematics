"""Drawing exports; connectivity stays in the GeoPackage."""

import os
from pathlib import Path

from qgis.PyQt.QtCore import QFile
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsDxfExport,
    QgsFeatureRequest,
    QgsLayoutExporter,
    QgsNullSymbolRenderer,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsMapLayerStyle,
    QgsPrintLayout,
    QgsProviderRegistry,
    QgsTextFormat,
)

from .compat import DXF_SUCCESS, FONT_BOLD, WRITE_ONLY


def _label_font(label, font, color):
    if hasattr(label, 'setTextFormat'):
        fmt = QgsTextFormat()
        fmt.setFont(font)
        fmt.setSize(font.pointSizeF())
        fmt.setColor(QColor(color))
        label.setTextFormat(fmt)
    else:  # QGIS 3.22 predates the text-format API for layout labels.
        label.setFont(font)
        label.setFontColor(QColor(color))


def _bind_paper_layers(layout, map_item, project, layers):
    references, styles = [], {}
    for layer in layers:
        registered = project.mapLayer(
            layer.customProperty('fqsd/source_layer_id', layer.id())
        )
        target = registered if registered is not None else layer
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        references.append(target)
        styles[target.id()] = style.xmlData()
    # QgsLayoutItemMap stores weak references. Retain unregistered review
    # layers for the lifetime of their layout, too.
    layout._fqsd_export_layers = references
    map_item.setLayers(references)
    map_item.setKeepLayerSet(True)
    map_item.setLayerStyleOverrides(styles)
    map_item.setKeepLayerStyles(True)


def _workspace_layer(project, path):
    for layer in project.mapLayers().values():
        if layer.providerType() == 'ogr':
            source = (
                QgsProviderRegistry.instance()
                .decodeUri('ogr', layer.source())
                .get('path', '')
            )
            if source and os.path.abspath(source) == os.path.abspath(path):
                return layer
    return None


def drawing_layouts_for_workspace(project, path):
    """Resolve before old memory/data layers are removed, including moved
    projects."""
    matches = []
    for layout in project.layoutManager().layouts():
        source = layout.customProperty('fqsd/workspace', '')
        if not source:
            continue
        reference = project.mapLayer(
            layout.customProperty('fqsd/workspace_layer', '')
        )
        if reference is not None:
            source = (
                QgsProviderRegistry.instance()
                .decodeUri(reference.providerType(), reference.source())
                .get('path', source)
            )
        if os.path.abspath(source) == os.path.abspath(path):
            matches.append(layout)
    return matches


def rebind_drawing_layouts(project, layers, path, layouts):
    """Restore this workspace's print maps after regenerating memory layers."""
    source = _workspace_layer(project, path)
    for layout in layouts:
        layout.setCustomProperty('fqsd/workspace', os.path.abspath(path))
        if source is not None:
            layout.setCustomProperty('fqsd/workspace_layer', source.id())
        for item in layout.items():
            if isinstance(item, QgsLayoutItemMap) and item.customProperty(
                'fqsd/drawing_map', False
            ):
                _bind_paper_layers(layout, item, project, layers)


def make_layout(project, layers, path, extent, language='en'):
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName('FiberQ Schematic - A3')
    layout.setCustomProperty('fqsd/workspace', os.path.abspath(path))
    source = _workspace_layer(project, path)
    if source is not None:
        layout.setCustomProperty('fqsd/workspace_layer', source.id())
    layout.pageCollection().pages()[0].setPageSize(QgsLayoutSize(420, 297))
    title = QgsLayoutItemLabel(layout)
    title.setText('FIBERQ  /  SCHEMATIC DESIGNER')
    _label_font(title, QFont('DejaVu Sans', 18, FONT_BOLD), '#112d58')
    layout.addLayoutItem(title)
    title.attemptMove(QgsLayoutPoint(16, 9))
    title.attemptResize(QgsLayoutSize(350, 17))
    subtitle = QgsLayoutItemLabel(layout)
    from .i18n import translate

    subtitle.setText(
        os.path.basename(path)
        + translate(
            '  |  Schematic positions; not geographic locations', language
        )
    )
    _label_font(subtitle, QFont('DejaVu Sans', 9), '#12213d')
    layout.addLayoutItem(subtitle)
    subtitle.attemptMove(QgsLayoutPoint(17, 25))
    subtitle.attemptResize(QgsLayoutSize(375, 8))
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(14, 40))
    map_item.attemptResize(QgsLayoutSize(392, 241))
    map_item.setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
    map_item.setCustomProperty('fqsd/drawing_map', True)
    # Layout maps hold weak layer references. Bind registered layers where
    # possible and save the supplied paper styles as overrides, so export and
    # reopening a saved project cannot fall back to the dark canvas styles.
    _bind_paper_layers(layout, map_item, project, layers)
    map_item.zoomToExtent(extent)
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeColor(QColor('#b7c3d6'))
    return layout


def export_layout(layout, path, extension):
    exporter = QgsLayoutExporter(layout)
    result = (
        exporter.exportToPdf(path, QgsLayoutExporter.PdfExportSettings())
        if extension == 'pdf'
        else exporter.exportToSvg(path, QgsLayoutExporter.SvgExportSettings())
    )
    if result != QgsLayoutExporter.Success:
        raise RuntimeError('Drawing export failed: {}'.format(result))


def export_dxf(canvas, layers, extent, path):
    prepared_layers = []
    for layer in layers:
        if layer.customProperty('fqsd/labels_only', False):
            # DXF should contain text, without the transparent label-anchor
            # glyph.
            # Keep the live canvas renderer intact: it needs a real marker
            # symbol
            # for QGIS to register the labels when rendering the map.
            copy = layer.materialize(QgsFeatureRequest())
            copy.setName(layer.name())
            copy.setRenderer(QgsNullSymbolRenderer())
            copy.setLabeling(layer.labeling().clone())
            copy.setLabelsEnabled(True)
            # QGIS writes label values directly into MTEXT. Escape literal
            # formatting characters on this disposable copy so customer text
            # such as "Free {customer}" survives the CAD round trip.
            text_index = copy.fields().indexFromName('text')
            if text_index >= 0:
                changes = {}
                for feature in copy.getFeatures():
                    value = feature['text']
                    if isinstance(value, str):
                        escaped = (
                            value.replace('\\', '\\\\')
                            .replace('{', '\\{')
                            .replace('}', '\\}')
                        )
                        if escaped != value:
                            changes[feature.id()] = {text_index: escaped}
                if changes and not copy.dataProvider().changeAttributeValues(
                    changes
                ):
                    raise RuntimeError('Cannot prepare DXF label text.')
            prepared_layers.append(copy)
        else:
            prepared_layers.append(layer)
    exporter = QgsDxfExport()
    exporter.addLayers(
        [QgsDxfExport.DxfLayer(layer, -1) for layer in prepared_layers]
    )
    symbology = (
        Qgis.FeatureSymbologyExport.PerSymbolLayer
        if hasattr(Qgis, 'FeatureSymbologyExport')
        else QgsDxfExport.SymbolLayerSymbology
    )
    exporter.setSymbologyExport(symbology)
    exporter.setSymbologyScale(max(1.0, canvas.scale()))
    exporter.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
    exporter.setExtent(extent)
    exporter.setMapSettings(canvas.mapSettings())
    device = QFile(path)
    if not device.open(WRITE_ONLY):
        raise RuntimeError('Cannot open DXF output file.')
    try:
        result = exporter.writeToFile(device, 'UTF-8')
    finally:
        device.close()
    if result != DXF_SUCCESS:
        raise RuntimeError('DXF export failed: {}'.format(result))
    # QGIS writes an R2004 (AC1018) DXF. Its UTF-8 codec leaves DWGCODEPAGE
    # blank, so CAD/GDAL readers interpret UTF-8 text as ANSI and corrupt names
    # and mathematical signs. R2004 supports explicit UTF-16 \U+XXXX escapes.
    destination = Path(path)
    contents = destination.read_text(encoding='utf-8')
    escaped = []
    for character in contents:
        if ord(character) <= 127:
            escaped.append(character)
        else:
            units = character.encode('utf-16-be')
            escaped.extend(
                '\\U+{:04X}'.format(int.from_bytes(units[i: i + 2], 'big'))
                for i in range(0, len(units), 2)
            )
    contents = ''.join(escaped).replace(
        '$DWGCODEPAGE\n  3\n\n', '$DWGCODEPAGE\n  3\nANSI_1252\n'
    )
    destination.write_text(contents, encoding='ascii')
