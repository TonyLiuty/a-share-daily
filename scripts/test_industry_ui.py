"""Regression tests for classification boundaries and sparse report layouts."""
import unittest

from report_ui import industry_watchlist, render
from insights_ui import selection_audit_view


class IndustryViewTests(unittest.TestCase):
    def test_unsaved_inputs_are_not_displayed_as_empty_or_zero_coverage(self):
        page=selection_audit_view({'input_available':False,'inputs':{
            'reasons':['empty_universe','insufficient_core_field:pe'],
            'factors':{'pe':{'valid_count':0,'count':0,'coverage':0}}}})
        self.assertIn('原始预选池未归档',page)
        self.assertNotIn('预选池为空',page)
        self.assertNotIn('有效0/0条',page)

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
        self.assertIn('<td>选股行业分类体系</td><td>申万</td>',page)
        self.assertIn('<td>选股行业分类层级</td><td>一级</td>',page)
        self.assertIn('银行 2只',page)
        self.assertIn('2.0',page)

    def test_asof_mapping_schema_and_unknown_shortfall(self):
        page = selection_audit_view({'mode':'diagnostic','mapping':{'taxonomy':'sw','level':'primary',
            'strategy_version':'1.1.0','target_count':10,'selected_count':7,'primary_counts':{'银行':2}}})
        self.assertIn('<td>选股行业分类体系</td><td>申万（sw）</td>',page)
        self.assertIn('<td>选股行业分类层级</td><td>一级（primary）</td>',page)
        self.assertIn('候选不足数量未记录',page)
        self.assertNotIn('缺少3只',page)
        self.assertIn('1.1.0',page)

    def test_empty_audit_is_optional(self):
        self.assertEqual(selection_audit_view(None),'')
        self.assertEqual(selection_audit_view({}),'')

    def test_quality_reasons_include_field_and_recorded_coverage(self):
        page=selection_audit_view({'mode':'diagnostic','inputs':{
            'reasons':['insufficient_core_field:pe','insufficient_industry_coverage'],
            'factors':{'pe':{'valid_count':80,'count':100,'coverage':.8}},
            'thresholds':{'core':.95,'industry':1},'industry_coverage':.9},
            'selected':{'reasons':['unknown_primary_industry']}})
        self.assertIn('市盈率PE（pe）有效数据覆盖率未达门槛，有效80/100条（80.0%），要求95.0%',page)
        self.assertIn('预选池行业标签覆盖率未达门槛，当前90.0%，要求100.0%',page)
        self.assertIn('观察股缺少一级行业标签',page)
        self.assertNotIn('insufficient_core_field',page)

    def test_classification_version_is_not_mapping_method_version(self):
        page=selection_audit_view({'inputs':{'taxonomy':'sw','level':'primary','taxonomy_version':'2021'},'mapping':{'version':'asof-v1'}})
        self.assertIn('<td>选股行业分类版本</td><td>2021</td>',page)
        self.assertIn('<td>映射方法版本</td><td>asof-v1</td>',page)
        missing=selection_audit_view({'mapping':{'version':'asof-v1'}})
        self.assertIn('<td>选股行业分类版本</td><td>未记录</td>',missing)

    def test_strict_pass_is_not_enforcement_and_unknown_reason_kept(self):
        page=selection_audit_view({'mode':'diagnostic','strict_enabled':True,'strict_eligible':True,
            'inputs':{'reasons':['new_reason:<unsafe>']}})
        self.assertIn('仅诊断；未记录已执行行业约束',page)
        self.assertIn('<td>严格质量校验</td><td>已启用；通过</td>',page)
        self.assertIn('待解释的质量原因：new_reason:&lt;unsafe&gt;',page)

    def test_equal_names_do_not_join_different_classifications(self):
        picks = [['排名', '代码', '名称', '行业', '分数'], ['2', '000001', '测试银行', '银行', '81']]
        context = {'industries': {'source': 'sina', 'rows': [{'name': '银行', 'ret_1d': 9.99}]}}
        page = industry_watchlist(picks, context)
        self.assertNotIn('9.99', page)
        self.assertIn('href="#stock-2"', page)
        self.assertIn('测试银行</a>', page)
        self.assertIn('000001 · 综合分 81', page)

    def test_company_summary_uses_own_code_and_short_text(self):
        picks = [['排名','代码','名称','行业','分数'],['1','000001','甲公司','电子','80'],['2','000002','乙公司','电子','79']]
        page = industry_watchlist(picks,{'stock_briefs':[
            {'code':'000002','business':'乙的业务','drivers':'量价 90','risk':'60日回撤-9%'},
            {'code':'000001','business':'甲'*100,'drivers':'趋势 95','risk':'20日涨幅+4%；60日回撤-5%'}]})
        self.assertEqual(page.count('class="industry-company"'),2)
        first=page.split('class="industry-company"')[1]
        self.assertIn('趋势 95',first)
        self.assertNotIn('乙的业务',first)
        self.assertIn('甲'*71+'…',first)
        self.assertNotIn('甲'*72,first)
        self.assertIn('20日涨幅+4%；60日回撤-5%',first)

    def test_missing_company_summary_uses_only_recorded_price_fields(self):
        picks = [['排名','代码','名称','行业','分数','20日涨跌','60日涨跌'],['1','000001','甲','银行','80','+3.1%','—']]
        page = industry_watchlist(picks,None)
        self.assertIn('主营资料暂缺',page)
        self.assertIn('因子明细暂缺',page)
        self.assertIn('20日涨跌 +3.1%',page)
        self.assertNotIn('60日涨跌',page)

    def test_company_summary_escapes_untrusted_text(self):
        picks = [['排名','代码','名称','行业','分数'],['1','000001','甲','银行','80']]
        page = industry_watchlist(picks,{'stock_briefs':[{'code':'000001','business':'<img src=x>','drivers':'A&B','risk':'<script>x</script>'}]})
        self.assertIn('&lt;img src=x&gt;',page)
        self.assertIn('A&amp;B',page)
        self.assertNotIn('<script>',page)

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
