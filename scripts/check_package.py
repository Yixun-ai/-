#!/usr/bin/env python3
"""Install a built wheel offline into a fresh venv and exercise real entry points.

Prepare runtime dependency wheels separately, then pass --wheelhouse. This script
never contacts a package index or reads credentials. All test data is temporary.
"""
import argparse
import email
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def clean_environment():
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['PYTHONUTF8'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    env['PIP_NO_INDEX'] = '1'
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    return env


def run(command, cwd, env, input_text=None):
    result = subprocess.run([str(value) for value in command], cwd=str(cwd), env=env,
                            input=input_text, capture_output=True, text=True, encoding='utf-8', timeout=60)
    if result.returncode:
        raise RuntimeError('Command failed (%d): %s\n%s\n%s' %
                           (result.returncode, command[0], result.stdout, result.stderr))
    return result.stdout


def helpers():
    path = Path(__file__).with_name('smoke_desktop.py')
    spec = importlib.util.spec_from_file_location('sku_kit_smoke_helper', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_http(command, cwd, env, expected_version, smoke):
    log_path = cwd / 'http.log'
    with log_path.open('wb') as log:
        process = subprocess.Popen([str(value) for value in command] + ['--no-open', '--port', '0'],
                                   cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            output = log_path.read_text(encoding='utf-8', errors='replace')
            match = re.search(r'http://127\.0\.0\.1:\d+', output)
            if match:
                smoke.verify(match.group(0), expected_version=expected_version)
                return
            if process.poll() is not None:
                raise RuntimeError('Installed HTTP entry point exited:\n' + output)
            time.sleep(0.1)
        raise RuntimeError('Installed HTTP entry point timed out:\n' + log_path.read_text(errors='replace'))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def probe(expected_version):
    """Run under the fresh venv's interpreter with -I, outside the source tree."""
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('The installed probe must run inside its fresh virtual environment')
    package = importlib.import_module('sku_kit')
    importlib.import_module('sku_kit.engine')
    importlib.import_module('sku_kit.server')
    importlib.import_module('sku_kit.cli')
    importlib.import_module('sku_kit.mcp_server')
    origin = Path(package.__file__).resolve()
    if Path(sys.prefix).resolve() not in origin.parents:
        raise AssertionError('sku_kit was imported from outside the clean venv: ' + str(origin))
    for module in ('engine', 'server', 'cli', 'verify', 'table_io'):
        if module in sys.modules or importlib.util.find_spec(module) is not None:
            raise AssertionError('Installation leaked a top-level module: ' + module)
    if importlib.metadata.version('sku-kit') != expected_version:
        raise AssertionError('Installed metadata version differs from source/wheel version')
    smoke = helpers()
    env = clean_environment()
    scripts = Path(sys.executable).parent
    suffix = '.exe' if os.name == 'nt' else ''
    commands = {name: scripts / (name + suffix) for name in ('sku-kit', 'sku-kit-cli', 'sku-kit-mcp')}
    for command in commands.values():
        if not command.is_file():
            raise AssertionError('Missing installed console entry point: ' + str(command))
    with tempfile.TemporaryDirectory(prefix='sku-kit-installed-cwd-') as temp:
        cwd = Path(temp).resolve()
        if ROOT == cwd or ROOT in cwd.parents:
            raise AssertionError('Installed test must run outside the repository')
        table = cwd / '产品.csv'
        table_bytes = 'SKU,name\n001,测试杯子\n002,测试包\n'.encode('utf-8-sig')
        table.write_bytes(table_bytes)
        images = cwd / 'images'
        images.mkdir()
        originals = {'001.png': smoke.png(25, 130, 70), '002.png': smoke.png(140, 40, 20)}
        for name, data in originals.items():
            (images / name).write_bytes(data)
        version = run([commands['sku-kit-cli'], '--version'], cwd, env).strip()
        if version != expected_version:
            raise AssertionError('CLI version does not match installed distribution')
        common = ['--table', str(table), '--images', str(images)]
        report = json.loads(run([commands['sku-kit-cli'], 'check'] + common, cwd, env))
        if report['status'] != 'ok' or report['summary']['errors'] != 0:
            raise AssertionError('Installed CLI check failed: %s' % report)
        output = cwd / '交付.zip'
        report = json.loads(run([commands['sku-kit-cli'], 'pack'] + common + ['--output', str(output)], cwd, env))
        if not report.get('export', {}).get('written'):
            raise AssertionError('Installed CLI did not write its delivery package')
        with zipfile.ZipFile(output) as archive:
            if archive.read('source/产品.csv') != table_bytes:
                raise AssertionError('Installed CLI changed original table bytes')
            for name, original in originals.items():
                if archive.read('products/%s/%s' % (name[:3], name)) != original:
                    raise AssertionError('Installed CLI changed original image bytes')
        verification = json.loads(run([commands['sku-kit-cli'], 'verify', '--package', str(output)], cwd, env))
        if verification['status'] != 'ok':
            raise AssertionError('Installed CLI package verification failed')
        checklist_path = cwd / '补件清单.csv'
        checklist = json.loads(run([commands['sku-kit-cli'], 'checklist'] + common +
                                   ['--output', str(checklist_path)], cwd, env))
        if not checklist.get('export', {}).get('written') or not checklist_path.read_bytes():
            raise AssertionError('Installed CLI could not export its checklist')
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25',
             'capabilities': {}, 'clientInfo': {'name': 'installed-wheel-smoke', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'sku_check',
             'arguments': {'table': table.name, 'images': images.name}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'sku_pack',
             'arguments': {'table': table.name, 'images': images.name, 'output': 'mcp-delivery.zip'}}},
        ]
        replies = [json.loads(line) for line in run([commands['sku-kit-mcp'], '--root', str(cwd)], cwd, env,
                   '\n'.join(json.dumps(message, ensure_ascii=False) for message in messages) + '\n').splitlines()]
        if [reply.get('id') for reply in replies] != [1, 2, 3, 4] or any('error' in reply for reply in replies):
            raise AssertionError('Installed MCP handshake or tool invocation failed: %s' % replies)
        if replies[0]['result']['serverInfo']['version'] != expected_version:
            raise AssertionError('Installed MCP version differs')
        if {tool['name'] for tool in replies[1]['result']['tools']} != {'sku_check', 'sku_pack', 'sku_checklist'}:
            raise AssertionError('Installed MCP tool list differs')
        tool_result = replies[2]['result']
        if tool_result.get('isError') or json.loads(tool_result['content'][0]['text'])['status'] != 'ok':
            raise AssertionError('Installed MCP could not check real local files')
        tool_result = replies[3]['result']
        pack_report = json.loads(tool_result['content'][0]['text'])
        if tool_result.get('isError') or not pack_report.get('export', {}).get('written'):
            raise AssertionError('Installed MCP could not export the checked files')
        with zipfile.ZipFile(cwd / 'mcp-delivery.zip') as archive:
            if archive.read('source/产品.csv') != table_bytes:
                raise AssertionError('Installed MCP changed original table bytes')
            for name, original in originals.items():
                if archive.read('products/%s/%s' % (name[:3], name)) != original:
                    raise AssertionError('Installed MCP changed original image bytes')
        check_http([commands['sku-kit']], cwd, env, expected_version, smoke)
        check_http([sys.executable, '-I', '-m', 'sku_kit'], cwd, env, expected_version, smoke)
    print('PASS: offline wheel install; isolated package namespace; external-cwd CLI/MCP/module/HTTP/static/check/pack/verify.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--wheel', type=Path)
    group.add_argument('--wheel-dir', type=Path)
    parser.add_argument('--wheelhouse', type=Path, help='Local runtime dependency wheels; no index is contacted')
    parser.add_argument('--expected-version')
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        if not args.expected_version:
            parser.error('--probe requires --expected-version')
        probe(args.expected_version)
        return 0
    if not args.wheelhouse or not (args.wheel or args.wheel_dir):
        parser.error('Pass --wheel or --wheel-dir and a prepared --wheelhouse; installation is offline')
    candidates = [args.wheel] if args.wheel else sorted(args.wheel_dir.glob('*.whl'))
    if len(candidates) != 1 or not candidates[0].is_file():
        parser.error('Expected exactly one built project wheel')
    wheel = candidates[0].resolve()
    smoke = helpers()
    expected_version = args.expected_version or smoke.source_version()
    declared = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    with zipfile.ZipFile(wheel) as archive:
        members = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(members) != 1:
            raise ValueError('Wheel must contain exactly one distribution METADATA file')
        metadata = email.message_from_bytes(archive.read(members[0]))
        if metadata['Version'] != expected_version or metadata['Name'] != declared['name']:
            raise AssertionError('Wheel name/version differs from source metadata')
        if metadata['Requires-Python'] != declared['requires-python']:
            raise AssertionError('Wheel Python baseline differs from pyproject.toml')
    with tempfile.TemporaryDirectory(prefix='sku-kit-wheel-check-') as temp:
        root = Path(temp).resolve()
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        env = clean_environment()
        result = subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--only-binary=:all:',
                                 '--find-links', str(args.wheelhouse.resolve()), str(wheel)],
                                cwd=str(root), env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
        if result.returncode:
            raise RuntimeError('Offline installation failed. Prepare matching dependency wheels first:\n' + result.stdout + result.stderr)
        print(run([python, '-I', Path(__file__).resolve(), '--probe', '--expected-version', expected_version], root, env).strip())
    return 0


if __name__ == '__main__':
    sys.exit(main())
