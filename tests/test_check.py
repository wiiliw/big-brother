import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from datetime import date

spec = importlib.util.spec_from_file_location('check', Path(__file__).resolve().parents[1] / 'scripts/check.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'docs').mkdir()
        (self.root / 'SKILL.md').write_text('---\nname: dage-skill\ndescription: test\n---\n', encoding='utf-8')
        self.entry = dict(path='SKILL.md', as_of='2026-09-01', last_verified='2026-09-01', status='verified', review_days=30, scope='All')
        self.save()

    def save(self):
        (self.root / 'docs/catalog.json').write_text(json.dumps([self.entry]), encoding='utf-8')

    def run_check(self):
        return module.check(self.root, date(2026, 9, 17))

    def test_clean(self):
        self.assertEqual(self.run_check(), ([], []))

    def test_missing_link(self):
        (self.root / 'README.md').write_text('[missing](absent.md)', encoding='utf-8')
        self.assertTrue(self.run_check()[0])

    def test_encoded_chinese_link(self):
        (self.root / 'docs/资料.md').write_text('资料', encoding='utf-8')
        (self.root / 'README.md').write_text('[ok](docs/%E8%B5%84%E6%96%99.md#title)', encoding='utf-8')
        self.assertFalse(self.run_check()[0])

    def test_stale_pending(self):
        self.entry.update(as_of='2026-06', last_verified=None, status='pending')
        self.save()
        errors, warnings = self.run_check()
        self.assertFalse(errors)
        self.assertEqual(len(warnings), 2)

    def test_future_verification(self):
        self.entry['last_verified'] = '2027-01-01'
        self.save()
        self.assertTrue(self.run_check()[0])

    def test_unregistered_research(self):
        (self.root / '研究.md').write_text('# 新资料', encoding='utf-8')
        self.assertTrue(self.run_check()[0])

    def test_bad_catalog(self):
        (self.root / 'docs/catalog.json').write_text('{', encoding='utf-8')
        self.assertTrue(self.run_check()[0])

    def test_conflict_and_bad_code(self):
        (self.root / 'README.md').write_text('<<<<<<< HEAD\n60000.SH\n=======\n>>>>>>> branch', encoding='utf-8')
        self.assertEqual(len(self.run_check()[0]), 2)


if __name__ == '__main__':
    unittest.main()
