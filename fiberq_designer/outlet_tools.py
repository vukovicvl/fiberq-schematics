"""Frame picking and uncommitted outlet wizard previews."""

from qgis.core import QgsGeometry
from qgis.PyQt.QtWidgets import QDialog

from .compat import enum_compat
from .geometry import odf_point
from .outlet_dialog import OutletDialog


class OutletTools:
    def start_outlet(self):
        if not self.ensure_ready():
            return
        target = (
            self.selected[1]
            if self.selected and self.selected[0] == 'elements'
            else None
        )
        self.activate('frame_outlet')
        if target and (
            self.repository.find('housings', target) is not None
            or self.repository.find('closures', target) is not None
        ):
            self.configure_outlet(target)

    def outlet_click(self, click):
        element = self.get_element(click)
        if element is None:
            raise ValueError('Choose a native housing or N/NL closure frame.')
        identity = element['obj_id']
        module = (
            self.housing_module_at(identity, click)
            if self.repository.find('housings', identity) is not None
            else 0
        )
        self.configure_outlet(identity, module)

    def configure_outlet(self, identity, module_no=1):
        self.service.outlet_context(identity, module_no)
        dialog = OutletDialog(self, identity, module_no)
        self.outlet_dialog = dialog
        if dialog.exec() != enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.stop_tool()
            self.note('Outlet cancelled. No cable or connection saved.')
            return
        cable = self.service.add_outlet(
            identity,
            dialog.properties(),
            dialog.length.value(),
            dialog.mode.currentData(),
            dialog.module.currentData(),
            dialog.label.text(),
            dialog.real_length.value(),
        )
        self.stop_tool()
        self.selected = ('cables', cable)
        self.refresh()
        self.fit()
        self.note(
            'Outlet saved. Use Join / Group join for splices or Add cable '
            'for adapter terminations.'
        )

    def preview_outlet(self, plan):
        context = plan['context']
        paths = list(plan['paths'])
        if plan['housing_plan']:
            props = plan['housing_plan']['properties']
            layout = self.preview_odf(props)
            paths += [
                [
                    odf_point(p, context['origin'], context['angle'])
                    for p in path
                ]
                for path in layout.module_frames
                + [path for number, path in layout.lines]
            ]
        elif not context['housing']:
            left = min(0, plan['drawing']['left'] - 2)
            bottom = min(context['bottom'], plan['drawing']['last_row'] - 2)
            paths.append(
                [
                    odf_point(p, context['origin'], context['angle'])
                    for p in (
                        (left, context['top']),
                        (context['right'], context['top']),
                        (context['right'], bottom),
                        (left, bottom),
                        (left, context['top']),
                    )
                ]
            )
        self.click_tool.show_bundle(paths)
        bounds = self.to_canvas_geometry(
            QgsGeometry.fromMultiPolylineXY(paths)
        ).boundingBox()
        if not self.canvas.extent().contains(bounds):
            bounds.combineExtentWith(self.canvas.extent())
            bounds.grow(20)
            self.canvas.setExtent(bounds)
            self.canvas.refresh()

    def edit_outlet_selected(self, cable):
        dialog = OutletDialog(self, cable=cable)
        self.outlet_dialog = dialog
        if dialog.exec() == enum_compat(QDialog, 'Accepted', 'DialogCode'):
            self.service.edit_catalog_cable(
                cable['cable_id'],
                dialog.label.text(),
                dialog.properties(),
                dialog.real_length.value(),
            )
            self.refresh()
            self.note(
                'Outlet properties saved; fiber and endpoint identities '
                'retained.'
            )
