"""One deterministic engine shared by CLI, MCP, HTTP, and the minimal UI."""
import csv
import hashlib
import io
import json
import re
import unicodedata
import warnings
import zipfile
from pathlib import PurePosixPath
from PIL import Image, UnidentifiedImageError

from table_io import parse_table, SKU_ALIASES

VERSION = '0.4.0'
EXTENSIONS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP',
              '.gif': 'GIF', '.bmp': 'BMP', '.avif': 'AVIF'}
MAX_TOTAL = 240 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(value):
    value = unicodedata.normalize('NFC', value)
    value = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', value).strip(' .')
    # Keep ordinary file extensions readable when truncating long supplier names.
    suffix = PurePosixPath(value).suffix
    if 0 < len(suffix.encode('utf-8')) <= 16:
        stem = value[:-len(suffix)]
        stem = stem.encode('utf-8')[:160-len(suffix.encode('utf-8'))].decode('utf-8', 'ignore').rstrip(' .')
        value = (stem or '_unnamed') + suffix
    else:
        value = value.encode('utf-8')[:160].decode('utf-8', 'ignore').rstrip(' .') or '_unnamed'
    if re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', value, re.I):
        value = '_' + value
    return value


def unique_name(value, used):
    original = safe_name(value)
    path = PurePosixPath(original)
    stem, extension = path.stem, path.suffix
    candidate, count = original, 2
    while unicodedata.normalize('NFC', candidate).casefold() in used:
        candidate = '%s_%d%s' % (stem, count, extension); count += 1
    used.add(unicodedata.normalize('NFC', candidate).casefold())
    return candidate


def match_sku(filename, skus, mode='exact', case_sensitive=True):
    normalize = (lambda x: x) if case_sensitive else (lambda x: x.casefold())
    stem = normalize(PurePosixPath(filename).stem)
    exact = [s for s in skus if normalize(s) == stem]
    if exact:
        return exact
    if mode == 'exact':
        return []
    return [s for s in skus if stem.startswith(normalize(s))
            and re.match(r'^[-_ ]', stem[len(normalize(s)):])]


def inspect_image(data, extension):
    if len(data) > 30 * 1024 * 1024:
        raise ValueError('单张图片超过 30 MB。')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > 25_000_000:
                raise ValueError('图片超过 2500 万像素。')
            if image.format != EXTENSIONS[extension]:
                raise ValueError('扩展名与实际图片格式不一致。')
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            frames = getattr(image, 'n_frames', 1)
            if frames > 100:
                raise ValueError('动画超过 100 帧，当前版本不支持完整验证。')
            for frame in range(frames):
                image.seek(frame); image.load()
    return width, height


def analyze(table_name, table_bytes, images, sku_column=None, sheet=None,
            match_mode='exact', case_sensitive=True, min_width=0, min_height=0,
            assignments=None, duplicate_policy='error'):
    if duplicate_policy not in ('error', 'repeat_rows'):
        raise ValueError('duplicate_policy 必须为 error 或 repeat_rows。')
    assignments = {} if assignments is None else assignments
    if not isinstance(assignments, dict):
        raise ValueError('人工确认必须是按图片路径索引的对象。')
    source_names = {x['name'] for x in images}
    if set(assignments) - source_names:
        raise ValueError('人工确认引用了不存在的图片，请重新核对。')
    if match_mode not in ('exact', 'suffix'):
        raise ValueError('match_mode 必须为 exact 或 suffix。')
    if not isinstance(case_sensitive, bool):
        raise ValueError('case_sensitive 必须为布尔值。')
    for value in (min_width, min_height):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 20000:
            raise ValueError('最小宽高必须为 0～20,000 的整数。')
    table = parse_table(table_bytes, PurePosixPath(table_name).suffix.lower().lstrip('.'), sheet)
    table_digest = sha(table_bytes)
    headers, rows = table['headers'], table['rows']
    if sku_column is None:
        candidates = [h for h in headers if h.strip().casefold() in SKU_ALIASES]
        if len(candidates) != 1:
            raise ValueError('无法唯一确定 SKU 列，请指定 sku_column。可选：' + ', '.join(headers))
        sku_column = candidates[0]
    if sku_column not in headers:
        raise ValueError('指定 SKU 列不存在：' + str(sku_column))
    col = headers.index(sku_column)
    context_digest = sha(json.dumps({'table_sha256':table_digest,'sheet':table['sheet'],
                                     'sku_column':sku_column},sort_keys=True,ensure_ascii=False).encode())
    if len(images) > 5000 or sum(len(x['data']) for x in images) > MAX_TOTAL:
        raise ValueError('图片资料最多 5,000 个文件，总计 240 MB。')
    issues = []
    def issue(code, severity, subject, message, row=None):
        item = {'code': code, 'severity': severity, 'subject': subject, 'message': message}
        if row is not None:
            item['row'] = row
        issues.append(item)
    skus, products, counts = [], [], {}
    name_column = next((i for i,h in enumerate(headers) if h.strip().casefold() in
                        ('名称','产品名称','商品名称','name','title','product name')), None)
    for row, kinds, row_number in zip(rows, table['cell_types'], table['row_numbers']):
        raw = row[col]; sku = raw.strip()
        products.append({'row': row_number, 'sku': sku,
                         'name': row[name_column] if name_column is not None else ''})
        if not sku:
            issue('EMPTY_SKU', 'error', '', 'SKU 为空。', row_number)
            continue
        if sku != raw:
            issue('SKU_WHITESPACE', 'error', raw, '编号前后有空白，请在原表修正。', row_number)
        if re.search(r'[\x00-\x1f\x7f]', sku):
            issue('SKU_CONTROL_CHARACTER', 'error', sku, '编号含控制字符。', row_number)
        if kinds[col] != 'text':
            issue('SKU_NOT_TEXT', 'error', sku, 'XLSX 编号必须为文本；数字可能已丢失前导零或精度，公式不作为编号。', row_number)
        counts.setdefault(sku, []).append(row_number)
        if sku not in skus:
            skus.append(sku)
    for sku, positions in counts.items():
        if len(positions) > 1:
            names = {p['name'].strip() for p in products if p['sku'] == sku and p['name'].strip()}
            if duplicate_policy == 'repeat_rows' and len(names) <= 1:
                issue('REPEATED_SKU_ROWS', 'warning', sku, '按同一商品多条记录处理，共用图片文件夹；原表保留所有行。所在行：' + ', '.join(map(str, positions)))
            elif duplicate_policy == 'repeat_rows':
                issue('DUPLICATE_SKU_CONFLICT', 'error', sku, '同一编号对应不同产品名称，不能按重复记录合并。所在行：' + ', '.join(map(str, positions)))
            else:
                issue('DUPLICATE_SKU', 'error', sku, '编号重复，所在行：' + ', '.join(map(str, positions)))
    seen_case = {}
    for sku in skus:
        key = unicodedata.normalize('NFC', sku).casefold()
        seen_case.setdefault(key, []).append(sku)
    for collisions in seen_case.values():
        if len(collisions) > 1:
            issue('SKU_CASE_COLLISION', 'error', ' / '.join(collisions), '编号只在大小写或 Unicode 形式上不同，跨系统可能冲突。')
    inventory, paths = [], set()
    for asset in sorted(images, key=lambda x: x['name']):
        name, data = asset['name'], asset['data']
        if not isinstance(name, str) or not name or '\\' in name or name.startswith('/') or '..' in name.split('/') or '\x00' in name:
            raise ValueError('图片相对路径无效。')
        if name in paths:
            raise ValueError('重复输入路径：' + name)
        paths.add(name)
        extension = PurePosixPath(name).suffix.lower()
        item = {'source': name, 'sha256': sha(data), 'size': len(data), 'sku': None,
                'candidates': [], 'valid': False, 'width': None, 'height': None,
                'assignment_source': 'filename'}
        inventory.append(item)
        if extension not in EXTENSIONS:
            if name in assignments:
                raise ValueError('不支持的文件不能人工分配为商品图片：' + name)
            issue('UNSUPPORTED_FILE', 'warning', name, '不支持的文件，不参与图片导出。')
            continue
        candidates = match_sku(PurePosixPath(name).name, skus, match_mode, case_sensitive)
        item['candidates'] = candidates
        if name in assignments:
            decision = assignments[name]
            if not isinstance(decision, dict) or set(decision) != {'sku','sha256','table_sha256','context_sha256'}:
                raise ValueError('人工确认格式无效：' + name)
            if decision['sha256'] != item['sha256'] or decision['table_sha256'] != table_digest or decision['context_sha256'] != context_digest:
                raise ValueError('人工确认对应的产品表或图片已变化，请重新核对：' + name)
            if decision['sku'] is not None and (not isinstance(decision['sku'], str) or decision['sku'] not in skus):
                raise ValueError('人工确认的产品编号不存在：' + name)
            item['sku'] = decision['sku']
            item['assignment_source'] = 'manual'
            if decision['sku'] is None:
                issue('UNMATCHED_IMAGE', 'warning', name, '已人工选择暂不归类；允许提醒后单独存放。')
        elif len(candidates) > 1:
            issue('AMBIGUOUS_MATCH', 'error', name, '匹配多个编号，不自动选择：' + ', '.join(candidates))
        elif len(candidates) == 1:
            item['sku'] = candidates[0]
        else:
            issue('UNMATCHED_IMAGE', 'warning', name, '未匹配到 SKU；允许警告导出时放入 unmatched/。')
        try:
            item['width'], item['height'] = inspect_image(data, extension)
            item['valid'] = True
        except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            reason = str(error) if isinstance(error, ValueError) else '无法完整解码，文件可能损坏或当前解码器不支持。'
            issue('INVALID_IMAGE', 'error', name, '图片校验未通过：' + reason)
            continue
        if item['width'] < min_width or item['height'] < min_height:
            issue('SMALL_IMAGE', 'warning', name, '尺寸 %d×%d 低于要求 %d×%d；不自动放大。' % (item['width'], item['height'], min_width, min_height))
    have_images = {x['sku'] for x in inventory if x['valid'] and x['sku']}
    for sku in skus:
        if sku not in have_images:
            issue('MISSING_IMAGE', 'error', sku, '缺少可验证的匹配图片。')
    errors = sum(x['severity'] == 'error' for x in issues)
    warning_count = len(issues) - errors
    report = {'schema_version': '1.1', 'engine_version': VERSION,
              'status': 'error' if errors else ('warning' if warning_count else 'ok'),
              'summary': {'products': len(products), 'images': len(inventory),
                          'matched_images': sum(x['valid'] and bool(x['sku']) for x in inventory),
                          'errors': errors, 'warnings': warning_count},
              'rules': {'sku_column': sku_column, 'sheet': table['sheet'], 'match_mode': match_mode,
                        'case_sensitive': case_sensitive, 'min_width': min_width, 'min_height': min_height,
                        'duplicate_policy': duplicate_policy},
              'table_sha256': table_digest, 'products': products, 'images': inventory, 'issues': issues,
              'context_sha256': context_digest,
              'assignments': json.loads(json.dumps(assignments)),
              'limits': {'content_identity_checked': False, 'image_decoder': 'Pillow'}}
    return report, table


def csv_bytes(rows):
    stream = io.StringIO(newline=''); writer = csv.writer(stream)
    def protect(value):
        text = str(value)
        return "'" + text if re.match(r'^[\s]*[=+@-]', text) or text.startswith(('\t', '\r')) else text
    writer.writerows([[protect(x) for x in row] for row in rows])
    return stream.getvalue().encode('utf-8-sig')


def checklist(report):
    """User-facing handoff list can be downloaded even if packaging is blocked."""
    titles = {'MISSING_IMAGE':'需要补图','INVALID_IMAGE':'需要替换图片','DUPLICATE_SKU':'核对重复编号',
              'DUPLICATE_SKU_CONFLICT':'核对编号与名称','EMPTY_SKU':'补充产品编号',
              'AMBIGUOUS_MATCH':'确认图片归属','UNMATCHED_IMAGE':'核对多余图片','SMALL_IMAGE':'核对图片尺寸',
              'REPEATED_SKU_ROWS':'确认重复记录'}
    rows = [['处理事项','级别','产品编号','产品名称','文件路径','表格行号','说明']]
    for issue in report['issues']:
        image = next((x for x in report['images'] if x['source'] == issue['subject']), None)
        sku = image['sku'] if image else issue['subject']
        if image and not sku:
            sku = ' / '.join(image['candidates'])
        products = [x for x in report['products'] if x['sku'] == sku]
        name = next((x['name'] for x in products if x['name']), '')
        row_numbers = str(issue.get('row','')) or ', '.join(str(x['row']) for x in products)
        rows.append([titles.get(issue['code'],'核对资料'), '需要修改' if issue['severity']=='error' else '提醒',
                     sku or '', name, image['source'] if image else '', row_numbers, issue['message']])
    return csv_bytes(rows)


def package(report, table, table_name, table_bytes, assets, allow_warnings=False):
    if report['summary']['errors']:
        raise ValueError('存在阻断错误，请修复后重新检查。不能强制导出。')
    if report['summary']['warnings'] and not allow_warnings:
        raise ValueError('存在警告；查看后明确设置 allow_warnings 才能导出。')
    if sha(table_bytes) != report['table_sha256']:
        raise ValueError('产品表在检查后变化，请重新检查。')
    source = {x['name']: x['data'] for x in assets}
    for item in report['images']:
        if item['source'] not in source or sha(source[item['source']]) != item['sha256']:
            raise ValueError('图片在检查后变化，请重新检查。')
    folders, used, file_names = {}, set(), {}
    for product in report['products']:
        sku = product['sku']
        if sku and sku not in folders:
            folders[sku] = unique_name(sku, used)
    entries, manifest = {}, []
    for item in report['images']:
        if not item['valid']:
            continue
        directory = 'products/' + folders[item['sku']] if item['sku'] else 'unmatched'
        used_names = file_names.setdefault(directory, set())
        target = directory + '/' + unique_name(PurePosixPath(item['source']).name, used_names)
        entries[target] = source[item['source']]
        manifest.append({'source': item['source'], 'target': target, 'sku': item['sku'], 'sha256': item['sha256']})
    # Keep the original table byte-for-byte; do not silently rewrite SKU values.
    entries['source/' + safe_name(PurePosixPath(table_name).name)] = table_bytes
    entries['reports/issues.csv'] = csv_bytes([['code', 'severity', 'subject', 'row', 'message']] +
        [[x['code'], x['severity'], x['subject'], x.get('row', ''), x['message']] for x in report['issues']])
    entries['reports/report.json'] = json.dumps(report, ensure_ascii=False, indent=2).encode()
    entries['reports/manifest.json'] = json.dumps({'files': manifest, 'sku_folders': folders,
                                                 'manual_assignments': report.get('assignments', {})}, ensure_ascii=False, indent=2).encode()
    entries['reports/补件清单.csv'] = checklist(report)
    entries['README.txt'] = ('SKU Kit v%s\n原始图片及 source/ 中的原表按字节保留。\n' % VERSION +
        'reports/manifest.json 提供原路径、目标路径和 SHA-256。\n'
        'unmatched/ 包含明确允许警告后导出的未匹配图片。\n'
        '不判断图片实际商品内容，不代表电商平台审核通过。\n').encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    payload = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if archive.testzip() is not None:
            raise ValueError('导出 ZIP 校验失败。')
        for item in manifest:
            if sha(archive.read(item['target'])) != item['sha256']:
                raise ValueError('导出图片校验失败。')
    return payload
