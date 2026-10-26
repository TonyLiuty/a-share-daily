import io
import unittest
import pandas as pd
from fetch_industry_master import parse_sector


class ParseSectorTests(unittest.TestCase):
    def content(self, **changes):
        row = {'行业名称': '银行', '股票代码': '000001', '股票名称': '样本', '计入日期': '1991-01-01'}
        row.update(changes)
        buffer = io.BytesIO()
        pd.DataFrame([row]).to_excel(buffer, index=False, engine='openpyxl')
        return buffer.getvalue()

    def test_preserve_code_and_separate_inclusion(self):
        row = parse_sector(self.content(), '银行')[0]
        self.assertEqual(row['code'], '000001')
        self.assertEqual(row['provider_inclusion_date'], '1991-01-01')
        self.assertNotIn('observed_date', row)

    def test_reject_inconsistent_label(self):
        with self.assertRaises(ValueError):
            parse_sector(self.content(行业名称='电子'), '银行')

    def test_reject_partial_code(self):
        with self.assertRaises(ValueError):
            parse_sector(self.content(股票代码='1'), '银行')


if __name__ == '__main__':
    unittest.main()
