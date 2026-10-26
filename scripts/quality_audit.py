"""As-of diagnostics for the existing strategy; strict mode is an opt-in gate."""
import re
from selection_quality import assess_selection_inputs, assess_selected_industries


def audit_selection(inputs, picks, max_per_industry=2):
    inputs = inputs.to_dict('records') if hasattr(inputs, 'to_dict') else list(inputs)
    picks = picks.to_dict('records') if hasattr(picks, 'to_dict') else list(picks)
    selected = []
    for row in picks:
        record = dict(row)
        # This is the explicitly supplied company SW hierarchy, not a mapping
        # inferred from a coincidentally equal market-board name.
        hierarchy = str(row.get('sw_industry') or '')
        parts = re.split(r'\s+[—–]\s+', hierarchy, maxsplit=1)
        record['primary_industry'] = parts[0].strip() if len(parts) == 2 else ''
        selected.append(record)
    systems = {str(r.get('industry_taxonomy') or '').strip() for r in inputs}
    levels = {str(r.get('industry_level') or '').strip() for r in inputs}
    taxonomy = next(iter(systems)) if len(systems) == 1 and '' not in systems else None
    level = next(iter(levels)) if len(levels) == 1 and '' not in levels else None
    input_check = assess_selection_inputs(inputs, taxonomy=taxonomy, level=level)
    selected_check = assess_selected_industries(selected, max_per_industry=max_per_industry)
    return {'schema_version': 1, 'mode': 'diagnostic',
            'input_scope': 'preselected cohort, not the complete market',
            'input_available': bool(inputs), 'inputs': input_check,
            'selected': selected_check,
            'strict_eligible': input_check['allowed'] and selected_check['allowed'],
            'note': 'Classification taxonomy/version must be supplied upstream before strict eligibility can pass. Diagnostics do not change historical scores or selections.'}


def enforce_quality(audit):
    if not audit['strict_eligible']:
        reasons = audit['inputs']['reasons'] + audit['selected']['reasons']
        raise ValueError('strict quality gate rejected selection: ' + ', '.join(reasons))
