import unittest
from quality_audit import audit_selection, enforce_quality


class QualityIntegrationTests(unittest.TestCase):
    def test_unknown_cannot_pass_strict_gate(self):
        rows=[{'industry':'未分类','sw_industry':'银行 — 国有银行'}]*7
        audit=audit_selection(rows,rows)
        self.assertEqual(audit['selected']['primary_top_share'],1)
        with self.assertRaises(ValueError):enforce_quality(audit)

    def test_explicit_consistent_mapping_can_pass(self):
        rows=[{'industry':f'行业{x}','industry_taxonomy':'test-taxonomy-v1','industry_level':'level-1',
               'sw_industry':f'一级{x} — 细分','amount':1e9,'return_60d_snapshot':1,
               'turnover_pct':2,'pe':10,'change_pct':1} for x in range(5)]
        audit=audit_selection(rows,rows)
        self.assertTrue(audit['strict_eligible'])
        enforce_quality(audit)

    def test_old_report_does_not_invent_input_coverage(self):
        audit=audit_selection([], [{'industry':'银行','sw_industry':'银行 — 国有银行'}])
        self.assertFalse(audit['input_available'])
        self.assertFalse(audit['strict_eligible'])

if __name__=='__main__':unittest.main()
