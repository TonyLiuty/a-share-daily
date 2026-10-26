"""Regression tests for classification boundaries and sparse report layouts."""
import unittest

from report_ui import industry_watchlist, render


class IndustryViewTests(unittest.TestCase):
    def test_equal_names_do_not_join_different_classifications(self):
        picks = [['排名', '代码', '名称', '行业', '分数'], ['2', '000001', '测试银行', '银行', '81']]
        context = {'industries': {'source': 'sina', 'rows': [{'name': '银行', 'ret_1d': 9.99}]}}
        page = industry_watchlist(picks, context)
        self.assertNotIn('9.99', page)
        self.assertIn('href="#stock-2"', page)
        self.assertIn('测试银行</a>（000001，81分）', page)

    def test_missing_industry_is_visible_and_names_are_escaped(self):
        page = industry_watchlist([['排名','代码','名称','行业','分数'], ['1','000001','A<B','','—']], None)
        self.assertIn('行业未分类', page)
        self.assertIn('A&lt;B', page)
        self.assertNotIn('A<B', page)

    def test_positive_bottom_is_named_correctly_without_empty_third_column(self):
        md = '### 行业轮动\n\n**领涨行业**：电子 +2.00%。\n**领跌行业**：银行 +0.10%。\n'
        page = render(md, '报告 2026-10-26', '')
        self.assertEqual(page.count('class="sector"'), 2)
        self.assertIn('<h3>涨幅靠后行业</h3>', page)
        self.assertNotIn('<h3>领跌行业</h3>', page)

    def test_explicit_bottom_label_does_not_duplicate_legacy_bottom(self):
        md = '### 行业轮动\n\n**领涨行业**：电子 +2.00%。\n**领跌行业**：银行 +0.10%。\n**涨幅靠后行业**：煤炭 +0.20%。\n'
        page = render(md, '报告', '')
        self.assertEqual(page.count('class="sector"'), 2)
        self.assertEqual(page.count('<h3>涨幅靠后行业</h3>'), 1)

    def test_sparse_news_and_stock_data_stay_sparse(self):
        md = '### 重大事件与市场影响分析\n\n| 消息 | 影响 | 时间 | 来源 |\n|---|---|---|---|\n| [消息一](https://example.com/1) | 中性：资料不足 | 盘后：待观察 | 示例 |\n'
        page = render(md, '报告', '')
        self.assertEqual(page.count('<article class="event">'), 1)
        self.assertEqual(page.count('class="stock-detail"'), 0)
        self.assertNotIn('<h3>领跌行业</h3>', page)


if __name__ == '__main__':
    unittest.main()
