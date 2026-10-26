import json
import unittest
from evaluate_research import evaluate


def row(date, code, ret, benchmark=.01):
    item = dict(signal_date=date, code=code)
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


if __name__ == '__main__':
    unittest.main()
