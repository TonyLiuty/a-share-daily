import unittest
from report_insights import fixed_panel, mature_history, industries, stock_brief, capture_inputs, json_read, industry_history
from news_evidence import allowed

class InsightsTests(unittest.TestCase):
    def test_takeaways_describe_results_not_audit_counts(self):
        from insights_ui import takeaways
        context={'market_returns':{'上证指数':-.39,'深证成指':-.64,'创业板指':-.60,'沪深300':-.60},'industries':{'rows':[{'name':'煤炭','state':'由涨转跌','ret_1d':-1.83}]},'model_summary':'**表现偏弱，当前不支持稳定盈利结论**；累计理论收益-1.95%，累计相对沪深300差额-0.31%'}
        result=takeaways(context)
        self.assertEqual(len(result),2)
        self.assertIn('四大指数全跌',result[0])
        self.assertIn('煤炭由涨转跌',result[1])
        self.assertNotIn('理论收益',''.join(result))
        self.assertNotIn('跑输',''.join(result))
        self.assertNotIn('核验',''.join(result))
        self.assertNotIn('**',''.join(result))

    def test_fixed_intersection(self):
        result=fixed_panel([{'code':'1','change_pct':2},{'code':'2','change_pct':''}], [{'code':'1','change_pct':-1},{'code':'3','change_pct':5}], ['000001','000002','000003'])
        self.assertEqual(result['coverage_count'],1)
        self.assertEqual(result['common_count'],1)
        self.assertEqual(result['comparable_previous']['median_pct'],-1)

    def test_mature_by_horizon(self):
        rows=[{'code':'1','signal_date':'2026-09-23','ret_1d':'9'}, {'code':'1','signal_date':'2026-09-22','ret_1d':'.01'}, {'code':'1','signal_date':'2026-09-15','ret_5d':'.02'}]
        result=mature_history(rows,[{'code':'000001','name':'样本'}],'2026-09-23',True)
        self.assertEqual(result['stocks'][0]['horizons']['1']['signal_date'],'2026-09-22')
        self.assertEqual(result['stocks'][0]['horizons']['5']['signal_date'],'2026-09-15')
        self.assertIsNone(result['stocks'][0]['horizons']['20'])

    def test_legacy_partial(self):
        result=industries('已降级新浪行业\n**领涨行业**：传媒 +2.81%；煤炭 +1.39%')
        self.assertFalse(result['complete'])
        self.assertEqual(result['source'],'sina')
        self.assertEqual(len(result['rows']),2)

    def test_financial_brief(self):
        brief=stock_brief({'code':'1','sw_industry':'银行','cash_profit_ratio':'20'})
        self.assertIn('资产质量',brief['financial'])
        self.assertNotIn('覆盖账面',brief['financial'])

    def test_domains(self):
        self.assertTrue(allowed('https://www.oecd.org/en/report'))
        self.assertFalse(allowed('https://oecd.org.evil.example/report'))
        self.assertFalse(allowed('http://127.0.0.1/'))

    def test_capture_and_contiguous_industry(self):
        import tempfile
        from pathlib import Path
        import pandas as pd
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'state').mkdir()
            dates=['2026-09-17','2026-09-18','2026-09-21','2026-09-22','2026-09-23']
            sector={'source':'新浪行业备用源','rows':[{'name':'样本行业','change_pct':1,'amount':100}]}
            for date in dates:
                directory=root/'daily'/date
                capture_inputs(directory,pd.DataFrame([{'code':'000001','amount':100,'change_pct':1}]),sector,pd.DataFrame([{'code':'000001','signal_date':date}]),{'events':[]},pd.DataFrame({'date':dates}))
                (directory/'report.md').write_text('# 样本报告',encoding='utf-8')
            current=json_read(directory/'industry_snapshot.json')
            self.assertEqual(current['source'],'sina')
            result=industry_history(directory,dates,current,current)
            self.assertAlmostEqual(result['rows'][0]['ret_5d'],(1.01**5-1)*100)
            self.assertIsNone(result['rows'][0]['ret_20d'])
            missing=industry_history(directory,dates[1:],current,current)
            self.assertIsNone(missing['rows'][0]['ret_5d'])

if __name__=='__main__':unittest.main()
