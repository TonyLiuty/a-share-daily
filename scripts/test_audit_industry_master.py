import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from audit_industry_master import audit
from import_industry_mapping import import_mapping


class MasterAuditTests(unittest.TestCase):
    def test_source_names_without_snapshot_do_not_guess_conflicting_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source=root/'source.csv'
            source.write_text('code,sw_industry,observed_date,source,name\n000001,银行,2026-10-26,a,甲\n000001,电子,2026-10-26,b,甲\n000003,银行,2026-10-26,a,旧名\n000003,电子,2026-10-26,b,新名\n',encoding='utf-8')
            payload=import_mapping(source,'2026-10-26',taxonomy='sw',taxonomy_version='2021')
            master=root/'master.json';master.write_text(json.dumps(payload),encoding='utf-8')
            snapshot=root/'snapshot.csv';snapshot.write_text('code,name\n000002,乙\n',encoding='utf-8')
            result=audit(master,snapshot)
            first,second=result['master_review']
            self.assertEqual(first['name'],'甲')
            self.assertEqual(first['name_source'],'archived_source_csv')
            self.assertEqual(second['name'],'')
            self.assertEqual(set(second['candidate_names']),{'旧名','新名'})
            self.assertEqual(second['status'],'conflicting_same_date')

    def test_review_preserves_conflict_candidates_dates_and_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source=root/'source.csv'
            source.write_text('code,sw_industry,observed_date,source\n000001,银行,2026-10-26,a\n000001,电子,2026-10-26,b\n000003,,2026-10-26,c\n',encoding='utf-8')
            payload=import_mapping(source,'2026-10-26',taxonomy='sw',taxonomy_version='2021')
            master=root/'master.json';master.write_text(json.dumps(payload),encoding='utf-8')
            snapshot=root/'snapshot.csv';snapshot.write_text('code,name\n000001,甲公司\n000002,乙公司\n',encoding='utf-8')
            before=master.read_bytes(),source.read_bytes(),snapshot.read_bytes()
            result=audit(master,snapshot)
            self.assertEqual(result['master_review_count'],2)
            self.assertEqual(result['universe_review_count'],2)
            conflict=result['universe_review'][0]
            self.assertEqual(conflict['name'],'甲公司')
            self.assertEqual(conflict['status'],'conflicting_same_date')
            self.assertEqual({r['sw_industry'] for r in conflict['candidate_observations']},{'银行','电子'})
            self.assertEqual({r['source'] for r in conflict['candidate_observations']},{'a','b'})
            self.assertEqual(result['universe_review'][1]['candidate_observations'],[])
            self.assertEqual(before,(master.read_bytes(),source.read_bytes(),snapshot.read_bytes()))

    def prepare(self, root):
        source = root/'source.csv'
        source.write_text('code,sw_industry,observed_date,source\n000001,银行,2026-10-26,official\n', encoding='utf-8')
        master = root/'master.json'
        master.write_text(json.dumps(import_mapping(source, '2026-10-26', taxonomy='sw', taxonomy_version='2021')), encoding='utf-8')
        snapshot = root/'snapshot.csv'
        snapshot.write_text('code\n000001\n000002\n', encoding='utf-8')
        return source, master, snapshot

    def test_coverage_does_not_infer_missing_classification(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, master, snapshot = self.prepare(Path(temporary))
            result = audit(master, snapshot)
            self.assertEqual(result['status_counts'], {'mapped': 1, 'missing': 1})
            self.assertEqual(result['coverage'], .5)
            self.assertEqual(result['unique_codes'], 2)

    def test_existing_source_cannot_be_replaced_by_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, master, snapshot = self.prepare(Path(temporary))
            before = source.read_bytes()
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('audit_industry_master.py')),
                                     '--master', str(master), '--snapshot', str(snapshot), '--output', str(source)],
                                    capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
