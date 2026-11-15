"""Pure network rules run with standard Python, without QGIS."""

import unittest

from fiberq_designer.domain import (connected_ports, port_specs,
                                    validate_connection, validate_vertices)


class DomainTests(unittest.TestCase):
    def test_catalog_capacity_and_unique_port_numbers(self):
        for kind in ('CABLE', 'ODF', 'CLOSURE', 'CABINET', 'FDH', 'FAT', 'PATCH_PANEL', 'OLT', 'ONT'):
            for count in (1, 12, 24, 97, 576):
                specs = port_specs(kind, count)
                self.assertEqual(count, len(specs))
                self.assertEqual(count, len({p.number for p in specs}))
        specs = port_specs('SPLITTER', 8)
        self.assertEqual(['input'] + ['output'] * 8, [p.role for p in specs])

    def test_invalid_capacity_and_route_rejected(self):
        for kind, count in [('ODF', 0), ('CLOSURE', 577), ('SPLITTER', 7), ('UNKNOWN', 12)]:
            with self.assertRaises(ValueError):
                port_specs(kind, count)
        for route in ([], [(0, 0)], [(0, 0), (0, 0)], [(0, 0), (float('nan'), 1)]):
            with self.assertRaises(ValueError):
                validate_vertices(route)

    def test_closure_internal_splices_and_occupancy(self):
        a = {'port_id': 'a', 'obj_id': 'closure', 'role': 'splice'}
        b = {'port_id': 'b', 'obj_id': 'closure', 'role': 'splice'}
        validate_connection(a, b, {'closure': 'CLOSURE'}, set())
        for other, kinds, occupied in [(a, {'closure': 'CLOSURE'}, set()),
                                       (b, {'closure': 'ODF'}, set()),
                                       (b, {'closure': 'CLOSURE'}, {'a'})]:
            with self.assertRaises(ValueError):
                validate_connection(a, other, kinds, occupied)

    def test_trace_follows_cable_splice_and_splitter_branches(self):
        ports = [{'port_id': p, 'obj_id': 'split', 'role': role}
                 for p, role in [('in', 'input'), ('out1', 'output'), ('out2', 'output')]]
        ports += [{'port_id': 'start', 'obj_id': 'odf', 'role': 'terminal'},
                  {'port_id': 'end', 'obj_id': 'ont', 'role': 'input'}]
        trace = connected_ports('start', ports, [{'obj_id': 'split', 'kind': 'SPLITTER'}],
                                [{'from_id': 'out1', 'to_id': 'end'}],
                                [{'a_id': 'start', 'b_id': 'in'}])
        self.assertEqual({'start', 'in', 'out1', 'out2', 'end'}, trace)


if __name__ == '__main__':
    unittest.main()

