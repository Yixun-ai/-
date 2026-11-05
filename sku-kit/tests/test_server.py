import io
import unittest
import zipfile
from table_io import parse_table


class TableTests(unittest.TestCase):
    def test_csv_bom_quoted_and_leading_zero(self):
        result = parse_table('\ufeffSKU,名称\r\n001,"杯子,绿色"\r\n002,包'.encode(), 'csv')
        self.assertEqual(result['rows'][0], ['001', '杯子,绿色'])

    def test_gbk_semicolon(self):
        result = parse_table('SKU;名称\nA;产品\nB;图片'.encode('gb18030'), 'csv')
        self.assertEqual(result['headers'], ['SKU', '名称'])

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            parse_table(b'SKU,name\n', 'csv')

    def test_xlsx_first_sheet_shared_and_inline_sparse(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as book:
            book.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Products" sheetId="1" r:id="rId7"/></sheets></workbook>')
            book.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId7" Target="worksheets/sheet2.xml"/></Relationships>')
            book.writestr('xl/sharedStrings.xml', '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>SKU</t></si><si><t>001</t></si></sst>')
            book.writestr('xl/worksheets/sheet2.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>规格</t></is></c><c r="C1" t="inlineStr"><is><t>名称</t></is></c></row><row><c r="A2" t="s"><v>1</v></c><c r="C2" t="inlineStr"><is><t>杯子</t></is></c></row></sheetData></worksheet>')
        result = parse_table(archive.getvalue(), 'xlsx')
        self.assertEqual(result['rows'][0], ['001', '', '杯子'])
        self.assertEqual(result['headers'][1], '规格')


if __name__ == '__main__':
    unittest.main()
