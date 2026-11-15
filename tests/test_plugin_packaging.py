"""Pure checks for reproducible plugin builds and the upload boundary."""

from configparser import ConfigParser
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from tools.build_plugin import ROOT, build, package_sources
from tools.validate_package import validate


class PluginPackagingTests(unittest.TestCase):
    def test_reproducible_zip_contains_identical_allowlisted_sources(self):
        with tempfile.TemporaryDirectory(prefix='fiberq-package-') as tmp:
            output = Path(tmp)
            path = build(output)
            first = path.read_bytes()
            build(output)
            self.assertEqual(first, path.read_bytes())
            manifest = json.loads(
                (output / 'package-manifest.json').read_text()
            )
            self.assertEqual(
                hashlib.sha256(first).hexdigest(), manifest['sha256']
            )
            sources = package_sources()
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(len(sources), len(archive.namelist()))
                for relative, source in sources.items():
                    member = 'fiberq_schematics/' + relative
                    data = archive.read(member)
                    self.assertEqual(source.read_bytes(), data, member)
                    self.assertEqual(
                        hashlib.sha256(data).hexdigest(),
                        manifest['source_files'][member],
                    )
            self.assertEqual('passed', validate(path)['status'])

    def test_validation_rejects_unsafe_archive_paths_and_permissions(self):
        for member, mode in (
            ('fiberq_schematics/../escape.py', stat.S_IFREG | 0o644),
            ('fiberq_schematics/.secrets.baseline', stat.S_IFREG | 0o644),
            ('fiberq_schematics/link.py', stat.S_IFLNK | 0o777),
            ('fiberq_schematics/run.py', stat.S_IFREG | 0o755),
            ('fiberq_schematics/cache.pyc', stat.S_IFREG | 0o644),
        ):
            with self.subTest(member=member):
                with tempfile.TemporaryDirectory(
                    prefix='fiberq-bad-zip-'
                ) as tmp:
                    path = Path(tmp) / 'invalid.zip'
                    with zipfile.ZipFile(path, 'w') as archive:
                        info = zipfile.ZipInfo(member)
                        info.create_system = 3
                        info.external_attr = mode << 16
                        archive.writestr(info, b'example')
                    with self.assertRaises(ValueError):
                        validate(path)

    def test_release_identity_icon_and_help_match_supplied_details(self):
        parser = ConfigParser(interpolation=None)
        parser.read(ROOT / 'metadata.txt', encoding='utf-8')
        metadata = parser['general']
        expected = dict(
            name='FiberQ Schematics',
            author='Vladimir Vukovic',
            version='1.0.0',
            email='vukovicvl@fiberq.net',
            homepage='https://www.fiberq.net/fiberq-schematics/',
        )
        for field, value in expected.items():
            self.assertEqual(value, metadata[field])
        self.assertEqual(
            b'\x89PNG\r\n\x1a\n', (ROOT / 'icon.png').read_bytes()[:8]
        )
        html = (ROOT / 'help/index.html').read_text()
        for value in (
            metadata['homepage'],
            metadata['tracker'],
            metadata['repository'],
            'https://github.com/sponsors/vukovicvl',
            metadata['email'],
        ):
            self.assertIn(value, html)
        self.assertNotIn('fiberq_schematic_designer.py', package_sources())


if __name__ == '__main__':
    unittest.main()
