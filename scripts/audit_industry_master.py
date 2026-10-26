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
    payload=json.loads(Path(master).read_text(encoding='utf-8'))
    observations=payload.get('observations',[])
    with Path(snapshot).open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    codes = [row['code'].zfill(6) for row in rows]
    counts = Counter(mapping.get(code, {}).get('mapping_status', 'missing') for code in codes)
    names={row['code'].zfill(6):str(row.get('name') or row.get('名称') or '').strip() for row in rows}
    source_names={}
    with Path(metadata['resolved_source_file']).open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            label=str(row.get('name') or '').strip()
            if label:source_names.setdefault(row['code'],set()).add(label)
    evidence={}
    for item in observations:
        code=str(item.get('code','')).zfill(6)
        evidence.setdefault(code,[]).append({key:item.get(key) for key in ('sw_industry','source','observed_date','source_line','file_sha256')})

    def review(code):
        item=mapping.get(code,{})
        candidates=sorted(source_names.get(code,set()))
        snapshot_name=names.get(code,'')
        name=snapshot_name or (candidates[0] if len(candidates)==1 else '')
        name_source='market_snapshot' if snapshot_name else ('archived_source_csv' if len(candidates)==1 else 'unresolved')
        return {'code':code,'name':name,'name_source':name_source,'candidate_names':candidates,
                'status':item.get('mapping_status','missing'),
                'mapping_date':item.get('mapping_date'),
                'candidate_observations':evidence.get(code,[]),
                'latest_candidate_observations':[row for row in evidence.get(code,[]) if row.get('observed_date')==item.get('mapping_date')],
                'review_note':'No classification inferred; retain unknown until dated source evidence resolves it.'}

    universe_review=[review(code) for code in sorted(set(codes)) if mapping.get(code,{}).get('mapping_status')!='mapped']
    master_review=[review(code) for code in sorted(mapping) if mapping[code].get('mapping_status')!='mapped']
    return {'master_metadata': metadata, 'universe_file': str(Path(snapshot).resolve()),
            'universe_sha256': hashlib.sha256(Path(snapshot).read_bytes()).hexdigest(),
            'universe_rows': len(rows), 'unique_codes': len(set(codes)), 'status_counts': dict(counts),
            'coverage': counts['mapped']/len(rows) if rows else None,
            'universe_review_count':len(universe_review),'universe_review':universe_review,
            'master_review_count':len(master_review),'master_review':master_review,
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
