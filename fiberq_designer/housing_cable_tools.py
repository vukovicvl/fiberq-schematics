"""Housing-first Add cable command and uncommitted frame/connector previews."""

from qgis.core import QgsGeometry
from qgis.PyQt.QtWidgets import QDialog

from .compat import enum_compat
from .geometry import odf_drawing, odf_properties
from .housing_cable_dialog import AddHousingCableDialog


class HousingCableTools:
    def start_add_housing_cable(self):
        if not self.ensure_ready():
            return
        target = (
            self.selected[1]
            if self.selected
            and self.selected[0] == 'elements'
            and self.repository.find('housings', self.selected[1]) is not None
            else None
        )
        self.activate('add_housing_cable')
        self.pending = (target, 1) if target else None
        self.note(
            'Click a cable near the end to add.'
            if target
            else 'Click the housing/module frame, then the cable end to add.'
        )

    def housing_module_at(self, identity, click):
        element = self.repository.find('elements', identity)
        record = self.repository.find('housings', identity)
        props = odf_properties(element, record)
        from .geometry import odf_point

        for index, frame in enumerate(
            odf_drawing(element, record).module_frames
        ):
            shape = QgsGeometry.fromPolygonXY(
                [
                    [
                        odf_point(
                            p,
                            element.geometry().asPoint(),
                            props.orientation,
                            props.mirrored,
                        )
                        for p in frame
                    ]
                ]
            )
            if shape.intersects(QgsGeometry.fromPointXY(click)):
                return index + 1
        return 1

    def show_housing_add_preview(self, identity, plan):
        props = plan['properties']
        self.click_tool.clear()
        self.click_tool.show_housing(
            self.preview_odf(props),
            self.repository.find('elements', identity).geometry().asPoint(),
            props.orientation,
            props.mirrored,
        )

    def add_housing_cable_click(self, click):
        if self.pending is None:
            element = self.get_element(click)
            if (
                element is None
                or self.repository.find('housings', element['obj_id']) is None
            ):
                raise ValueError(
                    'Choose an ODF, panel, cabinet or FAT/OTB housing frame.'
                )
            self.pending = (
                element['obj_id'],
                self.housing_module_at(element['obj_id'], click),
            )
            self.note(
                'Click a cable near its end. The form previews additional '
                'terminations before saving.'
            )
            return
        identity, module_no = self.pending
        cable_id, side = self.cable_end_at(click)
        dialog = AddHousingCableDialog(
            self, identity, cable_id, side, module_no
        )
        self.add_cable_dialog = dialog
        if dialog.exec() != enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.click_tool.clear()
            self.note(
                'Add cable cancelled. Choose another cable end or press Esc.'
            )
            return
        self.service.add_housing_cable(
            identity,
            cable_id,
            side,
            dialog.additional.value(),
            dialog.module.currentData(),
        )
        self.stop_tool()
        self.selected = ('elements', identity)
        self.refresh()
        self.note(
            'Cable added. Existing connected ports, fiber identities and '
            'connections retained.'
        )

    def preview_add_housing_cable(self, click):
        if self.pending is None:
            return
        identity, module_no = self.pending
        try:
            cable_id, side = self.cable_end_at(click)
            plan = self.service.housing_cable_plan(
                identity, cable_id, side, module_no=module_no
            )
            self.show_housing_add_preview(identity, plan)
        except ValueError:
            self.click_tool.clear()

    def detach_selected_housing_cable(self):
        if not self.ensure_ready():
            return
        self.activate('detach_housing_cable')
        self.note(
            'Click a fiber near the attached cable end to detach. The cable '
            'and housing remain in the drawing.'
        )

    def detach_housing_cable_click(self, click):
        cable_id, side = self.cable_end_at(click)
        self.service.detach_cable_end(cable_id, side)
        self.stop_tool()
        self.selected = ('cables', cable_id)
        self.refresh()
        self.note(
            'Cable end detached; other cables and the housing are retained. '
            'Undo restores the attachment.'
        )
