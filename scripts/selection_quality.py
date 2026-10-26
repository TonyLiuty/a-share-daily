"""Read-only gates for selection inputs and industry constraints.

Callers supply a common industry taxonomy and level. No mapping, score filling,
selection, or historical rewriting occurs in this module.
"""
from collections import Counter
from math import isfinite


UNKNOWN = {'', '未分类', '未知', 'nan', 'none', '<na>', '--'}
CORE_FIELDS = ('amount', 'return_60d_snapshot', 'turnover_pct', 'pe', 'change_pct')


def _records(rows):
    return rows.to_dict('records') if hasattr(rows, 'to_dict') else list(rows)


def _label(value):
    text = str(value).strip() if value is not None else ''
    return '' if text.lower() in UNKNOWN else text


def _valid_number(value, positive=False):
    try:
        value = float(value)
        return isfinite(value) and (not positive or value > 0)
    except (ValueError, TypeError):
        return False


def _result(reasons, **details):
    return {'status': 'passed' if not reasons else 'rejected',
            'allowed': not reasons, 'reasons': reasons, **details}


def assess_selection_inputs(rows, *, taxonomy=None, level=None, taxonomy_version=None,
                            industry_field='industry', min_industry_coverage=1.0,
                            min_core_coverage=0.95):
    """Assess the *filtered preselection universe*, before ranking or diversification.

    taxonomy/level describe all labels, not vendor names. Per-row taxonomy/level
    overrides expose accidental mixing. Unknown labels must later be rejected by
    assess_selected_industries even if an explicit lower input threshold is used.
    """
    for threshold in (min_industry_coverage, min_core_coverage):
        if not 0 <= threshold <= 1:
            raise ValueError('coverage thresholds must be between 0 and 1')
    records = _records(rows)
    n = len(records)
    reasons = []
    if not n:
        reasons.append('empty_universe')
    classified = sum(bool(_label(r.get(industry_field))) for r in records)
    coverage = classified / n if n else 0.0
    if coverage < min_industry_coverage:
        reasons.append('insufficient_industry_coverage')
    systems = {_label(r.get('industry_taxonomy')) or _label(taxonomy) for r in records}
    levels = {_label(r.get('industry_level')) or _label(level) for r in records}
    versions = {_label(r.get('taxonomy_version')) for r in records}
    if not _label(taxonomy_version) or '' in versions:
        reasons.append('unknown_industry_taxonomy_version')
    if len(versions - {''}) > 1 or (versions - {''} and _label(taxonomy_version) and _label(taxonomy_version) not in versions):
        reasons.append('mixed_industry_taxonomy_version')
    if not _label(taxonomy) or '' in systems:
        reasons.append('unknown_industry_taxonomy')
    if len(systems) > 1 or (systems and _label(taxonomy) not in systems):
        reasons.append('mixed_industry_taxonomy')
    if not _label(level) or '' in levels:
        reasons.append('unknown_industry_level')
    if len(levels) > 1 or (levels and _label(level) not in levels):
        reasons.append('mixed_industry_level')
    factors = {}
    for field in CORE_FIELDS:
        count = sum(_valid_number(r.get(field), field in ('amount', 'pe')) for r in records)
        fraction = count / n if n else 0.0
        factors[field] = {'valid_count': count, 'count': n, 'coverage': fraction}
        if fraction < min_core_coverage:
            reasons.append('insufficient_core_field:' + field)
    return _result(reasons, count=n, classified_count=classified,
                   industry_coverage=coverage, taxonomy=taxonomy, level=level,
                   taxonomy_version=taxonomy_version, observed_taxonomy_versions=sorted(versions),
                   thresholds={'industry': min_industry_coverage, 'core': min_core_coverage},
                   factors=factors)


def assess_selected_industries(rows, *, max_per_industry=2,
                               industry_field='industry', primary_field='primary_industry',
                               max_primary_share=0.4):
    """Reject unknown selections and cap breaches; describe equal-weight concentration.

    The primary industry must be explicitly mapped; parsing a vendor's hierarchy
    string is not a reliable mapping. max_primary_share is a configurable research
    guard, not a recommended portfolio weight. HHI excludes unknowns from category
    sums but keeps the full pool denominator; unknown_share is reported separately.
    """
    if max_per_industry < 1 or not 0 < max_primary_share <= 1:
        raise ValueError('invalid industry guard thresholds')
    records = _records(rows)
    n = len(records)
    counts = Counter(_label(r.get(industry_field)) for r in records)
    primary = Counter(_label(r.get(primary_field)) for r in records)
    reasons = []
    if not n:
        reasons.append('empty_selection')
    if counts.get('', 0):
        reasons.append('unknown_selected_industry')
    if primary.get('', 0):
        reasons.append('unknown_primary_industry')
    violations = {name: count for name, count in counts.items() if name and count > max_per_industry}
    if violations:
        reasons.append('industry_cap_exceeded')
    primary_counts = {name: count for name, count in primary.items() if name}
    share = max(primary_counts.values(), default=0) / n if n else 0.0
    if share > max_primary_share:
        reasons.append('primary_concentration_exceeded')
    return _result(reasons, count=n, industry_counts=dict(counts),
                   cap_violations=violations, max_per_industry=max_per_industry,
                   primary_counts=primary_counts, primary_top_share=share,
                   primary_hhi=sum((count / n)**2 for count in primary_counts.values()) if n else None,
                   unknown_share=counts.get('', 0) / n if n else None,
                   unknown_primary_share=primary.get('', 0) / n if n else None,
                   max_primary_share=max_primary_share)
