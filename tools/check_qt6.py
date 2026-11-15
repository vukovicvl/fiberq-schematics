"""Gate the exact ZIP using QGIS's unmodified pyqt5_to_pyqt6 checker."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

if __package__:
    from .validate_package import validate
else:
    from validate_package import validate


def assess_result(returncode, output, files_unchanged=True):
    """A successful dry-run exit alone does not mean compatibility passed."""
    findings = [line for line in output.splitlines() if '.py:' in line]
    environment_errors = [
        line
        for line in output.splitlines()
        if any(
            message in line
            for message in (
                'only a partial upgrade',
                'PyQt5 has been found',
                'Traceback (most recent call last)',
                'ImportError:',
                'ModuleNotFoundError:',
            )
        )
    ]
    started = '=== dry_run mode | Start Logs ===' in output
    return dict(
        passed=(
            returncode == 0
            and started
            and not findings
            and not environment_errors
            and files_unchanged
        ),
        returncode=returncode,
        checker_started=started,
        findings=findings,
        finding_count=len(findings),
        environment_errors=environment_errors,
        input_files_unchanged=files_unchanged,
    )


def run_check(archive_path, checker, reports):
    """Extract a validated archive and reject every reported Qt6 finding."""
    structure = validate(archive_path)
    checker_path = Path(shutil.which(str(checker)) or checker).resolve()
    if not checker_path.is_file():
        raise FileNotFoundError(
            'Official QGIS checker not found: ' + str(checker)
        )
    reports.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fiberq-qt6-') as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                destination = root.joinpath(*member.filename.split('/'))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*.py')
        }
        command = [sys.executable, str(checker_path), str(root), '--dry_run']
        result = subprocess.run(
            command, capture_output=True, text=True, check=False
        )
        output = result.stdout + result.stderr
        unchanged = all(
            path.exists()
            and hashlib.sha256(path.read_bytes()).hexdigest() == digest
            for path, digest in before.items()
        )
        record = assess_result(result.returncode, output, unchanged)
    record.update(
        archive=archive_path.name,
        archive_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        checker_sha256=hashlib.sha256(checker_path.read_bytes()).hexdigest(),
        python_files=structure['python_files'],
    )
    (reports / 'qt6-check.txt').write_text(output, encoding='utf-8')
    (reports / 'qt6-check.json').write_text(
        json.dumps(record, indent=2) + '\n', encoding='utf-8'
    )
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--checker', default='pyqt5_to_pyqt6.py')
    parser.add_argument(
        '--reports', type=Path, default=Path('release/reports')
    )
    args = parser.parse_args()
    outcome = run_check(args.archive, args.checker, args.reports)
    print(json.dumps(outcome, indent=2))
    sys.exit(0 if outcome['passed'] else 1)
