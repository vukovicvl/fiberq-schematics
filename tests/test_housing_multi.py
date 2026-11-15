import json
import unittest
from dataclasses import replace

from fiberq_designer.element_layout import housing_layout
from fiberq_designer.housing_catalog import OdfProperties, HousingProperties
from fiberq_designer.housing_multi import read_layout_state


class MultiHousingTests(unittest.TestCase):
    def test_rack_rows_can_expand_upward_without_moving_the_original_connector(self):
        props = OdfProperties(capacity=12, requested_used=4)
        base = housing_layout(props, 'OR 1')
        rows = [0, -.8, -1.6, -2.4, 10, 9.2, 8.4, 7.6, -3.2, -4, -4.8, -5.6]
        state = json.dumps(dict(version=1, rows=[rows], visible=list(range(1, 13)), bounds=[[11.6, -11.2]]))
        expanded = housing_layout(replace(props, layout_state=state), 'OR 1')
        self.assertEqual(base.ports[1], expanded.ports[1])
        self.assertEqual((0, 10), expanded.ports[5])
        self.assertAlmostEqual(11.6, expanded.frame[0][1])
        self.assertTrue(any(text.text == 'OR 1' and abs(text.point[1] - 17.9) < 1e-6 for text in expanded.texts))

    def test_cabinet_keeps_only_saved_visible_connectors_and_source_glyph_dimensions(self):
        props = HousingProperties(kind='FDH', capacity=12, mounting='indoor', requested_used=2)
        rows = [-.8 * (11 - i) for i in range(12)]
        rows[9] = -20
        state = json.dumps(dict(version=1, rows=[rows], visible=[12, 11, 10], bounds=[[2, -22]]))
        expanded = housing_layout(replace(props, layout_state=state), 'FDH 1')
        self.assertEqual({10, 11, 12}, expanded.visible_ports)
        self.assertEqual((0, -20), expanded.ports[10])
        glyph = next(points for number, points in expanded.lines if number == 10)
        self.assertAlmostEqual(.91, glyph[1][0] - glyph[0][0])
        self.assertAlmostEqual(.754, glyph[0][1] - glyph[2][1])
        self.assertEqual({'10', '11', '12'}, {t.text for t in expanded.texts if abs(t.height - .3888) < 1e-8})

    def test_overlapping_module_frames_or_connector_rows_are_rejected(self):
        for state, capacity, modules in (
                (dict(version=1, rows=[[0, 0]], visible=[1, 2], bounds=[[2, -2]]), 2, 1),
                (dict(version=1, rows=[[0], [-1]], visible=[1, 2], bounds=[[2, -2], [0, -3]]), 1, 2),
                (dict(version=1, rows=[[0]], visible=[2], bounds=[[2, -2]]), 1, 1),
                (dict(version=1, rows=[[0]], visible=[1], bounds=[[float('nan'), -2]]), 1, 1)):
            with self.assertRaises(ValueError):
                read_layout_state(json.dumps(state), capacity, modules)


if __name__ == '__main__':
    unittest.main()
