"""Language parity, text provenance and controlled-message coverage."""

from string import Formatter
import unittest

from fiberq_designer.housing_catalog import housing_name
from fiberq_designer.closure_layout import ClosureProperties
from fiberq_designer.i18n import Phrase, SR, message, report_strings, translate
from translation_inventory import inventory


class LanguageTests(unittest.TestCase):
    def test_source_default_prefixes_and_explicit_names(self):
        for kind, english, serbian in (('FDH', 'FDH ', 'ODO '), ('FAT', 'FAT ', 'ZOK '),
                ('MDU', 'MDU ', 'MDU '), ('OTB', 'OTB ', 'OTB '), ('ODF', 'OR ', 'OR '), ('PATCH_PANEL', 'PP ', 'PP ')):
            self.assertEqual(english + '1', housing_name('', kind, language='en'))
            self.assertEqual(serbian + '2', housing_name('', kind, [serbian + '1'], language='sr'))
            self.assertEqual(english + 'Free', housing_name(english + 'Free', kind, language='sr'))
            self.assertEqual(serbian + 'Working', housing_name(serbian + 'Working', kind, language='en'))

    def test_templates_preserve_customer_values_braces_and_paths(self):
        value = 'Free {x} / Warning & Working / Čačak'
        self.assertEqual('Izmeni ' + value, message('Edit {}', value, language='sr'))
        self.assertEqual('Deonica kabla 2 je prekratka za vlakno 7.', translate('Cable segment 2 is too short for fiber 7.', 'sr'))
        self.assertEqual('Vec postoji objekat sa nazivom: ' + value, translate('An object already has this name: ' + value, 'sr'))
        self.assertEqual('Sacuvano /tmp/Working.gpkg', translate('Saved /tmp/Working.gpkg', 'sr'))
        self.assertEqual('&Da', translate('&Yes', 'sr'))
        self.assertEqual('Oznaka:', translate('Label:', 'sr'))

    def test_report_provenance_translates_system_text_only(self):
        spec = Phrase('{} ports', 24) + ' · ' + 'Free' + ' · ' + Phrase('indoor')
        report = dict(title='Bill of materials', columns=['Item', 'Specification', 'Status'],
            rows=[[Phrase('FDH'), spec, Phrase('designed')], ['Working', 'Warning', 'Free']],
            warnings=[Phrase('Path: {} → {}. Real length is the entered/surveyed value.', 'Free {x}', 'Working')], text_provenance=True)
        rendered = report_strings(report, 'sr')
        self.assertEqual(['ODO', '24 portova · Free · unutra', 'projektovano'], rendered['rows'][0])
        self.assertEqual(['Working', 'Warning', 'Free'], rendered['rows'][1])
        self.assertIn('Free {x} → Working', rendered['notes'][0])
        self.assertEqual('24 ports · Free · indoor', spec)

    def test_catalog_templates_keep_placeholder_names_and_formats(self):
        def fields(value):
            result, automatic = [], 0
            for literal, field, spec, conversion in Formatter().parse(value):
                if field is not None:
                    result.append((field or str(automatic), spec, conversion))
                    automatic += field == ''
            return sorted(result)
        for source, target in SR.items():
            self.assertEqual(fields(source), fields(target), source)

    def test_controlled_messages_and_diagnostics_have_no_missing_translation(self):
        rows = inventory()
        self.assertGreater(len(rows), 700)
        self.assertEqual([], [(file, line, text) for file, line, text, state, value in rows if state == 'missing'])

    def test_closure_caption_keeps_entered_location_number(self):
        props = ClosureProperties(location='manhole', location_number='Working Čačak')
        self.assertEqual('manhole Working Čačak', props.caption)
        self.assertEqual('okno Working Čačak', props.caption_in('sr'))
