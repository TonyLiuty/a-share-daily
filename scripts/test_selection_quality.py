"""Synthetic P0 acceptance cases; no market requests or production writes."""
import copy
import unittest
from selection_quality import assess_selection_inputs, assess_selected_industries


def universe():
    return [dict(code=str(i), industry='细分' + str(i // 2), primary_industry='一级' + str(i // 2),
                 amount=1e9, return_60d_snapshot=5, turnover_pct=3, pe=10, change_pct=1,taxonomy_version='2021')
            for i in range(10)]


class SelectionQualityTests(unittest.TestCase):
    def assess(self, rows, **kwargs):
        return assess_selection_inputs(rows, taxonomy='申万', level='二级', taxonomy_version='2021', **kwargs)

    def test_missing_mixed_and_mismatched_version_rejected(self):
        rows=universe();rows[0].pop('taxonomy_version')
        self.assertIn('unknown_industry_taxonomy_version',self.assess(rows)['reasons'])
        rows=universe();rows[0]['taxonomy_version']='2014'
        self.assertIn('mixed_industry_taxonomy_version',self.assess(rows)['reasons'])
        rows=universe()
        result=assess_selection_inputs(rows,taxonomy='申万',level='二级',taxonomy_version='2014')
        self.assertFalse(result['allowed'])
        self.assertIn('mixed_industry_taxonomy_version',result['reasons'])

    def test_classified_balanced_passes_and_preserves_input(self):
        rows = universe()
        before = copy.deepcopy(rows)
        self.assertTrue(self.assess(rows)['allowed'])
        result = assess_selected_industries(rows)
        self.assertTrue(result['allowed'])
        self.assertAlmostEqual(result['primary_hhi'], .2)
        self.assertEqual(rows, before)

    def test_unknown_fallback_never_bypasses_cap(self):
        rows = universe()
        for row in rows:
            row.update(industry='未分类', primary_industry=None, return_60d_snapshot=float('nan'))
        result = self.assess(rows)
        self.assertEqual(result['industry_coverage'], 0)
        self.assertIn('insufficient_core_field:return_60d_snapshot', result['reasons'])
        self.assertFalse(assess_selected_industries(rows)['allowed'])

    def test_partial_missing_is_reported_and_rejected(self):
        rows = universe()
        rows[0]['industry'] = None
        rows[1]['pe'] = -1
        result = self.assess(rows)
        self.assertEqual(result['industry_coverage'], .9)
        self.assertEqual(result['factors']['pe']['coverage'], .9)
        self.assertFalse(result['allowed'])

    def test_taxonomy_and_level_mixing_rejected(self):
        rows = universe()
        rows[0].update(industry_taxonomy='新浪', industry_level='一级')
        reasons = self.assess(rows)['reasons']
        self.assertIn('mixed_industry_taxonomy', reasons)
        self.assertIn('mixed_industry_level', reasons)

    def test_primary_concentration_visible_despite_fine_cap(self):
        rows = universe()
        for row in rows[:6]:
            row['primary_industry'] = '银行'
        result = assess_selected_industries(rows)
        self.assertEqual(result['cap_violations'], {})
        self.assertEqual(result['primary_top_share'], .6)
        self.assertIn('primary_concentration_exceeded', result['reasons'])

    def test_known_industry_cap_breach(self):
        rows = universe()
        for row in rows[:3]:
            row['industry'] = '银行'
        self.assertEqual(assess_selected_industries(rows)['cap_violations'], {'银行': 3})

    def test_empty_and_nonfinite_values_rejected(self):
        self.assertFalse(self.assess([])['allowed'])
        self.assertFalse(assess_selected_industries([])['allowed'])
        rows = universe()
        rows[0]['amount'] = float('inf')
        self.assertEqual(self.assess(rows)['factors']['amount']['coverage'], .9)


if __name__ == '__main__':
    unittest.main()
