"""Point-in-time local SW industry mapping and deterministic industry selection.

Historical candidates are observations saved on a report date, not a complete
historical security master. Unobserved stocks remain unknown. No network access.
"""
import csv
import math
import re
from datetime import date
from pathlib import Path


SW_PRIMARY = frozenset('农林牧渔 基础化工 钢铁 有色金属 电子 家用电器 食品饮料 纺织服饰 轻工制造 医药生物 公用事业 交通运输 房地产 商贸零售 社会服务 综合 建筑材料 建筑装饰 电力设备 国防军工 计算机 传媒 通信 银行 非银金融 汽车 机械设备 煤炭 石油石化 环保 美容护理'.split())


def _day(value):
    text = str(value)
    date.fromisoformat(text)
    return text


def _records(rows):
    return rows.to_dict('records') if hasattr(rows, 'to_dict') else list(rows)


def primary_industry(sw_industry):
    """Only accept an explicit recognized first segment; never infer from company."""
    primary = re.split(r'\s*[—–－-]\s*', str(sw_industry or '').strip(), maxsplit=1)[0]
    return primary if primary in SW_PRIMARY else None


def load_history(daily_directory, as_of):
    """Read only dated candidates files visible at as_of (inclusive)."""
    as_of = _day(as_of)
    observations = []
    for path in sorted(Path(daily_directory).glob('*/candidates.csv')):
        try:
            observed = _day(path.parent.name)
        except ValueError:
            continue
        if observed > as_of:
            continue
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                observations.append({'code': row.get('code', ''), 'sw_industry': row.get('sw_industry', ''),
                                     'observed_date': observed, 'source': str(path.resolve())})
    return observations


def build_mapping(observations, as_of):
    """Latest dated valid local observation per code; same-date conflicts stay unknown."""
    as_of = _day(as_of)
    latest = {}
    for row in _records(observations):
        observed = _day(row['observed_date'])
        raw_code = str(row.get('code') or '').strip()
        code = raw_code.zfill(6)
        label = str(row.get('sw_industry') or '').strip()
        primary = primary_industry(label)
        if observed > as_of or not re.fullmatch(r'\d{1,6}', raw_code) or not primary:
            continue
        entry = {'code': code, 'sw_industry': label, 'primary_industry': primary,
                 'industry_taxonomy': '申万', 'industry_level': '一级',
                 'mapping_date': observed, 'mapping_source': str(row.get('source') or 'local_saved_candidates'),
                 'mapping_status': 'mapped'}
        prior = latest.get(code)
        if prior is None or observed > prior['mapping_date']:
            latest[code] = entry
        elif observed == prior['mapping_date'] and label != prior['sw_industry']:
            prior['mapping_status'] = 'conflicting_same_date'
            prior['primary_industry'] = None
    return latest


def attach_mapping(rows, mapping, as_of):
    """Return new row dicts with explicit mapping provenance, preserving input scores."""
    as_of = _day(as_of)
    output = []
    for row in _records(rows):
        current = dict(row)
        code = str(row.get('code') or '').strip().zfill(6)
        entry = mapping.get(code)
        if entry and entry['mapping_date'] <= as_of and entry['mapping_status'] == 'mapped':
            current.update(entry)
        else:
            current.update(primary_industry=None, mapping_date=None, mapping_source=None,
                           mapping_status='unmapped', industry_taxonomy='申万', industry_level='一级')
        output.append(current)
    return output


def select_by_primary(rows, count=10, max_per_industry=2):
    """Score-descending, code-tie-break selection. Unknowns never receive an exemption."""
    if count < 1 or max_per_industry < 1:
        raise ValueError('selection count and industry cap must be positive')
    valid, rejected = [], []
    for row in _records(rows):
        copied = dict(row)
        try:
            score = float(copied.get('total_score'))
        except (ValueError, TypeError):
            score = float('nan')
        if copied.get('mapping_status') != 'mapped' or copied.get('primary_industry') not in SW_PRIMARY:
            rejected.append({'code': copied.get('code'), 'reason': 'unmapped_primary_industry'})
        elif not math.isfinite(score):
            rejected.append({'code': copied.get('code'), 'reason': 'invalid_score'})
        else:
            valid.append(copied)
    valid.sort(key=lambda row: (-float(row['total_score']), str(row.get('code', '')).zfill(6)))
    selected, counts, seen = [], {}, set()
    for row in valid:
        code = str(row.get('code', '')).zfill(6)
        if code in seen:
            rejected.append({'code': code, 'reason': 'duplicate_code'})
            continue
        seen.add(code)
        industry = row['primary_industry']
        if counts.get(industry, 0) >= max_per_industry:
            rejected.append({'code': code, 'reason': 'industry_cap'})
            continue
        if len(selected) >= count:
            continue
        row['rank'] = len(selected) + 1
        selected.append(row)
        counts[industry] = counts.get(industry, 0) + 1
    return {'selected': selected, 'requested_count': count, 'selected_count': len(selected),
            'shortfall': max(0, count - len(selected)), 'industry_counts': counts,
            'max_per_industry': max_per_industry, 'rejected': rejected}
