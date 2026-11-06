import base64
import io
import json
import threading
import unittest
import urllib.request
import urllib.error
import zipfile
from PIL import Image
from unittest.mock import patch

from engine import analyze
from server import Handler, LocalHTTPServer


class HTTPTests(unittest.TestCase):
    def test_local_server_starts_without_hostname_resolution(self):
        with patch('socket.getfqdn', side_effect=OSError('resolver unavailable')) as resolver:
            with LocalHTTPServer(('127.0.0.1', 0), Handler) as local:
                self.assertEqual(local.server_name, '127.0.0.1')
                self.assertGreater(local.server_port, 0)
            resolver.assert_not_called()

    @classmethod
    def setUpClass(cls):
        cls.server = LocalHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        buffer=io.BytesIO();Image.new('RGB',(32,32),'red').save(buffer,'PNG')
        self.image=buffer.getvalue();self.table=b'SKU,name\n001,cup'
        self.request={'table':{'name':'products.csv','content':base64.b64encode(self.table).decode()},
                      'images':[{'name':'001.png','content':base64.b64encode(self.image).decode()}], 'options':{}}

    def post(self, path, payload=None, origin=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(payload if payload is not None else self.request).encode(),
                                   headers={'Content-Type':'application/json','Origin':origin or self.url})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status,response.headers,response.read()
        except urllib.error.HTTPError as error:
            return error.code,error.headers,error.read()

    def test_http_equals_direct_engine(self):
        code,_,raw=self.post('/api/check')
        direct,_=analyze('products.csv',self.table,[{'name':'001.png','data':self.image}])
        self.assertEqual(code,200);self.assertEqual(json.loads(raw),direct)

    def test_http_pack_roundtrip(self):
        code,headers,raw=self.post('/api/pack')
        self.assertEqual(code,200);self.assertEqual(headers['Content-Type'],'application/zip')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIsNone(z.testzip());self.assertEqual(z.read('products/001/001.png'),self.image)
            self.assertEqual(z.read('source/products.csv'),self.table)

    def test_errors_block_http_even_allow_warnings(self):
        self.request['table']['content']=base64.b64encode(b'SKU,name\n001,cup\n001,duplicate').decode()
        self.request['allow_warnings']=True
        code,_,raw=self.post('/api/pack')
        self.assertEqual(code,422);self.assertGreater(json.loads(raw)['summary']['errors'],0)

    def test_external_origin_rejected(self):
        code,_,_=self.post('/api/check',origin='https://example.com')
        self.assertEqual(code,400)

    def test_invalid_base64_rejected(self):
        self.request['table']['content']='not valid base64!'
        code,_,raw=self.post('/api/check');self.assertEqual(code,400)
        self.assertEqual(json.loads(raw)['status'],'invalid_input')

    def test_invalid_options_rejected(self):
        self.request['options']['min_width']=1.5
        code,_,_=self.post('/api/check');self.assertEqual(code,400)

    def test_checklist_download_with_errors(self):
        self.request['images']=[]
        code,headers,raw=self.post('/api/checklist')
        self.assertEqual(code,200);self.assertIn('text/csv',headers['Content-Type'])
        self.assertIn('需要补图',raw.decode('utf-8-sig'))

    def test_http_manual_assignment_and_stale_rejection(self):
        self.request['images'][0]['name']='unknown.png'
        _,_,raw=self.post('/api/check');report=json.loads(raw)
        self.request['options']['assignments']={'unknown.png':{'sku':'001','sha256':report['images'][0]['sha256'],
          'table_sha256':report['table_sha256'],'context_sha256':report['context_sha256']}}
        code,_,raw=self.post('/api/check');self.assertEqual(code,200);self.assertEqual(json.loads(raw)['status'],'ok')
        self.request['table']['content']=base64.b64encode(b'SKU,name\n001,new').decode()
        code,_,_=self.post('/api/check');self.assertEqual(code,400)
