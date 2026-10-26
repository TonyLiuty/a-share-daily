import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from import_industry_mapping import import_mapping


class IndustryImportTests(unittest.TestCase):
    def load(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mapping.csv'
            with path.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['code', 'sw_industry', 'observed_date', 'source'])
                writer.writerows(rows)
            original = path.read_bytes()
            result = import_mapping(path, '2026-10-25', taxonomy='sw', taxonomy_version='2021')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(result['metadata']['file_sha256'], hashlib.sha256(original).hexdigest())
            return result

    def test_latest_visible_and_provenance(self):
        result = self.load([['000001', '银行 — 银行', '2026-10-20', 'provider A'], ['000001', '非银金融 — 证券', '2026-10-24', 'provider B']])
        item = result['mapping']['000001']
        self.assertEqual(item['primary_industry'], '非银金融')
        self.assertEqual(item['mapping_date'], '2026-10-24')
        self.assertEqual(item['mapping_source'], 'provider B')

    def test_future_file_is_rejected(self):
        with self.assertRaises(ValueError):
            self.load([['000001', '银行', '2026-10-26', 'provider']])

    def test_conflicting_and_missing_latest_never_restore_old_label(self):
        result = self.load([['000001', '银行', '2026-10-20', 'a'], ['000001', '', '2026-10-24', 'a'], ['000002', '银行', '2026-10-24', 'a'], ['000002', '电子', '2026-10-24', 'b']])
        self.assertEqual(result['mapping']['000001']['mapping_status'], 'unmapped')
        self.assertEqual(result['mapping']['000002']['mapping_status'], 'conflicting_same_date')
        self.assertEqual(result['metadata']['mapped_count'], 0)

    def test_source_and_code_required(self):
        for code, source in [('1', 'a'), ('000001', '')]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.load([[code, '银行', '2026-10-24', source]])

    def test_same_label_duplicate_sources_are_auditable(self):
        result = self.load([['000001', '银行', '2026-10-24', 'b'], ['000001', '银行', '2026-10-24', 'a']])
        self.assertEqual(result['mapping']['000001']['mapping_source'], 'a; b')
        self.assertEqual(result['metadata']['record_count'], 2)


if __name__ == '__main__':
    unittest.main()
