#!/usr/bin/env python3
"""Build an onedir desktop distribution on its target operating system."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile

if __package__:
    from ._version import VERSION
else:
    from _version import VERSION

PYINSTALLER_VERSION = '6.22.3'
SOURCE = Path(__file__).resolve().parent
REPOSITORY = SOURCE.parent


def build_command(output):
    """Use absolute paths so building does not depend on the caller's directory."""
    return [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--onedir', '--console', '--noupx', '--name', 'SKU-Kit',
            '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
            '--specpath', str(output / 'spec'),
            '--paths', str(SOURCE), '--add-data', str(SOURCE / 'static') + ':static',
            str(SOURCE / 'desktop.py')]


def write_launchers(bundle):
    if os.name == 'nt':
        (bundle / 'Start SKU Kit.bat').write_bytes(
            b'@echo off\r\n"%~dp0SKU-Kit.exe" %*\r\nif errorlevel 1 pause\r\n')
    else:
        launcher = bundle / 'Start SKU Kit.command'
        launcher.write_text('#!/bin/sh\nAPP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                            '"$APP_DIR/SKU-Kit" "$@"\n'
                            'status=$?\nif [ "$status" -ne 0 ]; then\n'
                            '  printf "SKU Kit failed. Press Enter to close.\\n"\n'
                            '  read -r answer\nfi\nexit "$status"\n', encoding='utf-8')
        launcher.chmod(0o755)
    (bundle / 'READ-ME.txt').write_text(
        'SKU Kit — 商品资料检查工具\n\n'
        '先完整解压整个 SKU-Kit 文件夹，再双击启动脚本。无需安装 Python。\n'
        'Windows：双击 Start SKU Kit.bat；Mac：双击 Start SKU Kit.command。\n'
        '网页会在默认浏览器打开；使用期间请保留终端窗口，按 Ctrl+C 停止。\n'
        '没有自动打开网页时，复制终端显示的本机网址到浏览器。\n'
        '不要移走 _internal 文件夹。此版本未商业签名或公证。\n\n'
        'SKU Kit — local product-file checker\n\n'
        'Extract the WHOLE folder before starting. Keep the _internal folder.\n'
        'Windows: double-click Start SKU Kit.bat.\n'
        'macOS: double-click Start SKU Kit.command.\n'
        'Your browser opens a local 127.0.0.1 address. No Python installation is needed.\n'
        'Keep the console open while working; Ctrl+C stops the server.\n'
        'If the browser does not open, copy the address printed in the console.\n'
        'All product inputs stay in process memory; saved projects contain your input files.\n'
        'The build is unsigned. Automated smoke tests do not replace testing on your own OS.\n',
        encoding='utf-8')


def archive_bundle(bundle, output):
    label = '%s-%s' % (platform.system().lower(), platform.machine().lower())
    archive = output / ('SKU-Kit-%s.zip' % label)
    # macOS framework resources contain directory symlinks. Dereference them before
    # zipping, so a regular ZIP extractor need not know how to recreate symlinks.
    with tempfile.TemporaryDirectory(prefix='archive-', dir=str(output)) as temp:
        staged = Path(temp) / bundle.name
        shutil.copytree(bundle, staged, symlinks=False)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as packed:
            for path in sorted(staged.rglob('*')):
                if path.is_file():
                    packed.write(path, str(path.relative_to(staged.parent)))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix('.zip.sha256')
    checksum.write_text('%s  %s\n' % (digest, archive.name), encoding='ascii')
    return archive, checksum


def copy_dependency_notices(bundle):
    notices = bundle / 'third-party-licenses'
    notices.mkdir(exist_ok=True)
    pillow = importlib.metadata.distribution('Pillow')
    for item in pillow.files or ():
        if '/licenses/' in str(item).replace('\\', '/'):
            path = Path(pillow.locate_file(item))
            if path.is_file():
                shutil.copy2(path, notices / ('Pillow-' + path.name))
    for path in (Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt',
                 Path(sys.base_prefix) / 'LICENSE.txt'):
        if path.is_file():
            shutil.copy2(path, notices / 'Python-LICENSE.txt')
            break


def project_license():
    """Locate our notice in either a source checkout or an installed wheel."""
    for candidate in (REPOSITORY / 'LICENSE', SOURCE / 'LICENSE'):
        if candidate.is_file():
            return candidate
    if __package__:
        try:
            distribution = importlib.metadata.distribution('sku-kit')
        except importlib.metadata.PackageNotFoundError:
            return None
        for item in distribution.files or ():
            if str(item).replace('\\', '/').endswith('.dist-info/licenses/LICENSE'):
                candidate = Path(distribution.locate_file(item))
                if candidate.is_file():
                    return candidate
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    build_root = REPOSITORY if (REPOSITORY / 'pyproject.toml').is_file() else Path.cwd()
    parser.add_argument('--output', type=Path, default=build_root / 'build-desktop')
    args = parser.parse_args()
    output = args.output.resolve()
    try:
        installed = importlib.metadata.version('pyinstaller')
    except importlib.metadata.PackageNotFoundError:
        parser.error('Install pyinstaller==%s in a build virtual environment first.' % PYINSTALLER_VERSION)
    if installed != PYINSTALLER_VERSION:
        parser.error('Expected pyinstaller==%s; found %s.' % (PYINSTALLER_VERSION, installed))
    if not (SOURCE / 'static' / 'index.html').is_file():
        parser.error('Missing static/index.html')
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['PYINSTALLER_CONFIG_DIR'] = str(output / 'cache')
    subprocess.run(build_command(output), cwd=str(SOURCE), env=env, check=True)
    bundle = output / 'dist' / 'SKU-Kit'
    write_launchers(bundle)
    copy_dependency_notices(bundle)
    # Include original license notices with the redistributable runtime.
    license_file = project_license()
    if license_file is not None:
        shutil.copy2(license_file, bundle / 'SKU-Kit-LICENSE.txt')
    metadata = {'sku_kit': VERSION, 'python': platform.python_version(), 'system': platform.system(),
                'architecture': platform.machine(), 'pyinstaller': installed,
                'pillow': importlib.metadata.version('Pillow')}
    (bundle / 'build-info.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    archive, checksum = archive_bundle(bundle, output)
    print('Bundle: %s\nArchive: %s\nSHA-256: %s' % (bundle, archive, checksum), flush=True)


if __name__ == '__main__':
    main()
