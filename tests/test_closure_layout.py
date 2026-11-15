"""Independent source fixtures for the N/NL enclosure."""

from dataclasses import replace
import unittest

from fiberq_designer.closure_layout import (ClosureProperties, closure_layout, closure_name,
    cross_section, local_point, nearest_segment)


class ClosureLayoutTests(unittest.TestCase):
    def test_source_frame_title_caption_and_no_adapters(self):
        layout = closure_layout(ClosureProperties(location_number='12'), 'N 1', 8.0, 2.0, -10.8)
        self.assertEqual([(0.0, 2.0), (8.0, 2.0), (8.0, -10.8), (0.0, -10.8), (0.0, 2.0)], layout.frame)
        self.assertEqual({}, layout.ports)
        self.assertEqual([], layout.lines)
        self.assertEqual([], layout.dots)
        self.assertEqual(['N 1', 'manhole 12'], [t.text for t in layout.texts])
        self.assertEqual((4.0, 4.6), layout.texts[0].point)
        self.assertEqual(1.08, layout.texts[0].height)
        self.assertAlmostEqual(3.3, layout.texts[1].point[1])
        self.assertEqual(0.648, layout.texts[1].height)

    def test_source_numeric_and_custom_naming(self):
        self.assertEqual('N 3', closure_name('', 'N', ['n 1', 'N 2', 'NL 3']))
        self.assertEqual('NL 1', closure_name('', 'NL', ['N 1']))
        self.assertEqual('NL 3/1', closure_name(' 3/1 ', 'NL'))
        self.assertEqual('N 5-2.1', closure_name('5-2.1'))
        self.assertEqual('Custom closure', closure_name('Custom closure', 'NL'))
        self.assertEqual('NL 7', closure_name('NL 7', 'NL'))
        with self.assertRaises(ValueError):
            closure_name('two\nlines')

    def test_source_cross_section_ignores_ended_fibers_until_fallback(self):
        paths = [[(-5, 0), (5, 0)], [(-5, -0.8), (5, -0.8)], [(-6, 50), (-2, 50)]]
        self.assertEqual((-0.8, 0.0), cross_section(paths))
        self.assertEqual((-0.8, 50), cross_section(paths, 20))
        self.assertEqual((-4, 3), cross_section([[(0, 3), (0, -4)]]))

    def test_local_axes_and_nearest_bent_segment(self):
        _, index, anchor, angle = nearest_segment([(0, 0), (100, 0), (100, 200)], (103, 70))
        self.assertEqual(1, index)
        self.assertEqual((100, 70), anchor)
        self.assertEqual(90, angle)
        for actual, expected in zip(local_point((120, 100), (100, 70), angle), (3, -2)):
            self.assertAlmostEqual(expected, actual)

    def test_bounds_location_and_empty_caption(self):
        props = ClosureProperties()
        for changes in ({'closure_type': 'X'}, {'location': 'unknown'}, {'status': 'bad'}, {'location_number': 'x\ny'}):
            with self.assertRaises(ValueError):
                replace(props, **changes).validate()
        for width in (0, 1.59, float('nan')):
            with self.assertRaises(ValueError):
                closure_layout(props, 'N 1', width, 2, -2)
        layout = closure_layout(replace(props, location=''), 'N 1', 1.6, 2, -2)
        self.assertEqual(1, len(layout.texts))
        self.assertEqual('12', replace(props, location='', location_number='12').caption)


if __name__ == '__main__':
    unittest.main()
