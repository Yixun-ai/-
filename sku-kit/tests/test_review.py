import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image

from engine import analyze, package, checklist
from cli import run_paths
from mcp_server import MCP


def image(name='unknown.png', color='green'):
    buffer=io.BytesIO();Image.new('RGB',(32,32),color).save(buffer,'PNG')
    return {'name':name,'data':buffer.getvalue()}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.table=b'SKU,name\n001,cup'
        self.images=[image()]
        self.before,_=analyze('products.csv',self.table,self.images)
        self.assignment={'unknown.png':{'sku':'001','sha256':self.before['images'][0]['sha256'],
                         'table_sha256':self.before['table_sha256'],'context_sha256':self.before['context_sha256']}}

    def test_manual_assignment_resolves_missing_and_unmatched(self):
        after,_=analyze('products.csv',self.table,self.images,assignments=self.assignment)
        self.assertEqual(after['status'],'ok')
        self.assertEqual(after['images'][0]['sku'],'001')
        self.assertEqual(after['images'][0]['assignment_source'],'manual')

    def test_assignment_audit_in_package(self):
        after,parsed=analyze('products.csv',self.table,self.images,assignments=self.assignment)
        data=package(after,parsed,'products.csv',self.table,self.images)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(z.read('products/001/unknown.png'),self.images[0]['data'])
            self.assertEqual(json.loads(z.read('reports/manifest.json'))['manual_assignments'],self.assignment)

    def test_changed_image_cannot_reuse_decision(self):
        with self.assertRaises(ValueError):
            analyze('products.csv',self.table,[image(color='red')],assignments=self.assignment)

    def test_changed_table_cannot_reuse_decision(self):
        with self.assertRaises(ValueError):
            analyze('products.csv',b'SKU,name\n001,new name',self.images,assignments=self.assignment)

    def test_changed_sku_column_cannot_reuse_decision(self):
        raw=b'SKU,code,name\n001,001,cup'
        before,_=analyze('products.csv',raw,self.images,sku_column='SKU')
        assignment={'unknown.png':{'sku':'001','sha256':before['images'][0]['sha256'],
                    'table_sha256':before['table_sha256'],'context_sha256':before['context_sha256']}}
        with self.assertRaises(ValueError):
            analyze('products.csv',raw,self.images,sku_column='code',assignments=assignment)

    def test_unknown_target_and_source_rejected(self):
        self.assignment['unknown.png']['sku']='999'
        with self.assertRaises(ValueError):analyze('products.csv',self.table,self.images,assignments=self.assignment)
        with self.assertRaises(ValueError):analyze('products.csv',self.table,self.images,assignments={'missing.png':{}})

    def test_corrupt_file_cannot_be_fixed_by_assignment(self):
        assets=[{'name':'unknown.png','data':b'broken'}]
        before,_=analyze('products.csv',self.table,assets)
        self.assignment['unknown.png'].update(sha256=before['images'][0]['sha256'])
        after,_=analyze('products.csv',self.table,assets,assignments=self.assignment)
        self.assertGreater(after['summary']['errors'],0)
        self.assertIn('INVALID_IMAGE',[x['code'] for x in after['issues']])

    def test_explicit_unassignment_does_not_auto_match(self):
        assets=[image('001.png'),image('001.jpg')]
        # second PNG is deliberately named .jpg; ensure manual action cannot bypass format checks.
        before,_=analyze('products.csv',self.table,assets)
        assignment={'001.png':{'sku':None,'sha256':before['images'][0]['sha256'],
                    'table_sha256':before['table_sha256'],'context_sha256':before['context_sha256']}}
        by_name={x['source']:x for x in before['images']};assignment['001.png']['sha256']=by_name['001.png']['sha256']
        after,_=analyze('products.csv',self.table,assets,assignments=assignment)
        self.assertIsNone(next(x for x in after['images'] if x['source']=='001.png')['sku'])

    def test_report_does_not_change_if_input_assignment_mutates(self):
        after,_=analyze('products.csv',self.table,self.images,assignments=self.assignment)
        self.assignment['unknown.png']['sku']=None
        self.assertEqual(after['assignments']['unknown.png']['sku'],'001')

    def test_repeat_rows_must_be_explicit_and_warn(self):
        raw=b'SKU,name,qty\n001,cup,1\n001,cup,2'
        assets=[image('001.png')]
        strict,_=analyze('products.csv',raw,assets)
        loose,_=analyze('products.csv',raw,assets,duplicate_policy='repeat_rows')
        self.assertGreater(strict['summary']['errors'],0)
        self.assertEqual(loose['summary']['errors'],0)
        self.assertIn('REPEATED_SKU_ROWS',[x['code'] for x in loose['issues']])

    def test_conflicting_names_block_repeat_policy(self):
        report,_=analyze('products.csv',b'SKU,name\n001,cup\n001,bag',[image('001.png')],duplicate_policy='repeat_rows')
        self.assertIn('DUPLICATE_SKU_CONFLICT',[x['code'] for x in report['issues']])

    def test_checklist_works_with_blocking_errors(self):
        report,_=analyze('products.csv',self.table,[])
        data=checklist(report).decode('utf-8-sig')
        self.assertIn('需要补图',data);self.assertIn('001',data);self.assertIn('cup',data)

    def test_cli_assignments_and_checklist_real_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'images').mkdir()
            (root/'products.csv').write_bytes(self.table)
            (root/'images/unknown.png').write_bytes(self.images[0]['data'])
            (root/'assignments.json').write_text(json.dumps(self.assignment))
            result=run_paths('pack',root/'products.csv',root/'images',output=root/'delivery.zip',assignments_file=root/'assignments.json')
            self.assertTrue(result['export']['written'])
            result=run_paths('checklist',root/'products.csv',root/'images',output=root/'needs.csv')
            self.assertTrue(result['export']['written'])
            self.assertIn('需要补图',(root/'needs.csv').read_text(encoding='utf-8-sig'))

    def test_mcp_can_apply_direct_decision_and_export_checklist(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'images').mkdir();(root/'products.csv').write_bytes(self.table)
            (root/'images/unknown.png').write_bytes(self.images[0]['data'])
            server=MCP(root)
            server.dispatch('initialize',{'protocolVersion':'2025-11-25'});server.dispatch('notifications/initialized',{})
            result=server.dispatch('tools/call',{'name':'sku_check','arguments':{'table':'products.csv','images':'images','assignments':self.assignment}})
            self.assertFalse(result['isError']);self.assertEqual(json.loads(result['content'][0]['text'])['status'],'ok')
            result=server.dispatch('tools/call',{'name':'sku_checklist','arguments':{'table':'products.csv','images':'images','output':'needs.csv'}})
            self.assertFalse(result['isError']);self.assertTrue((root/'needs.csv').is_file())
