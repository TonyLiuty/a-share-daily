import json
from pathlib import Path
import shutil
import tempfile
import unittest
from experimental_selection import load_master
from import_industry_mapping import import_mapping


class MasterIntegrityTests(unittest.TestCase):
    def prepare(self, root):
        source=root/'master.csv'
        source.write_text('code,sw_industry,observed_date,source\n000001,银行,2026-10-26,official\n',encoding='utf-8')
        payload=import_mapping(source,'2026-10-26',taxonomy='sw',taxonomy_version='2021')
        path=root/'master.json'
        path.write_text(json.dumps(payload),encoding='utf-8')
        return source,path,payload

    def test_modified_valid_industry_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            _,path,payload=self.prepare(Path(temp))
            payload['mapping']['000001'].update(sw_industry='电子',primary_industry='电子')
            path.write_text(json.dumps(payload),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'disagree'):load_master(path)

    def test_source_change_and_missing_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source,path,_=self.prepare(Path(temp))
            source.write_text('changed',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):load_master(path)
            source.unlink()
            with self.assertRaisesRegex(ValueError,'unavailable'):load_master(path)

    def test_modified_observations_and_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            _,path,payload=self.prepare(Path(temp))
            payload['observations'][0]['source']='different'
            path.write_text(json.dumps(payload),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'observations'):load_master(path)
            _,path,payload=self.prepare(Path(temp))
            payload['metadata']['mapped_count']=2
            path.write_text(json.dumps(payload),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'mapped_count'):load_master(path)

    def test_archive_pair_can_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            original=root/'original';original.mkdir()
            source,path,_=self.prepare(original)
            moved=root/'moved';moved.mkdir()
            shutil.move(str(source),moved/source.name)
            shutil.move(str(path),moved/path.name)
            mapping,meta=load_master(moved/path.name)
            self.assertEqual(mapping['000001']['primary_industry'],'银行')
            self.assertTrue(meta['source_verified'])
            self.assertEqual(Path(meta['resolved_source_file']),moved/source.name)

    def test_windows_source_path_can_resolve_adjacent_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            source,path,payload=self.prepare(Path(temp))
            payload['metadata']['source_file']=r'C:\archived-on-another-machine\master.csv'
            path.write_text(json.dumps(payload),encoding='utf-8')
            _,meta=load_master(path)
            self.assertEqual(meta['resolved_source_file'],str(source.resolve()))


if __name__=='__main__':unittest.main()
