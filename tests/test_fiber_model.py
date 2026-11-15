import unittest
from dataclasses import replace

from fiberq_designer.active_layout import ActiveProperties, active_layout
from fiberq_designer.fiber_model import PURPOSES, fiber_spans, validate_fiber_data
from fiberq_designer.domain import connected_ports


class FiberModelTests(unittest.TestCase):
    def test_cut_breaks_graph_and_keeps_first_span_identity(self):
        fibers = [dict(fiber_id='f')]
        ends = [dict(fiber_id='f', side='A', end_id='a', port_id='p'),
                dict(fiber_id='f', side='B', end_id='b', port_id='q')]
        nodes = [dict(node_id='l', fiber_id='f', at=.4), dict(node_id='r', fiber_id='f', at=.6)]
        cuts = [dict(fiber_id='f', left_id='l', right_id='r')]
        spans = fiber_spans('f', ends, cuts, nodes)
        self.assertEqual([('f', 'a', 'l', 0, .4), ('r', 'r', 'b', .6, 1)],
            [(s['span_id'], s['a_id'], s['b_id'], s['start'], s['end']) for s in spans])
        self.assertEqual({'p', 'a', 'l'}, connected_ports('p', [], [], [], fibers, ends, cuts, nodes))
        joined = connected_ports('p', [], [], [], fibers, ends, cuts, nodes, [dict(a_id='l', b_id='r')])
        self.assertIn('q', joined)
        self.assertEqual('f', fiber_spans('f', ends)[0]['span_id'])

    def test_rejects_overlap_and_orphaned_ends(self):
        ends = [dict(fiber_id='f', side='A', end_id='a'), dict(fiber_id='f', side='B', end_id='b')]
        nodes = [dict(node_id=n, at=f) for n, f in (('a1', .2), ('b1', .5), ('a2', .4), ('b2', .6))]
        with self.assertRaises(ValueError):
            fiber_spans('f', ends, [dict(fiber_id='f', left_id='a1', right_id='b1'),
                                   dict(fiber_id='f', left_id='a2', right_id='b2')], nodes)
        with self.assertRaises(ValueError):
            fiber_spans('f', ends[:1])

    def test_purpose_styles_are_distinct_and_metadata_is_validated(self):
        self.assertEqual(10, len(PURPOSES))
        self.assertEqual(10, len({(p.color, p.width) for p in PURPOSES.values()}))
        self.assertEqual(('#ff0000', .30), (PURPOSES['nmWork'].color, PURPOSES['nmWork'].width))
        for purpose in PURPOSES:
            validate_fiber_data(purpose, 'SM', 'Customer "A"')
        for args in (('other', '', ''), ('', 'other', ''), ('', '', 'two\nlines')):
            with self.assertRaises(ValueError):
                validate_fiber_data(*args)

    def test_source_splitter_input_and_output_anchor_have_same_body(self):
        out = active_layout(ActiveProperties(count=8))
        inp = active_layout(ActiveProperties(count=8, anchor='input'))
        self.assertEqual((0, 0), out.ports[1])
        self.assertAlmostEqual(0, inp.ports[0][0])
        self.assertAlmostEqual(0, inp.ports[0][1])
        self.assertAlmostEqual(10.5, out.loss)
        for layout in (out, inp):
            self.assertAlmostEqual(2, layout.frame[1][0] - layout.frame[0][0])
            self.assertAlmostEqual(1.6, layout.frame[0][1] - layout.frame[3][1])
            self.assertAlmostEqual(.8, layout.ports[1][1] - layout.ports[2][1])

    def test_source_olt_and_ont_dimensions_and_connector_reference(self):
        olt = active_layout(ActiveProperties(kind='OLT', count=16))
        self.assertEqual((0, 0), olt.ports[1])
        self.assertEqual((-18, 1.6), olt.frame[0])
        self.assertEqual((-4, 1.6), olt.frame[1])
        self.assertAlmostEqual(-12, olt.ports[16][1])
        ont = active_layout(ActiveProperties(kind='ONT', count=1, address='Customer'))
        self.assertEqual((0, 0), ont.ports[1])
        self.assertEqual((4, 1.3), ont.frame[0])
        self.assertEqual((13, -1.3), ont.frame[2])
        self.assertTrue(any(t[2] == 'Customer' for t in ont.texts))
        for props in (ActiveProperties(count=3), ActiveProperties(kind='OLT', count=0),
                      ActiveProperties(kind='ONT', count=2), replace(ActiveProperties(), address='x\ny')):
            with self.assertRaises(ValueError):
                props.validate()
