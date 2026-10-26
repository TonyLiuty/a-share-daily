import json
import unittest
from evaluate_research import evaluate


def row(date, code, ret, benchmark=.01):
    # The core numerical fixtures use canonical CSV security codes.
    item = dict(signal_date=date, code=code.zfill(6))
    for horizon in (1, 5, 20):
        item[f'ret_{horizon}d'] = ret
        item[f'benchmark_ret_{horizon}d'] = benchmark
    return item


class ResearchEvaluationTests(unittest.TestCase):
    def test_incomplete_date_excluded_rather_than_average_survivor(self):
        result = evaluate([row('2026-01-01', '1', .5), row('2026-01-01', '2', ''), row('2026-01-02', '1', -.02)])
        stats = result['horizons']['5']
        self.assertEqual(stats['all_n'], 3)
        self.assertEqual(stats['mature_n'], 2)
        self.assertEqual(stats['complete_dates'], ['2026-01-02'])
        self.assertEqual(stats['incomplete_dates_n'], 1)
        self.assertEqual(stats['daily_mean_net_return'], -.02)
        self.assertEqual(result['independent_stocks_n'], 2)
        self.assertAlmostEqual(result['same_stock_repeat_ratio'], 1/3)

    def test_zero_is_mature_not_positive_and_cost_is_additional(self):
        stats = evaluate([row('2026-01-01', '1', 0, 0)])['horizons']['1']
        self.assertEqual(stats['mature_n'], 1)
        self.assertEqual(stats['daily_win_rate'], 0)
        self.assertEqual(stats['daily_mean_excess_return'], 0)
        self.assertAlmostEqual(stats['additional_round_trip_cost']['5bp'], -.0005)
        self.assertAlmostEqual(stats['additional_round_trip_cost']['10bp'], -.001)

    def test_dates_equal_weight_not_rows_and_no_compounded_output(self):
        result = evaluate([row('2026-01-01', '1', .1), row('2026-01-01', '2', .1), row('2026-01-02', '1', -.1)])
        self.assertAlmostEqual(result['horizons']['20']['daily_mean_net_return'], 0)
        self.assertNotIn('equity', json.dumps(result))

    def test_benchmark_requires_full_paired_cohort(self):
        stats = evaluate([row('2026-01-01', '1', .2, ''), row('2026-01-02', '2', .1, .02)])['horizons']['1']
        self.assertEqual(stats['complete_dates_n'], 2)
        self.assertEqual(stats['paired_benchmark_dates_n'], 1)
        self.assertAlmostEqual(stats['daily_mean_excess_return'], .08)
        self.assertAlmostEqual(stats['paired_daily_mean_net_return'], .1)

    def test_nonfinite_and_empty_stay_missing(self):
        result = evaluate([row('2026-01-01', '1', float('nan'))])
        self.assertIsNone(result['horizons']['1']['daily_mean_net_return'])
        json.dumps(result, allow_nan=False)
        self.assertIsNone(evaluate([])['same_stock_repeat_ratio'])

    def test_duplicate_records_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([row('2026-01-01', '1', .1), row('2026-01-01', '000001', .1)])

    def test_mixed_versions_on_different_dates_rejected(self):
        first=dict(row('2026-01-01','1',.1),strategy_version='1.0.0')
        second=dict(row('2026-01-02','2',.2),strategy_version='1.1.0')
        with self.assertRaisesRegex(ValueError,'strategy_version'):
            evaluate([first,second])
        second.pop('strategy_version')
        with self.assertRaisesRegex(ValueError,'strategy_version'):
            evaluate([first,second])

    def test_single_or_unknown_version_is_identified(self):
        fixture=row('2026-01-01','1',.1)
        unknown=evaluate([fixture])
        self.assertIsNone(unknown['strategy_version'])
        self.assertEqual(unknown['strategy_version_status'],'unknown')
        declared=evaluate([dict(fixture,strategy_version='1.0.0')])
        self.assertEqual(declared['strategy_version'],'1.0.0')
        self.assertEqual(unknown['horizons'],declared['horizons'])

    def test_dates_require_exact_iso_and_valid_calendar(self):
        for value in ('2026-1-01','20260101','2026-02-30','2026-01-01T00:00:00',' 2026-01-01','2026-01-01 ',None,20260101):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'signal_date'):
                evaluate([dict(row('2026-01-01','1',.1),signal_date=value)])
        self.assertEqual(evaluate([row('2024-02-29','1',.1)])['signal_dates_n'],1)

    def test_codes_are_six_ascii_digits_without_repair(self):
        for value in ('1','00001','0000001','00001A','０００００１',' 000001','000001 ',None,1):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'code'):
                evaluate([dict(row('2026-01-01','1',.1),code=value)])
        result=evaluate([row('2026-01-01','000001',.1),row('2026-01-01','600000',.2)])
        self.assertEqual(result['independent_stocks_n'],2)


if __name__ == '__main__':
    unittest.main()
