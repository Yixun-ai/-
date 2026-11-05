import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import build_desktop
import desktop


class DesktopTests(unittest.TestCase):
    def test_entry_keeps_server_arguments_and_exit_status(self):
        with patch('server.main', return_value=3) as start:
            self.assertEqual(desktop.main(), 3)
            start.assert_called_once_with()

    def test_startup_exception_is_visible_and_returns_failure(self):
        output = io.StringIO()
        with patch('server.main', side_effect=RuntimeError('visible error')):
            with contextlib.redirect_stderr(output):
                self.assertEqual(desktop.main(), 1)
        self.assertIn('visible error', output.getvalue())

    def test_bundle_command_includes_static_and_console(self):
        output = Path(tempfile.gettempdir()) / 'space in path'
        command = build_desktop.build_command(output)
        self.assertIn('--onedir', command)
        self.assertIn('--console', command)
        self.assertIn(str(build_desktop.SOURCE / 'static') + ':static', command)
        self.assertEqual(command[-1], str(build_desktop.SOURCE / 'desktop.py'))

    def test_archive_keeps_nested_runtime_files_and_checksum(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            bundle = output / 'dist' / 'SKU-Kit'
            (bundle / '_internal').mkdir(parents=True)
            (bundle / 'SKU-Kit').write_bytes(b'executable')
            (bundle / '_internal' / 'asset.txt').write_bytes(b'resource')
            # A regular unzip must also receive files behind framework directory links.
            if hasattr(Path, 'symlink_to'):
                try:
                    (bundle / 'linked-runtime').symlink_to('_internal', target_is_directory=True)
                except OSError:
                    pass  # Windows may require elevated symlink privileges.
            build_desktop.write_launchers(bundle)
            archive, checksum = build_desktop.archive_bundle(bundle, output)
            with zipfile.ZipFile(archive) as packed:
                self.assertEqual(packed.read('SKU-Kit/_internal/asset.txt'), b'resource')
                if (bundle / 'linked-runtime').exists():
                    self.assertEqual(packed.read('SKU-Kit/linked-runtime/asset.txt'), b'resource')
            self.assertIn(archive.name, checksum.read_text())


if __name__ == '__main__':
    unittest.main()
