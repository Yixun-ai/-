"""Exercise package imports without installing generic top-level module names."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from _version import VERSION

SOURCE = Path(__file__).resolve().parents[1]
BOOTSTRAP = """
import importlib.util
import sys
from pathlib import Path
source = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('sku_kit', source / '__init__.py',
                                           submodule_search_locations=[str(source)])
package = importlib.util.module_from_spec(spec)
sys.modules['sku_kit'] = package
spec.loader.exec_module(package)
"""


class PackageTests(unittest.TestCase):
    def execute(self, code):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-c', BOOTSTRAP + code, str(SOURCE)],
                                    cwd=directory, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_package_imports_stay_in_their_namespace(self):
        output = self.execute("""
import json
import sku_kit.cli
import sku_kit.mcp_server
import sku_kit.server
import sku_kit.verify
import sku_kit.build_desktop
assert not {'engine', 'table_io', 'cli', 'server', 'verify', '_version'} & set(sys.modules)
assert sku_kit.cli.VERSION == sku_kit.__version__ == sku_kit.build_desktop.VERSION
assert sku_kit.server.ROOT == source
assert (sku_kit.server.ROOT / 'static' / 'index.html').is_file()
assert sku_kit.build_desktop.project_license() == source.parent / 'LICENSE'
assert sku_kit.build_desktop.build_command(Path.cwd())[-1] == str(source / 'desktop.py')
print(json.dumps({'version': sku_kit.__version__}))
""")
        self.assertEqual(json.loads(output)['version'], VERSION)

    def test_package_desktop_calls_relative_server(self):
        self.execute("""
from unittest.mock import patch
import sku_kit.desktop
import sku_kit.server
with patch.object(sku_kit.server, 'main', return_value=7) as launch:
    assert sku_kit.desktop.main() == 7
    launch.assert_called_once_with()
assert 'server' not in sys.modules
""")

    def test_python_module_entry_uses_desktop_exit_status(self):
        self.execute("""
from unittest.mock import patch
import runpy
import sku_kit.desktop
with patch.object(sku_kit.desktop, 'main', return_value=7) as launch:
    try:
        runpy.run_module('sku_kit', run_name='__main__')
    except SystemExit as result:
        assert result.code == 7
    else:
        raise AssertionError('module entry did not exit')
    launch.assert_called_once_with()
""")


if __name__ == '__main__':
    unittest.main()
