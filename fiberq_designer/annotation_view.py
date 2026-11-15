"""Render saved report tables and legends using the regular CAD export path."""

import json
import math

from qgis.core import QgsGeometry, QgsRectangle

from .annotation_layout import legend_primitives, table_primitives
from .geometry import housing_font_metrics, pt
from .operation_view import pinned_text


def draw_annotations(scene, repository):
    signature = None
    for record in repository.features('annotations'):
        payload = json.loads(record['payload'])
        if payload.get('source_hash'):
            if signature is None:
                from .workspace_service import WorkspaceService

                service = WorkspaceService()
                service.repo = repository
                signature = service.report_signature()
            if signature != payload['source_hash']:
                from .i18n import translate

                payload['notes'] = payload['notes'] + [
                    translate(
                        'STALE REPORT — regenerate after design changes.',
                        payload['language'],
                    )
                ]
        lines, texts, dots = (
            legend_primitives(payload)
            if record['kind'] == 'legend'
            else table_primitives(payload)
        )
        at = record.geometry().asPoint()

        def world(x, y):
            return pt(at.x() + x, at.y() + y)

        bounds = QgsRectangle(at.x() - 3, at.y() - 3, at.x() + 3, at.y() + 3)
        for points, ink, width in lines:
            geometry = QgsGeometry.fromPolylineXY(
                [world(x, y) for x, y in points]
            )
            scene._add(
                scene.annotation_lines,
                geometry,
                annotation_id=record['annotation_id'],
                color=ink or '#333333',
                width=width,
            )
            bounds.combineExtentWith(geometry.boundingBox())
        for entry in texts:
            x, y, value, height, bold, ink = entry[:6]
            align = entry[6] if len(entry) == 7 else 'Left'
            pinned_text(
                scene,
                record['annotation_id'],
                world(x, y),
                value,
                height,
                ink=ink or None,
                bold=bold,
                baseline=align == 'Left',
                align=align,
            )
            width = housing_font_metrics(value, bold=bold)[1] * height
            left = (
                x - width
                if align == 'Right'
                else x - width / 2
                if align == 'Center'
                else x
            )
            bounds.combineExtentWith(
                QgsRectangle(
                    at.x() + left,
                    at.y() + y - height,
                    at.x() + left + width,
                    at.y() + y + height,
                )
            )
        for x, y, diameter in dots:
            ring = [
                world(
                    x + diameter / 2 * math.cos(i * math.tau / 20),
                    y + diameter / 2 * math.sin(i * math.tau / 20),
                )
                for i in range(21)
            ]
            scene._add(
                scene.housing_dots,
                QgsGeometry.fromPolygonXY([ring]),
                obj_id=record['annotation_id'],
                port_id='',
            )
        scene.operation_hits[('annotations', record['annotation_id'])] = (
            QgsGeometry.fromRect(bounds)
        )
        scene.text_bounds.combineExtentWith(bounds)
