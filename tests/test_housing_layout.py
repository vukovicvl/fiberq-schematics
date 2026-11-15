"""Independent source fixtures for the FiberQ CAD single-module ODF."""

from dataclasses import replace
import unittest

from fiberq_designer.element_layout import housing_layout, odf_layout, transform
from fiberq_designer.housing_catalog import HousingProperties, OdfProperties, housing_name, module_labels, odf_description, odf_name


class HousingLayoutTests(unittest.TestCase):
    def test_odf24_source_geometry(self):
        layout = odf_layout(OdfProperties(), 'OR 1')
        expected_frame = [(-10.6, 1.6), (1.0, 1.6), (1.0, -20.8),
                          (-10.6, -20.8), (-10.6, 1.6)]
        for actual, expected in zip(layout.frame, expected_frame):
            for coordinate, target in zip(actual, expected):
                self.assertAlmostEqual(target, coordinate)
        self.assertEqual(24, len(layout.ports))
        for n, expected in ((1, 0), (12, -8.8), (13, -10.4), (24, -19.2)):
            self.assertAlmostEqual(expected, layout.ports[n][1])
        self.assertEqual(0, layout.lines[0][0])
        for actual, expected in zip(layout.lines[0][1], [(-4.8, 1.6), (-4.8, -20.8)]):
            for coordinate, target in zip(actual, expected):
                self.assertAlmostEqual(target, coordinate)
        self.assertEqual(24, len(layout.dots))
        self.assertEqual(73, len(layout.lines))
        self.assertEqual('OR 1', layout.texts[0].text)
        self.assertEqual([str(n) for n in range(1, 25)], [t.text for t in layout.texts[1:]])
        self.assertEqual('Right', layout.texts[-1].align)

    def test_used_rows_and_long_rack_label(self):
        properties = replace(OdfProperties(), requested_used=3, rack_module='RACK 12 - MODULE 1')
        layout = odf_layout(properties, 'OR 2', rack_width=18)
        self.assertAlmostEqual(-18.4, layout.ports[24][1])
        self.assertAlmostEqual(-24.6, layout.frame[0][0])
        self.assertEqual(24, len(layout.ports))

    def test_capacity_validation_and_names(self):
        for capacity in (12, 24, 48, 72, 96, 144, 288, 576):
            self.assertEqual(capacity, len(odf_layout(OdfProperties(capacity=capacity), 'OR 1').ports))
        for changes in ({'capacity': 13}, {'modules': 0}, {'modules': 25}, {'requested_used': -1}, {'requested_used': 25},
                        {'orientation': 360}, {'status': 'unknown'}, {'model': 'two\nlines'}):
            with self.assertRaises(ValueError):
                replace(OdfProperties(), **changes).validate()
        self.assertEqual('OR 3', odf_name(' ', ['OR 1', 'OR 2']))
        self.assertEqual('OR 3', odf_name('', iter(['OR 2', 'or 1'])))
        self.assertEqual('OR 7', odf_name('7'))
        self.assertEqual('OR 7', odf_name('OR 7'))

    def test_rotated_anchor_transform(self):
        self.assertEqual((110, 180), transform((1, -2), (100, 200)))
        for result, expected in zip(transform((1, -2), (100, 200), 90), (120, 210)):
            self.assertAlmostEqual(expected, result)

    def test_two_module_source_frames_labels_and_local_numbers(self):
        layout = odf_layout(OdfProperties(modules=2), 'OR 1')
        self.assertEqual(48, len(layout.ports))
        self.assertEqual(2, len(layout.module_frames))
        self.assertAlmostEqual(-19.6, layout.module_frames[0][2][1])
        self.assertAlmostEqual(-20.4, layout.module_frames[1][0][1])
        self.assertAlmostEqual(-41.6, layout.module_frames[1][2][1])
        self.assertEqual(['OR 1', 'M1', 'M2'], [t.text for t in layout.texts[:3]])
        self.assertEqual([str(n) for n in range(1, 25)] * 2, [t.text for t in layout.texts[3:]])
        self.assertEqual(['R1, M8', 'R1, M9'], module_labels('R1, M8', 2))
        self.assertEqual(['R1, M1', 'R1, M2'], module_labels('R1', 2))
        self.assertEqual([''], module_labels('', 1))

    def test_inherited_rows_spares_and_mirroring(self):
        rows = tuple(-0.8 * i - 0.8 * (i // 6) for i in range(48))
        layout = odf_layout(OdfProperties(row_positions=rows, requested_used=0), 'OR 1')
        self.assertAlmostEqual(-5.6, layout.ports[7][1])
        self.assertAlmostEqual(rows[-1] - 1.6, layout.frame[2][1])
        self.assertEqual((206, 200), transform((-10.6, 0), (100, 200), mirrored=True))

    def test_maximum_modular_odf_layout(self):
        layout = odf_layout(OdfProperties(capacity=576, modules=24), 'OR 1')
        self.assertEqual(13824, len(layout.ports))
        self.assertEqual(24, len(layout.module_frames))
        self.assertEqual('576', layout.texts[-1].text)

    def test_patch_panel_catalogue_prefix_and_independent_names(self):
        for capacity in (12, 24, 48, 72, 96, 144):
            props = HousingProperties(kind='PATCH_PANEL', capacity=capacity)
            self.assertEqual(capacity, len(odf_layout(props, 'PP 1').ports))
        for capacity in (13, 288, 576):
            with self.assertRaises(ValueError):
                HousingProperties(kind='PATCH_PANEL', capacity=capacity).validate()
        self.assertEqual('PP 1', housing_name('', 'PATCH_PANEL', ['OR 1']))
        self.assertEqual('PP 3', housing_name('', 'PATCH_PANEL', ['PP 2', 'pp 1', 'OR 3']))
        self.assertEqual('PP 7', housing_name('PP 7', 'PATCH_PANEL'))
        self.assertEqual('PP 7', housing_name('7', 'PATCH_PANEL'))
        self.assertIn('PP <auto>', odf_description('', HousingProperties(kind='PATCH_PANEL')))

    def test_patch_panel_two_module_source_fixture_and_maximum(self):
        props = HousingProperties(kind='PATCH_PANEL', modules=2)
        layout = odf_layout(props, 'PP 1')
        self.assertEqual(['PP 1', 'M1', 'M2'], [t.text for t in layout.texts[:3]])
        self.assertEqual([str(n) for n in range(1, 25)] * 2, [t.text for t in layout.texts[3:]])
        self.assertAlmostEqual(-19.6, layout.module_frames[0][2][1])
        self.assertAlmostEqual(-20.4, layout.module_frames[1][0][1])
        self.assertAlmostEqual(-41.6, layout.module_frames[1][2][1])
        maximum = odf_layout(replace(props, capacity=144, modules=24), 'PP 2')
        self.assertEqual(3456, len(maximum.ports))
        self.assertEqual(24, len(maximum.module_frames))

    def test_cabinet_source_frame_and_descending_ports(self):
        props = HousingProperties(kind='FDH', mounting='indoor', capacity=48, requested_used=4)
        layout = housing_layout(props, 'FDH 1')
        self.assertEqual({45, 46, 47, 48}, layout.visible_ports)
        for n, y in ((48, 0.0), (47, -0.8), (46, -1.6), (45, -2.4)):
            self.assertAlmostEqual(y, layout.ports[n][1])
        self.assertEqual((-11.5, 2.0), layout.frame[0])
        self.assertAlmostEqual(-4.4, layout.frame[2][1])
        self.assertEqual((0.5, 2.0), layout.frame[1])
        self.assertEqual([(0, [(-8.7, 2.0), (-8.7, -4.4)])], layout.lines[:1])
        self.assertEqual(['FDH 1 (48)', '48', 'indoor', '48', '47', '46', '45'], [t.text for t in layout.texts])
        self.assertEqual(48, len(layout.ports))
        default = housing_layout(replace(props, requested_used=None), 'FDH 1')
        self.assertEqual(set(range(43, 49)), default.visible_ports)
        empty = housing_layout(replace(props, requested_used=0), 'FDH 1')
        self.assertEqual(set(), empty.visible_ports)
        self.assertEqual(0, len(empty.dots))

    def test_fdh12_splitter_source_fixture(self):
        props = HousingProperties(kind='FDH', mounting='indoor', capacity=12,
                                  requested_used=1, splitter_ratio=8)
        layout = housing_layout(props, 'FDH 1')
        self.assertEqual(set(range(1, 9)) | {12}, layout.visible_ports)
        self.assertAlmostEqual(8.4, layout.frame[0][1])
        self.assertAlmostEqual(-2.0, layout.frame[2][1])
        for n in range(1, 9):
            self.assertAlmostEqual(0.8 * (9 - n), layout.ports[n][1])
        self.assertEqual((0.0, 0.0), layout.ports[12])
        self.assertEqual(['1:8', '10.5 dB'], [t.text for t in layout.splitter.texts])
        self.assertAlmostEqual(-4.6, layout.splitter.frame[0][0])
        self.assertAlmostEqual(4.4, layout.splitter.frame[0][1])
        self.assertAlmostEqual(3.6, layout.splitter.ports[0][1])
        self.assertEqual((0.0, 0.0), layout.splitter.paths[0][0])

    def test_cabinet_catalogues_mdu_and_splitter_overflow(self):
        for kind, capacities in (('FDH', (12, 24, 48, 72, 96)), ('MDU', (12, 24, 48, 72, 96, 144))):
            for capacity in capacities:
                props = HousingProperties(kind=kind, mounting='free-standing', capacity=capacity, address='Entrance 2')
                layout = housing_layout(props, kind + ' 1')
                self.assertEqual(capacity, len(layout.ports))
                self.assertEqual('Entrance 2', layout.texts[1].text)
            self.assertEqual(kind + ' 2', housing_name('', kind, [kind + ' 1', 'OR 2', 'PP 2']))
        for changes in ({'splitter_ratio': 16}, {'splitter_ratio': 8, 'requested_used': 5},
                        {'modules': 2}, {'rack_module': 'M1'}, {'mounting': 'outdoor', 'kind': 'MDU'},
                        {'address': 'two\nlines'}, {'splitter_feed': 13}):
            with self.assertRaises(ValueError):
                replace(HousingProperties(kind='FDH', mounting='indoor', capacity=12), **changes).validate()

    def test_fat_all_capacities_and_source_frame(self):
        for capacity in (1, 2, 4, 6, 8, 12, 16, 24):
            props = HousingProperties(kind='FAT', mounting='wall', capacity=capacity)
            layout = housing_layout(props, 'FAT 1')
            self.assertEqual(set(range(1, capacity + 1)), set(layout.ports))
            self.assertEqual(capacity, len(layout.dots))
            self.assertEqual(3 * capacity, len(layout.lines))
            self.assertEqual((0.0, 0.0), layout.ports[capacity])
            self.assertEqual((-5.2, 1.5), layout.frame[0])
            self.assertEqual((1.0, 1.5), layout.frame[1])
        props = HousingProperties(kind='FAT', mounting='wall', capacity=12, requested_used=4, address='Entrance 2')
        layout = housing_layout(props, 'FAT 1')
        self.assertAlmostEqual(-10.3, layout.frame[2][1])
        self.assertEqual(['Entrance 2', 'FAT 1', '12xSC/APC', 'wall'], [t.text for t in layout.texts[:4]])
        self.assertAlmostEqual(7.38, layout.texts[0].point[1])
        self.assertAlmostEqual(5.14, layout.texts[1].point[1])
        self.assertAlmostEqual(3.04, layout.texts[2].point[1])
        self.assertEqual([str(n) for n in range(12, 0, -1)], [t.text for t in layout.texts[4:]])
        self.assertEqual('Right', layout.texts[-1].align)
        self.assertEqual(0.588, layout.texts[-1].height)

    def test_fat12_splitter_source_feed_outputs_and_spares(self):
        props = HousingProperties(kind='FAT', mounting='pole', capacity=12, splitter_ratio=8)
        layout = housing_layout(props, 'FAT 1')
        self.assertAlmostEqual(-9.8, layout.frame[0][0])
        self.assertAlmostEqual(10.3, layout.frame[0][1])
        self.assertAlmostEqual(-1.5, layout.frame[2][1])
        for n, y in ((1, 8.8), (8, 3.2), (9, 2.4), (10, 1.6), (11, 0.8), (12, 0.0)):
            self.assertAlmostEqual(y, layout.ports[n][1])
        self.assertEqual(12, len(layout.dots))
        self.assertEqual(['1:8', '10.5 dB'], [t.text for t in layout.splitter.texts])
        self.assertAlmostEqual(6.0, layout.splitter.ports[0][1])
        self.assertAlmostEqual(-0.92418, layout.splitter.paths[0][1][0])
        self.assertAlmostEqual(-4.6, layout.splitter.frame[0][0])
        self.assertAlmostEqual(6.8, layout.splitter.frame[0][1])

    def test_fat_inherited_rows_continue_only_after_used_adapters(self):
        props = HousingProperties(kind='FAT', mounting='indoor', capacity=24, requested_used=4,
                                  row_positions=(0.0, -1.0, -2.0, -4.0, -8.0, -12.0))
        layout = housing_layout(props, 'FAT 1')
        self.assertEqual([0.0, -1.0, -2.0, -4.0], [layout.ports[n][1] for n in range(24, 20, -1)])
        self.assertAlmostEqual(-4.8, layout.ports[20][1])
        self.assertAlmostEqual(-20.0, layout.ports[1][1])
        default = housing_layout(replace(props, row_positions=(), requested_used=None), 'FAT 1')
        self.assertAlmostEqual(-19.2, default.ports[1][1])
        empty = housing_layout(replace(props, row_positions=(), requested_used=0), 'FAT 1')
        self.assertAlmostEqual(-18.4, empty.ports[1][1])

    def test_fat_catalogue_validation_and_prefix(self):
        props = HousingProperties(kind='FAT', mounting='wall', capacity=12)
        self.assertEqual('FAT 2', housing_name('', 'FAT', ['FAT 1', 'FDH 2']))
        self.assertEqual('FAT 7', housing_name('FAT 7', 'FAT'))
        self.assertIn('with splitter 1:8', odf_description('', replace(props, splitter_ratio=8)))
        for changes in ({'capacity': 3}, {'capacity': 48}, {'modules': 2}, {'rack_module': 'M1'},
                        {'mounting': 'outdoor'}, {'splitter_ratio': 16}, {'splitter_ratio': 8, 'requested_used': 2},
                        {'capacity': 8, 'splitter_ratio': 2}):
            with self.assertRaises(ValueError):
                replace(props, **changes).validate()

    def test_otb_uses_the_confirmed_fat_family_with_own_prefix(self):
        for capacity in (1, 2, 4, 6, 8, 12, 16, 24):
            fat = HousingProperties(kind='FAT', mounting='wall', capacity=capacity)
            normal = housing_layout(fat, 'FAT 1')
            otb = housing_layout(replace(fat, kind='OTB'), 'OTB 1')
            self.assertEqual(normal.frame, otb.frame)
            self.assertEqual(normal.ports, otb.ports)
            self.assertEqual(normal.lines, otb.lines)
            self.assertEqual(normal.dots, otb.dots)
        split_fat = housing_layout(replace(fat, splitter_ratio=8), 'FAT 1')
        split_otb = housing_layout(replace(fat, kind='OTB', splitter_ratio=8), 'OTB 1')
        self.assertEqual(split_fat.splitter, split_otb.splitter)
        self.assertEqual('OTB 1', housing_name('', 'OTB', ['FAT 1']))
        self.assertEqual('OTB 2', housing_name('', 'OTB', ['FAT 2', 'OTB 1']))
        self.assertEqual('OTB 7', housing_name('OTB 7', 'OTB'))


if __name__ == '__main__':
    unittest.main()
