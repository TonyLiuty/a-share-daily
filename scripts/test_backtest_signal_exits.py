import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_signal_exits import Bar, simulate, summarize


class ExitBacktestTests(unittest.TestCase):
    def row(self):
        return {"signal_date": "2026-01-02", "entry_date": "2026-01-05", "code": "600519", "name": "示例"}

    def test_stop_loss_has_priority_when_both_barriers_hit(self):
        bars = [
            Bar("2026-01-05", 100, 100, 100, 100),
            Bar("2026-01-06", 100, 115, 90, 100),
        ]
        trade = simulate(self.row(), bars, 0.08, 0.12, 20, 5)
        self.assertEqual(trade.exit_reason, "stop_loss")
        self.assertAlmostEqual(trade.gross_return, -0.08)

    def test_take_profit(self):
        bars = [Bar("2026-01-05", 100, 113, 99, 110)]
        trade = simulate(self.row(), bars, 0.08, 0.12, 20, 5)
        self.assertEqual(trade.exit_reason, "take_profit")
        self.assertAlmostEqual(trade.gross_return, 0.12)

    def test_summary_costs_are_round_trip_deductions(self):
        bars = [Bar("2026-01-05", 100, 100, 100, 100)]
        trade = simulate(self.row(), bars, 0.08, 0.12, 1, 5)
        summary = summarize([trade], (5, 10))
        self.assertAlmostEqual(summary["cost_sensitivity"]["10bp"], -0.001)


if __name__ == "__main__":
    unittest.main()
