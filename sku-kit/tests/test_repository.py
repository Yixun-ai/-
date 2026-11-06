import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'check_repository.py'
SPEC = importlib.util.spec_from_file_location('repository_check', SCRIPT)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class RepositoryCheckTests(unittest.TestCase):
    def test_valid_encoded_image_reference_and_parenthesized_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / '图 片(1).png').write_bytes(b'image')
            doc = root / 'README.md'
            doc.write_text('![image](图%20片(1).png)\n[ref]: <图 片(1).png>\n', encoding='utf-8')
            self.assertEqual(CHECKER.check_markdown(doc, root), [])

    def test_code_and_absolute_path_examples_are_not_false_broken_links(self):
        content = ('```md\n[example](missing.md)\n```\n~~~\n[example](missing2.md)\n~~~\n'
                   '`[inline](missing3.md)`\n    [indented](missing4.md)\n'
                   '[local](/Users/example/file.md)\n[windows](C:\\demo\\file.md)\n'
                   '[remote](https://example.invalid/not-fetched)\n[anchor](#section)\n')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            doc = root / 'README.md'
            doc.write_text(content, encoding='utf-8')
            self.assertEqual(CHECKER.check_markdown(doc, root), [])

    def test_missing_links_and_repository_escape_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            doc = root / 'README.md'
            doc.write_text('[broken](missing.md)\n[escape](../outside.md)\n', encoding='utf-8')
            problems = CHECKER.check_markdown(doc, root)
            self.assertEqual(len(problems), 2)
            self.assertIn('missing relative link', problems[0])
            self.assertIn('escapes repository', problems[1])

    def test_duplicate_form_identifiers_and_nonboolean_requirement_fail(self):
        form = {'name': 'Report', 'description': 'Problem', 'body': [
            {'type': 'input', 'id': 'same', 'attributes': {'label': 'First'}},
            {'type': 'input', 'id': 'same', 'attributes': {'label': 'Second'}, 'validations': {'required': 'yes'}}]}
        problems = CHECKER.check_issue_form(form, 'test')
        self.assertTrue(any('duplicate id' in problem for problem in problems))
        self.assertTrue(any('must be boolean' in problem for problem in problems))

    def test_valid_markdown_and_checkbox_form(self):
        form = {'name': 'Report', 'description': 'Problem', 'body': [
            {'type': 'markdown', 'attributes': {'value': 'Describe the issue'}},
            {'type': 'checkboxes', 'id': 'confirmation', 'attributes': {'label': 'Confirm',
                'options': [{'label': 'No secrets included', 'required': True}]}}]}
        self.assertEqual(CHECKER.check_issue_form(form, 'test'), [])


if __name__ == '__main__':
    unittest.main()
