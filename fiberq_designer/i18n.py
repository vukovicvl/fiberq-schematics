"""FiberQ-only English/Serbian UI; source phrases retain the MIT notice.

i18n_sr.json is derived from FiberQ CAD fq-jezik.lsp, Copyright 2026
Vladimir Vukovic. See FIBERQ_CAD_LICENSE.txt.
"""

import json
from pathlib import Path
import re
from string import Formatter
from functools import lru_cache


SR = json.loads(
    Path(__file__).with_name('i18n_sr.json').read_text(encoding='utf-8')
)
SR.update(
    dict(
        pair.split('|', 1)
        for pair in """Draw|Crtanje
Edit|Izmena
Fibers|Vlakna
Workspace|Radni prostor
Reports|Izvestaji
Settings|Podesavanja
New|Novo
Open|Otvori
Save project|Sacuvaj projekat
Undo|Ponisti
Redo|Ponovi
Select|Izbor
Stop / Pan|Prekini / Pomeranje prikaza
Ready|Spremno
Details|Podaci
Properties|Svojstva
Delete|Obrisi
Move|Pomeri
Cut|Preseci
Through|Prolaz
Join|Spoji
Group join|Spoji grupu
Splice point|Tacka varenja
Branch|Odvod
Slack|Rezerva
Fiber data|Podaci vlakna
Trace fiber|Trasa vlakna
Fit view|Prikazi sve
Dark canvas|Tamna podloga
Label|Oznaka
Name|Naziv
Type|Vrsta
Model|Model
Category|Kategorija
Cable type|Tip kabla
Orientation|Orijentacija
Horizontal|Horizontalno
Horizontal left|Horizontalno ulevo
Vertical up|Vertikalno gore
Vertical down|Vertikalno dole
Drawing status|Status crteza
Designed|Projektovano
As-built|Izvedeno
Color code|Raspored boja
Fiber type|Vrsta vlakna
Purpose|Namena
Description|Opis
Length|Duzina
Ratio|Odnos
Placement anchor|Tacka umetanja
First output endpoint|Kraj prvog izlaza
Input endpoint|Kraj ulaza
Customer / address|Korisnik / adresa
Snap and connect to a free connector or fiber end|Hvatanje i povezivanje \
slobodnog konektora ili kraja vlakna
Snap to the first housing connector|Hvatanje prvog konektora kucista
Snap|Hvatanje
Join snap|Hvatanje pri spajanju
Snap tolerance (pixels)|Tolerancija hvatanja (pikseli)
Enable endpoint snapping|Ukljuci hvatanje krajeva
Language|Jezik
English|Engleski
Serbian|Srpski
Create cable|Nacrtaj kabl
Cable properties|Svojstva kabla
Create SPLITTER|Nacrtaj spliter
Create OLT|Nacrtaj OLT
Create ONT|Nacrtaj ONT
Edit SPLITTER|Izmeni spliter
Edit OLT|Izmeni OLT
Edit ONT|Izmeni ONT
Keep current purpose|Zadrzi trenutnu namenu
Keep current type|Zadrzi trenutnu vrstu
Replace description (blank clears it)|Zameni opis (prazno brise opis)
Unknown|Nepoznato
Working|Radno
Planned|Planirano
Under construction|U izgradnji
Accepted|Primljeno
Reserved|Rezervisano
Spare|Rezervno
Splitter|Spliter
Damaged|Osteceno
Free|Slobodno
Join a group of fibers|Spajanje grupe vlakana
Number of pairs|Broj parova
Destination numbering step|Korak numeracije odredista
Connection|Veza
Fusion splice · show dots|Varenje · prikazi tacke
Through · no dots|Prolaz · bez tacaka
Patch|Prespoj
Branch / divert fiber ends|Odvod krajeva vlakana
Consecutive fibers from the chosen one|Uzastopna vlakna od izabranog
Free cable end to divert|Slobodan kraj kabla za odvod
A end|Kraj A
B end|Kraj B
Capacity · tubes × fibers|Kapacitet · cevcice × vlakna
Real cable length|Stvarna duzina kabla
Schematic length|Duzina na semi
Drawing|Crtanje
Bill of materials|Specifikacija materijala
Optical path|Opticka trasa
Network checks|Provera mreze
Optical budget|Opticki budzet
Legend|Legenda
Place in drawing|Postavi u crtez
Export CSV|Izvezi CSV
Close|Zatvori
Cancel|Otkazi
OK|U redu
Item|Stavka
Specification|Specifikacija
Status|Status
Quantity|Kolicina
Unit|Jedinica
Step|Korak
From|Od
To|Do
Details|Podaci
Severity|Nivo
Object|Objekat
Finding|Nalaz
Calculation / assumption|Proracun / pretpostavka
Loss (dB) / result|Gubitak (dB) / rezultat
Manual inputs|Rucni unos
Selected optical path|Izabrana opticka trasa
Real path length (km)|Stvarna duzina trase (km)
Splices|Varenja
Connectors|Konektori
Fiber|Vlakno
Cable|Kabl
Cable / tube|Kabl / cevcica
Cable length|Duzina kabla
Cable slack|Rezerva kabla
Fusion splice|Varenje
Closure|Nastavak
Fiber purpose|Namena vlakna
Engineering margin|Projektna rezerva
Total required budget|Potreban opticki budzet
Included in required budget|Ukljuceno u potreban budzet
Sum of losses and margin|Zbir gubitaka i rezerve
Catalogue / entered loss|Kataloski / uneti gubitak
Calculate|Izracunaj
Start|Pocetak
Destination|Odrediste
Wavelength (nm)|Talasna duzina (nm)
Splitter losses (dB, comma separated)|Gubici splitera (dB, razdvojeni zarezom)
Workspace settings|Podesavanja radnog prostora
General|Opste
Optical losses|Opticki gubici
Cable catalogue|Katalog kablova
Color codes|Rasporedi boja
Splice loss (dB)|Gubitak varenja (dB)
Connector loss (dB)|Gubitak konektora (dB)
Engineering margin (dB)|Projektna rezerva (dB)
Default color code|Podrazumevani raspored boja
Add row|Dodaj red
Remove row|Ukloni red
Capacities (e.g. 2x6,4x12)|Kapaciteti (npr. 2x6,4x12)
Colors (source numbers 1–12)|Boje (izvorni brojevi 1–12)
Group suffixes|Oznake grupa
Blue|Plava
Orange|Narandzasta
Green|Zelena
Brown|Braon
Gray|Siva
White|Bela
Red|Crvena
Black|Crna
Yellow|Zuta
Violet|Ljubicasta
Rose|Roze
Aqua|Tirkizna
each|kom
fiber|vlakno
unknown|nepoznato
designed|projektovano
as_built|izvedeno
Warning|Upozorenje
Error|Greska
PASS|PROLAZI
FAIL|NE PROLAZI""".splitlines()
    )
)
SR.update(
    {
        'Frame outlet': 'Izvod iz rama',
        'Create frame outlet': 'Novi izvod iz rama',
        'Outlet properties': 'Svojstva izvoda',
        'Frame': 'Ram',
        'Fiber route': 'Putanja vlakana',
        'Tubes': 'Cevcice',
        'Fibers per tube': 'Vlakana po cevcici',
        'Elbow · reach the connector column': 'Lakat · do kolone konektora',
        'Straight · free ends for splicing': 'Prava · slobodni krajevi za '
        'varenje',
        'Add cable': 'Dodaj kabl',
        'Add cable to existing housing': 'Dodavanje kabla u postojece kuciste',
        'Connect additional fibers': 'Povezi dodatna vlakna',
        'Housing': 'Kuciste',
        'Cable end': 'Kraj kabla',
        'Module': 'Modul',
        'Detach cable end': 'Odvoji kraj kabla',
        'Widen only · no new terminations': 'Samo prosiri · bez novih '
        'zavrsetaka',
        'Aerial': 'Vazdusni',
        'Duct': 'Podzemni',
        'Micro duct': 'Mikrocevi',
        'Indoor': 'Instalacioni',
        'FDH / MDU cabinet': 'ODO / MDU ormar',
        'ODF / patch panel': 'ODF / prespojni panel',
        'Data and checks': 'Podaci i provere',
        'Optical budget and legend': 'Opticki budzet i legenda',
        'BOM': 'Specifikacija',
        'Check': 'Provera',
        'Route report': 'Izvestaj trase',
        'Source': 'Izvor',
        'Read path': 'Ucitaj trasu',
        'Input': 'Ulaz',
        'Ports': 'Portovi',
        'PON ports': 'PON portovi',
        'Address': 'Adresa',
        'Mounting': 'Montaza',
        'Connector': 'Konektor',
        'Capacity': 'Kapacitet',
        'Modules': 'Moduli',
        'Used': 'Zauzeto',
        'Number of modules': 'Broj modula',
        'Horizontal left': 'Horizontalno ulevo',
        'OK': 'U redu',
        'Ordinary': 'Obican',
        'Latent': 'Latentni',
        'Location': 'Mesto',
        'Drawing stopped. Select a command to continue.': 'Crtanje je '
        'prekinuto. '
        'Izaberite komandu.',
        'Workspace ready. Place equipment on the canvas.': 'Radni prostor je '
        'spreman. Postavi'
        'te opremu na '
        'crtez.',
        'Endpoint snapping enabled.': 'Hvatanje krajeva je ukljuceno.',
        'Endpoint snapping disabled; click within 3 pixels of an exact '
        'endpoint.': 'Hvatanje je '
        'iskljuceno; '
        'kliknite do 3 '
        'piksela od '
        'tacnog kraja.',
        'Choose the optical start, then its destination. Snap highlights '
        'exact endpoints.': 'Izaberite '
        'pocetak pa '
        'odrediste '
        'opticke trase. '
        'Hvatanje oznacav'
        'a tacne krajeve.',
        'Click two free fiber ends, cut ends or optical ports.': (
            'Kliknite dva slobodna kraja vlakna, preseka ili opticka porta.'
        ),
        'Choose the second free endpoint or port.': 'Izaberite drugi '
        'slobodan kraj ili port.',
        'Choose the table/legend insertion point. Esc cancels.': (
            'Izaberite tacku za tabelu/legendu. Esc otkazuje.'
        ),
        'Choose the table/legend insertion point. Esc cancels '
        'without saving.': (
            'Izaberite tacku za tabelu/legendu. Esc otkazuje bez upisa.'
        ),
        'STALE REPORT — regenerate after design changes.': 'ZASTAREO IZVESTA'
        'J — ponovo '
        'napravite posle '
        'izmene projekta.',
        'Delivered catalogues and TIA-598 / IEC 60304 are retained. Add '
        'custom copies here. Changes affect future cables; existing cable '
        'definitions are frozen.': 'Isporuceni '
        'katalozi i '
        'TIA-598 / IEC '
        '60304 se zadrzav'
        'aju. Dodajte '
        'svoje kopije. '
        'Izmene vaze za '
        'buduce kablove; '
        'postojeci cuvaju '
        'svoje definicije.',
        'Categories: ': 'Kategorije: ',
        'Color code: ': 'Raspored boja: ',
        'Storage / topology': 'Zapisi / topologija',
        'Working fiber': 'Radno vlakno',
        'Optical path': 'Opticka trasa',
        'Connection': 'Veza',
        'termination': 'zavrsetak',
        'splice': 'varenje',
        'patch': 'prespoj',
        'drop': 'privod',
        'through': 'prolaz',
        'Fiber quantities count logical fibers once; cut sections are not '
        'extra fibers. Unspecified cable lengths are excluded from metre '
        'totals.': 'Vlakna se broje '
        'po svom identite'
        'tu; preseci nisu '
        'dodatna vlakna. '
        'Nepoznate duzine '
        'nisu ukljucene u '
        'zbir metara.',
        'Checks are read-only. A warning requires design review; it does not '
        'change the network.': 'Provera samo '
        'cita podatke. '
        'Upozorenje trazi '
        'pregled projekta'
        '; ne menja mrezu.',
        'B+/C+ are the CAD reference ceilings, not a transceiver or '
        'receiver-overload certification. Verify equipment specifications '
        'and measured losses.': 'B+/C+ su granice '
        'iz CAD reference'
        ', a ne potvrda '
        'primopredajnika '
        'ili preopterecen'
        'ja prijemnika. '
        'Proverite '
        'specifikacije '
        'opreme i izmeren'
        'e gubitke.',
    }
)

SR.update(
    json.loads(
        Path(__file__)
        .with_name('i18n_extra_sr.json')
        .read_text(encoding='utf-8')
    )
)
for verb, translated_verb in (
    ('Create', 'Napravi'),
    ('Edit', 'Izmeni'),
    ('Place', 'Postavi'),
    ('Attach', 'Prikljuci'),
):
    for kind in (
        'ODF',
        'Patch Panel',
        'FDH Cabinet',
        'MDU Cabinet',
        'FAT Box',
        'OTB Box',
        'ODF module',
        'Patch panel',
        'FDH cabinet',
        'MDU cabinet',
        'FAT box',
        'OTB box',
        'SPLITTER',
        'OLT',
        'ONT',
    ):
        SR.setdefault(
            verb + ' ' + kind, translated_verb + ' ' + SR.get(kind, kind)
        )


def _message_patterns():
    patterns = []
    for source, target in SR.items():
        parts = list(Formatter().parse(source))
        if not any(
            field is not None for literal, field, spec, conversion in parts
        ):
            continue
        if (
            sum(
                c.isalpha()
                for literal, field, spec, conversion in parts
                for c in literal
            )
            < 3
        ):
            continue
        fields, regex, automatic = {}, [], 0
        for literal, field, spec, conversion in parts:
            regex.append(re.escape(literal))
            if field is None:
                continue
            key = field or str(automatic)
            automatic += field == ''
            if key in fields:
                regex.append('(?P=' + fields[key] + ')')
            else:
                name = 'v' + str(len(fields))
                fields[key] = name
                regex.append('(?P<' + name + '>.*?)')
        patterns.append(
            (
                sum(len(p[0]) for p in parts),
                re.compile(''.join(regex), re.DOTALL),
                target,
                fields,
            )
        )
    return sorted(patterns, key=lambda p: p[0], reverse=True)


_PATTERNS = _message_patterns()


def message(template, *values, language='en', **named):
    """Translate a template before inserting unmodified user values."""
    return translate(template, language).format(*values, **named)


def ui_language(widget):
    while widget is not None:
        if hasattr(widget, 'workspace_values'):
            return widget.workspace_values['language']
        widget = widget.parent() if hasattr(widget, 'parent') else None
    return 'en'


def ui_message(widget, template, *values, **named):
    return message(template, *values, language=ui_language(widget), **named)


class Phrase(str):
    """Report text with explicit system templates and protected user values."""

    def __new__(cls, template, *values):
        template = str(template)
        result = str.__new__(
            cls, template.format(*values) if values else template
        )
        result.parts = [('format' if values else 'term', template, values)]
        return result

    def render(self, language):
        return ''.join(
            message(
                template,
                *(
                    v.render(language) if isinstance(v, Phrase) else v
                    for v in values
                ),
                language=language,
            )
            if kind == 'format'
            else translate(template, language)
            if kind == 'term'
            else template
            for kind, template, values in self.parts
        )

    def __add__(self, other):
        result = str.__new__(Phrase, str(self) + str(other))
        result.parts = self.parts + (
            other.parts
            if isinstance(other, Phrase)
            else [('raw', str(other), ())]
        )
        return result

    def __radd__(self, other):
        result = str.__new__(Phrase, str(other) + str(self))
        result.parts = [('raw', str(other), ())] + self.parts
        return result


def report_strings(report, language):
    if report.get('text_provenance'):
        rows = [
            [
                value.render(language)
                if isinstance(value, Phrase)
                else str(value)
                for value in row
            ]
            for row in report['rows']
        ]
    else:
        # Compatibility for externally assembled tables from earlier
        # milestones.
        rows = [
            [
                translate(str(value), language)
                if column in (0, 2, 4)
                else str(value)
                for column, value in enumerate(row)
            ]
            for row in report['rows']
        ]
    return dict(
        title=translate(report['title'], language),
        columns=[translate(c, language) for c in report['columns']],
        rows=rows,
        notes=[
            v.render(language)
            if isinstance(v, Phrase)
            else translate(v, language)
            for v in report.get('warnings', [])
        ],
    )


def user_text(widget):
    """Mark label/combo values as user data to preserve them verbatim."""
    widget.setProperty('fqsd_i18n_user', True)
    if hasattr(widget, 'setTextFormat'):
        from .compat import PLAIN_TEXT

        widget.setTextFormat(PLAIN_TEXT)
    return widget


def translate(value, language='en'):
    if language != 'sr' or not isinstance(value, str) or not value.strip():
        return value
    return _translate_text(value)


@lru_cache(maxsize=4096)
def _translate_text(value):
    language = 'sr'
    if value in SR:
        return SR[value]
    if value.startswith('&') and value[1:] in SR:
        return '&' + SR[value[1:]]
    if value.endswith(':') and value[:-1] in SR:
        return SR[value[:-1]] + ':'
    for unused, pattern, target, fields in _PATTERNS:
        match = pattern.fullmatch(value)
        if match:
            result, automatic = [], 0
            for literal, field, spec, conversion in Formatter().parse(target):
                result.append(literal)
                if field is not None:
                    key = field or str(automatic)
                    automatic += field == ''
                    result.append(match.group(fields[key]))
            return ''.join(result)
    if '\n' in value:
        return '\n'.join(
            translate(line, language) for line in value.split('\n')
        )
    # Ellipses/punctuation are a Qt presentation difference from CAD captions.
    tail = (
        '…' if value.endswith('…') else '...' if value.endswith('...') else ''
    )
    base = value[: -len(tail)] if tail else value
    if base in SR:
        return SR[base] + tail
    # Known label fragments only; identifiers, model names and user text are
    # kept.
    if ': ' in value:
        prefix, rest = value.split(': ', 1)
        if prefix + ': ' in SR:
            nested = prefix in (
                'Invalid multi-cable housing layout',
                'Invalid saved drawing annotation',
            )
            return SR[prefix + ': '] + (
                translate(rest, language) if nested else rest
            )
        if prefix in SR:
            return (
                SR[prefix]
                + ': '
                + (
                    translate(rest, language)
                    if prefix in ('Error', 'Warning')
                    else rest
                )
            )
    for source, target in SR.items():
        if (
            (
                len(source) >= 8
                or source
                in (
                    'Saved ',
                    'Undid: ',
                    'Redid: ',
                    'Edit ',
                    'Create ',
                    'Place ',
                    'Attach ',
                    'Delete ',
                )
            )
            and source[-1:].isspace()
            and value.startswith(source)
        ):
            return target + value[len(source):]
        if (
            len(source) >= 8
            and source[:1].isspace()
            and value.endswith(source)
        ):
            return value[: -len(source)] + target
    return value


def localize_tree(root, language, recursive=True):
    from qgis.PyQt.QtWidgets import (
        QAbstractButton,
        QComboBox,
        QFileDialog,
        QGroupBox,
        QLabel,
        QTabBar,
        QTableWidget,
    )

    objects = [root]
    while objects:
        obj = objects.pop()
        if isinstance(obj, QFileDialog):
            continue
        if recursive:
            objects.extend(reversed(obj.children()))
        if obj.property('fqsd_i18n_user'):
            continue
        for getter, setter in (
            ('windowTitle', 'setWindowTitle'),
            ('toolTip', 'setToolTip'),
            ('statusTip', 'setStatusTip'),
            ('placeholderText', 'setPlaceholderText'),
            ('specialValueText', 'setSpecialValueText'),
            ('suffix', 'setSuffix'),
        ):
            if hasattr(obj, getter) and hasattr(obj, setter):
                _localize_property(obj, getter, setter, language)
        if isinstance(obj, (QLabel, QAbstractButton, QGroupBox)):
            _localize_property(
                obj,
                'title' if isinstance(obj, QGroupBox) else 'text',
                'setTitle' if isinstance(obj, QGroupBox) else 'setText',
                language,
            )
        # QAction imports differ across Qt versions; test its meta-class name.
        if obj.inherits('QAction'):
            _localize_property(obj, 'text', 'setText', language)
        if isinstance(obj, QComboBox) and not obj.isEditable():
            blocked = obj.blockSignals(True)
            for i in range(obj.count()):
                if obj.itemData(i) is not None:
                    key = 'fqsd_i18n_item_' + str(i)
                    current, last = (
                        obj.itemText(i),
                        obj.property(key + '_last'),
                    )
                    original = (
                        obj.property(key) if last == current else current
                    )
                    obj.setProperty(key, original)
                    value = translate(original, language)
                    obj.setProperty(key + '_last', value)
                    if current != value:
                        obj.setItemText(i, value)
            obj.blockSignals(blocked)
        # A QTabWidget and its child QTabBar share their titles. Translating
        # both caches the translated text as the child's original, preventing
        # a dialog first opened in Serbian from switching back to English.
        if isinstance(obj, QTabBar):
            for i in range(obj.count()):
                key = 'fqsd_i18n_tab_' + str(i)
                original = obj.property(key) or obj.tabText(i)
                obj.setProperty(key, original)
                value = translate(original, language)
                if obj.tabText(i) != value:
                    obj.setTabText(i, value)
        if isinstance(obj, QTableWidget):
            for i in range(obj.columnCount()):
                header = obj.horizontalHeaderItem(i)
                if header is not None:
                    key = 'fqsd_i18n_header_' + str(i)
                    original = obj.property(key) or header.text()
                    obj.setProperty(key, original)
                    value = translate(original, language)
                    if header.text() != value:
                        header.setText(value)


def _localize_property(obj, getter, setter, language):
    current = (
        obj.full_text
        if getter == 'text' and hasattr(obj, 'full_text')
        else getattr(obj, getter)()
    )
    key = 'fqsd_i18n_' + getter
    original, last = obj.property(key), obj.property(key + '_last')
    if current == last and obj.property(key + '_language') == language:
        return
    if original is None or (last is not None and current != last):
        original = current
    value = translate(original, language)
    obj.setProperty(key, original)
    obj.setProperty(key + '_last', value)
    obj.setProperty(key + '_language', language)
    if current != value:
        getattr(obj, setter)(value)
