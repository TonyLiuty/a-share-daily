"""Opt-in SW-primary research policy; isolated from existing strategy history."""
import csv
from datetime import date, timedelta
from pathlib import Path
from industry_mapping import load_history, build_mapping, attach_mapping, select_by_primary

VERSION = '1.1.0'


def prepare_policy(config, output, requested=None, history=None):
    policy=requested or config.get('industry_policy','legacy')
    if policy=='legacy':
        if config.get('strategy_version')==VERSION:
            raise ValueError('experimental version cannot use legacy policy')
        return dict(config)
    if policy!='sw-primary':raise ValueError('unknown industry policy')
    history=history or config.get('industry_history')
    if not history or not Path(history).is_dir():
        raise ValueError('sw-primary requires --industry-history pointing to archived daily reports')
    output=Path(output).resolve(); history=Path(history).resolve()
    if output==history.parent:
        raise ValueError('experimental output must be separate from archived production output')
    signal_path=output/'state'/'signals.csv'
    if signal_path.exists():
        with signal_path.open(encoding='utf-8-sig',newline='') as stream:
            if any(row.get('strategy_version')!=VERSION for row in csv.DictReader(stream)):
                raise ValueError('cannot mix experimental selection with existing strategy signals')
    if config.get('strategy_version') not in ('1.0.0',VERSION):
        raise ValueError('unexpected strategy version for experimental policy')
    result=dict(config)
    result.update(strategy_version=VERSION,industry_policy=policy,industry_history=str(history))
    return result


def mapped_selection(rows, history, signal_date, count=10, cap=2):
    # Prior-date records only: same-day company data may have been collected
    # after the selection being reconstructed.
    cutoff=(date.fromisoformat(signal_date)-timedelta(days=1)).isoformat()
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
           'source':'archived_company_profiles','cutoff':cutoff,'strategy_version':VERSION,
           'mapped_count':known,'total_count':len(mapped),'coverage':known/len(mapped) if mapped else 0,
           'target_count':count,'selected_count':result['selected_count'],'shortfall':result['shortfall'],
           'primary_counts':result['industry_counts'],
           'eligible':bool(mapped) and known==len(mapped) and result['shortfall']==0,
           'limitations':'Local historical candidates are not a complete security master. Full scored-universe coverage is required before writing experimental signals.'}
    return mapped,result,audit


def require_mapped_selection(audit):
    if not audit['eligible']:
        raise ValueError(f"industry mapping insufficient: {audit['mapped_count']}/{audit['total_count']} scored stocks, selection shortfall {audit['shortfall']}; no experimental signals written")
