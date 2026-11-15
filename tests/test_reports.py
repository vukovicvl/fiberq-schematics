import json
import math
import unittest
from copy import deepcopy

from fiberq_designer.annotation_layout import legend_payload, legend_primitives, table_payload, validate_annotation
from fiberq_designer.cable_catalog import CableProperties
from fiberq_designer.i18n import translate
from fiberq_designer.reports import find_path, optical_graph
from fiberq_designer.workspace_model import DEFAULT_SETTINGS, budget_calculation, validate_settings


class ReportTests(unittest.TestCase):
    def test_source_budget_sm_and_mm_independent_arithmetic(self):
        sm = budget_calculation(2, 8, 4, [7.3, 10.5])
        self.assertAlmostEqual(23.28, sm['total_db'])
        self.assertTrue(sm['b_plus'])
        mm = budget_calculation(2, 8, 4, [7.3, 10.5], 'MM', 850)
        self.assertAlmostEqual(28.8, mm['total_db'])
        self.assertFalse(mm['b_plus'])
        self.assertTrue(mm['c_plus'])
        self.assertEqual(.22, budget_calculation(1, 0, 0, [], 'SM', 1550)['attenuation'])

    def test_budget_thresholds_negative_nonfinite_and_type_validation(self):
        settings = deepcopy(DEFAULT_SETTINGS)
        settings['margin_db'] = 0
        self.assertTrue(budget_calculation(0, 0, 0, [28], settings=settings)['b_plus'])
        self.assertFalse(budget_calculation(0, 0, 0, [28.001], settings=settings)['b_plus'])
        for args in ((-1, 0, 0), (math.nan, 0, 0), (1, -1, 0), (1, 1.5, 0)):
            with self.assertRaises(ValueError):
                budget_calculation(*args)
        with self.assertRaises(ValueError):
            budget_calculation(1, 0, 0, [], 'MM', 1490)

    def test_settings_reject_duplicate_catalogues_invalid_sequences_and_nan(self):
        for key, value in (('snap_pixels', 0), ('margin_db', math.nan), ('language', 'xx'), ('snap', 'true')):
            settings = deepcopy(DEFAULT_SETTINGS)
            settings[key] = value
            with self.assertRaises(ValueError):
                validate_settings(settings)
        settings = deepcopy(DEFAULT_SETTINGS)
        settings['color_codes'] = [dict(name='BAD', colors=[12], suffixes=['-p'])]
        with self.assertRaises(ValueError):
            validate_settings(settings)
        settings = deepcopy(DEFAULT_SETTINGS)
        settings['attenuation']['SM'] = []
        with self.assertRaises(ValueError):
            validate_settings(settings)
        payload = legend_payload(deepcopy(DEFAULT_SETTINGS))
        payload['color_code'] = {'invalid': 'value'}
        with self.assertRaises(ValueError):
            validate_annotation('legend', payload)

    def test_custom_cable_definition_is_self_contained_and_group_suffixes_follow_sequence(self):
        definition = json.dumps(dict(capacities=[[2, 6]], colors=[6, 2, 0], suffixes=['-a', '-b']))
        props = CableProperties(model='CUSTOM', catalog_model='CUSTOM', tubes=2, fibers_per_tube=6,
                                color_code='CUSTOM', definition=definition).validate()
        self.assertEqual(('#ff0000', 'Red'), props.color(1))
        self.assertEqual(('#ff0000', 'Red-a'), props.color(4))
        self.assertEqual(('#ff0000', 'Red-b'), props.color(7))
        self.assertEqual(('#ff0000', 'Red-3p'), props.color(10))
        self.assertEqual(definition, props.attributes()['catalogue_data'])

    def test_unique_path_and_disconnected_or_parallel_loop_rejection(self):
        graph = {'a': [('b', 'ab')], 'b': [('a', 'ab'), ('c', 'bc')], 'c': [('b', 'bc')], 'x': []}
        edges = {key: dict(edge_id=key) for key in ('ab', 'bc')}
        self.assertEqual(['a', 'b', 'c'], find_path(graph, edges, 'a', 'c')[0])
        with self.assertRaisesRegex(ValueError, 'No continuous'):
            find_path(graph, edges, 'a', 'x')
        graph['a'].append(('c', 'ac'))
        graph['c'].append(('a', 'ac'))
        edges['ac'] = dict(edge_id='ac')
        with self.assertRaisesRegex(ValueError, 'More than one'):
            find_path(graph, edges, 'a', 'c')

    def test_legend_uses_active_color_code_source_pitch_and_all_purposes(self):
        settings = deepcopy(DEFAULT_SETTINGS)
        settings['color_code'] = 'IEC 60304'
        payload = legend_payload(settings)
        validate_annotation('legend', payload)
        lines, texts, dots = legend_primitives(payload)
        self.assertIn(([(0, 0), (110, 0)], '', .13), lines)
        self.assertTrue(any(entry[2] == '1.Red' and entry[5] == '#ff0000' for entry in texts))
        self.assertEqual(6, len(dots))
        self.assertTrue(any(ink == '#00a57c' for points, ink, width in lines))

    def test_annotation_validation_and_language_snapshot(self):
        report = dict(title='Bill of materials', columns=['Item', 'Quantity'], rows=[['Fiber', '12']], warnings=[])
        saved = table_payload(report, 'sr')
        self.assertEqual('Specifikacija materijala', saved['title'])
        self.assertEqual('Vlakno', saved['rows'][0][0])
        validate_annotation('bom', saved)
        saved['rows'][0].append('extra')
        with self.assertRaises(ValueError):
            validate_annotation('bom', saved)

    def test_ui_translation_keeps_identifiers_and_user_labels(self):
        self.assertEqual('Podesavanja', translate('Settings', 'sr'))
        self.assertEqual('Opticki budzet…', translate('Optical budget…', 'sr'))
        self.assertEqual('Customer Building 12', translate('Customer Building 12', 'sr'))
        self.assertEqual('Settings', translate('Settings', 'en'))


if __name__ == '__main__':
    unittest.main()
