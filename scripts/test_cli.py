import os
from pathlib import Path
import subprocess
import sys
import unittest


class CliEncodingTests(unittest.TestCase):
    def test_chinese_help_on_non_chinese_windows_encoding(self):
        env=dict(os.environ,PYTHONIOENCODING='cp1252')
        script=Path(__file__).parent/'run_daily.py'
        result=subprocess.run([sys.executable,str(script),'--help'],env=env,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
        help_text=result.stdout.decode('utf-8')
        self.assertIn('每日A股',help_text)
        self.assertIn('--strict-quality',help_text)

if __name__=='__main__':unittest.main()
