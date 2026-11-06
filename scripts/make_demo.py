#!/usr/bin/env python3
"""Generate opt-in synthetic onboarding data without third-party dependencies."""
import argparse
import hashlib
from pathlib import Path
import shutil
import struct
import zipfile
import zlib


def png(color):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    width = height = 320
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            pixels.extend(color if 95 <= x < 225 and 65 <= y < 255 else (246, 245, 240))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(pixels))) + chunk(b'IEND', b''))


GUIDE = '''SKU Kit — 合成示例 / Synthetic sample data

所有商品、图片均为演示占位资料，不是客户数据。仅供练习，不代表视觉识别能力。
These are synthetic placeholders, not customer files. SKU Kit does not infer product identity from image content.

1. 先选 clean/products.csv 和 clean/images，保持默认规则，检查并下载资料包。
   Select clean/products.csv and clean/images; default matching should pass.
2. 练习人工确认：新建一批，选择 needs-review/products.csv 与 needs-review/images。
   Select needs-review/products.csv and needs-review/images for manual review practice.
3. 第二张图的文件名无法匹配。按本示例约定，浅色示意图属于产品 002；在“看图核对”中调整归属到 002。
   The beige placeholder is explicitly defined by this example as SKU 002. Assign it in the image-review dialog.
4. 再次检查通过后打包。原文件不改动，可保存进度后继续。
   Recheck and export. Originals stay unchanged; save a project file to continue later.

不必使用这些示例。打开应用不会自动载入它们。
The app never loads these samples automatically.
'''


def generate(output, archive=None):
    output = Path(output).expanduser().resolve()
    archive = Path(archive).expanduser().resolve() if archive else None
    if output.exists():
        raise ValueError('示例目录已存在，拒绝覆盖：' + str(output))
    if not output.parent.is_dir():
        raise ValueError('请先创建示例目录的父目录。')
    if archive:
        if archive.exists() or archive.with_suffix(archive.suffix + '.sha256').exists():
            raise ValueError('归档或校验和已存在，拒绝覆盖。')
        if not archive.parent.is_dir() or output == archive or output in archive.parents:
            raise ValueError('归档必须在示例目录外的现有文件夹中。')
    output.mkdir()
    try:
        table = 'SKU,产品名称\n001,绿色示意商品\n002,浅色示意商品\n'.encode('utf-8-sig')
        green, beige = png((141,159,137)), png((197,174,138))
        for case in ('clean', 'needs-review'):
            images = output / case / 'images'
            images.mkdir(parents=True)
            (images.parent / 'products.csv').write_bytes(table)
            (images / '001.png').write_bytes(green)
            (images / ('002.png' if case == 'clean' else '待确认.png')).write_bytes(beige)
        (output / 'README.txt').write_text(GUIDE, encoding='utf-8')
        if archive:
            with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
                for path in sorted(output.rglob('*')):
                    if path.is_file():
                        item = zipfile.ZipInfo('SKU-Kit-demo/' + path.relative_to(output).as_posix(), (2026,1,1,0,0,0))
                        item.compress_type = zipfile.ZIP_DEFLATED
                        z.writestr(item, path.read_bytes())
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with archive.with_suffix(archive.suffix + '.sha256').open('x', encoding='ascii') as stream:
                stream.write(digest + '  ' + archive.name + '\n')
    except BaseException:
        # Only this newly created synthetic output is removed; existing folders
        # were rejected before writes. Never clean a user-supplied input folder.
        shutil.rmtree(output)
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    try:
        print(generate(args.output, args.archive))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
