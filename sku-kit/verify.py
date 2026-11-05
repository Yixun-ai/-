"""Bounded, read-only integrity verification for SKU Kit delivery ZIPs.

The manifest is not a signature. A successful result means the files agree with
this package's own records, not that a particular person created the package.
"""
import hashlib
import io
import json
import re
import stat
import struct
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from engine import EXTENSIONS, MAX_TOTAL, unique_name
from table_io import MAX_TABLE

MAX_PACKAGE = 320 * 1024 * 1024
MAX_UNPACKED = 320 * 1024 * 1024
MAX_METADATA = 32 * 1024 * 1024
MAX_FILES = 5006  # 5,000 exported images, the original table, and five reports.
CHUNK_SIZE = 1024 * 1024
FIXED_FILES = {'README.txt', 'reports/report.json', 'reports/manifest.json',
               'reports/issues.csv', 'reports/补件清单.csv'}
HASH = re.compile(r'^[0-9a-f]{64}$')


class _Failure(ValueError):
    def __init__(self, code, subject, message):
        self.issue = {'code': code, 'subject': subject, 'message': message}


def _require(condition, code, subject, message):
    if not condition:
        raise _Failure(code, subject, message)


def _integer(value, maximum, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def _digest(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def _path(value, archive=False):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 4096:
        return False
    if '\\' in value or value.startswith('/') or any(ord(x) < 32 or ord(x) == 127 for x in value):
        return False
    if any(x in ('', '.', '..') for x in value.split('/')):
        return False
    if archive and any(re.search(r'[<>:"|?*]', part) or part != part.rstrip(' .') or
                       re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', part, re.I)
                       for part in value.split('/')):
        return False
    return True


def _directory_check(stream, size):
    """Check the EOCD before ZipFile can allocate an unbounded central directory."""
    _require(22 <= size <= MAX_PACKAGE, 'ZIP_SIZE_LIMIT', '', '交付包为空或超过 320 MB。')
    stream.seek(max(0, size - 65557))
    tail = stream.read(min(size, 65557))
    offset = tail.rfind(b'PK\x05\x06')
    _require(offset >= 0 and len(tail) >= offset + 22, 'INVALID_ZIP', '', '不是完整的标准 ZIP 文件。')
    fields = struct.unpack_from('<4s4H2LH', tail, offset)
    _, disk, directory_disk, disk_count, count, directory_size, directory_offset, comment_size = fields
    _require(offset + 22 + comment_size == len(tail), 'INVALID_ZIP', '', 'ZIP 结尾或注释长度无效。')
    _require(disk == directory_disk == 0 and disk_count == count, 'INVALID_ZIP', '', '不支持分卷 ZIP。')
    _require(count <= MAX_FILES and directory_size <= 8 * 1024 * 1024,
             'ZIP_ENTRY_LIMIT', '', 'ZIP 条目数量或目录大小超过限制。')
    _require(directory_offset + directory_size == size - len(tail) + offset,
             'INVALID_ZIP', '', 'ZIP 目录位置无效，不支持 ZIP64 或附加数据。')
    stream.seek(0)
    _require(stream.read(4) == b'PK\x03\x04', 'INVALID_ZIP', '', 'ZIP 文件头无效。')
    return count


def _json(archive, info):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'INVALID_JSON', info.filename, 'JSON 存在重复键。')
            result[key] = value
        return result
    try:
        raw = archive.read(info)
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    except _Failure:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise _Failure('INVALID_JSON', info.filename, 'JSON 格式无效。')


def _hash_entry(archive, info):
    digest, count = hashlib.sha256(), 0
    with archive.open(info) as member:
        while True:
            chunk = member.read(CHUNK_SIZE)
            if not chunk:
                break
            count += len(chunk)
            _require(count <= info.file_size, 'ZIP_SIZE_MISMATCH', info.filename, '文件实际大小与 ZIP 记录不一致。')
            digest.update(chunk)
    _require(count == info.file_size, 'ZIP_SIZE_MISMATCH', info.filename, '文件长度与 ZIP 记录不一致。')
    return digest.hexdigest()


def _legacy_safe_name(value):
    # v0.3 truncated the entire filename and could lose a long name's extension.
    # Keep verifying its original target mapping after the v0.4 naming fix.
    value = unicodedata.normalize('NFC', value)
    value = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', value).strip(' .')
    value = value.encode('utf-8')[:160].decode('utf-8', 'ignore') or '_unnamed'
    if re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', value, re.I):
        value = '_' + value
    return value


def _legacy_unique_name(value, used):
    original = _legacy_safe_name(value)
    path = PurePosixPath(original)
    candidate, count = original, 2
    while unicodedata.normalize('NFC', candidate).casefold() in used:
        candidate = '%s_%d%s' % (path.stem, count, path.suffix); count += 1
    used.add(unicodedata.normalize('NFC', candidate).casefold())
    return candidate


def _records(report, manifest):
    _require(isinstance(report, dict) and report.get('schema_version') == '1.1',
             'INVALID_REPORT', 'reports/report.json', '仅支持 SKU Kit schema 1.1 的报告。')
    _require(isinstance(report.get('engine_version'), str) and 0 < len(report['engine_version']) <= 64,
             'INVALID_REPORT', 'engine_version', '引擎版本格式无效。')
    _require(isinstance(manifest, dict) and set(manifest) == {'files', 'sku_folders', 'manual_assignments'},
             'INVALID_MANIFEST', 'reports/manifest.json', '清单结构无效。')
    products, images, issues = (report.get(key) for key in ('products', 'images', 'issues'))
    _require(isinstance(products, list) and 1 <= len(products) <= 20000 and
             isinstance(images, list) and len(images) <= 5000 and isinstance(issues, list) and len(issues) <= 60000,
             'INVALID_REPORT', 'reports/report.json', '商品、图片或问题记录无效。')
    name_for = _legacy_unique_name if report['engine_version'].startswith('0.3.') else unique_name
    folders, folder_names = {}, set()
    for product in products:
        _require(isinstance(product, dict) and isinstance(product.get('sku'), str) and product['sku'] and
                 isinstance(product.get('name'), str) and _integer(product.get('row'), 2147483647, 1),
                 'INVALID_REPORT', 'products', '商品记录字段类型无效。')
        if product['sku'] not in folders:
            folders[product['sku']] = name_for(product['sku'], folder_names)
    _require(manifest['sku_folders'] == folders, 'INVALID_MANIFEST', 'sku_folders', '商品文件夹与报告不一致。')
    assignments = report.get('assignments')
    _require(isinstance(assignments, dict) and manifest['manual_assignments'] == assignments,
             'INVALID_MANIFEST', 'manual_assignments', '人工确认记录与报告不一致。')
    _require(_digest(report.get('table_sha256')) and _digest(report.get('context_sha256')),
             'INVALID_REPORT', 'table_sha256', '表格或检查上下文 SHA-256 格式无效。')
    rules = report.get('rules')
    _require(isinstance(rules, dict) and isinstance(rules.get('sku_column'), str) and
             (rules.get('sheet') is None or isinstance(rules['sheet'], str)) and
             rules.get('match_mode') in ('exact', 'suffix') and type(rules.get('case_sensitive')) is bool and
             _integer(rules.get('min_width'), 20000) and _integer(rules.get('min_height'), 20000) and
             rules.get('duplicate_policy') in ('error', 'repeat_rows'),
             'INVALID_REPORT', 'rules', '检查规则字段类型无效。')
    context = {'table_sha256': report['table_sha256'], 'sheet': rules.get('sheet'), 'sku_column': rules['sku_column']}
    context_digest = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    _require(report['context_sha256'] == context_digest,
             'INVALID_REPORT', 'context_sha256', '检查上下文与表格摘要或选择的列不一致。')
    seen, expected, names, total = {}, [], {}, 0
    for image in images:
        _require(isinstance(image, dict) and _path(image.get('source')) and image['source'] not in seen,
                 'INVALID_REPORT', 'images', '图片源路径无效或重复。')
        source = image['source']; seen[source] = image
        _require(type(image.get('valid')) is bool and _integer(image.get('size'), 30 * 1024 * 1024)
                 and _digest(image.get('sha256')) and
                 (image.get('sku') is None or isinstance(image['sku'], str) and image['sku'] in folders),
                 'INVALID_REPORT', source, '图片类型、大小、编号或 SHA-256 格式无效。')
        total += image['size']
        _require(total <= MAX_TOTAL, 'ZIP_SIZE_LIMIT', 'images', '报告图片总量超过 240 MB。')
        decision = assignments.get(source)
        if decision is not None:
            _require(isinstance(decision, dict) and set(decision) == {'sku', 'sha256', 'table_sha256', 'context_sha256'} and
                     decision['sku'] == image['sku'] and decision['sha256'] == image['sha256'] and
                     decision['table_sha256'] == report['table_sha256'] and decision['context_sha256'] == report['context_sha256'] and
                     image.get('assignment_source') == 'manual',
                     'INVALID_REPORT', source, '人工确认记录与图片或表格不一致。')
        else:
            _require(source not in assignments and image.get('assignment_source') == 'filename',
                     'INVALID_REPORT', source, '人工确认记录无效或缺失。')
        if not image['valid']:
            _require(PurePosixPath(source).suffix.lower() not in EXTENSIONS and image['sku'] is None,
                     'INVALID_REPORT', source, '交付报告不能包含未通过检查的受支持图片。')
            continue
        _require(PurePosixPath(source).suffix.lower() in EXTENSIONS and
                 _integer(image.get('width'), 25000000, 1) and _integer(image.get('height'), 25000000, 1) and
                 image['width'] * image['height'] <= 25000000,
                 'INVALID_REPORT', source, '有效图片的扩展名或尺寸记录无效。')
        directory = 'products/' + folders[image['sku']] if image['sku'] else 'unmatched'
        target = directory + '/' + name_for(PurePosixPath(source).name, names.setdefault(directory, set()))
        expected.append({'source': source, 'target': target, 'sku': image['sku'], 'sha256': image['sha256']})
    _require(set(folders) == {image['sku'] for image in images if image['valid'] and image['sku']},
             'INVALID_REPORT', 'products', '存在未交付有效图片的商品。')
    _require(set(assignments) <= set(seen), 'INVALID_REPORT', 'assignments', '人工确认引用不存在的源文件。')
    _require(isinstance(manifest['files'], list) and len(manifest['files']) == len(expected),
             'INVALID_MANIFEST', 'files', '清单图片数量与报告不一致。')
    for actual, wanted in zip(manifest['files'], expected):
        _require(isinstance(actual, dict) and set(actual) == set(wanted) and actual == wanted and
                 (actual['sku'] is None or isinstance(actual['sku'], str)),
                 'INVALID_MANIFEST', 'files', '清单图片的源路径、目标路径、编号或 SHA-256 与报告不一致。')
    for issue in issues:
        _require(isinstance(issue, dict) and issue.get('severity') == 'warning' and
                 all(isinstance(issue.get(key), str) for key in ('code', 'subject', 'message')),
                 'INVALID_REPORT', 'issues', '交付报告只应包含警告，问题记录字段必须有效。')
    summary = report.get('summary')
    wanted_summary = {'products': len(products), 'images': len(images),
                      'matched_images': sum(image['valid'] and bool(image['sku']) for image in images),
                      'errors': 0, 'warnings': len(issues)}
    _require(isinstance(summary, dict) and summary == wanted_summary and
             all(type(value) is int for value in summary.values()) and
             report.get('status') == ('warning' if issues else 'ok'),
             'INVALID_REPORT', 'summary', '汇总数量、状态与报告明细不一致。')
    return expected, seen


def _verify(archive, count, result):
    infos = archive.infolist()
    _require(len(infos) == count, 'INVALID_ZIP', '', 'ZIP 目录中的条目数量不一致。')
    entries, normalized, total = {}, set(), 0
    for info in infos:
        name = info.filename
        _require(info.orig_filename == name and _path(name, archive=True) and not info.is_dir(),
                 'UNSAFE_PATH', name, '不安全的 ZIP 路径或目录条目。')
        key = unicodedata.normalize('NFC', name).casefold()
        _require(key not in normalized, 'DUPLICATE_ENTRY', name, 'ZIP 包含重复或跨系统冲突的路径。')
        normalized.add(key); entries[name] = info
        mode = info.external_attr >> 16
        _require(stat.S_IFMT(mode) in (0, stat.S_IFREG), 'UNSAFE_ENTRY', name, 'ZIP 仅允许普通文件。')
        _require(not info.flag_bits & 1 and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                 'UNSUPPORTED_ZIP', name, '不支持加密 ZIP 或此压缩方式。')
        maximum = MAX_TABLE if name.startswith('source/') else (30 * 1024 * 1024 if name.startswith(('products/', 'unmatched/')) else MAX_METADATA)
        _require(0 <= info.file_size <= maximum and info.compress_size >= 0,
                 'ZIP_SIZE_LIMIT', name, 'ZIP 文件解压后超过单文件大小限制。')
        total += info.file_size
        _require(total <= MAX_UNPACKED, 'ZIP_SIZE_LIMIT', '', 'ZIP 解压后总量超过 320 MB。')
    result['summary'].update({'files': len(infos), 'bytes': total})
    _require(FIXED_FILES <= set(entries), 'MISSING_FILE', '', '缺少 SKU Kit 清单、报告或交付说明。')
    sources = [name for name in entries if name.startswith('source/')]
    _require(len(sources) == 1 and len(sources[0].split('/')) == 2 and
             PurePosixPath(sources[0]).suffix.lower() in ('.csv', '.xlsx'),
             'INVALID_SOURCE', 'source/', '必须包含一份 CSV 或 XLSX 原始产品表。')
    report = _json(archive, entries['reports/report.json'])
    manifest = _json(archive, entries['reports/manifest.json'])
    expected, images = _records(report, manifest)
    wanted = FIXED_FILES | set(sources) | {item['target'] for item in expected}
    _require(wanted == set(entries), 'FILE_SET_MISMATCH', '', '交付文件缺失，或存在清单之外的额外文件。')
    result['summary']['images'] = len(expected)
    hashes = {item['target']: item['sha256'] for item in expected}
    hashes[sources[0]] = report['table_sha256']
    sizes = {item['target']: images[item['source']]['size'] for item in expected}
    for name, info in entries.items():
        if name in sizes:
            _require(info.file_size == sizes[name], 'SIZE_MISMATCH', name, '图片字节数与报告不一致。')
        actual_hash = _hash_entry(archive, info)  # Reads every entry to EOF, so zipfile checks every CRC.
        if name in hashes:
            _require(actual_hash == hashes[name], 'HASH_MISMATCH', name, '文件 SHA-256 与交付记录不一致。')


def verify_package(package_source):
    """Accept a path, bytes or seekable binary stream; never extract or mutate it.

    Returns status ok / error / invalid_input. ``error`` means an unreadable ZIP,
    unsupported format, inconsistent records or failed integrity checks;
    ``invalid_input`` means the supplied path/stream could not be read.
    """
    result = {'kind': 'package_verification', 'status': 'error',
              'summary': {'files': 0, 'images': 0, 'bytes': 0, 'errors': 0}, 'issues': [],
              'limits': {'authenticity_checked': False, 'content_identity_checked': False,
                         'max_package_bytes': MAX_PACKAGE, 'max_unpacked_bytes': MAX_UNPACKED,
                         'max_files': MAX_FILES},
              'notice': '清单没有数字签名；核验只确认包内记录与文件一致，不证明发送者身份或商品内容。'}
    stream, owned, position = None, False, None
    try:
        if isinstance(package_source, (str, Path)):
            path = Path(package_source).expanduser()
            if path.is_symlink() or not path.is_file():
                raise OSError()
            stream = path.open('rb'); owned = True
        elif isinstance(package_source, (bytes, bytearray)):
            _require(len(package_source) <= MAX_PACKAGE, 'ZIP_SIZE_LIMIT', '', '交付包超过 320 MB。')
            stream = io.BytesIO(package_source); owned = True
        else:
            stream = package_source
            position = stream.tell()
        stream.seek(0, 2); size = stream.tell()
        count = _directory_check(stream, size)
        with zipfile.ZipFile(stream, 'r') as archive:
            _verify(archive, count, result)
        result['status'] = 'ok'
    except _Failure as error:
        result['issues'].append(error.issue)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, RuntimeError, NotImplementedError):
        result['issues'].append({'code': 'INVALID_ZIP', 'subject': '', 'message': 'ZIP 损坏、CRC 错误或压缩记录无效。'})
    except (OSError, AttributeError, TypeError):
        result['status'] = 'invalid_input'
        result['issues'].append({'code': 'READ_FAILED', 'subject': '', 'message': '无法读取交付包，请检查文件是否存在、可读且支持定位。'})
    except Exception:
        result['issues'].append({'code': 'INVALID_PACKAGE', 'subject': '', 'message': '交付包内容或结构无效，核验未通过。'})
    finally:
        if stream is not None:
            try:
                if owned:
                    stream.close()
                elif position is not None:
                    stream.seek(position)
            except Exception:
                pass
    result['summary']['errors'] = len(result['issues'])
    return result
