"""Strict, dependency-free CSV / XLSX reader. Never evaluates formulas."""
import csv
import io
import posixpath
import zipfile
import xml.etree.ElementTree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
MAX_TABLE = 20 * 1024 * 1024


def parse_table(data, kind, sheet=None):
    if len(data) > MAX_TABLE:
        raise ValueError('表格超过 20 MB，请拆分。')
    records, types, sheets = [], [], []
    if kind == 'csv':
        content = None
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                content = data.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        if content is None or '\x00' in content:
            raise ValueError('CSV 编码无效，请另存为 UTF-8。')
        try:
            dialect = csv.Sniffer().sniff(content[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(content, newline=''), dialect, strict=True)
        for row in reader:
            if any(x.strip() for x in row):
                records.append((reader.line_num, row))
                types.append(['text'] * len(row))
            if len(records) > 20001:
                raise ValueError('产品记录超过 20,000 条。')
    elif kind == 'xlsx':
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            infos = book.infolist()
            if len(infos) > 3000 or sum(x.file_size for x in infos) > 80 * 1024 * 1024:
                raise ValueError('XLSX 解压后超过限制，请拆分。')
            if len({x.filename for x in infos}) != len(infos):
                raise ValueError('XLSX 中存在重复内部路径。')
            def xml(path):
                raw = book.read(path)
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                    raise ValueError('不支持 XML 实体声明。')
                return ET.fromstring(raw)
            shared = []
            if 'xl/sharedStrings.xml' in book.namelist():
                shared = [''.join(t.text or '' for t in si.findall('.//s:t', NS))
                          for si in xml('xl/sharedStrings.xml').findall('s:si', NS)]
            definitions = xml('xl/workbook.xml').findall('s:sheets/s:sheet', NS)
            sheets = [x.get('name') for x in definitions]
            if not definitions:
                raise ValueError('XLSX 没有工作表。')
            if sheet is None and len(sheets) != 1:
                raise ValueError('存在多张工作表，请明确指定 sheet：' + ', '.join(sheets))
            selected = next((x for x in definitions if x.get('name') == sheet), None) if sheet else definitions[0]
            if selected is None:
                raise ValueError('指定工作表不存在：' + str(sheet))
            rid = selected.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            relationships = xml('xl/_rels/workbook.xml.rels')
            relation = next((x for x in relationships if x.get('Id') == rid), None)
            if relation is None or relation.get('TargetMode') == 'External':
                raise ValueError('工作表关系无效。')
            target = relation.get('Target', '')
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            if not path.startswith('xl/') or '..' in path.split('/'):
                raise ValueError('工作表路径无效。')
            for index, row in enumerate(xml(path).findall('s:sheetData/s:row', NS), 1):
                values, kinds, used = [], [], set()
                for cell in row.findall('s:c', NS):
                    # Excel often serializes style-only empty cells far beyond
                    # the real table. Ignore them, but never ignore formulas.
                    if (cell.find('s:f', NS) is None and cell.find('s:v', NS) is None
                            and not any(t.text for t in cell.findall('s:is//s:t', NS))):
                        continue
                    letters = ''.join(c for c in cell.get('r', '') if c.isalpha())
                    col = 0
                    for letter in letters.upper():
                        col = col * 26 + ord(letter) - 64
                    if not 1 <= col <= 256 or col in used:
                        raise ValueError('XLSX 列号无效、重复或超过 256 列。')
                    used.add(col)
                    while len(values) < col:
                        values.append(''); kinds.append('text')
                    val = cell.find('s:v', NS)
                    value = (val.text or '') if val is not None else ''
                    kind_value = cell.get('t', 'n')
                    if kind_value == 's':
                        offset = int(value)
                        if not 0 <= offset < len(shared):
                            raise ValueError('共享字符串索引无效。')
                        value = shared[offset]; kind_value = 'text'
                    elif kind_value == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('s:is//s:t', NS)); kind_value = 'text'
                    elif kind_value == 'str':
                        kind_value = 'text'
                    if cell.find('s:f', NS) is not None:
                        kind_value = 'formula'
                    elif kind_value == 'n':
                        kind_value = 'number'
                    elif kind_value == 'e':
                        kind_value = 'error'
                    values[col - 1] = value; kinds[col - 1] = kind_value
                while values and values[-1] == '' and kinds[-1] not in ('formula', 'error'):
                    values.pop(); kinds.pop()
                if any(str(x).strip() for x in values) or any(k in ('formula', 'error') for k in kinds):
                    records.append((int(row.get('r', index)), values)); types.append(kinds)
                if len(records) > 20001:
                    raise ValueError('产品记录超过 20,000 条。')
    else:
        raise ValueError('只支持 CSV 和 XLSX，暂不支持 XLS。')
    if len(records) < 2:
        raise ValueError('需要表头及至少一条产品记录。')
    headers = records[0][1]
    if not 1 <= len(headers) <= 256:
        raise ValueError('表头必须为 1～256 列。')
    if any(not x.strip() for x in headers) or len(set(headers)) != len(headers):
        raise ValueError('表头存在空列名或重复列名，请修正。')
    if len({x.strip().casefold() for x in headers}) != len(headers):
        raise ValueError('表头去空格或忽略大小写后重复，请修正。')
    width = len(headers)
    rows, row_types = [], []
    for (_, values), kinds in zip(records[1:], types[1:]):
        if len(values) > width or (kind == 'csv' and len(values) != width):
            raise ValueError('数据列数与表头不一致，请检查分隔符、引号和空列。')
        rows.append(values + [''] * (width - len(values)))
        row_types.append(kinds + ['text'] * (width - len(kinds)))
    return {'headers': headers, 'rows': rows, 'row_numbers': [x[0] for x in records[1:]],
            'cell_types': row_types, 'sheets': sheets, 'sheet': sheet or (sheets[0] if sheets else None)}
