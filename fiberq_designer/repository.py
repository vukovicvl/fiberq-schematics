"""QGIS reads and atomic GDAL writes to one independent GeoPackage."""

from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
import sqlite3
import json
import hashlib

from osgeo import gdal, ogr
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .schema import (
    ID_FIELDS,
    KEY_VERSIONS,
    LAYERS,
    V4_FIELDS,
    V5_FIELDS,
    V7_FIELDS,
    V8_FIELDS,
    V9_FIELDS,
    V10_FIELDS,
    VERSION,
)


def fields_dict(feature):
    return {field.name(): feature[field.name()] for field in feature.fields()}


class GeoPackageRepository:
    def __init__(self, path, layers):
        self.path = str(path)
        self.layers = layers
        self.dataset = None
        self.history = []
        self.future = []
        self.in_command = False
        self.before_commit = None

    @staticmethod
    def _create_layers(path, keys, new=False):
        for key in keys:
            geometry, fields = LAYERS[key]
            memory = QgsVectorLayer(geometry + '?crs=EPSG:3857', key, 'memory')
            if not memory.isValid():
                raise RuntimeError('Cannot create layer ' + key)
            memory.dataProvider().addAttributes(
                [QgsField(k, t) for k, t in fields]
            )
            memory.updateFields()
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.layerName = 'fqsd_' + key
            options.actionOnExistingFile = (
                QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
                if new
                else (
                    QgsVectorFileWriter.ActionOnExistingFile
                    .CreateOrOverwriteLayer
                )
            )
            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                memory,
                str(path),
                QgsProject.instance().transformContext(),
                options,
            )
            if result[0] != QgsVectorFileWriter.WriterError.NoError:
                raise RuntimeError('Cannot create {}: {}'.format(key, result))
            new = False
        with closing(sqlite3.connect(str(path))) as connection, connection:
            connection.execute(
                'CREATE TABLE IF NOT EXISTS fqsd_metadata '
                '(key TEXT PRIMARY KEY, value TEXT NOT NULL)'
            )
            connection.execute(
                'INSERT OR REPLACE INTO fqsd_metadata VALUES (?, ?)',
                ('schema_version', str(VERSION)),
            )

    @classmethod
    def create(cls, path):
        if Path(path).exists():
            raise ValueError(
                'Workspace already exists; use Open or choose a new name.'
            )
        try:
            cls._create_layers(path, LAYERS, new=True)
            return cls.open(path)
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise

    @classmethod
    def open(cls, path):
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError('Workspace file does not exist.')
        with closing(
            sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        ) as connection:
            tables = {
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            version = 1
            if 'fqsd_metadata' in tables:
                record = connection.execute(
                    'SELECT value FROM fqsd_metadata WHERE '
                    "key='schema_version'"
                ).fetchone()
                if record is None:
                    raise ValueError('Workspace schema version is missing.')
                version = int(record[0])
        if version > VERSION or version < 1:
            raise ValueError('Unsupported workspace version: ' + str(version))
        missing = [k for k in LAYERS if 'fqsd_' + k not in tables]
        if version == VERSION and missing:
            raise ValueError('Workspace is incomplete: ' + ', '.join(missing))
        required = [key for key in LAYERS if KEY_VERSIONS[key] <= version]
        for key in required:
            cls._load_layer(path, key, version)
        if version < VERSION:
            if any(
                'fqsd_' + key in tables
                for key in LAYERS
                if KEY_VERSIONS[key] > version
            ):
                raise ValueError(
                    'Unversioned application table found; refusing to '
                    'replace it.'
                )
            target = path.with_name(path.stem + '_v{}.gpkg'.format(VERSION))
            suffix = 2
            while target.exists():
                target = path.with_name(
                    path.stem + '_v{}_{}.gpkg'.format(VERSION, suffix)
                )
                suffix += 1
            with (
                closing(
                    sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
                ) as source,
                closing(sqlite3.connect(str(target))) as destination,
            ):
                source.backup(destination)
            try:
                added = [key for key in LAYERS if KEY_VERSIONS[key] > version]
                cls._create_layers(target, added)
                with (
                    closing(sqlite3.connect(str(target))) as connection,
                    connection,
                ):
                    additions = {
                        key: V4_FIELDS.get(key, set())
                        | V5_FIELDS.get(key, set())
                        | V7_FIELDS.get(key, set())
                        | V8_FIELDS.get(key, set())
                        | V9_FIELDS.get(key, set())
                        | V10_FIELDS.get(key, set())
                        for key in set(V4_FIELDS)
                        | set(V5_FIELDS)
                        | set(V7_FIELDS)
                        | set(V8_FIELDS)
                        | set(V9_FIELDS)
                        | set(V10_FIELDS)
                    }
                    for key, fields in additions.items():
                        found = {
                            row[1]
                            for row in connection.execute(
                                'PRAGMA table_info(fqsd_' + key + ')'
                            )
                        }
                        for name, field_type in LAYERS[key][1]:
                            if name in fields and name not in found:
                                sql_type = (
                                    'REAL'
                                    if name == 'angle'
                                    else 'INTEGER'
                                    if name
                                    in (
                                        'module_port',
                                        'group_size',
                                        'capacity',
                                        'module_count',
                                        'mirrored',
                                        'splitter_ratio',
                                        'splitter_feed',
                                        'tube_count',
                                        'tube_no',
                                        'tube_fiber',
                                    )
                                    else 'TEXT'
                                )
                                connection.execute(
                                    'ALTER TABLE fqsd_{} '
                                    'ADD COLUMN {} {}'.format(
                                        key, name, sql_type
                                    )
                                )
                migrated = cls(
                    target,
                    {key: cls._load_layer(target, key) for key in LAYERS},
                )
                cls._populate_migration(migrated, version)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            path = target
        layers = {key: cls._load_layer(path, key) for key in LAYERS}
        return cls(path, layers)

    @staticmethod
    def _load_layer(path, key, version=VERSION):
        layer = QgsVectorLayer(
            '{}|layername=fqsd_{}'.format(path, key), 'FQSD | ' + key, 'ogr'
        )
        if not layer.isValid():
            raise ValueError('Missing or invalid fqsd_' + key)
        expected = {name for name, _ in LAYERS[key][1]}
        if version < 4:
            expected -= V4_FIELDS.get(key, set())
        if version < 5:
            expected -= V5_FIELDS.get(key, set())
        if version < 7:
            expected -= V7_FIELDS.get(key, set())
        if version < 8:
            expected -= V8_FIELDS.get(key, set())
        if version < 9:
            expected -= V9_FIELDS.get(key, set())
        if version < 10:
            expected -= V10_FIELDS.get(key, set())
        if not expected.issubset({f.name() for f in layer.fields()}):
            raise ValueError('Missing fields in fqsd_' + key)
        geometry = LAYERS[key][0]
        if (geometry == 'None' and layer.isSpatial()) or (
            geometry != 'None'
            and QgsWkbTypes.flatType(layer.wkbType())
            != QgsWkbTypes.parseType(geometry)
        ):
            raise ValueError('Unexpected geometry type in fqsd_' + key)
        if geometry != 'None' and layer.crs().authid() != 'EPSG:3857':
            raise ValueError(
                'Expected schematic EPSG:3857 coordinates in fqsd_' + key
            )
        layer.setReadOnly(True)
        return layer

    @staticmethod
    def _populate_migration(repo, version):
        from .domain import uid
        from .housing_catalog import module_labels

        elements = {e['obj_id']: e for e in repo.features('elements')}
        ports = {p['port_id']: p for p in repo.features('ports')}
        with repo.atomic():
            if version < 10:
                for housing in repo.features('housings'):
                    repo.update('housings', housing.id(), {'layout_state': ''})
            if version < 9:
                for cable in repo.features('cables'):
                    repo.update('cables', cable.id(), {'catalogue_data': ''})
            if version < 7:
                from .cable_catalog import LEGACY_CABLE, LEGACY_FIBER

                for cable in repo.features('cables'):
                    repo.update('cables', cable.id(), LEGACY_CABLE)
                for fiber in repo.features('fibers'):
                    repo.update('fibers', fiber.id(), LEGACY_FIBER)
            if version < 8:
                cables = {c['cable_id']: c for c in repo.features('cables')}
                for fiber in repo.features('fibers'):
                    cable = cables[fiber['cable_id']]
                    native = cable['cable_version'] == 'cad-1'
                    repo.update(
                        'fibers',
                        fiber.id(),
                        {
                            'purpose': 'nmFree' if native else '',
                            'fiber_type': cable['fiber_type']
                            if native
                            else '',
                            'description': '',
                        },
                    )
            if version < 5:
                for housing in repo.features('housings'):
                    repo.update(
                        'housings',
                        housing.id(),
                        {
                            'address': '',
                            'splitter_ratio': 0,
                            'splitter_feed': 0,
                        },
                    )
            if version >= 9:
                # A schema-only addition must not mark an otherwise current
                # frozen report stale. Keep already stale reports stale.
                snapshot = {
                    k: v
                    for k, v in repo.snapshot().items()
                    if k != 'annotations'
                }
                legacy = {
                    k: [
                        (
                            fid,
                            {
                                name: value
                                for name, value in attrs.items()
                                if version >= 10
                                or name not in V10_FIELDS.get(k, set())
                            },
                            wkb,
                        )
                        for fid, attrs, wkb in records
                    ]
                    for k, records in snapshot.items()
                    if KEY_VERSIONS[k] <= version
                }
                old_hash = hashlib.sha256(
                    json.dumps(legacy, sort_keys=True, default=str).encode(
                        'utf-8'
                    )
                ).hexdigest()
                new_hash = hashlib.sha256(
                    json.dumps(snapshot, sort_keys=True, default=str).encode(
                        'utf-8'
                    )
                ).hexdigest()
                for annotation in repo.features('annotations'):
                    try:
                        payload = json.loads(annotation['payload'])
                    except (ValueError, TypeError):
                        # Annotation validation reports malformed input.
                        continue
                    if (
                        isinstance(payload, dict)
                        and payload.get('source_hash') == old_hash
                    ):
                        payload['source_hash'] = new_hash
                        repo.update(
                            'annotations',
                            annotation.id(),
                            {
                                'payload': json.dumps(
                                    payload,
                                    ensure_ascii=False,
                                    allow_nan=False,
                                )
                            },
                        )
            if version >= 4:
                return
            for housing in repo.features('housings'):
                element = elements.get(housing['obj_id'])
                if element is None:
                    raise ValueError('Housing references missing equipment.')
                module_id = housing['module_id']
                repo.update(
                    'housings',
                    housing.id(),
                    {
                        'capacity': int(element['count']),
                        'module_count': 1,
                        'mirrored': 0,
                        'row_positions': '[]',
                        'angle': housing['orientation'],
                    },
                )
                repo.insert(
                    'modules',
                    {
                        'module_id': module_id,
                        'obj_id': element['obj_id'],
                        'number': 1,
                        'label': module_labels(housing['rack_module'], 1)[0],
                        'capacity': int(element['count']),
                    },
                )
                for port in ports.values():
                    if port['obj_id'] == element['obj_id']:
                        repo.update(
                            'ports',
                            port.id(),
                            {
                                'module_id': module_id,
                                'module_port': int(port['fiber_no']),
                            },
                        )
            for cable in repo.features('cables'):
                repo.update(
                    'cables',
                    cable.id(),
                    {'layout_mode': 'legacy', 'group_size': 12},
                )
            for fiber in repo.features('fibers'):
                for field, side in (('a_id', 'A'), ('b_id', 'B')):
                    port = ports.get(fiber[field])
                    if port is None:
                        raise ValueError(
                            'Cable fiber references a missing port.'
                        )
                    repo.insert(
                        'fiber_ends',
                        {
                            'end_id': uid(),
                            'fiber_id': fiber['fiber_id'],
                            'side': side,
                            'port_id': port['port_id'],
                        },
                        port.geometry(),
                    )

    def features(self, key):
        if self.dataset is None:
            return list(self.layers[key].getFeatures())
        source = self.dataset.GetLayerByName('fqsd_' + key)
        source.ResetReading()
        result = []
        for record in source:
            feature = QgsFeature(self.layers[key].fields())
            feature.setId(record.GetFID())
            for field in feature.fields():
                feature[field.name()] = (
                    record.GetFID()
                    if field.name() == 'fid'
                    else record.GetField(field.name())
                )
            shape = record.GetGeometryRef()
            if shape is not None:
                geometry = QgsGeometry()
                geometry.fromWkb(bytes(shape.ExportToWkb()))
                feature.setGeometry(geometry)
            result.append(feature)
        return result

    def find(self, key, value):
        return next(
            (f for f in self.features(key) if f[ID_FIELDS[key]] == value), None
        )

    @staticmethod
    def _ok(result):
        if result != 0:
            raise RuntimeError(
                gdal.GetLastErrorMsg() or 'GeoPackage write failed.'
            )

    @contextmanager
    def atomic(self):
        if self.dataset is not None:
            yield
            return
        if any(layer.isEditable() for layer in self.layers.values()):
            raise ValueError(
                'Commit or roll back QGIS layer edits before editing the '
                'schematic.'
            )
        with (
            ogr.ExceptionMgr(useExceptions=True)
            if hasattr(ogr, 'ExceptionMgr')
            else nullcontext()
        ):
            self.dataset = ogr.Open(self.path, 1)
            if self.dataset is None:
                raise RuntimeError('Cannot open workspace for writing.')
            try:
                self._ok(self.dataset.StartTransaction())
                try:
                    yield
                    self._ok(self.dataset.CommitTransaction())
                except BaseException:
                    self.dataset.RollbackTransaction()
                    raise
            finally:
                self.dataset = None
                self.refresh()

    def refresh(self):
        for layer in self.layers.values():
            layer.dataProvider().reloadData()
            layer.updateExtents(True)
            layer.triggerRepaint()

    def insert(self, key, attrs, geometry=None, fid=None):
        if self.dataset is None:
            raise RuntimeError('Write requires a repository command.')
        if key in ('cables', 'fibers'):
            from .cable_catalog import LEGACY_CABLE, LEGACY_FIBER

            attrs = dict(
                LEGACY_CABLE if key == 'cables' else LEGACY_FIBER, **attrs
            )
        layer = self.dataset.GetLayerByName('fqsd_' + key)
        feature = ogr.Feature(layer.GetLayerDefn())
        if fid is not None:
            feature.SetFID(fid)
        for name, _ in LAYERS[key][1]:
            value = attrs.get(name)
            if value is not None:
                feature.SetField(name, value)
        if geometry is not None and not geometry.isNull():
            feature.SetGeometry(
                ogr.CreateGeometryFromWkb(bytes(geometry.asWkb()))
            )
        self._ok(layer.CreateFeature(feature))
        return feature.GetFID()

    def update(self, key, fid, attrs=None, geometry=None):
        if self.dataset is None:
            raise RuntimeError('Write requires a repository command.')
        layer = self.dataset.GetLayerByName('fqsd_' + key)
        feature = layer.GetFeature(fid)
        if feature is None:
            raise ValueError('Feature no longer exists.')
        for name, value in (attrs or {}).items():
            if value is None:
                feature.SetFieldNull(name)
            else:
                feature.SetField(name, value)
        if geometry is not None:
            feature.SetGeometry(
                ogr.CreateGeometryFromWkb(bytes(geometry.asWkb()))
            )
        self._ok(layer.SetFeature(feature))

    def delete(self, key, fids):
        if self.dataset is None:
            raise RuntimeError('Write requires a repository command.')
        layer = self.dataset.GetLayerByName('fqsd_' + key)
        for fid in fids:
            self._ok(layer.DeleteFeature(fid))

    def snapshot(self):
        return {
            k: [
                (
                    f.id(),
                    {name: f[name] for name, _ in LAYERS[k][1]},
                    bytes(f.geometry().asWkb()) if f.hasGeometry() else None,
                )
                for f in self.features(k)
            ]
            for k in LAYERS
        }

    @contextmanager
    def command(self, name):
        if self.in_command:
            yield
            return
        self.in_command = True
        try:
            with self.atomic():
                before = self.snapshot()
                yield
                if self.before_commit is not None:
                    self.before_commit()
                after = self.snapshot()
            if before != after:
                self.history.append((name, before, after))
                self.history = self.history[-20:]
                self.future.clear()
        finally:
            self.in_command = False

    def _restore(self, state):
        with self.atomic():
            for key in (
                'fiber_purposes',
                'drawing_languages',
                'outlets',
                'housing_cables',
                'annotations',
                'workspace_settings',
                'fiber_joints',
                'fiber_marks',
                'fiber_cuts',
                'fiber_nodes',
                'fiber_routes',
                'slack',
                'active_equipment',
                'closure_members',
                'closures',
                'links',
                'contained_splitters',
                'fiber_ends',
                'fibers',
                'cables',
                'ports',
                'modules',
                'housings',
                'frames',
                'elements',
            ):
                self.delete(key, [f.id() for f in self.features(key)])
            for key, records in state.items():
                for fid, attrs, wkb in records:
                    geometry = None
                    if wkb is not None:
                        geometry = QgsGeometry()
                        geometry.fromWkb(wkb)
                    self.insert(key, attrs, geometry, fid)

    def undo(self):
        if not self.history:
            return ''
        entry = self.history[-1]
        self._restore(entry[1])
        self.history.pop()
        self.future.append(entry)
        return entry[0]

    def redo(self):
        if not self.future:
            return ''
        entry = self.future[-1]
        self._restore(entry[2])
        self.future.pop()
        self.history.append(entry)
        return entry[0]
