"""Workspace settings, read-only reports and transactional drawing
annotations."""

import json
import math
import hashlib
from datetime import datetime, timezone

from qgis.core import QgsGeometry

from .annotation_layout import validate_annotation
from .domain import uid
from .reports import bom_report, check_report, route_report
from .workspace_model import read_settings, validate_settings


class WorkspaceService:
    def save_drawing_language(self, key, identity):
        if self.settings()['language'] != 'en':
            self.repo.insert(
                'drawing_languages',
                dict(
                    object_id=identity,
                    record_key=key,
                    language=self.settings()['language'],
                ),
            )

    def drawing_language(self, key, identity):
        record = self.repo.find('drawing_languages', identity)
        return (
            record['language']
            if record is not None and record['record_key'] == key
            else 'en'
        )

    def delete_drawing_language(self, identity):
        record = self.repo.find('drawing_languages', identity)
        if record is not None:
            self.repo.delete('drawing_languages', [record.id()])

    def report_signature(self):
        snapshot = {
            key: value
            for key, value in self.repo.snapshot().items()
            if key != 'annotations'
        }
        return hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()

    def settings(self):
        return read_settings(self.repo.features('workspace_settings'))

    def save_settings(self, values):
        validate_settings(values)
        with self.repo.command('Save workspace settings'):
            record = self.repo.find('workspace_settings', 'workspace')
            attrs = dict(
                setting_id='workspace',
                payload=json.dumps(
                    values, ensure_ascii=False, allow_nan=False
                ),
            )
            if record is None:
                self.repo.insert('workspace_settings', attrs)
            else:
                self.repo.update('workspace_settings', record.id(), attrs)

    def report_data(self):
        return {key: self.rows(key) for key in self.repo.layers}

    def bill_of_materials(self):
        return bom_report(self.report_data())

    def route_report(self, start, destination):
        return route_report(self.report_data(), start, destination)

    def network_checks(self):
        error = None
        try:
            self.validate()
        except (ValueError, KeyError, TypeError) as failure:
            error = str(failure)
        return check_report(self.report_data(), error)

    def add_annotation(self, kind, payload, point):
        if kind != 'legend':
            payload = dict(payload)
            payload.setdefault('source_hash', self.report_signature())
            payload.setdefault(
                'created_at',
                datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
            )
        validate_annotation(kind, payload)
        if not all(math.isfinite(v) for v in (point.x(), point.y())):
            raise ValueError('Annotation position must be finite.')
        identity = uid()
        with self.repo.command('Place ' + kind + ' annotation'):
            self.repo.insert(
                'annotations',
                dict(
                    annotation_id=identity,
                    kind=kind,
                    payload=json.dumps(
                        payload, ensure_ascii=False, allow_nan=False
                    ),
                ),
                QgsGeometry.fromPointXY(point),
            )
        return identity

    def move_annotation(self, identity, point):
        record = self.repo.find('annotations', identity)
        if record is None or not all(
            math.isfinite(v) for v in (point.x(), point.y())
        ):
            raise ValueError(
                'Choose an existing annotation and a finite position.'
            )
        with self.repo.command('Move drawing annotation'):
            self.repo.update(
                'annotations',
                record.id(),
                geometry=QgsGeometry.fromPointXY(point),
            )

    def delete_annotation(self, identity):
        record = self.repo.find('annotations', identity)
        if record is not None:
            with self.repo.command('Delete drawing annotation'):
                self.repo.delete('annotations', [record.id()])

    def validate_workspace(self):
        targets = {
            key: {
                r['cable_id' if key == 'cables' else 'obj_id']
                for r in self.repo.features(key)
            }
            for key in ('cables', 'housings', 'closures', 'active_equipment')
        }
        seen = set()
        for record in self.repo.features('drawing_languages'):
            key, identity = record['record_key'], record['object_id']
            if (
                identity in seen
                or key not in targets
                or identity not in targets[key]
                or record['language'] not in ('en', 'sr')
            ):
                raise ValueError('Invalid saved drawing language or owner.')
            seen.add(identity)
        self.settings()
        for record in self.repo.features('annotations'):
            try:
                validate_annotation(
                    record['kind'], json.loads(record['payload'])
                )
            except (ValueError, TypeError) as error:
                raise ValueError(
                    'Invalid saved drawing annotation: ' + str(error)
                ) from error
            if not record.hasGeometry() or record.geometry().isEmpty():
                raise ValueError('Drawing annotation needs a position.')
            point = record.geometry().asPoint()
            if not all(math.isfinite(v) for v in (point.x(), point.y())):
                raise ValueError('Drawing annotation position must be finite.')
