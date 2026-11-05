#!/usr/bin/env python3
"""Model-independent CLI. stdout is exactly one JSON object."""
import argparse
import json
import os
import sys
from pathlib import Path

from engine import VERSION, MAX_TOTAL, analyze, package, checklist
from table_io import MAX_TABLE
from verify import verify_package


def load_inputs(table_path, images_path):
    table = Path(table_path).expanduser()
    directory = Path(images_path).expanduser()
    if table.is_symlink() or directory.is_symlink():
        raise ValueError('输入不得为符号链接。')
    if not table.is_file() or not directory.is_dir():
        raise ValueError('产品表文件或图片文件夹不存在。')
    if table.stat().st_size > MAX_TABLE:
        raise ValueError('产品表超过 20 MB。')
    table_bytes = table.read_bytes()
    assets, total = [], 0
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('图片文件夹中存在符号链接，请移除或复制实际文件：' + str(path))
        if not path.is_file():
            continue
        if len(assets) >= 5000:
            raise ValueError('图片文件夹超过 5,000 个文件。')
        size = path.stat().st_size; total += size
        if size > 30 * 1024 * 1024 or total > MAX_TOTAL:
            raise ValueError('文件超过 30 MB 或资料总量超过 240 MB，请分批处理。')
        assets.append({'name': path.relative_to(directory).as_posix(), 'data': path.read_bytes()})
    return table.name, table_bytes, assets


def write_new(output, payload):
    path = Path(output).expanduser()
    if not path.parent.is_dir():
        raise ValueError('输出父目录不存在，请先创建目录。')
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError('输出文件已存在，拒绝覆盖：' + str(path))
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return str(path.resolve())


def run_paths(command, table, images, output=None, allow_warnings=False, assignments_file=None, **options):
    if assignments_file:
        if 'assignments' in options:
            raise ValueError('assignments 和 assignments_file 不能同时指定。')
        path = Path(assignments_file).expanduser()
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('人工确认文件超过 2 MB。')
        options['assignments'] = json.loads(path.read_text(encoding='utf-8-sig'))
    name, data, assets = load_inputs(table, images)
    report, parsed = analyze(name, data, assets, **options)
    if command in ('pack', 'checklist'):
        if not output:
            raise ValueError('导出必须明确指定 output。')
        output_path = Path(output).expanduser().resolve()
        image_root = Path(images).expanduser().resolve()
        if output_path == image_root or image_root in output_path.parents:
            raise ValueError('输出 ZIP 必须位于图片输入文件夹之外。')
        if command == 'checklist':
            payload = checklist(report)
            report['export'] = {'written': True, 'path': write_new(output, payload), 'bytes': len(payload)}
            return report
        if report['summary']['errors'] or (report['summary']['warnings'] and not allow_warnings):
            report['export'] = {'written': False, 'reason': '先修复错误；仅警告可在检查后明确允许。'}
            return report
        payload = package(report, parsed, name, data, assets, allow_warnings)
        report['export'] = {'written': True, 'path': write_new(output, payload), 'bytes': len(payload)}
    return report


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({'status': 'invalid_input', 'error': message}, ensure_ascii=False))
        raise SystemExit(1)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    parser = JsonParser(description='SKU Kit: check / pack / checklist / verify; JSON output, no model API required.')
    parser.add_argument('--version', action='version', version=VERSION)
    sub = parser.add_subparsers(dest='command', required=True, parser_class=JsonParser)
    for command in ('check', 'pack', 'checklist'):
        child = sub.add_parser(command)
        child.add_argument('--table', required=True)
        child.add_argument('--images', required=True)
        child.add_argument('--sku-column')
        child.add_argument('--sheet')
        child.add_argument('--match-mode', choices=['exact', 'suffix'], default='exact')
        child.add_argument('--ignore-case', action='store_true')
        child.add_argument('--min-width', type=int, default=0)
        child.add_argument('--min-height', type=int, default=0)
        child.add_argument('--duplicate-policy', choices=['error','repeat_rows'], default='error')
        child.add_argument('--assignments-file')
        if command in ('pack','checklist'):
            child.add_argument('--output', required=True)
        if command == 'pack':
            child.add_argument('--allow-warnings', action='store_true')
    verification = sub.add_parser('verify', help='核验已有交付 ZIP；不解压，不修改文件。')
    verification.add_argument('--package', required=True)
    options = vars(parser.parse_args())
    if options['command'] == 'verify':
        report = verify_package(options['package'])
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return {'ok': 0, 'error': 2, 'invalid_input': 1}[report['status']]
    options['case_sensitive'] = not options.pop('ignore_case')
    try:
        report = run_paths(**options)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report.get('export', {}).get('written'):
            return 0
        return 0 if report['status'] == 'ok' else 2
    except Exception as error:
        print(json.dumps({'status': 'invalid_input', 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
