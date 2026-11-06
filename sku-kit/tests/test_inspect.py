import io
import json
import threading
import unittest
import urllib.request
import urllib.error
import zipfile
from http.server import ThreadingHTTPServer

from engine import VERSION, analyze, package, sha
from server import Handler
from table_io import inspect_table
from test_engine import picture, workbook


ROWS = '<row r="1"><c r="A1" t="inlineStr"><is><t>货号</t></is></c><c r="B1" t="inlineStr"><is><t>产品名称</t></is></c></row><row r="7"><c r="A7" t="inlineStr"><is><t>001</t></is></c><c r="B7" t="inlineStr"><is><t>杯子</t></is></c></row>'


class InspectionTests(unittest.TestCase):
    def test_csv_sniffer_cannot_silently_strip_sku_whitespace(self):
        for table in (b'SKU, name\n 001, cup\n', b'name, SKU\ncup, 001\n'):
            report, _ = analyze('products.csv', table, [picture()])
            self.assertIn('SKU_WHITESPACE', {x['code'] for x in report['issues']})
            self.assertEqual(report['status'], 'error')

    def test_csv_multiline_cells_use_spreadsheet_row_numbers(self):
        info = inspect_table(b'SKU,name\n001,"cup\nred"\n\n002,bag\n', 'csv')
        self.assertEqual(info['row_numbers'], [2,4])
        self.assertEqual(info['preview'][0][1], 'cup\nred')

    def test_multi_sheet_requires_explicit_choice(self):
        data = workbook(ROWS, extra_sheet=True)
        info = inspect_table(data, 'xlsx')
        self.assertTrue(info['requires_sheet'])
        self.assertEqual(info['sheets'], ['Products', 'Other'])
        self.assertEqual(info['headers'], [])
        selected = inspect_table(data, 'xlsx', 'Other')
        self.assertEqual(selected['sheet'], 'Other')
        self.assertEqual(selected['sku_candidates'], ['货号'])
        self.assertEqual(selected['preview'], [['001', '杯子']])
        self.assertEqual(selected['row_numbers'], [7])

    def test_preview_bounded_and_does_not_select_between_two_sku_columns(self):
        data = ('SKU,货号,name\n' + '\n'.join('00%d,alt%d,cup' % (i,i) for i in range(8))).encode()
        info = inspect_table(data, 'csv')
        self.assertEqual(info['row_count'], 8)
        self.assertEqual(len(info['preview']), 3)
        self.assertEqual(info['sku_candidates'], ['SKU', '货号'])
        self.assertEqual(info['preview'][0][0], '000')

    def test_bad_or_missing_sheet_never_falls_back(self):
        with self.assertRaises(ValueError):
            inspect_table(workbook(ROWS), 'xlsx', '不存在')
        with self.assertRaises(ValueError):
            inspect_table(b'SKU,SKU\n001,002', 'csv')

    def test_long_original_names_retain_readable_extensions_in_delivery(self):
        name = '商品详情' * 15 + '.png'
        table_name = '供应商产品资料' * 12 + '.csv'
        table = b'SKU,name\n001,cup'
        image = picture(name)
        report, _ = analyze(table_name, table, [image])
        decision = {'sku': '001', 'sha256': sha(image['data']),
                    'table_sha256': report['table_sha256'], 'context_sha256': report['context_sha256']}
        report, parsed = analyze(table_name, table, [image], assignments={name: decision})
        with zipfile.ZipFile(io.BytesIO(package(report, parsed, table_name, table, [image]))) as z:
            exported = [p for p in z.namelist() if p.startswith('products/')]
            originals = [p for p in z.namelist() if p.startswith('source/')]
            self.assertTrue(exported[0].endswith('.png'))
            self.assertTrue(originals[0].endswith('.csv'))
            self.assertEqual(z.read(exported[0]), image['data'])
            self.assertEqual(z.read(originals[0]), table)


class InspectionHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_health_and_inspect_use_actual_server(self):
        with urllib.request.urlopen(self.url+'/api/health') as response:
            self.assertEqual(json.load(response)['version'], VERSION)
        import base64
        request = urllib.request.Request(self.url+'/api/inspect',
            data=json.dumps({'table': {'name':'test.csv','content':base64.b64encode(b'SKU,name\n001,cup').decode()}}).encode(),
            headers={'Content-Type':'application/json','Origin':self.url})
        with urllib.request.urlopen(request) as response:
            result = json.load(response)
        self.assertEqual(result['preview'], [['001','cup']])
        self.assertEqual(result['sku_candidates'], ['SKU'])

    def test_inspect_rejects_external_origin(self):
        request = urllib.request.Request(self.url+'/api/inspect',data=b'{}',
            headers={'Content-Type':'application/json','Origin':'https://external.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)

    def test_verification_endpoint_checks_raw_zip_and_reports_modified_files(self):
        table = b'SKU,name\n001,cup'
        assets = [picture()]
        report, parsed = analyze('products.csv', table, assets)
        original = package(report, parsed, 'products.csv', table, assets)
        def send(data):
            req = urllib.request.Request(self.url+'/api/verify', data=data,
                headers={'Content-Type':'application/zip','Origin':self.url})
            with urllib.request.urlopen(req) as response:
                return json.load(response)
        self.assertEqual(send(original)['status'], 'ok')
        rewritten = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(rewritten,'w') as target:
            for name in source.namelist():
                content = source.read(name)
                if name.startswith('products/'):
                    content += b'changed'
                target.writestr(name, content)
        failure = send(rewritten.getvalue())
        self.assertEqual(failure['status'], 'error')
        self.assertEqual(failure['issues'][0]['subject'], 'products/001/001.png')
