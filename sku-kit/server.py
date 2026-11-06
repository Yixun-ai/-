#!/usr/bin/env python3
"""SKU Kit: loopback-only HTTP adapter for the shared engine."""
import argparse
import json
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 70 * 1024 * 1024
if __package__:
    from .table_io import parse_table, inspect_table
    from .engine import VERSION, analyze, package, checklist
    from .verify import verify_package
else:
    from table_io import parse_table, inspect_table
    from engine import VERSION, analyze, package, checklist
    from verify import verify_package
import base64


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'static'), **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        super().end_headers()

    def do_GET(self):
        if self.path == '/api/health':
            body = json.dumps({'status': 'ok', 'version': VERSION, 'storage': 'memory',
                               'model_api_required': False}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        try:
            expected_host = '127.0.0.1:%d' % self.server.server_port
            if self.headers.get('Host') != expected_host:
                raise ValueError('无效的本机请求。')
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + expected_host:
                raise ValueError('拒绝外部网页请求。')
            if self.path not in ('/api/table?kind=csv', '/api/table?kind=xlsx', '/api/inspect', '/api/verify', '/api/check', '/api/pack', '/api/checklist'):
                raise ValueError('接口不存在。')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_UPLOAD:
                raise ValueError('请求为空或超过 70 MB；网页端请分批处理。')
            raw = self.rfile.read(length)
            if self.path == '/api/verify':
                if self.headers.get('Content-Type', '').split(';')[0] not in ('application/zip', 'application/octet-stream'):
                    raise ValueError('需要上传 ZIP 文件。')
                data = verify_package(raw)
            elif self.path.startswith('/api/table?'):
                data = parse_table(raw, self.path.rsplit('=', 1)[1])
            else:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('需要 application/json。')
                request = json.loads(raw)
                table_input = request['table']
                if not isinstance(table_input['name'], str):
                    raise ValueError('表格名称无效。')
                table_bytes = base64.b64decode(table_input['content'], validate=True)
                if self.path == '/api/inspect':
                    data = inspect_table(table_bytes, Path(table_input['name']).suffix.lower().lstrip('.'), request.get('sheet'))
                    body = json.dumps(data, ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers(); self.wfile.write(body)
                    return
                assets = [{'name': x['name'], 'data': base64.b64decode(x['content'], validate=True)} for x in request['images']]
                if len(table_bytes) + sum(len(x['data']) for x in assets) > 50 * 1024 * 1024:
                    raise ValueError('网页端总输入上限为 50 MB；更大批次请用命令行。')
                options = request.get('options', {})
                report, parsed = analyze(table_input['name'], table_bytes, assets, **options)
                if self.path == '/api/checklist':
                    body = checklist(report)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/csv; charset=utf-8')
                    self.send_header('Content-Disposition', 'attachment; filename="checklist.csv"')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers(); self.wfile.write(body)
                    return
                if self.path == '/api/pack':
                    allow = request.get('allow_warnings', False)
                    if type(allow) is not bool:
                        raise ValueError('allow_warnings 必须为布尔值。')
                    if report['summary']['errors'] or (report['summary']['warnings'] and not allow):
                        body = json.dumps(report, ensure_ascii=False).encode()
                        self.send_response(422)
                        self.send_header('Content-Type', 'application/json; charset=utf-8')
                    else:
                        body = package(report, parsed, table_input['name'], table_bytes, assets, allow)
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/zip')
                        self.send_header('Content-Disposition', 'attachment; filename="sku-kit.zip"')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers(); self.wfile.write(body)
                    return
                data = report
            status = 200
        except Exception as error:
            data = {'status': 'invalid_input', 'error': str(error) if isinstance(error, ValueError) else '输入解析失败，请检查文件格式及请求字段。'}
            status = 400
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0, help='0 selects a free local port automatically')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        print('无法使用指定端口，请不带 --port 自动选择端口，或指定其他端口重试。')
        return 1
    url = 'http://127.0.0.1:%d' % server.server_port
    print('SKU Kit 已启动：%s\n仅本机访问；关闭终端或按 Ctrl+C 停止。' % url, flush=True)
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
