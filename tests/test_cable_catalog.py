import unittest
from dataclasses import replace

from fiberq_designer.cable_catalog import CATALOGUE, CableProperties, cable_rows, color


class CableCatalogueTests(unittest.TestCase):
    def test_source_catalogue_exact_capacities_and_model_order(self):
        self.assertEqual(['kbAerial', 'kbDuct', 'kbMicro', 'kbIndoor'], list(CATALOGUE))
        self.assertEqual(['BUTTERFLY H', 'TOMM 03', 'RISER H'], list(CATALOGUE['kbIndoor']))
        self.assertEqual((36, 12), CATALOGUE['kbAerial']['ADSS 03'][-1])
        self.assertNotIn((10, 24), CATALOGUE['kbAerial']['ADSS 03'])
        self.assertIn((10, 24), CATALOGUE['kbDuct']['TOSM 03'])
        self.assertEqual(((1, 1), (1, 2)), CATALOGUE['kbIndoor']['BUTTERFLY H'])
        for category, models in CATALOGUE.items():
            for model, capacities in models.items():
                for tubes, fibers in capacities:
                    props = CableProperties(category, model, model, tubes, fibers).validate()
                    self.assertEqual(tubes * fibers, len(props.rows))
                    self.assertEqual(0, props.rows[0])
                    self.assertEqual(-8 * (tubes * fibers - 1) - 8 * (tubes - 1), props.rows[-1])

    def test_two_by_six_and_one_by_twelve_are_distinct(self):
        two = CableProperties(tubes=2, fibers_per_tube=6)
        one = CableProperties()
        self.assertEqual(one.count, two.count)
        self.assertEqual(-56, two.rows[6])
        self.assertEqual(-48, one.rows[6])
        self.assertNotEqual(one.attributes(), two.attributes())

    def test_inherited_grid_and_extension_follow_tube_boundaries(self):
        source = cable_rows(12, 6)
        self.assertEqual(source, cable_rows(12, 12, source))
        self.assertEqual([0, -8, -24, -32, -48, -56], cable_rows(6, 2, [0, -8, -24]))
        for invalid in ([1], [0, 0], [0, float('nan')], [0, 8]):
            with self.assertRaises(ValueError):
                cable_rows(12, 12, invalid)

    def test_source_color_sequences_restart_in_each_tube_and_ring_groups(self):
        self.assertEqual('Blue', color(1)[1])
        self.assertEqual('#0000ff', color(1)[0])
        self.assertEqual('#7f3f00', color(4)[0])
        self.assertEqual('#ff7fdf', color(11)[0])
        self.assertEqual('Red', color(1, 'IEC 60304')[1])
        self.assertEqual('Green', color(2, 'IEC 60304')[1])
        self.assertEqual('Blue-p', color(13)[1])
        self.assertEqual('Blue-2p', color(25)[1])
        self.assertEqual(color(1)[0], color(13)[0])

    def test_custom_model_inherits_catalogue_and_bad_capacity_is_rejected(self):
        self.assertEqual('Custom "quoted" model (1x12)', replace(CableProperties(), model='Custom "quoted" model').validate().description)
        for changes in ({'category': 'other'}, {'tubes': 2, 'fibers_per_tube': 5}, {'model': '\n'},
                        {'color_code': 'unknown'}, {'fiber_type': 'other'}, {'status': 'other'},
                        {'row_positions': (0,) * 13}):
            with self.assertRaises(ValueError):
                replace(CableProperties(), **changes).validate()
