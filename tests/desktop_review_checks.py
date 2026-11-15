"""Regressions found while reviewing the actual Desktop application."""

from qgis.PyQt.QtWidgets import QLabel, QTabWidget
from pathlib import Path
import gc
import shutil

from qgis.core import QgsLayoutItemMap, QgsMapLayerStyle, QgsProject

from fiberq_designer import exports
from fiberq_designer.annotation_layout import legend_payload
from fiberq_designer.geometry import pt
from fiberq_designer.housing_catalog import OdfProperties


class DesktopReviewChecks:
    def test_review_paper_layout_retains_styles_selection_and_saved_project_references(self):
        self.service.add_odf('1', OdfProperties(), pt(0, 0))
        self.service.add_annotation('legend', legend_payload(self.service.settings()), pt(300, 0))
        dock = self.open_dock()
        dock.dark_canvas.setChecked(True)
        dock.scene.annotation_lines.selectAll()
        selected = dock.scene.annotation_lines.selectedFeatureIds()
        paper = dock.drawing_layers(paper=True)
        self.assertTrue(all(layer.selectedFeatureCount() == 0 for layer in paper))
        self.assertEqual(selected, dock.scene.annotation_lines.selectedFeatureIds())
        layout = dock.make_layout()
        del paper
        gc.collect()
        item = next(item for item in layout.items() if isinstance(item, QgsLayoutItemMap))
        self.assertEqual(len(dock.scene.layers()), len(item.layers()))
        self.assertTrue(all(QgsProject.instance().mapLayer(layer.id()) is layer for layer in item.layers()))
        self.assertTrue(item.keepLayerStyles())
        labels = dock.scene.housing_labels
        self.assertEqual('#eeeeee', labels.labeling().settings().format().color().name())
        copy = labels.clone()
        QgsMapLayerStyle(item.layerStyleOverrides()[labels.id()]).writeToLayer(copy)
        self.assertEqual('#333333', copy.labeling().settings().format().color().name())
        svg = Path(self.directory.name) / 'paper.svg'
        exports.export_layout(layout, str(svg), 'svg')
        self.assertTrue('#333333' in svg.read_text(), 'Paper ink is missing from the SVG.')
        self.assertFalse('#eeeeee' in svg.read_text(), 'Dark-canvas ink leaked into the paper SVG.')
        self.assertEqual('#eeeeee', labels.labeling().settings().format().color().name())
        project = QgsProject.instance()
        project.layoutManager().addLayout(layout)
        path = str(Path(self.directory.name) / 'paper.qgz')
        self.assertTrue(project.write(path))
        moved = Path(self.directory.name) / 'moved-project'
        moved.mkdir()
        shutil.copy2(path, moved / 'paper.qgz')
        shutil.copy2(self.path, moved / 'design.gpkg')
        project.clear()
        self.assertTrue(project.read(str(moved / 'paper.qgz')))
        dock.attach(str(moved / 'design.gpkg'))
        saved = project.layoutManager().layouts()[0]
        item = next(item for item in saved.items() if isinstance(item, QgsLayoutItemMap))
        self.assertEqual(len(dock.scene.layers()), len(item.layers()))
        self.assertTrue(item.keepLayerStyles())
        exports.export_layout(saved, str(svg), 'svg')
        self.assertTrue('#333333' in svg.read_text(), 'Reopened layout lost its paper ink.')
        self.assertFalse('#eeeeee' in svg.read_text(), 'Reopened layout used dark-canvas ink.')

    def test_review_settings_first_opened_in_serbian_can_return_to_english(self):
        from fiberq_designer.i18n import localize_tree
        from fiberq_designer.report_dialogs import SettingsDialog
        self.m9c_language('sr')
        dock = self.open_dock()
        dialog = SettingsDialog(dock, self.service.settings())
        tabs = dialog.findChild(QTabWidget)
        for language, expected in (('sr', 'Opste'), ('en', 'General'), ('sr', 'Opste'), ('en', 'General')):
            localize_tree(dialog, language)
            self.assertEqual(expected, tabs.tabText(0))
            self.assertEqual('sr', dialog.language.currentData())
        dialog.reject()

    def test_review_report_notes_share_csv_and_drawing_text_provenance(self):
        # The Console launcher intentionally reloads these modules. Resolve
        # their classes at execution time, as the production UI does.
        from fiberq_designer.i18n import Phrase, report_strings
        from fiberq_designer.report_dialogs import ReportDialog
        dock = self.open_dock()
        start = Phrase('{} / F{} / {}', 'Free', 1, Phrase('cut left'))
        report = dict(title='Optical path', columns=['From', 'To'], rows=[['Free', 'Working']],
                      warnings=[Phrase('Path: {} → {}. Real length is the entered/surveyed value.', start, 'Warning')],
                      text_provenance=True)
        expected = report_strings(report, 'sr')['notes'][0]
        self.assertIn('Free', expected)
        self.assertIn('Warning', expected)
        self.assertNotIn('cut left', expected)
        dialog = ReportDialog(dock, report, 'sr')
        self.assertIn(expected, [label.text() for label in dialog.findChildren(QLabel)])
        dialog.reject()
