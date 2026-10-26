"""Opt-in SW-primary research policy; isolated from existing strategy history."""
import csv
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path, PureWindowsPath
from industry_mapping import load_history, build_mapping, attach_mapping, select_by_primary, primary_industry
from import_industry_mapping import iso_day, import_mapping

VERSION = '1.1.0'


def load_master(path):
    content=Path(path).read_bytes()
    payload=json.loads(content)
    if not isinstance(payload,dict) or payload.get('schema_version')!=1:
        raise ValueError('unsupported industry master schema')
    meta=payload.get('metadata',{})
    if (meta.get('taxonomy'),meta.get('taxonomy_version'),meta.get('level'))!=('sw','2021','primary'):
        raise ValueError('industry master must declare sw/2021/primary')
    iso_day(meta.get('cutoff',''))
    if not re.fullmatch(r'[0-9a-f]{64}',str(meta.get('file_sha256',''))):
        raise ValueError('industry master requires source file SHA-256')
    mapping=payload.get('mapping')
    if not isinstance(mapping,dict):raise ValueError('industry master mapping must be an object')
    for code,item in mapping.items():
        if not re.fullmatch(r'\d{6}',code) or not isinstance(item,dict) or item.get('code')!=code:
            raise ValueError('invalid industry master security key')
        observed=iso_day(item.get('mapping_date',''))
        if observed>meta['cutoff']:raise ValueError('mapping date exceeds master cutoff')
        if item.get('mapping_status') not in ('mapped','unmapped','conflicting_same_date'):
            raise ValueError('unknown industry master mapping status')
        if item.get('file_sha256')!=meta['file_sha256'] or not item.get('mapping_source'):
            raise ValueError('industry master row provenance missing or inconsistent')
        if item.get('industry_taxonomy')!='申万' or item.get('industry_level')!='一级' or item.get('taxonomy_version')!='2021':
            raise ValueError('industry master row classification metadata inconsistent')
        if item['mapping_status']=='mapped' and (not primary_industry(item.get('sw_industry')) or primary_industry(item.get('sw_industry'))!=item.get('primary_industry')):
            raise ValueError('mapped primary industry disagrees with explicit SW label')
        if item['mapping_status']!='mapped' and item.get('primary_industry') is not None:
            raise ValueError('unknown/conflicting mapping must not provide primary industry')
    source_name=meta.get('source_file')
    if not isinstance(source_name,str) or not source_name:
        raise ValueError('industry master requires archived source CSV')
    # Prefer the adjacent original when the archive has been moved as a pair.
    # A changed adjacent copy must fail instead of silently using another file.
    basename=PureWindowsPath(source_name).name if '\\' in source_name else Path(source_name).name
    adjacent=Path(path).resolve().parent/basename
    source=adjacent if adjacent.exists() else Path(source_name)
    if not source.is_file():raise ValueError('archived industry source CSV is unavailable')
    actual=hashlib.sha256(source.read_bytes()).hexdigest()
    if actual!=meta['file_sha256']:raise ValueError('archived industry source CSV hash mismatch')
    rebuilt=import_mapping(source,meta['cutoff'],taxonomy='sw',taxonomy_version='2021')
    if mapping!=rebuilt['mapping'] or payload.get('observations')!=rebuilt['observations']:
        raise ValueError('industry master mapping or observations disagree with archived CSV')
    for field in ('record_count','security_count','mapped_count','coverage','mapping_date','source'):
        if meta.get(field)!=rebuilt['metadata'].get(field):
            raise ValueError(f'industry master metadata {field} disagrees with archived CSV')
    return mapping,dict(meta,artifact_sha256=hashlib.sha256(content).hexdigest(),
                        source_verified=True,resolved_source_file=str(source.resolve()))


def prepare_policy(config, output, requested=None, history=None, master=None):
    policy=requested or config.get('industry_policy','legacy')
    if policy=='legacy':
        if config.get('strategy_version')==VERSION:
            raise ValueError('experimental version cannot use legacy policy')
        return dict(config)
    if policy!='sw-primary':raise ValueError('unknown industry policy')
    if master is not None and history is not None:raise ValueError('choose industry master or history, not both')
    if master is None and history is None:
        master=config.get('industry_master'); history=config.get('industry_history')
    if master and history:raise ValueError('choose industry master or history, not both')
    output=Path(output).resolve()
    if master:
        master=Path(master).resolve()
        if not master.is_file():raise ValueError('industry master file does not exist')
        if master.is_relative_to(output):raise ValueError('industry master cannot be inside experimental output')
        load_master(master)
    elif history:
        history=Path(history).resolve()
        if not history.is_dir():raise ValueError('industry history directory does not exist')
        if output==history.parent or history.is_relative_to(output) or output.is_relative_to(history):
            raise ValueError('experimental output must be separate from archived production output')
    else:raise ValueError('sw-primary requires --industry-history or --industry-master')
    signal_path=output/'state'/'signals.csv'
    if signal_path.exists():
        with signal_path.open(encoding='utf-8-sig',newline='') as stream:
            if any(row.get('strategy_version')!=VERSION for row in csv.DictReader(stream)):
                raise ValueError('cannot mix experimental selection with existing strategy signals')
    if config.get('strategy_version') not in ('1.0.0',VERSION):
        raise ValueError('unexpected strategy version for experimental policy')
    result=dict(config)
    result.pop('industry_history',None); result.pop('industry_master',None)
    result.update(strategy_version=VERSION,industry_policy=policy)
    result['industry_master' if master else 'industry_history']=str(master or history)
    return result


def mapped_selection(rows, history, signal_date, count=10, cap=2, master=None):
    # Prior-date records only: same-day company data may have been collected
    # after the selection being reconstructed.
    cutoff=(date.fromisoformat(signal_date)-timedelta(days=1)).isoformat()
    master_meta=None
    if master:
        if history:raise ValueError('choose industry master or history, not both')
        mapping,master_meta=load_master(master)
    else:
        mapping=build_mapping(load_history(history,cutoff),cutoff)
    mapped=attach_mapping(rows,mapping,cutoff)
    for row in mapped:
        row['snapshot_industry']=row.get('industry')
        row['industry']=row.get('primary_industry') or '未分类'
        if 'sw_industry' in row:
            row['mapping_sw_industry']=row.pop('sw_industry')
    result=select_by_primary(mapped,count,cap)
    known=sum(row.get('mapping_status')=='mapped' for row in mapped)
    audit={'taxonomy':'sw','level':'primary','version':'asof-v1',
           'source':'imported_local_csv' if master else 'archived_company_profiles','cutoff':cutoff,'strategy_version':VERSION,
           'mapped_count':known,'total_count':len(mapped),'coverage':known/len(mapped) if mapped else 0,
           'target_count':count,'selected_count':result['selected_count'],'shortfall':result['shortfall'],
           'primary_counts':result['industry_counts'],
           'eligible':bool(mapped) and known==len(mapped) and result['shortfall']==0,
           'limitations':'Local historical candidates are not a complete security master. Full scored-universe coverage is required before writing experimental signals.'}
    if master_meta:
        audit['master_metadata']=master_meta
        audit['master_path']=str(Path(master).resolve())
        audit['late_mapping_count']=sum(item['mapping_date']>cutoff for item in mapping.values())
    return mapped,result,audit


def require_mapped_selection(audit):
    if not audit['eligible']:
        raise ValueError(f"industry mapping insufficient: {audit['mapped_count']}/{audit['total_count']} scored stocks, selection shortfall {audit['shortfall']}; no experimental signals written")
