#!/usr/bin/env python3
"""Small stdio MCP adapter: explicitly supports 2024/2025 handshake versions."""
import argparse
import json
import sys
from pathlib import Path
if __package__:
    from .cli import run_paths
    from .engine import VERSION
else:
    from cli import run_paths
    from engine import VERSION

VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
PROPERTIES = {
    'table': {'type': 'string', 'description': 'CSV/XLSX path within the configured root.'},
    'images': {'type': 'string', 'description': 'Image directory within the configured root.'},
    'sku_column': {'type': 'string', 'description': 'Exact column name; omit only if uniquely identifiable.'},
    'sheet': {'type': 'string', 'description': 'Required for workbooks with multiple sheets.'},
    'match_mode': {'type': 'string', 'enum': ['exact', 'suffix'], 'default': 'exact'},
    'case_sensitive': {'type': 'boolean', 'default': True},
    'min_width': {'type': 'integer', 'minimum': 0, 'maximum': 20000, 'default': 0},
    'min_height': {'type': 'integer', 'minimum': 0, 'maximum': 20000, 'default': 0},
    'duplicate_policy': {'type':'string','enum':['error','repeat_rows'],'default':'error'},
    'assignments_file': {'type':'string','description':'Optional JSON of hash-bound manual assignments, within root.'},
    'assignments': {'type':'object','description':'Explicit manual decisions keyed by relative image path; obtain hashes from sku_check.',
                    'additionalProperties':{'type':'object','properties':{'sku':{'type':['string','null']},
                      'sha256':{'type':'string'},'table_sha256':{'type':'string'},'context_sha256':{'type':'string'}},
                      'required':['sku','sha256','table_sha256','context_sha256'],'additionalProperties':False}},
}


class ProtocolError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class MCP:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('root 必须是资料文件夹。')
        self.initialized = False
        self.ready = False

    def tools(self):
        tools = []
        for name in ('sku_check', 'sku_pack', 'sku_checklist'):
            props = dict(PROPERTIES); required = ['table', 'images']
            if name in ('sku_pack','sku_checklist'):
                props.update({'output': {'type': 'string', 'description': 'New ZIP path within root, outside images. Never overwrites.'},
                              'allow_warnings': {'type': 'boolean', 'default': False}})
                required.append('output')
                if name == 'sku_checklist':
                    props.pop('allow_warnings')
                    props['output']={'type':'string','description':'New CSV checklist path within root, outside images. Never overwrites.'}
            tools.append({'name': name, 'description': 'Check SKU asset integrity without writes.' if name == 'sku_check' else
                          ('Write a human-readable CSV checklist, including blocking issues.' if name=='sku_checklist' else
                           'Create a verified ZIP only when explicitly requested. Errors always block export.'),
                          'inputSchema': {'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False},
                          'annotations': {'readOnlyHint': name == 'sku_check', 'destructiveHint': False, 'openWorldHint': False}})
        return tools

    def path(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError('路径必须是非空字符串。')
        raw = Path(value).expanduser()
        raw = raw if raw.is_absolute() else self.root / raw
        resolved = raw.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError('路径超出配置的 root。')
        # Reject symlinks including parent components within the exposed root.
        candidate = self.root
        for component in raw.relative_to(self.root).parts:
            candidate = candidate / component
            if candidate.is_symlink():
                raise ValueError('不允许通过符号链接访问。')
        return str(resolved)

    def dispatch(self, method, params):
        if method == 'initialize':
            if self.initialized:
                raise ProtocolError(-32600, 'Already initialized')
            requested = params.get('protocolVersion')
            version = requested if requested in VERSIONS else VERSIONS[-1]
            self.initialized = True
            return {'protocolVersion': version, 'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'sku-kit', 'version': VERSION},
                    'instructions': 'Run sku_check first. Never claim image identity was verified. Errors block packaging.'}
        if method == 'notifications/initialized':
            if self.initialized:
                self.ready = True
            return None
        if method == 'ping':
            return {}
        if not self.ready:
            raise ProtocolError(-32002, 'Initialize and send notifications/initialized first')
        if method == 'tools/list':
            return {'tools': self.tools()}
        if method == 'tools/call':
            name = params.get('name')
            tool = next((x for x in self.tools() if x['name'] == name), None)
            if tool is None:
                raise ProtocolError(-32602, 'Unknown tool')
            try:
                arguments = params.get('arguments', {})
                if not isinstance(arguments, dict):
                    raise ValueError('arguments 必须是对象。')
                schema = tool['inputSchema']
                if set(arguments) - set(schema['properties']) or any(x not in arguments for x in schema['required']):
                    raise ValueError('参数缺失或存在未知字段。')
                for key, value in arguments.items():
                    typ = schema['properties'][key]['type']
                    if (typ == 'string' and not isinstance(value, str)) or (typ == 'boolean' and type(value) is not bool) or (typ == 'integer' and type(value) is not int):
                        raise ValueError('参数类型错误：' + key)
                    if typ == 'object' and not isinstance(value, dict):
                        raise ValueError('参数类型错误：' + key)
                options = dict(arguments)
                for key in ('table', 'images', 'output', 'assignments_file'):
                    if key in options:
                        options[key] = self.path(options[key])
                report = run_paths({'sku_check':'check','sku_pack':'pack','sku_checklist':'checklist'}[name], **options)
                failed = name == 'sku_pack' and not report.get('export', {}).get('written', False)
                return {'content': [{'type': 'text', 'text': json.dumps(report, ensure_ascii=False)}], 'isError': failed}
            except Exception as error:
                return {'content': [{'type': 'text', 'text': json.dumps({'status': 'invalid_input', 'error': str(error)}, ensure_ascii=False)}], 'isError': True}
        raise ProtocolError(-32601, 'Method not found')

    def handle(self, message):
        request_id = message.get('id') if isinstance(message, dict) else None
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32600, 'message': 'Invalid request'}}
        try:
            params = message.get('params', {})
            if not isinstance(params, dict):
                raise ProtocolError(-32602, 'params must be an object')
            result = self.dispatch(message['method'], params)
            if 'id' not in message:
                return None
            return {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except ProtocolError as error:
            if 'id' not in message:
                return None
            return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': error.code, 'message': error.message}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, help='Only expose this project-data directory to tools.')
    args = parser.parse_args(); server = MCP(args.root)
    for line in sys.stdin:
        try:
            message = json.loads(line)
            reply = server.handle(message)
        except json.JSONDecodeError:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        except Exception:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32603, 'message': 'Internal error'}}
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
