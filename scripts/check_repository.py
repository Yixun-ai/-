#!/usr/bin/env python3
"""Offline repository checks: local links, Python syntax, metadata and issue forms.

Requires PyYAML for YAML validation. No external URLs or user data are opened.
"""
import argparse
import ast
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import unquote, urlsplit

REPOSITORY = Path(__file__).resolve().parent.parent
REQUIRED_DOCUMENTS = (
    'README.md', 'README.en.md', 'LICENSE', 'CONTRIBUTING.md', 'SECURITY.md',
    'SUPPORT.md', 'CHANGELOG.md', 'CODE_OF_CONDUCT.md', 'docs/quickstart.md',
    'docs/troubleshooting.md', 'docs/desktop.md', '.github/pull_request_template.md',
)
SKIP_DIRECTORIES = {'.git', '__pycache__', 'node_modules', 'build', 'dist',
                    'build-desktop', 'wheelhouse', 'private-data', '.pytest_cache'}


def repository_files(root):
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names
                          if name not in SKIP_DIRECTORIES and not name.startswith(('.venv', 'venv'))
                          and not name.endswith('.egg-info'))
        for name in sorted(files):
            yield Path(directory) / name


def prose_lines(content):
    """Ignore fenced and indented code while preserving document line numbers."""
    fence = None
    for number, line in enumerate(content.splitlines(), 1):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            continue
        if fence is not None or line.startswith(('    ', '\t')):
            continue
        # Inline code can contain example Markdown links; it is not navigable prose.
        yield number, re.sub(r'(`+).*?\1', '', line)


def markdown_targets(content):
    for number, line in prose_lines(content):
        # Match ordinary destinations, including balanced parentheses in filenames.
        for match in re.finditer(r'!?\[[^\]\n]*\]\(\s*(<[^>\n]+>|(?:[^\s()]|\([^()]*\))+)', line):
            yield number, match.group(1).strip('<>')
        for match in re.finditer(r'^ {0,3}\[[^\]\n]+\]:\s*(<[^>\n]+>|\S+)', line):
            yield number, match.group(1).strip('<>')
        for match in re.finditer(r'<(?:a|img)\b[^>]*?\b(?:href|src)=["\']([^"\']+)["\']', line, re.I):
            yield number, match.group(1)


def check_markdown(path, root):
    problems = []
    for number, destination in markdown_targets(path.read_text(encoding='utf-8')):
        # Absolute local paths are documentation examples, not repository links.
        if destination.startswith(('/', '~', '#', '\\')) or re.match(r'^[A-Za-z]:[\\/]', destination):
            continue
        parsed = urlsplit(destination)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        target = (path.parent / unquote(parsed.path)).resolve()
        if root != target and root not in target.parents:
            problems.append('%s:%d: relative link escapes repository: %s' % (path.relative_to(root), number, destination))
        elif not target.exists():
            problems.append('%s:%d: missing relative link target: %s' % (path.relative_to(root), number, destination))
    return problems


def check_issue_form(form, label):
    problems = []
    if not isinstance(form, dict):
        return [label + ': issue form must be a mapping']
    for key in ('name', 'description'):
        if not isinstance(form.get(key), str) or not form[key].strip():
            problems.append(label + ': missing nonempty ' + key)
    body = form.get('body')
    if not isinstance(body, list) or not body:
        return problems + [label + ': body must be a nonempty list']
    identifiers = set()
    for index, field in enumerate(body):
        subject = '%s: body[%d]' % (label, index)
        if not isinstance(field, dict) or field.get('type') not in {'markdown', 'input', 'textarea', 'dropdown', 'checkboxes'}:
            problems.append(subject + ': invalid field type')
            continue
        attributes = field.get('attributes')
        if not isinstance(attributes, dict):
            problems.append(subject + ': attributes must be a mapping')
            continue
        if field['type'] == 'markdown':
            if not isinstance(attributes.get('value'), str) or not attributes['value'].strip():
                problems.append(subject + ': markdown requires value text')
            continue
        identifier = field.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
            problems.append(subject + ': field requires a valid id')
        elif identifier in identifiers:
            problems.append(subject + ': duplicate id ' + identifier)
        else:
            identifiers.add(identifier)
        if not isinstance(attributes.get('label'), str) or not attributes['label'].strip():
            problems.append(subject + ': field requires a label')
        validations = field.get('validations', {})
        if not isinstance(validations, dict) or ('required' in validations and type(validations['required']) is not bool):
            problems.append(subject + ': validations.required must be boolean')
        if field['type'] in ('dropdown', 'checkboxes'):
            options = attributes.get('options')
            if not isinstance(options, list) or not options:
                problems.append(subject + ': options must be a nonempty list')
            elif field['type'] == 'dropdown' and any(not isinstance(option, str) for option in options):
                problems.append(subject + ': dropdown options must be strings')
            elif field['type'] == 'checkboxes':
                for option in options:
                    if (not isinstance(option, dict) or not isinstance(option.get('label'), str)
                            or ('required' in option and type(option['required']) is not bool)):
                        problems.append(subject + ': invalid checkbox option')
    return problems


def check_yaml(root):
    try:
        import yaml
    except ImportError:
        return ['YAML checks require PyYAML; install the CI tooling dependency PyYAML==6.0.3.']

    class UniqueLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
            if len(set(keys)) != len(keys):
                raise ValueError('duplicate YAML mapping key')
            return super().construct_mapping(node, deep=deep)

    problems = []
    folder = root / '.github' / 'ISSUE_TEMPLATE'
    for filename in ('bug_report.yml', 'feature_request.yml', 'config.yml'):
        if not (folder / filename).is_file():
            problems.append('.github/ISSUE_TEMPLATE/' + filename + ': required file missing')
    for path in sorted((root / '.github').rglob('*')):
        if path.suffix not in ('.yaml', '.yml'):
            continue
        label = str(path.relative_to(root))
        try:
            data = yaml.load(path.read_text(encoding='utf-8'), Loader=UniqueLoader)
            if path.parent.name == 'ISSUE_TEMPLATE' and path.stem != 'config':
                problems.extend(check_issue_form(data, label))
            elif path.parent.name == 'ISSUE_TEMPLATE':
                if not isinstance(data, dict) or type(data.get('blank_issues_enabled')) is not bool:
                    problems.append(label + ': blank_issues_enabled must be boolean')
                for contact in data.get('contact_links', []) if isinstance(data, dict) else []:
                    if not isinstance(contact, dict) or any(not isinstance(contact.get(key), str) or not contact[key].strip()
                                                            for key in ('name', 'url', 'about')):
                        problems.append(label + ': invalid contact_links entry')
            elif path.parent.name == 'workflows' and (not isinstance(data, dict) or not isinstance(data.get('jobs'), dict)):
                problems.append(label + ': workflow requires jobs mapping')
        except (ValueError, TypeError, yaml.YAMLError) as error:
            problems.append(label + ': invalid YAML: ' + str(error))
    return problems


def check_metadata(root):
    problems = []
    try:
        project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        if project.get('requires-python') != '>=3.11':
            problems.append('pyproject.toml: expected tested Python baseline >=3.11')
        dependencies = project.get('dependencies', [])
        requirements = [line.strip() for line in (root / 'sku-kit' / 'requirements.txt').read_text().splitlines()
                        if line.strip() and not line.lstrip().startswith('#')]
        normalize = lambda values: {re.sub(r'\s+', '', value).casefold() for value in values}
        if normalize(dependencies) != normalize(requirements):
            problems.append('requirements.txt and pyproject.toml runtime dependencies disagree')
        if 'pillow>=13.0.0,<14' not in normalize(dependencies):
            problems.append('pyproject.toml: expected security baseline Pillow>=13.0.0,<14')
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        problems.append('Cannot validate package metadata: ' + str(error))
    return problems


def check_repository(root, javascript=False, node='node'):
    root = root.resolve()
    problems = [name + ': required documentation missing' for name in REQUIRED_DOCUMENTS if not (root / name).is_file()]
    if javascript and shutil.which(node) is None:
        problems.append('JavaScript syntax checks require Node.js on PATH or an explicit --node executable.')
        javascript = False
    files = list(repository_files(root))
    for path in files:
        if path.suffix == '.md':
            problems.extend(check_markdown(path, root))
        elif path.suffix == '.py':
            try:
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path), feature_version=(3, 11))
            except (SyntaxError, UnicodeError) as error:
                problems.append(str(path.relative_to(root)) + ': invalid Python 3.11 syntax: ' + str(error))
        elif javascript and path.suffix in ('.js', '.mjs'):
            result = subprocess.run([node, '--check', str(path)], capture_output=True, text=True, encoding='utf-8')
            if result.returncode:
                problems.append(str(path.relative_to(root)) + ': invalid JavaScript syntax: ' + result.stderr.strip())
    problems.extend(check_metadata(root))
    problems.extend(check_yaml(root))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=REPOSITORY)
    parser.add_argument('--javascript', action='store_true', help='Also run installed Node.js syntax checks')
    parser.add_argument('--node', default='node', help='Node.js executable used with --javascript')
    args = parser.parse_args()
    problems = check_repository(args.root, args.javascript, args.node)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print('PASS: local documentation links, required files, YAML forms, package metadata and source syntax.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
