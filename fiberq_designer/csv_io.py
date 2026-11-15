"""Compatible CSV boundary, with unambiguous names and atomic batch writes."""

from collections import defaultdict
import csv

from .geometry import optional


def import_connections(service, path):
    names = defaultdict(list)
    for element in service.repo.features('elements'):
        names[str(element['name'])].append(element['obj_id'])
    ports = defaultdict(list)
    for port in service.repo.features('ports'):
        aliases = {str(port['label']).strip().upper()}
        if port['role'] == 'fiber' or str(port['label']).isdigit():
            aliases.add(str(int(port['fiber_no'])))
        if optional(port['module_port']) is not None:
            aliases.add(str(int(port['module_port'])))
        for alias in aliases:
            ports[(port['obj_id'], alias)].append(port)
    count, skipped = 0, []
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {'from_element', 'from_port', 'to_element', 'to_port'}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                'CSV needs columns: ' + ', '.join(sorted(required))
            )
        with service.repo.command('Import splice CSV'):
            for number, row in enumerate(reader, 2):
                try:
                    endpoints = []
                    for prefix in ('from', 'to'):
                        name = (row[prefix + '_element'] or '').strip()
                        if len(names[name]) != 1:
                            raise ValueError(
                                'Unknown or ambiguous element name: ' + name
                            )
                        label = (row[prefix + '_port'] or '').strip().upper()
                        matches = ports[(names[name][0], label)]
                        if len(matches) != 1:
                            raise ValueError(
                                'Unknown or ambiguous port: {} / {}. Use its '
                                'module/port label.'.format(name, label)
                            )
                        endpoints.append(matches[0])
                    service.add_link(*endpoints)
                    count += 1
                except (ValueError, KeyError, TypeError) as error:
                    skipped.append('{} ({})'.format(number, error))
    return count, skipped
