#!/usr/bin/env python3
"""Console desktop entry point; the same local engine serves source and bundles."""
import sys
import traceback
from pathlib import Path


def main():
    # Some Windows consoles cannot encode Chinese filenames with their default codec.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    root = Path(__file__).resolve().parent
    if not (root / 'static' / 'index.html').is_file():
        print('SKU Kit 启动失败：页面文件缺失，请完整解压下载包。', file=sys.stderr)
        return 1
    try:
        if __package__:
            from . import server
        else:
            import server
        return server.main() or 0
    except Exception:
        print('SKU Kit 启动失败，请保留以下错误信息以便反馈。', file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
