"""Run one deterministic, isolated part of the QGIS regression suite."""

import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def cases(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from cases(case)
        else:
            yield case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, required=True, help='Zero-based part index')
    parser.add_argument('--count', type=int, required=True, help='Number of isolated parts')
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    if not 0 <= args.index < args.count:
        parser.error('Require 0 <= index < count.')
    discovered = list(cases(unittest.defaultTestLoader.discover(str(ROOT / 'tests'))))
    selected = [case for index, case in enumerate(discovered) if index % args.count == args.index]
    identities = [case.id() for case in selected]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    payload = dict(index=args.index, count=args.count, total_discovered=len(discovered),
                   selected=identities, tests_run=result.testsRun, passed=result.wasSuccessful(),
                   failures=[case.id() for case, error in result.failures],
                   errors=[case.id() for case, error in result.errors],
                   skipped=[case.id() for case, reason in result.skipped])
    args.manifest.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
