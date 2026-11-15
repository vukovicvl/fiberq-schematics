"""Conservative inventory of controlled UI messages and surfaced diagnostics.

It excludes documentation, SQL, model/user data and technical identifiers.
Dynamic values are exercised separately by the QGIS interaction fixtures.
"""

import ast
from pathlib import Path
import re

from fiberq_designer.i18n import SR, translate


TECHNICAL = {'FiberQ', 'FiberQ · SCHEMATIC DESIGNER', 'FiberQ Schematic Designer', 'QGIS', 'PDF', 'SVG', 'DXF', 'CSV',
             'SM', 'MM', 'OLT', 'ONT', 'ODF', 'MDU', 'OTB', 'PON', 'IN', 'OUT', 'm', 'dB', 'nm',
             'GeoPackage (*.gpkg)', 'CSV (*.csv)', 'QGIS project (*.qgz)',
             'N', 'NL', '1', 'R', 'EPSG:3857', 'SC/APC'}


def literals(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'format':
        yield from literals(node.func.value)
    elif isinstance(node, ast.BinOp):
        yield from literals(node.left)
        yield from literals(node.right)
    elif isinstance(node, ast.IfExp):
        yield from literals(node.body)
        yield from literals(node.orelse)


def technical(value):
    return (value.rstrip('….') in TECHNICAL or value in ('FIBERQ', 'FIBERQ  /  SCHEMATIC DESIGNER') or not any(c.isalpha() for c in value) or
            re.fullmatch(r'[\s\d{}:.,·×x/()—%+\-]*[Fms]?\s*', value) is not None or
            ('dB' in value and set(re.findall(r'[A-Za-z]+', value)) <= {'dB', 'km', 'SM', 'MM', 'nm', 'f'}) or
            value.startswith(('FQ_', '__', 'fqsd_', '%Y-')))


def inventory(root=None):
    root = root or Path(__file__).resolve().parents[1]
    rows = set()
    constructors = {'QLabel', 'QPushButton', 'QCheckBox', 'QRadioButton', 'QGroupBox'}
    methods = {'note', 'setText', 'setWindowTitle', 'setToolTip', 'setPlaceholderText', 'setSpecialValueText', 'setSuffix', 'addRow', 'addItem', 'command'}
    for path in (root / 'fiberq_designer').glob('*.py'):
        if path.stem == 'i18n':
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            candidates = []
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                candidates.append(node.exc.args[0])
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ''
                if node.args and name in constructors | methods and not (name == 'command' and isinstance(node.func, ast.Name)):
                    candidates.append(node.args[0])
                if name == 'ui_message' and len(node.args) > 1:
                    candidates.append(node.args[1])
                if name in ('_panel', '_form_dialog') and len(node.args) > (1 if name == '_panel' else 2):
                    candidates.append(node.args[1 if name == '_panel' else 2])
                if name == 'command' and isinstance(node.func, ast.Name) and len(node.args) > 4:
                    candidates += [node.args[1], node.args[4]]
            for candidate in candidates:
                for value in literals(candidate):
                    if not value.strip():
                        continue
                    classification = 'technical' if technical(value) else 'translated' if value in SR or translate(value, 'sr') != value else 'missing'
                    rows.add((path.name, candidate.lineno, value, classification, translate(value, 'sr')))
    return sorted(rows)
