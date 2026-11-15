"""Independent FQIZVOD coordinate and repeated-column fixtures."""

import unittest

from fiberq_designer.outlet_layout import occupied_left, outlet_column, outlet_layout


class OutletLayoutTests(unittest.TestCase):
    def test_two_tubes_elbow_stagger_and_common_end_line(self):
        rows = [0, -.8, -1.6, -3.2, -4, -4.8]
        drawing = outlet_layout(rows, -10, -2, 12)
        expected_columns = [-6.8, -6, -5.2, -3.6, -2.8, -2]
        for row, x, path in zip(rows, expected_columns, drawing['paths']):
            for actual, expected in zip(path, [(x, -26.8), (x, -10 + row), (0, -10 + row)]):
                self.assertAlmostEqual(expected[0], actual[0])
                self.assertAlmostEqual(expected[1], actual[1])
        self.assertEqual('B', drawing['frame_side'])

    def test_straight_stagger_reverses_columns_and_has_no_elbow(self):
        drawing = outlet_layout([0, -.8, -2.4], 0, 4, 40, elbow=False, column=6)
        self.assertEqual([(4, 0), (4, -42.4)], drawing['paths'][0])
        self.assertAlmostEqual(1.6, drawing['paths'][2][0][0])
        self.assertAlmostEqual(-2.4, drawing['paths'][2][0][1])
        self.assertEqual('A', drawing['frame_side'])

    def test_repeated_column_search_chains_only_from_inside_frame(self):
        self.assertIsNone(occupied_left([-100, -30], -12, 2))
        self.assertEqual(-14, occupied_left([-2, -7, -12, -14, -40], -12, 2))
        self.assertAlmostEqual(-15.6, outlet_column(0, -14))
        self.assertEqual(-2, outlet_column(0))

    def test_bad_rows_and_short_nonfinite_lengths_are_rejected(self):
        for rows, length in (([], 12), ([1, 0], 12), ([0, 0], 12), ([0, -1], 11.9), ([0, -1], float('nan'))):
            with self.assertRaises(ValueError):
                outlet_layout(rows, 0, -2, length)
