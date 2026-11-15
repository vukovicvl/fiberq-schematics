"""Canvas input and transient route previews; no persistence in map events."""

from qgis.PyQt.QtGui import QColor
from qgis.core import QgsGeometry, QgsWkbTypes
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand

from .compat import KEY_BACKSPACE, KEY_ENTER, KEY_ESCAPE, KEY_RETURN


class ClickTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, editor):
        super().__init__(canvas)
        self.editor = editor
        self.canvasClicked.connect(self._clicked)
        self.preview = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.preview.setColor(QColor('#e59524'))
        self.preview.setWidth(3)
        self.reference = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.reference.setColor(QColor('#ee3333'))
        self.reference.setWidth(2)
        self.snap_marker = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.snap_marker.setColor(QColor('#00cddd'))
        self.snap_marker.setWidth(2)

    def _clicked(self, point, button):
        self.editor.canvas_click(self.editor.to_schematic(point), button)

    def canvasMoveEvent(self, event):
        self.editor.canvas_move(self.editor.to_schematic(event.mapPoint()))

    def canvasDoubleClickEvent(self, event):
        if self.editor.mode == 'select':
            self.editor.guard(self.editor.edit_selected)
        elif self.editor.mode in ('loose_draw', 'cad_cable'):
            self.editor.guard(self.editor.finish_route)

    def keyPressEvent(self, event):
        if event.text().lower() == 'i' and self.editor.mode == 'active_place':
            self.editor.toggle_active_anchor()
            return
        if event.key() == KEY_ESCAPE:
            self.editor.stop_tool()
        elif event.key() == KEY_BACKSPACE:
            self.editor.remove_last_bend()
        elif event.key() in (KEY_ENTER, KEY_RETURN):
            self.editor.guard(
                self.editor.finish_closure
                if self.editor.mode == 'closure'
                else self.editor.finish_route
            )
        else:
            super().keyPressEvent(event)

    def show_route(self, vertices):
        self.clear()
        self.preview.setWidth(3)
        self.preview.setColor(QColor('#e59524'))
        if len(vertices) >= 2:
            geometry = QgsGeometry.fromPolylineXY(vertices)
            geometry = self.editor.to_canvas_geometry(geometry)
            self.preview.setToGeometry(geometry, None)
            self.preview.show()

    def show_bundle(self, paths, valid=True, snap=None):
        self.clear()
        if paths:
            geometry = QgsGeometry.fromMultiPolylineXY(paths)
            self.preview.setToGeometry(
                self.editor.to_canvas_geometry(geometry), None
            )
            self.preview.setColor(QColor('#e59524' if valid else '#dd3333'))
            self.preview.setWidth(1)
            self.preview.show()
            first = QgsGeometry.fromPolylineXY(paths[0])
            self.reference.setToGeometry(
                self.editor.to_canvas_geometry(first), None
            )
            self.reference.show()
        if snap is not None:
            radius = self.editor.tolerance(snap, 5)
            from .geometry import pt

            ring = [
                pt(snap.x() - radius, snap.y() - radius),
                pt(snap.x() + radius, snap.y() - radius),
                pt(snap.x() + radius, snap.y() + radius),
                pt(snap.x() - radius, snap.y() + radius),
                pt(snap.x() - radius, snap.y() - radius),
            ]
            self.snap_marker.setToGeometry(
                self.editor.to_canvas_geometry(
                    QgsGeometry.fromPolylineXY(ring)
                ),
                None,
            )
            self.snap_marker.show()

    def show_housing(self, layout, at, orientation, mirrored=False):
        from .geometry import odf_point

        paths = layout.module_frames + [
            vertices
            for number, vertices in layout.lines
            if len(layout.ports) <= 240
            or number == 0
            or (number - 1) % 12 in (0, 11)
        ]
        if layout.splitter is not None:
            paths += [layout.splitter.frame] + list(
                layout.splitter.paths.values()
            )
        geometry = QgsGeometry.fromMultiPolylineXY(
            [
                [odf_point(p, at, orientation, mirrored) for p in path]
                for path in paths
            ]
        )
        self.preview.setToGeometry(
            self.editor.to_canvas_geometry(geometry), None
        )
        self.preview.show()

    def clear(self):
        self.preview.reset(QgsWkbTypes.LineGeometry)
        self.preview.hide()
        self.reference.reset(QgsWkbTypes.LineGeometry)
        self.reference.hide()
        self.snap_marker.reset(QgsWkbTypes.LineGeometry)
        self.snap_marker.hide()

    def deactivate(self):
        self.clear()
        self.editor.pending = None
        self.editor.route = None
        self.editor.housing_placement = None
        self.editor.housing_target = None
        self.editor.housing_layout = None
        self.editor.closure_placement = None
        self.editor.mode = None
        self.editor.catalog_placement = None
        self.editor.operation_placement = None
        for button in self.editor.tool_buttons.values():
            button.setChecked(False)
        if hasattr(self.editor, 'update_command_state'):
            self.editor.update_command_state()
        super().deactivate()

    def dispose(self):
        self.clear()
        self.canvas().scene().removeItem(self.preview)
        self.canvas().scene().removeItem(self.reference)
        self.canvas().scene().removeItem(self.snap_marker)
