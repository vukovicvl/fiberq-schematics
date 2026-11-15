"""The Qt6 gate must reject dry-run findings and incomplete introspection."""

import unittest

from tools.check_qt6 import assess_result


HEADER = '=== dry_run mode | Start Logs ===\n'


class Qt6GateTests(unittest.TestCase):
    def test_zero_exit_with_reported_enum_or_font_warning_fails(self):
        output = (
            HEADER
            + 'plugin/repository.py:63:16 - Enum error, add scope\n'
            + 'plugin/geometry.py:103:13 WARNING: removed font method\n'
        )
        result = assess_result(0, output)
        self.assertFalse(result['passed'])
        self.assertEqual(2, result['finding_count'])

    def test_zero_exit_with_partial_qgis_introspection_fails(self):
        output = (
            'QGIS classes not available for introspection, '
            'only a partial upgrade will be performed\n' + HEADER
        )
        self.assertFalse(assess_result(0, output)['passed'])

    def test_missing_or_failed_checker_is_not_reported_clean(self):
        for returncode, output in (
            (1, 'Traceback (most recent call last):\nImportError: Qt\n'),
            (0, ''),
        ):
            with self.subTest(returncode=returncode, output=output):
                self.assertFalse(assess_result(returncode, output)['passed'])

    def test_clean_dry_run_requires_unchanged_inputs(self):
        self.assertTrue(assess_result(0, HEADER)['passed'])
        self.assertFalse(assess_result(0, HEADER, False)['passed'])


if __name__ == '__main__':
    unittest.main()
