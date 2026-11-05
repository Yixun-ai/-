import base64
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image

from engine import analyze, package, match_sku, safe_name, sha
from table_io import parse_table
from cli import run_paths, write_new
from mcp_server import MCP


def picture(name='001.png', size=(20, 20)):
    stream = io.BytesIO(); Image.new('RGB', size, 'green').save(stream, 'PNG')
    return {'name': name, 'data': stream.getvalue()}


def workbook(rows, extra_sheet=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as book:
        book.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Products" r:id="r1"/>' + ('<sheet name="Other" r:id="r2"/>' if extra_sheet else '') + '</sheets></workbook>')
        book.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/><Relationship Id="r2" Target="worksheets/sheet1.xml"/></Relationships>')
        book.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+rows+'</sheetData></worksheet>')
    return stream.getvalue()


class EngineTests(unittest.TestCase):
    def check(self, text='SKU,name\n001,cup', images=None, **options):
        return analyze('products.csv', text.encode(), images if images is not None else [picture()], **options)

    def codes(self, report):
        return {x['code'] for x in report['issues']}

    def test_clean_and_leading_zero_preserved(self):
        report, _ = self.check()
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['products'][0]['sku'], '001')

    def test_exact_does_not_guess_suffix(self):
        report, _ = self.check(images=[picture('001_main.png')])
        self.assertIn('MISSING_IMAGE', self.codes(report))

    def test_suffix_ambiguity_blocks_instead_of_longest_guess(self):
        report, _ = self.check('SKU,name\nA,one\nA_RED,two', [picture('A_RED_main.png')], match_mode='suffix')
        self.assertIn('AMBIGUOUS_MATCH', self.codes(report))
        self.assertIsNone(report['images'][0]['sku'])

    def test_exact_name_has_priority(self):
        self.assertEqual(match_sku('A_RED.png', ['A', 'A_RED'], 'suffix'), ['A_RED'])

    def test_prefix_boundary(self):
        self.assertEqual(match_sku('0010.png', ['001'], 'suffix'), [])

    def test_duplicate_and_blank_sku(self):
        report, _ = self.check('SKU,name\n001,cup\n001,repeat\n,missing')
        self.assertTrue({'DUPLICATE_SKU', 'EMPTY_SKU'} <= self.codes(report))

    def test_case_collision_blocks_even_case_sensitive(self):
        report, _ = self.check('SKU,name\nabc,one\nABC,two', [picture('abc.png'), picture('ABC.png')])
        self.assertIn('SKU_CASE_COLLISION', self.codes(report))

    def test_whitespace_not_silently_fixed(self):
        report, _ = self.check('SKU,name\n 001,cup')
        self.assertIn('SKU_WHITESPACE', self.codes(report))

    def test_real_source_rows_with_blank_lines(self):
        report, _ = self.check('SKU,name\n\n001,cup\n\n,missing')
        self.assertEqual(next(x['row'] for x in report['issues'] if x['code']=='EMPTY_SKU'), 5)

    def test_corrupt_and_disguised_images(self):
        for asset in ({'name':'001.jpg','data':b'bad'}, picture('001.jpg')):
            report, _ = self.check(images=[asset])
            self.assertIn('INVALID_IMAGE', self.codes(report))

    def test_truncated_image(self):
        image = picture(); image['data'] = image['data'][:40]
        report, _ = self.check(images=[image])
        self.assertIn('INVALID_IMAGE', self.codes(report))

    def test_invalid_image_report_is_repeatable(self):
        assets=[{'name':'001.jpg','data':b'bad'}]
        self.assertEqual(self.check(images=assets)[0],self.check(images=assets)[0])

    def test_minimum_dimensions(self):
        report, _ = self.check(min_width=100)
        self.assertEqual(report['status'], 'warning')
        self.assertIn('SMALL_IMAGE', self.codes(report))

    def test_unknown_files_reported(self):
        report, _ = self.check(images=[picture(), {'name':'notes.txt','data':b'notes'}])
        self.assertIn('UNSUPPORTED_FILE', self.codes(report))

    def test_unsafe_relative_path_rejected(self):
        for name in ('../001.png', '/001.png', 'dir\\001.png'):
            with self.assertRaises(ValueError):
                self.check(images=[picture(name)])

    def test_duplicate_input_path_rejected(self):
        with self.assertRaises(ValueError):
            self.check(images=[picture(), picture()])

    def test_duplicate_headers_and_ragged_rows_rejected(self):
        for content in ('SKU,SKU\n001,001', 'SKU,name\n001,cup,extra', 'SKU,name\n001'):
            with self.assertRaises(ValueError):
                self.check(content)

    def test_ambiguous_sku_column_rejected(self):
        with self.assertRaises(ValueError):
            self.check('SKU,货号\n001,001')

    def test_xlsx_numeric_and_formula_sku_rejected(self):
        for cell in ('<c r="A2"><v>1</v></c>', '<c r="A2" t="str"><f>TEXT(1,"000")</f><v>001</v></c>'):
            raw = workbook('<row r="1"><c r="A1" t="inlineStr"><is><t>SKU</t></is></c></row><row r="2">'+cell+'</row>')
            report, _ = analyze('products.xlsx', raw, [picture()])
            self.assertIn('SKU_NOT_TEXT', self.codes(report))

    def test_multi_sheet_requires_explicit_name(self):
        rows='<row><c r="A1" t="inlineStr"><is><t>SKU</t></is></c></row><row><c r="A2" t="inlineStr"><is><t>001</t></is></c></row>'
        raw = workbook(rows, True)
        with self.assertRaises(ValueError):
            parse_table(raw, 'xlsx')
        self.assertEqual(parse_table(raw, 'xlsx', 'Products')['rows'], [['001']])

    def test_xlsx_style_only_cells_do_not_extend_table(self):
        rows=('<row r="1"><c r="A1" t="inlineStr"><is><t>SKU</t></is></c><c r="J1" s="1" t="n"/></row>'
              '<row r="2"><c r="A2" t="inlineStr"><is><t>001</t></is></c><c r="XFD2" s="1" t="n"/></row>')
        report, table=analyze('products.xlsx',workbook(rows),[picture()])
        self.assertEqual(report['status'],'ok')
        self.assertEqual(table['headers'],['SKU'])

    def test_uncached_formula_row_is_not_silently_dropped(self):
        rows=('<row r="1"><c r="A1" t="inlineStr"><is><t>SKU</t></is></c></row>'
              '<row r="2"><c r="A2"><f>1+1</f><v/></c></row>')
        report,_=analyze('products.xlsx',workbook(rows),[picture()])
        self.assertEqual(report['summary']['products'],1)
        self.assertIn('EMPTY_SKU',self.codes(report))

    def test_warnings_require_explicit_permission(self):
        assets=[picture(), picture('extra.png')]; source=b'SKU,name\n001,cup'
        report, table = self.check(images=assets)
        with self.assertRaises(ValueError):
            package(report,table,'products.csv',source,assets)
        payload=package(report,table,'products.csv',source,assets,True)
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            self.assertIn('unmatched/extra.png',z.namelist())

    def test_errors_cannot_be_overridden(self):
        source=b'SKU,name\n001,cup\n001,duplicate'; assets=[picture()]
        report, table=self.check(source.decode(), assets)
        with self.assertRaises(ValueError):
            package(report,table,'products.csv',source,assets,True)

    def test_archive_roundtrip_all_bytes_and_hashes(self):
        source=b'SKU,name\n001,cup';assets=[picture()]
        report, table=self.check(images=assets);payload=package(report,table,'products.csv',source,assets)
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(z.read('source/products.csv'),source)
            self.assertEqual(z.read('products/001/001.png'),assets[0]['data'])
            manifest=json.loads(z.read('reports/manifest.json'))
            self.assertEqual(sha(z.read(manifest['files'][0]['target'])), manifest['files'][0]['sha256'])
        self.assertEqual(payload,package(report,table,'products.csv',source,assets))

    def test_input_change_after_check_rejected(self):
        source=b'SKU,name\n001,cup';assets=[picture()]
        report, table=self.check(images=assets)
        with self.assertRaises(ValueError):
            package(report,table,'products.csv',source,[picture(size=(30,30))])

    def test_safe_filename_collision_preserves_both(self):
        assets=[picture('folder1/001.png'),picture('folder2/001.png')];source=b'SKU,name\n001,cup'
        report, table=self.check(images=assets)
        with zipfile.ZipFile(io.BytesIO(package(report,table,'products.csv',source,assets))) as z:
            self.assertIn('products/001/001.png',z.namelist())
            self.assertIn('products/001/001_2.png',z.namelist())

    def test_byte_safe_names(self):
        self.assertLessEqual(len(safe_name('中'*300).encode()),160)
        self.assertEqual(safe_name('CON'),'_CON')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'images').mkdir();(self.root/'images/001.png').write_bytes(picture()['data'])
        (self.root/'products.csv').write_bytes(b'SKU,name\n001,cup')

    def tearDown(self):
        self.temp.cleanup()

    def test_cli_exit_json_and_no_overwrite(self):
        args=[sys.executable,'cli.py','check','--table',str(self.root/'products.csv'),'--images',str(self.root/'images')]
        process=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual(json.loads(process.stdout)['status'],'ok')
        out=self.root/'result.zip'
        result=run_paths('pack',self.root/'products.csv',self.root/'images',output=out)
        self.assertTrue(result['export']['written']);before=out.read_bytes()
        with self.assertRaises(ValueError):
            run_paths('pack',self.root/'products.csv',self.root/'images',output=out)
        self.assertEqual(out.read_bytes(),before)

    def test_cli_findings_exit_two(self):
        (self.root/'products.csv').write_bytes(b'SKU,name\n001,cup\n002,missing')
        process=subprocess.run([sys.executable,'cli.py','check','--table',str(self.root/'products.csv'),'--images',str(self.root/'images')],capture_output=True,text=True)
        self.assertEqual(process.returncode,2);self.assertEqual(json.loads(process.stdout)['status'],'error')

    def test_output_inside_images_rejected(self):
        with self.assertRaises(ValueError):
            run_paths('pack',self.root/'products.csv',self.root/'images',output=self.root/'images/result.zip')

    def test_symlink_rejected(self):
        (self.root/'images/link.png').symlink_to(self.root/'images/001.png')
        with self.assertRaises(ValueError):
            run_paths('check',self.root/'products.csv',self.root/'images')

    def ready_mcp(self):
        server=MCP(self.root)
        server.handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})
        server.handle({'jsonrpc':'2.0','method':'notifications/initialized'})
        return server

    def test_mcp_handshake_list_check(self):
        server=self.ready_mcp()
        result=server.handle({'jsonrpc':'2.0','id':2,'method':'tools/list'})
        self.assertEqual(len(result['result']['tools']),3)
        result=server.handle({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'sku_check','arguments':{'table':'products.csv','images':'images'}}})
        self.assertFalse(result['result']['isError'])
        self.assertEqual(json.loads(result['result']['content'][0]['text'])['status'],'ok')

    def test_mcp_root_escape_and_bad_type(self):
        for args in ({'table':'../outside.csv','images':'images'}, {'table':'products.csv','images':'images','case_sensitive':'false'}):
            result=self.ready_mcp().handle({'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'sku_check','arguments':args}})
            self.assertTrue(result['result']['isError'])

    def test_mcp_requires_initialization(self):
        result=MCP(self.root).handle({'jsonrpc':'2.0','id':2,'method':'tools/list'})
        self.assertEqual(result['error']['code'],-32002)

    def test_mcp_stdio_process(self):
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'sku_check','arguments':{'table':'products.csv','images':'images'}}}]
        process=subprocess.run([sys.executable,'mcp_server.py','--root',str(self.root)],input='\n'.join(map(json.dumps,messages))+'\n',text=True,capture_output=True)
        replies=[json.loads(x) for x in process.stdout.splitlines()]
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual(len(replies),2)
        self.assertEqual(replies[-1]['id'],2);self.assertFalse(replies[-1]['result']['isError'])
