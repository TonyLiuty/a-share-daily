import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from audit_industry_master import audit
from import_industry_mapping import import_mapping


class MasterAuditTests(unittest.TestCase):
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
