"""Validate plugin metadata, source syntax, ZIP layout and file permissions."""

import argparse
import ast
from configparser import ConfigParser
import json
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlparse
import zipfile


REQUIRED = {
    '__init__.py',
    'plugin.py',
    'plugin_info.py',
    'metadata.txt',
    'icon.png',
    'README.md',
    'LICENSE',
    'FIBERQ_CAD_LICENSE.txt',
    'help/index.html',
    'fiberq_designer/app.py',
    'fiberq_designer/schema.py',
    'fiberq_designer/i18n_sr.json',
    'fiberq_designer/i18n_extra_sr.json',
}
ALLOWED_SUFFIXES = {
    '.py',
    '.json',
    '.png',
    '.bmp',
    '.md',
    '.html',
    '.csv',
    '.txt',
}


def validate(path):
    """Return a validation record; reject any invalid upload package."""
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('Upload ZIP exceeds the QGIS 25 MB limit.')
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(set(names)) != len(names):
            raise ValueError('Duplicate ZIP entries.')
        roots = {PurePosixPath(name).parts[0] for name in names}
        if roots != {'fiberq_schematics'}:
            raise ValueError('ZIP must contain the fiberq_schematics folder.')
        relative = set()
        python_count = 0
        for info in infos:
            member = PurePosixPath(info.filename)
            if (
                member.is_absolute()
                or '..' in member.parts
                or '\\' in info.filename
                or len(member.parts) < 2
                or any(part.startswith('.') for part in member.parts)
                or '__pycache__' in member.parts
            ):
                raise ValueError(
                    'Unsafe or hidden archive member: ' + str(member)
                )
            mode = info.external_attr >> 16
            if not stat.S_ISREG(mode) or mode & 0o133:
                raise ValueError(
                    'Unsafe file type or permissions: ' + str(member)
                )
            item = PurePosixPath(*member.parts[1:]).as_posix()
            relative.add(item)
            if member.suffix not in ALLOWED_SUFFIXES and item != 'LICENSE':
                raise ValueError('Unexpected file type: ' + item)
            content = archive.read(info)
            if member.suffix == '.py':
                ast.parse(content, filename=str(member))
                python_count += 1
            elif member.suffix == '.json':
                json.loads(content)
        if not REQUIRED.issubset(relative):
            raise ValueError('Missing files: ' + str(REQUIRED - relative))
        parser = ConfigParser(interpolation=None)
        parser.read_string(
            archive.read('fiberq_schematics/metadata.txt').decode('utf-8')
        )
        metadata = dict(parser['general'])
        for field in (
            'name',
            'description',
            'about',
            'version',
            'author',
            'email',
            'qgisminimumversion',
            'qgismaximumversion',
            'homepage',
            'repository',
            'tracker',
            'icon',
            'license',
        ):
            if not metadata.get(field, '').strip():
                raise ValueError('Missing metadata: ' + field)
        for field in ('version', 'qgisminimumversion', 'qgismaximumversion'):
            if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', metadata[field]):
                raise ValueError('Invalid version field: ' + field)
        for field in ('homepage', 'repository', 'tracker'):
            url = urlparse(metadata[field])
            if url.scheme != 'https' or not url.netloc:
                raise ValueError('Invalid HTTPS metadata URL: ' + field)
        if metadata['icon'] not in relative:
            raise ValueError('Metadata icon is missing.')
        entry = ast.parse(archive.read('fiberq_schematics/__init__.py'))
        if not any(
            isinstance(node, ast.FunctionDef) and node.name == 'classFactory'
            for node in entry.body
        ):
            raise ValueError('QGIS classFactory is missing.')
        if archive.testzip() is not None:
            raise ValueError('Archive CRC check failed.')
    return dict(
        status='passed',
        name=metadata['name'],
        version=metadata['version'],
        file_count=len(names),
        python_files=python_count,
        archive_bytes=path.stat().st_size,
        qgis_minimum=metadata['qgisminimumversion'],
        qgis_maximum=metadata['qgismaximumversion'],
        permissions='regular files, 0644',
        metadata_urls=(
            'HTTPS structure validated; availability checked separately'
        ),
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.archive), indent=2))
