"""Regression tests for classification boundaries and sparse report layouts."""
import unittest

from report_ui import industry_watchlist, render
from insights_ui import selection_audit_view


class IndustryViewTests(unittest.TestCase):
    def test_legacy_diagnostic_does_not_claim_enforcement(self):
        page = selection_audit_view({'mode':'diagnostic','strict_eligible':True,'selected':{'count':2}})
        self.assertIn('仅诊断；未记录已执行行业约束',page)
        self.assertNotIn('已启用严格校验',page)
        self.assertIn('未记录',page)

    def test_mapping_and_shortfall_are_explicit(self):
        page = selection_audit_view({'mode':'diagnostic','strategy_version':'2.0','target_count':10,'shortfall':3,
            'selected':{'count':7,'primary_counts':{'银行':2}},
            'mapping':{'taxonomy':'申万','level':'一级','source':'公司资料','version':'2021','mapped_count':100,'total_count':140,'coverage':100/140}})
        self.assertIn('100/140（71.4%）',page)
        self.assertIn('目标10只，实际7只，缺少3只',page)
        self.assertIn('申万 / 一级',page)
        self.assertIn('银行 2只',page)
        self.assertIn('2.0',page)

    def test_asof_mapping_schema_and_unknown_shortfall(self):
        page = selection_audit_view({'mode':'diagnostic','mapping':{'taxonomy':'sw','level':'primary',
            'strategy_version':'1.1.0','target_count':10,'selected_count':7,'primary_counts':{'银行':2}}})
        self.assertIn('申万（sw） / 一级（primary）',page)
        self.assertIn('候选不足数量未记录',page)
        self.assertNotIn('缺少3只',page)
        self.assertIn('1.1.0',page)

    def test_empty_audit_is_optional(self):
        self.assertEqual(selection_audit_view(None),'')
        self.assertEqual(selection_audit_view({}),'')

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
