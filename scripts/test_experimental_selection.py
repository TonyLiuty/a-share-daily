import csv
from pathlib import Path
import tempfile
import unittest
from experimental_selection import prepare_policy, mapped_selection, require_mapped_selection


class ExperimentalPolicyTests(unittest.TestCase):
    def test_default_keeps_legacy_and_experiment_refuses_production_output(self):
        config={'strategy_version':'1.0.0','weights':{'momentum':.3}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);history=root/'daily';history.mkdir()
            self.assertEqual(prepare_policy(config,root),config)
            with self.assertRaises(ValueError):prepare_policy(config,root,'sw-primary',history)
            result=prepare_policy(config,root/'experiment','sw-primary',history)
            self.assertEqual(result['strategy_version'],'1.1.0')
            self.assertEqual(config['strategy_version'],'1.0.0')
            self.assertEqual(result['weights'],config['weights'])

    def test_existing_legacy_signals_reject_mixing(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'history').mkdir();state=root/'experiment'/'state';state.mkdir(parents=True)
            (state/'signals.csv').write_text('strategy_version\n1.0.0\n',encoding='utf-8')
            with self.assertRaises(ValueError):prepare_policy({'strategy_version':'1.0.0'},state.parent,'sw-primary',root/'history')

    def test_same_day_profiles_are_not_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for day,code in [('2026-10-19','000001'),('2026-10-20','000002')]:
                folder=root/day;folder.mkdir()
                with (folder/'candidates.csv').open('w',encoding='utf-8',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=['code','sw_industry']);writer.writeheader()
                    writer.writerow({'code':code,'sw_industry':'银行 — 国有大型银行Ⅱ'})
            rows=[{'code':'000001','total_score':90},{'code':'000002','total_score':80}]
            _,result,audit=mapped_selection(rows,root,'2026-10-20',count=2)
            self.assertEqual(audit['mapped_count'],1)
            self.assertEqual(audit['cutoff'],'2026-10-19')
            self.assertEqual(result['shortfall'],1)
            self.assertNotIn('primary_industry',rows[0])
            with self.assertRaises(ValueError):require_mapped_selection(audit)

if __name__=='__main__':unittest.main()
