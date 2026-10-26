"""Import a dated, explicitly declared SW2021 local security master; no network."""
import argparse
import csv
import hashlib
import io
import json
import re
from datetime import date
from pathlib import Path
from industry_mapping import primary_industry


REQUIRED = {'code', 'sw_industry', 'observed_date', 'source'}


def iso_day(value):
    text = str(value).strip()
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', text):
        raise ValueError('dates must use YYYY-MM-DD')
    date.fromisoformat(text)
    return text


def import_mapping(path, as_of, *, taxonomy, taxonomy_version):
    """Reject future/invalid records, retain latest-date ambiguity as unknown.

    Dates are provider declarations: a checksum proves content identity, not when
    the data was actually available. Archive the original file at acquisition.
    """
    cutoff = iso_day(as_of)
    if taxonomy != 'sw' or taxonomy_version != '2021':
        raise ValueError('only explicitly declared sw taxonomy version 2021 is supported')
    path = Path(path)
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    if not REQUIRED.issubset(reader.fieldnames or []):
        raise ValueError('missing columns: ' + ','.join(sorted(REQUIRED - set(reader.fieldnames or []))))
    groups = {}
    observations = []
    for line, row in enumerate(reader, 2):
        code = str(row.get('code') or '').strip()
        if not re.fullmatch(r'[0-9]{6}', code):
            raise ValueError(f'line {line}: code must contain six digits')
        observed = iso_day(row.get('observed_date') or '')
        if observed > cutoff:
            raise ValueError(f'line {line}: future observation exceeds cutoff {cutoff}')
        source = str(row.get('source') or '').strip()
        if not source:
            raise ValueError(f'line {line}: source is required')
        for key, expected in (('taxonomy', taxonomy), ('taxonomy_version', taxonomy_version)):
            if row.get(key) and row[key].strip() != expected:
                raise ValueError(f'line {line}: conflicting {key}')
        label = str(row.get('sw_industry') or '').strip()
        item = {'code': code, 'sw_industry': label, 'observed_date': observed,
                'source': source, 'source_line': line, 'file_sha256': digest}
        observations.append(item)
        prior = groups.get(code, [])
        if not prior or observed > prior[0]['observed_date']:
            groups[code] = [item]
        elif observed == prior[0]['observed_date']:
            prior.append(item)
    mapping = {}
    for code, latest in sorted(groups.items()):
        labels = {item['sw_industry'] for item in latest}
        label = next(iter(labels)) if len(labels) == 1 else None
        primary = primary_industry(label)
        status = 'conflicting_same_date' if len(labels) > 1 else ('mapped' if primary else 'unmapped')
        mapping[code] = {'code': code, 'sw_industry': label, 'primary_industry': primary,
                         'mapping_status': status, 'mapping_date': latest[0]['observed_date'],
                         'mapping_source': '; '.join(sorted({item['source'] for item in latest})),
                         'industry_taxonomy': '申万', 'industry_level': '一级',
                         'taxonomy_version': taxonomy_version, 'file_sha256': digest}
    mapped = sum(item['mapping_status'] == 'mapped' for item in mapping.values())
    return {'schema_version': 1, 'metadata': {
                'taxonomy': taxonomy, 'taxonomy_version': taxonomy_version, 'level': 'primary',
                'cutoff': cutoff, 'source': 'imported_local_csv', 'source_file': str(path.resolve()),
                'file_sha256': digest, 'mapping_date': max((x['mapping_date'] for x in mapping.values()), default=None),
                'record_count': len(observations), 'security_count': len(mapping), 'mapped_count': mapped,
                'coverage': mapped / len(mapping) if mapping else 0,
                'availability': 'observed_date is declared availability; independently archive acquisition timestamp before historical use'},
            'mapping': mapping, 'observations': observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--taxonomy', choices=['sw'], required=True)
    parser.add_argument('--taxonomy-version', choices=['2021'], required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('input and output must differ')
    result = import_mapping(args.input, args.as_of, taxonomy=args.taxonomy, taxonomy_version=args.taxonomy_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps(result['metadata'], ensure_ascii=False))


if __name__ == '__main__':
    main()
