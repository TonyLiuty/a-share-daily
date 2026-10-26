"""Measure current mapping coverage of a saved universe, not historical returns."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from experimental_selection import load_master


def audit(master, snapshot):
    mapping, metadata = load_master(master)
    with Path(snapshot).open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    codes = [row['code'].zfill(6) for row in rows]
    counts = Counter(mapping.get(code, {}).get('mapping_status', 'missing') for code in codes)
    return {'master_metadata': metadata, 'universe_file': str(Path(snapshot).resolve()),
            'universe_sha256': hashlib.sha256(Path(snapshot).read_bytes()).hexdigest(),
            'universe_rows': len(rows), 'unique_codes': len(set(codes)), 'status_counts': dict(counts),
            'coverage': counts['mapped']/len(rows) if rows else None,
            'interpretation': 'Current mapping coverage of saved security codes only; not evidence of historical availability or investment performance.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in (args.master.resolve(), args.snapshot.resolve()):
        parser.error('output must differ from input files')
    result = audit(args.master, args.snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never replace an existing source archive or earlier audit, even when the
    # caller accidentally chooses metadata.source_file as the output path.
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result['status_counts']))
