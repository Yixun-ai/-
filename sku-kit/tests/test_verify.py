import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from engine import analyze, package, sha
from verify import MAX_FILES, MAX_PACKAGE, verify_package


def image(name='001.png'):
    buffer = io.BytesIO()
    Image.new('RGB', (12, 12), 'green').save(buffer, 'PNG')
    return {'name': name, 'data': buffer.getvalue()}


def delivery(extra=False, manual=False):
    raw = b'SKU,name\n001,cup'
    assets = [image('unknown.png' if manual else '001.png')]
    if extra:
        assets.extend([image('unmatched.png'), {'name': 'notes.txt', 'data': b'not exported'}])
    report, table = analyze('products.csv', raw, assets)
    if manual:
        decision = {'unknown.png': {'sku': '001', 'sha256': report['images'][0]['sha256'],
                    'table_sha256': report['table_sha256'], 'context_sha256': report['context_sha256']}}
        report, table = analyze('products.csv', raw, assets, assignments=decision)
    return package(report, table, 'products.csv', raw, assets, True)


def rewrite(payload, changes=None, remove=(), append=()):
    changes = changes or {}
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as original, zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as output:
        for entry in original.infolist():
            if entry.filename in remove:
                continue
            data = original.read(entry)
            if entry.filename in changes:
                value = changes[entry.filename]
                data = value(data) if callable(value) else value
            output.writestr(entry.filename, data)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            for name, data in append:
                output.writestr(name, data)
    return result.getvalue()


def mutate_json(payload, name, modify):
    def transform(raw):
        value = json.loads(raw)
        modify(value)
        return json.dumps(value, ensure_ascii=False).encode()
    return rewrite(payload, {name: transform})


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.payload = delivery()
        self.target = 'products/001/001.png'

    def rejected(self, payload, code=None):
        report = verify_package(payload)
        self.assertEqual(report['status'], 'error', report)
        self.assertEqual(report['summary']['errors'], len(report['issues']))
        if code:
            self.assertEqual(report['issues'][0]['code'], code, report)
        return report

    def test_valid_generated_package_bytes_path_and_stream(self):
        report = verify_package(self.payload)
        self.assertEqual(report['status'], 'ok', report)
        self.assertEqual(report['summary']['images'], 1)
        self.assertEqual(report['summary']['files'], 7)
        self.assertFalse(report['limits']['authenticity_checked'])
        self.assertFalse(report['limits']['content_identity_checked'])
        stream = io.BytesIO(self.payload); stream.seek(9)
        self.assertEqual(verify_package(stream)['status'], 'ok')
        self.assertEqual(stream.tell(), 9)
        self.assertFalse(stream.closed)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'delivery.zip'; path.write_bytes(self.payload)
            self.assertEqual(verify_package(path)['status'], 'ok')
            self.assertEqual(path.read_bytes(), self.payload)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_legacy_v03_report_and_readme_supported(self):
        payload = mutate_json(self.payload, 'reports/report.json', lambda report: report.update(engine_version='0.3.0'))
        payload = rewrite(payload, {'README.txt': lambda data: data.replace(b'v0.4.0', b'v0.3')})
        self.assertEqual(verify_package(payload)['status'], 'ok')

    def test_long_filename_collisions_in_current_and_legacy_packages(self):
        raw = b'SKU,name\n001,cup'
        name = '001_' + 'detail' * 35 + '.png'
        assets = [image('one/' + name), image('two/' + name)]
        report, table = analyze('products.csv', raw, assets, match_mode='suffix')
        current = package(report, table, 'products.csv', raw, assets)
        self.assertEqual(verify_package(current)['status'], 'ok')
        report['engine_version'] = '0.3.0'
        # These fixture names need only v0.3's byte-truncation step.
        with patch('engine.safe_name', lambda value: value.encode()[:160].decode('utf-8', 'ignore')):
            legacy = package(report, table, 'products.csv', raw, assets)
        self.assertEqual(verify_package(legacy)['status'], 'ok')

    def test_manual_assignment_and_unmatched_and_unsupported_input(self):
        for payload in (delivery(manual=True), delivery(extra=True)):
            self.assertEqual(verify_package(payload)['status'], 'ok')

    def test_original_image_modified_with_valid_recalculated_zip_crc(self):
        self.rejected(rewrite(self.payload, {self.target: b'x' * len(image()['data'])}), 'HASH_MISMATCH')

    def test_original_table_modified(self):
        self.rejected(rewrite(self.payload, {'source/products.csv': b'SKU,name\n001,bag'}), 'HASH_MISMATCH')

    def test_image_removed(self):
        self.rejected(rewrite(self.payload, remove=[self.target]), 'FILE_SET_MISMATCH')

    def test_extra_file(self):
        self.rejected(rewrite(self.payload, append=[('unexpected.txt', b'extra')]), 'FILE_SET_MISMATCH')

    def test_missing_manifest_and_other_required_files(self):
        for name in ('reports/manifest.json', 'reports/report.json', 'reports/issues.csv', 'README.txt'):
            self.rejected(rewrite(self.payload, remove=[name]), 'MISSING_FILE')

    def test_duplicate_archive_entries(self):
        self.rejected(rewrite(self.payload, append=[(self.target, b'bad')]), 'DUPLICATE_ENTRY')
        self.rejected(rewrite(self.payload, append=[('products/001/001.PNG', b'bad')]), 'DUPLICATE_ENTRY')

    def test_path_traversal_and_windows_unsafe_names_rejected(self):
        for name in ('../escape.txt', '/absolute.txt', 'C:/escape.txt', 'products/../escape.png',
                     'products\\001.png', 'products//001.png', 'products/./001.png', 'products/CON'):
            self.rejected(rewrite(self.payload, append=[(name, b'bad')]), 'UNSAFE_PATH')

    def test_symbolic_link_zip_entry(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as output, zipfile.ZipFile(io.BytesIO(self.payload)) as original:
            for entry in original.infolist():
                if entry.filename == self.target:
                    entry.create_system = 3; entry.external_attr = 0o120777 << 16
                output.writestr(entry, original.read(entry))
        self.rejected(stream.getvalue(), 'UNSAFE_ENTRY')

    def test_manifest_target_hash_size_type_forgery(self):
        mutations = [lambda manifest: manifest['files'][0].update(target='../escape.png'),
                     lambda manifest: manifest['files'][0].update(sha256='0' * 64),
                     lambda manifest: manifest['files'][0].update(sha256=123),
                     lambda manifest: manifest['files'][0].update(sku=True),
                     lambda manifest: manifest['files'][0].update(size=123),
                     lambda manifest: manifest.update(files={}),
                     lambda manifest: manifest.update(sku_folders={'001': '../outside'})]
        for mutation in mutations:
            self.rejected(mutate_json(self.payload, 'reports/manifest.json', mutation), 'INVALID_MANIFEST')

    def test_report_forged_types_hash_and_size(self):
        mutations = [lambda report: report['images'][0].update(size=True),
                     lambda report: report['images'][0].update(size=-1),
                     lambda report: report['images'][0].update(size='12'),
                     lambda report: report['images'][0].update(valid=1),
                     lambda report: report['images'][0].update(width=True),
                     lambda report: report['images'][0].update(sha256='invalid'),
                     lambda report: report.update(table_sha256='not a digest'),
                     lambda report: report.update(products=None),
                     lambda report: report['summary'].update(errors=False)]
        for mutation in mutations:
            self.rejected(mutate_json(self.payload, 'reports/report.json', mutation), 'INVALID_REPORT')
        self.rejected(mutate_json(self.payload, 'reports/report.json',
                                 lambda report: report['images'][0].update(size=2)), 'SIZE_MISMATCH')

    def test_manual_assignment_mismatch(self):
        payload = delivery(manual=True)
        payload = mutate_json(payload, 'reports/manifest.json', lambda manifest: manifest.update(manual_assignments={}))
        self.rejected(payload, 'INVALID_MANIFEST')

    def test_invalid_json_and_duplicate_json_keys(self):
        for value in (b'{bad', b'{"files":[],"files":[]}', b'{"files":NaN}', b'\xff'):
            self.rejected(rewrite(self.payload, {'reports/manifest.json': value}), 'INVALID_JSON')

    def test_unsupported_schema(self):
        self.rejected(mutate_json(self.payload, 'reports/report.json', lambda report: report.update(schema_version='2.0')), 'INVALID_REPORT')

    def test_truncated_not_zip_and_corrupt_crc_no_traceback(self):
        self.rejected(b'not zip')
        self.rejected(self.payload[:-12])
        with zipfile.ZipFile(io.BytesIO(self.payload)) as archive:
            info = archive.getinfo(self.target)
        data = bytearray(self.payload)
        name_size, extra_size = struct.unpack_from('<HH', data, info.header_offset + 26)
        data[info.header_offset + 30 + name_size + extra_size + info.compress_size // 2] ^= 255
        report = self.rejected(bytes(data))
        self.assertNotIn('Traceback', json.dumps(report))

    def test_excessive_declared_entry_size_rejected_before_open(self):
        data = bytearray(self.payload)
        directory_offset = data.find(b'PK\x01\x02')
        struct.pack_into('<L', data, directory_offset + 24, 34 * 1024 * 1024)
        self.rejected(bytes(data), 'ZIP_SIZE_LIMIT')

    def test_directory_count_limit_checked_before_zipfile_constructor(self):
        data = bytearray(self.payload)
        end = data.rfind(b'PK\x05\x06')
        struct.pack_into('<HH', data, end + 8, MAX_FILES + 1, MAX_FILES + 1)
        with patch('verify.zipfile.ZipFile', side_effect=AssertionError('must not allocate directory')):
            self.rejected(bytes(data), 'ZIP_ENTRY_LIMIT')

    def test_archive_size_limit_without_reading_entire_file(self):
        stream = io.BytesIO(self.payload)
        with patch('verify.MAX_PACKAGE', len(self.payload) - 1):
            self.rejected(stream, 'ZIP_SIZE_LIMIT')

    def test_unpacked_total_limit_checked_before_member_decompression(self):
        with patch('verify.MAX_UNPACKED', 1):
            self.rejected(self.payload, 'ZIP_SIZE_LIMIT')

    def test_encrypted_flags_and_unknown_compression(self):
        for field, value in ((8, 1), (10, 99)):
            data = bytearray(self.payload)
            directory = data.find(b'PK\x01\x02')
            struct.pack_into('<H', data, directory + field, value)
            self.rejected(bytes(data), 'UNSUPPORTED_ZIP')

    def test_missing_unreadable_and_nonseekable_input(self):
        for source in ('/this-file-does-not-exist.zip', object()):
            report = verify_package(source)
            self.assertEqual(report['status'], 'invalid_input')
            self.assertEqual(report['issues'][0]['code'], 'READ_FAILED')

    def test_manifest_is_not_a_signature(self):
        # A sender can consistently replace content and all its recorded hashes.
        # The verifier must not imply this package came from a trusted author.
        changed = b'SKU,name\n001,bag'
        payload = rewrite(self.payload, {'source/products.csv': changed})
        def change_report(report):
            report['table_sha256'] = sha(changed)
            context = {'table_sha256': report['table_sha256'], 'sheet': report['rules']['sheet'],
                       'sku_column': report['rules']['sku_column']}
            report['context_sha256'] = sha(json.dumps(context, sort_keys=True, ensure_ascii=False).encode())
        payload = mutate_json(payload, 'reports/report.json', change_report)
        report = verify_package(payload)
        self.assertEqual(report['status'], 'ok')
        self.assertFalse(report['limits']['authenticity_checked'])

    def test_cli_verify_exit_codes_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'package.zip'
            cases = [(self.payload, 0, 'ok'), (b'not a zip', 2, 'error'), (None, 1, 'invalid_input')]
            for data, code, status in cases:
                if data is None:
                    path.unlink()
                else:
                    path.write_bytes(data)
                process = subprocess.run([sys.executable, 'cli.py', 'verify', '--package', str(path)],
                                         capture_output=True, text=True)
                self.assertEqual(process.returncode, code, process.stderr)
                self.assertEqual(json.loads(process.stdout)['status'], status)
                self.assertEqual(process.stderr, '')


if __name__ == '__main__':
    unittest.main()
