import copy
import unittest
from industry_mapping import attach_mapping, build_mapping, primary_industry, select_by_primary


def observation(code, day, label):
    return dict(code=code, observed_date=day, sw_industry=label, source='saved:' + day)


class IndustryMappingTests(unittest.TestCase):
    def test_unicode_codes_are_not_valid_security_identifiers(self):
        self.assertEqual(build_mapping([observation('０００００１','2026-10-19','银行')],'2026-10-19'),{})

    def test_latest_invalid_does_not_restore_old_label(self):
        for label in ('', '未分类', '国有大型银行'):
            mapping=build_mapping([observation('1','2026-10-18','银行'),observation('1','2026-10-19',label)],'2026-10-19')
            self.assertEqual(mapping['000001']['mapping_status'],'unmapped')
            self.assertIsNone(mapping['000001']['primary_industry'])

    def test_same_day_unknown_and_known_conflict_in_any_order(self):
        rows=[observation('1','2026-10-19',''),observation('1','2026-10-19','银行')]
        for observations in (rows,list(reversed(rows))):
            mapping=build_mapping(observations,'2026-10-19')
            self.assertEqual(mapping['000001']['mapping_status'],'conflicting_same_date')
            self.assertIsNone(mapping['000001']['primary_industry'])

    def test_future_observation_cannot_fill_past(self):
        mapping = build_mapping([observation('1', '2026-10-20', '银行 — 国有大型银行')], '2026-10-19')
        self.assertEqual(mapping, {})
        self.assertIsNone(attach_mapping([dict(code='1')], mapping, '2026-10-19')[0]['primary_industry'])

    def test_latest_visible_record_and_provenance(self):
        rows = [observation('1', '2026-10-18', '银行 — 银行'), observation('1', '2026-10-19', '非银金融 — 证券'), observation('1', '2026-10-20', '电子 — 半导体')]
        entry = build_mapping(rows, '2026-10-19')['000001']
        self.assertEqual(entry['primary_industry'], '非银金融')
        self.assertEqual(entry['mapping_date'], '2026-10-19')
        self.assertEqual(entry['mapping_source'], 'saved:2026-10-19')

    def test_missing_and_unrecognized_not_guessed(self):
        for text in ('', '未分类', '国有大型银行', '新浪金融行业', 'nan'):
            self.assertIsNone(primary_industry(text))

    def test_cap_shortfall_and_inputs_unchanged(self):
        history = [observation(str(i), '2026-10-19', '银行 — 银行') for i in range(1, 6)]
        mapping = build_mapping(history, '2026-10-19')
        rows = [dict(code=str(i), total_score=100-i) for i in range(1, 7)]
        before = copy.deepcopy(rows)
        attached = attach_mapping(rows, mapping, '2026-10-19')
        mapped_before = copy.deepcopy(attached)
        result = select_by_primary(attached, count=10)
        self.assertEqual(result['selected_count'], 2)
        self.assertEqual(result['shortfall'], 8)
        self.assertEqual(result['industry_counts'], {'银行': 2})
        self.assertEqual([r['code'] for r in result['selected']], ['000001', '000002'])
        self.assertEqual(rows, before)
        self.assertEqual(attached, mapped_before)

    def test_same_date_conflict_rejected(self):
        mapping = build_mapping([observation('1', '2026-10-19', '银行'), observation('1', '2026-10-19', '电子')], '2026-10-19')
        self.assertEqual(mapping['000001']['mapping_status'], 'conflicting_same_date')
        self.assertIsNone(mapping['000001']['primary_industry'])

    def test_attach_defends_against_future_mapping_supplied(self):
        mapping = build_mapping([observation('1', '2026-10-20', '银行')], '2026-10-20')
        attached = attach_mapping([dict(code='1', total_score=80)], mapping, '2026-10-19')
        self.assertEqual(select_by_primary(attached)['selected_count'], 0)


if __name__ == '__main__':
    unittest.main()
