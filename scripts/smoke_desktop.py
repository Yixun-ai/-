#!/usr/bin/env python3
"""Launch the actual distributable and verify UI, checking, blocking, and ZIP bytes."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib


def png(red, green, blue):
    """Generate independent PNG input using only the standard library."""
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data +
                struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
    rows = (b'\x00' + bytes((red, green, blue)) * 64) * 64
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def request(url, path, payload=None, origin=None, raw_body=None):
    headers = {'Origin': origin or url}
    data = None
    if raw_body is not None:
        data = raw_body
        headers['Content-Type'] = 'application/zip'
    elif payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url + path, data=data, headers=headers)
    # A user's proxy settings must not route the smoke test away from loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def verify(url):
    status, _, data = request(url, '/api/health')
    health = json.loads(data)
    if (status != 200 or health.get('version') != '0.4.0' or health.get('status') != 'ok'
            or health.get('storage') != 'memory' or health.get('model_api_required') is not False):
        raise AssertionError('Unexpected health response: %s' % health)
    for path, marker in (('/', b'<html'), ('/app.mjs', b'import'), ('/style.css', b'{'),
                         ('/project.mjs', b'export')):
        status, _, data = request(url, path)
        if status != 200 or marker not in data:
            raise AssertionError('Bundled UI asset missing: %s (%s)' % (path, status))
    table = 'SKU,name\n001,测试商品一\n002,测试商品二\n'.encode('utf-8-sig')
    images = {'001.png': png(15, 130, 80), '002.png': png(140, 40, 20)}
    payload = {'table': {'name': 'products.csv', 'content': base64.b64encode(table).decode('ascii')},
               'images': [{'name': name, 'content': base64.b64encode(data).decode('ascii')}
                          for name, data in images.items()], 'options': {}}
    status, _, data = request(url, '/api/inspect', {'table': payload['table']})
    preview = json.loads(data)
    if (status != 200 or preview.get('headers') != ['SKU', 'name']
            or preview.get('sku_candidates') != ['SKU'] or preview.get('row_count') != 2
            or preview.get('preview') != [['001', '测试商品一'], ['002', '测试商品二']]):
        raise AssertionError('Table inspection failed: %s' % preview)
    status, _, data = request(url, '/api/check', payload)
    report = json.loads(data)
    if status != 200 or report['summary']['errors'] != 0:
        raise AssertionError('Valid check failed: %s' % report)
    if {item['sku'] for item in report['images']} != {'001', '002'}:
        raise AssertionError('Both input images must match their SKUs')
    status, headers, data = request(url, '/api/pack', payload)
    if status != 200 or not headers['Content-Type'].startswith('application/zip'):
        raise AssertionError('Valid pack failed: %s %r' % (status, data[:500]))
    package_data = data
    with zipfile.ZipFile(io.BytesIO(package_data)) as packed:
        if packed.testzip() is not None:
            raise AssertionError('Generated ZIP has a CRC error')
        if packed.read('source/products.csv') != table:
            raise AssertionError('Original table bytes changed')
        for name, original in images.items():
            if packed.read('products/%s/%s' % (name[:3], name)) != original:
                raise AssertionError('Original image bytes changed: ' + name)
    status, _, data = request(url, '/api/verify', raw_body=package_data)
    verification = json.loads(data)
    if status != 200 or verification.get('status') != 'ok' or verification['summary']['errors'] != 0:
        raise AssertionError('Original generated package did not verify: %s' % verification)
    modified = io.BytesIO()
    changed_path = 'products/001/001.png'
    with zipfile.ZipFile(io.BytesIO(package_data)) as original, zipfile.ZipFile(modified, 'w') as rewritten:
        for name in original.namelist():
            rewritten.writestr(name, png(0, 0, 0) if name == changed_path else original.read(name))
    status, _, data = request(url, '/api/verify', raw_body=modified.getvalue())
    verification = json.loads(data)
    if (status != 200 or verification.get('status') != 'error' or verification['summary']['errors'] == 0
            or changed_path not in {item.get('subject') for item in verification.get('issues', [])}):
        raise AssertionError('Modified image must fail package verification: %s' % verification)
    payload['images'].pop()
    status, _, data = request(url, '/api/check', payload)
    if status != 200 or json.loads(data)['summary']['errors'] == 0:
        raise AssertionError('A missing product image must be reported')
    payload['allow_warnings'] = True
    status, _, _ = request(url, '/api/pack', payload)
    if status != 422:
        raise AssertionError('Blocking errors must prevent ZIP export')
    status, _, _ = request(url, '/api/check', payload, origin='https://example.com')
    if status != 400:
        raise AssertionError('External browser origins must be rejected')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--executable', type=Path, help='The built SKU-Kit executable')
    group.add_argument('--source', type=Path, help='desktop.py, for source-only smoke checking')
    group.add_argument('--archive', type=Path, help='Extract and test a built distribution ZIP')
    args = parser.parse_args()
    archive_temp = None
    if args.archive:
        archive_temp = tempfile.TemporaryDirectory(prefix='sku-kit-extracted-')
        root = Path(archive_temp.name).resolve()
        with zipfile.ZipFile(args.archive) as packed:
            for item in packed.infolist():
                target = (root / item.filename).resolve()
                if root not in target.parents or '\\' in item.filename:
                    raise ValueError('Unsafe archive member: ' + item.filename)
                packed.extract(item, root)
                if os.name != 'nt':
                    mode = (item.external_attr >> 16) & 0o777
                    if mode:
                        target.chmod(mode)
        args.executable = root / 'SKU-Kit' / ('SKU-Kit.exe' if os.name == 'nt' else 'SKU-Kit')
    if args.executable:
        command = [str(args.executable.resolve())]
    else:
        command = [sys.executable, str(args.source.resolve())]
    command += ['--no-open', '--port', '0']
    env = os.environ.copy()
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    env['PYTHONUNBUFFERED'] = '1'
    with tempfile.TemporaryDirectory(prefix='sku-kit-smoke-') as directory:
        log_path = Path(directory) / 'server.log'
        with log_path.open('wb') as log:
            process = subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 45
            url = None
            while time.monotonic() < deadline:
                content = log_path.read_text(encoding='utf-8', errors='replace')
                match = re.search(r'http://127\.0\.0\.1:\d+', content)
                if match:
                    url = match.group(0)
                    break
                if process.poll() is not None:
                    raise RuntimeError('Desktop process exited during startup:\n' + content)
                time.sleep(0.1)
            if not url:
                raise RuntimeError('Desktop startup timed out:\n' + log_path.read_text(errors='replace'))
            verify(url)
            print('PASS: v0.4 health, packaged UI, table inspection, exact SKU matching, original ZIP bytes, '
                  'package verification and tamper rejection, missing-image blocking, origin guard.')
        except Exception:
            print(log_path.read_text(encoding='utf-8', errors='replace'), file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if archive_temp:
                archive_temp.cleanup()


if __name__ == '__main__':
    main()
