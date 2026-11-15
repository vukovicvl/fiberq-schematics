"""Prepare public source without private development artifacts."""

from argparse import ArgumentParser
from pathlib import Path
import shutil

from build_plugin import ROOT, package_sources


def prepare(destination):
    """Copy release sources, checks and CI; never copy caches or user data."""
    destination.mkdir(parents=True, exist_ok=True)
    sources = package_sources()
    # Preserve both the public README and the builder's original input name.
    sources['README_PLUGIN.md'] = ROOT / 'README_PLUGIN.md'
    for name in (
        '.gitignore',
        'requirements-dev.txt',
        'fiberq_schematic_designer.py',
        'RELEASE_NOTES.md',
    ):
        sources[name] = ROOT / name
    for folder in ('tools', 'tests', '.github', 'branding'):
        for path in sorted((ROOT / folder).rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            if path.suffix in {'.py', '.md', '.yml', '.yaml', '.png'}:
                sources[path.relative_to(ROOT).as_posix()] = path
    for relative, path in sources.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return destination


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(prepare(args.destination.resolve()))
