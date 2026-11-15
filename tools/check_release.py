"""Run unsuppressed security/quality checks on the exact upload ZIP."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from validate_package import validate


def run_checks(archive_path, reports):
    """Require clean Flake8, Bandit, secrets and structural results."""
    reports.mkdir(parents=True, exist_ok=True)
    structure = validate(archive_path)
    records = {'structure': structure}
    with tempfile.TemporaryDirectory(prefix='fiberq-release-check-') as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(archive_path) as archive:
            # validate() has rejected absolute/parent paths and symlinks.
            for member in archive.infolist():
                target = root.joinpath(*member.filename.split('/'))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
        package = root / 'fiberq_schematics'
        commands = {
            'flake8': [
                sys.executable,
                '-m',
                'flake8',
                '--isolated',
                '--jobs',
                '1',
                str(package),
            ],
            'bandit': [
                sys.executable,
                '-m',
                'bandit',
                '-r',
                str(package),
                '-f',
                'json',
                '-q',
            ],
            'detect-secrets': [
                sys.executable,
                str(Path(__file__).with_name('scan_secrets.py')),
                str(package),
            ],
        }
        for name, command in commands.items():
            result = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            (reports / (name + '.stdout.txt')).write_text(
                result.stdout, encoding='utf-8'
            )
            (reports / (name + '.stderr.txt')).write_text(
                result.stderr, encoding='utf-8'
            )
            record = {'returncode': result.returncode}
            if name == 'flake8':
                record['findings'] = len(result.stdout.splitlines())
                record['passed'] = (
                    result.returncode == 0 and not result.stdout.strip()
                )
            else:
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    data = None
                if data is None:
                    record.update(passed=False, error='Scanner did not run.')
                elif name == 'bandit':
                    record.update(
                        findings=len(data['results']),
                        errors=len(data.get('errors', [])),
                        passed=(
                            result.returncode == 0
                            and not data['results']
                            and not data.get('errors')
                        ),
                    )
                else:
                    findings = sum(map(len, data['results'].values()))
                    record.update(
                        findings=findings,
                        passed=result.returncode == 0 and findings == 0,
                    )
            records[name] = record
    records['passed'] = all(
        records[name]['passed']
        for name in ('flake8', 'bandit', 'detect-secrets')
    )
    (reports / 'release-checks.json').write_text(
        json.dumps(records, indent=2) + '\n', encoding='utf-8'
    )
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument(
        '--reports', type=Path, default=Path('release/reports')
    )
    args = parser.parse_args()
    outcome = run_checks(args.archive, args.reports)
    print(json.dumps(outcome, indent=2))
    sys.exit(0 if outcome['passed'] else 1)
