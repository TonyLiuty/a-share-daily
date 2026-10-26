"""Offline regression tests for meaningful semantic and presentation failures."""
import unittest
from event_analysis import assess, timing, rewrite_event_section
from report_ui import render
from run_daily import markdown_to_html


class EventTests(unittest.TestCase):
    def test_rating_is_not_transaction(self):
        direction, text = assess({'title':'摩根士丹利维持阿里增持评级'})
        self.assertEqual(direction, '观点信息')
        self.assertIn('不代表', text)

    def test_after_close_is_not_same_day_validation(self):
        label, text = timing({'time':'2026-09-23 16:02:31'}, '2026-09-23')
        self.assertEqual(label, '盘后消息')
        self.assertIn('不能用当日收盘', text)
        self.assertEqual(timing({'time':'2026-09-24 10:00:00'}, '2026-09-23')[0], '晚于报告日')
        self.assertEqual(timing({'time':'2026-09-23'}, '2026-09-23')[0], '时间待核验')

    def test_copper_directions(self):
        self.assertEqual(assess({'title':'伦铜库存减少1750吨'})[0], '铜价偏多')
        self.assertEqual(assess({'title':'伦铜库存增加1750吨'})[0], '铜价偏空')

    def test_negation_and_specific_products(self):
        self.assertEqual(assess({'title':'中方反对非法单边制裁'})[0], '中性')
        self.assertNotIn('电芯', assess({'title':'组串式逆变器涨价5%'})[1])
        self.assertEqual(assess({'title':'股东拟减持1%股份'})[0], '个股偏空')

    def test_foreign_pmi(self):
        self.assertEqual(assess({'title':'欧元区综合PMI升至53.1 预期51.7'})[0], '需求预期偏多')

    def test_unknown_is_not_positive(self):
        self.assertEqual(assess({'title':'行业会议召开'})[0], '方向不足')

    def test_empty_report_renders_missing_data(self):
        page = markdown_to_html('# 测试报告\n', '测试报告')
        self.assertIn('市场数据暂缺', page)
        self.assertIn('数据暂缺', page)


if __name__ == '__main__': unittest.main()
