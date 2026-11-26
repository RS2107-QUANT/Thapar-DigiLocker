"""Compile the supplied Thapar Excel timetable into a lightweight JSON index.

Reads the OOXML workbook with Python's standard library. This avoids loading a
large, heavily formatted spreadsheet whenever the desktop UI opens. The source
XLSX is unchanged and kept alongside the parsed JSON for traceability.
"""
from __future__ import annotations

import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

SOURCE = Path(__file__).parent / 'assets' / 'university_timetable_2026.xlsx'
OUTPUT = Path(__file__).parent / 'assets' / 'timetable_index.json'
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
CELL = re.compile(r'^([A-Z]+)(\d+)$')
BATCH = re.compile(r'^[1-4][A-Z][A-Z0-9]{1,5}$')

def location(ref):
    letters, digits = CELL.match(ref).groups()
    col = 0
    for a in letters:
        col = col * 26 + ord(a) - 64
    return int(digits), col


def load_sheet(archive, sheet_number, shared):
    root = ET.fromstring(archive.read(f'xl/worksheets/sheet{sheet_number}.xml'))
    cells = {}
    for row in root.find(NS + 'sheetData'):
        for cell in row.findall(NS + 'c'):
            point = location(cell.attrib['r'])
            v = cell.find(NS + 'v')
            if v is not None and v.text is not None:
                value = shared[int(v.text)] if cell.attrib.get('t') == 's' else v.text
            elif cell.attrib.get('t') == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.iter(NS + 't'))
            else:
                continue
            value = str(value).strip()
            if value:
                cells[point] = value
    # Merges can include groups (lecture), small tutorials, or paired practicals.
    # Resolve every displayed cell to the original upper-left cell.
    merged = {}
    node = root.find(NS + 'mergeCells')
    if node is not None:
        for span in node:
            name = span.attrib['ref']
            start, _, end = name.partition(':')
            r1, c1 = location(start)
            r2, c2 = location(end or start)
            if r1 > 180 or r2 < 3:
                continue
            for row in range(max(3, r1), min(180, r2) + 1):
                for col in range(c1, c2 + 1):
                    merged[(row, col)] = (r1, c1, c2)

    def effective(row, col):
        key = (row, col)
        if key in cells:
            return cells[key]
        anchor = merged.get(key)
        return cells.get((anchor[0], anchor[1]), '') if anchor else ''

    def span(row, col):
        anchor = merged.get((row, col))
        return (anchor[1], anchor[2]) if anchor else (col, col + 1)

    return cells, effective, span


def clock(period):
    n = 8 * 60 + (period - 1) * 50
    return f'{(n // 60) % 24:02}:{n % 60:02}'


def classify(subject):
    t = subject.upper().strip()
    if t in ('LAB', 'LAB-1', 'LAB-2', 'LAB-3') or t.endswith('P') or ' P' in t:
        return 'Practical'
    if t.endswith('T'):
        return 'Tutorial'
    if t.endswith('L'):
        return 'Lecture'
    return 'Class'


def compile_index():
    results = {}
    sheet_names = [
        'FIRST YEAR B', 'FIRST YEAR A', '2ND YEAR A', '2ND YEAR B',
        '3RD YEAR A', '3RD YEAR B', '4TH YEAR A', '4TH YEAR B',
    ]
    with zipfile.ZipFile(SOURCE) as archive:
        root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
        shared = [''.join(t.text or '' for t in item.iter(NS + 't'))
                  for item in root.findall(NS + 'si')]
        for sheet_number, sheet_name in enumerate(sheet_names, start=1):
            cells, effective, span = load_sheet(archive, sheet_number, shared)
            header = 7 if sheet_number in (1, 7, 8) else 6
            first = 9 if sheet_number == 1 else 7 if sheet_number == 6 else 8
            # Course header is one row above the practical batch row.
            batch_columns = sorted([
                (col, val.replace(' ', '').upper())
                for (row, col), val in cells.items()
                if row == header and BATCH.fullmatch(val.replace(' ', '').upper())
                and col >= 5
            ])
            for col, batch in batch_columns:
                # Some fourth-year batch codes appear twice: an old Jan–May
                # block and the current Jul–Dec block. Compare populated slots
                # and retain the column with the actual current timetable.
                branch_row = 4 if sheet_number in (1, 7, 8) else 3
                lecture_row = 5 if sheet_number in (1, 7, 8) else 4
                tutorial_row = 6 if sheet_number in (1, 7, 8) else 5
                branch = effective(branch_row, col)
                lecture = effective(lecture_row, col)
                tutorial = effective(tutorial_row, col)
                timetable = {}
                for d, day in enumerate(DAY_NAMES):
                    first_row = first + 28 * d
                    slots = []
                    for period in range(1, 15):
                        row = first_row + (period - 1) * 2
                        subject = effective(row, col).strip()
                        if not subject:
                            # Sometimes individual subject codes are in the right
                            # half of the two-column batch slot.
                            candidate = cells.get((row, col + 1), '').strip()
                            if re.match(r'^[A-Z]{2,}\d{2,}', candidate):
                                subject = candidate
                        if not subject:
                            continue
                        start_col, end_col = span(row, col)
                        pieces = []
                        for c in range(start_col, end_col + 1):
                            detail = effective(row + 1, c).strip()
                            if detail and detail not in pieces and detail != subject:
                                pieces.append(detail)
                        slots.append({
                            'period': period,
                            'time': f'{clock(period)}–{clock(period + 1)}',
                            'subject': subject,
                            'details': '  ·  '.join(pieces),
                            'type': classify(subject),
                        })
                    timetable[day] = slots
                candidate = {
                    'sheet': sheet_name,
                    'year': int(batch[0]),
                    'branch': branch or 'Not specified',
                    'lecture': lecture,
                    'tutorial': tutorial,
                    'days': timetable,
                }
                score = sum(len(timetable[d]) for d in DAY_NAMES)
                if batch not in results or score > sum(
                        len(results[batch]['days'][d]) for d in DAY_NAMES):
                    results[batch] = candidate
    OUTPUT.write_text(json.dumps({
        'source': 'TIME TABLE JULYTODEC2026(1).xlsx',
        'term': 'July–December 2026',
        'days': DAY_NAMES,
        'batches': results,
    }, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return results

if __name__ == '__main__':
    results = compile_index()
    print('Indexed', len(results), 'batches in', OUTPUT, 'size', OUTPUT.stat().st_size)
    for batch in ('2Q1A','2C1A','3C1A','1A1A','4Q1A'):
        if batch in results:
            d=results[batch]
            print('\n',batch, d['branch'], d['lecture'], d['tutorial'])
            for day,sessions in d['days'].items():
                print(' ', day, len(sessions), [(s['period'],s['subject'],s['details'][:30]) for s in sessions[:4]])
