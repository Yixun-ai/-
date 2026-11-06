import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

from engine import analyze


spec = importlib.util.spec_from_file_location('demo_generator', Path(__file__).resolve().parents[2] / 'scripts' / 'make_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DemoTests(unittest.TestCase):
    def report(self, directory):
        return analyze('products.csv', (directory/'products.csv').read_bytes(),
                       [{'name':p.name, 'data':p.read_bytes()} for p in sorted((directory/'images').iterdir())])[0]

    def test_examples_have_a_clean_path_and_an_explicit_manual_review_path(self):
        with tempfile.TemporaryDirectory() as temp:
            output = demo.generate(Path(temp)/'samples')
            self.assertEqual(self.report(output/'clean')['status'], 'ok')
            review = self.report(output/'needs-review')
            self.assertEqual({x['code'] for x in review['issues']}, {'UNMATCHED_IMAGE','MISSING_IMAGE'})

    def test_existing_data_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/'samples';output.mkdir();(output/'original.txt').write_bytes(b'keep')
            with self.assertRaises(ValueError):demo.generate(output)
            self.assertEqual((output/'original.txt').read_bytes(), b'keep')

    def test_archive_is_complete_and_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            demo.generate(root/'first',root/'first.zip')
            demo.generate(root/'second',root/'second.zip')
            self.assertEqual((root/'first.zip').read_bytes(),(root/'second.zip').read_bytes())
            with zipfile.ZipFile(root/'first.zip') as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(len(z.namelist()),7)
                self.assertTrue(z.read('SKU-Kit-demo/clean/products.csv').startswith(b'\xef\xbb\xbfSKU'))
