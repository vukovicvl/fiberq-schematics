"""Build a reproducible QGIS upload ZIP from an explicit file allowlist."""

import argparse
from configparser import ConfigParser
import hashlib
import json
from pathlib import Path
import shutil
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'fiberq_schematics'
ROOT_FILES = (
    '__init__.py',
    'plugin.py',
    'plugin_info.py',
    'metadata.txt',
    'icon.png',
    'LICENSE',
    'FIBERQ_CAD_LICENSE.txt',
    'CHANGELOG.md',
    'example_connections.csv',
)


def package_sources(root=ROOT):
    """Map archive-relative names to the unchanged source files."""
    result = {name: root / name for name in ROOT_FILES}
    result['README.md'] = root / 'README_PLUGIN.md'
    for folder, allowed in (
        ('fiberq_designer', {'.py', '.json', '.bmp', '.md'}),
        ('help', {'.html', '.png', '.svg'}),
    ):
        for path in sorted((root / folder).rglob('*')):
            relative = path.relative_to(root)
            if (
                path.is_file()
                and path.suffix.lower() in allowed
                and '__pycache__' not in relative.parts
                and not any(part.startswith('.') for part in relative.parts)
            ):
                result[relative.as_posix()] = path
    return result


def build(output):
    """Create one package folder, ZIP and SHA-256 manifest."""
    metadata = ConfigParser(interpolation=None)
    metadata.read(ROOT / 'metadata.txt', encoding='utf-8')
    version = metadata['general']['version']
    output.mkdir(parents=True, exist_ok=True)
    staging = output / 'package' / PACKAGE
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    sources = package_sources()
    manifest = {}
    archive_path = output / '{}-{}.zip'.format(PACKAGE, version)
    with zipfile.ZipFile(
        archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative, source in sorted(sources.items()):
            data = source.read_bytes()
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o644)
            member = PACKAGE + '/' + relative
            info = zipfile.ZipInfo(member, date_time=(2026, 11, 15, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            manifest[member] = hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (output / (archive_path.name + '.sha256')).write_text(
        digest + '  ' + archive_path.name + '\n', encoding='utf-8'
    )
    payload = dict(
        package=PACKAGE,
        version=version,
        archive=archive_path.name,
        sha256=digest,
        source_files=manifest,
    )
    (output / 'package-manifest.json').write_text(
        json.dumps(payload, indent=2) + '\n', encoding='utf-8'
    )
    return archive_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'release')
    arguments = parser.parse_args()
    print(build(arguments.output.resolve()))
