"""Trusted layout, deterministic manifests and content-addressed payloads."""
import ast
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from .engine import D, R, T
from .registry import canonical, digest, read_json, write_json

LAYOUT = Path(__file__).resolve().parents[1] / 'release-layout.json'


def load_layout():
    return read_json(LAYOUT)


def selected(path, layout):
    parts = Path(path).parts
    if set(parts) & set(layout['exclude_parts']) or Path(path).suffix in layout['exclude_suffixes']:
        return None
    if len(parts) < 3 or parts[0] not in layout['platforms']:
        return None
    platform = parts[0]
    if parts[1] == 'agents' and len(parts) == 3 and parts[2].startswith('al-') and Path(path).suffix == ('.toml' if platform == 'codex' else '.md'):
        return platform, 'role'
    prefix = platform + '/' + layout['package'] + '/'
    if path.startswith(prefix) and path[len(prefix):].split('/')[0] in layout['package_roots']:
        return platform, 'package'
    return None


def required(layout):
    result = set()
    for platform in layout['platforms']:
        prefix = platform + '/' + layout['package'] + '/'
        result.add(prefix + 'SKILL.md')
        result.update(prefix + 'scripts/' + name for name in layout['required_scripts'])
        if platform == 'claude':
            result.update(prefix + 'scripts/' + name for name in layout['required_claude_scripts'])
        suffix = '.toml' if platform == 'codex' else '.md'
        result.update(platform + '/agents/' + name + suffix for name in layout['roles'])
    return result


def runtime_schema(raw):
    tree = ast.parse(raw)
    versions = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in node.targets)
                and isinstance(node.value, ast.Constant) and type(node.value.value) is int]
    if len(versions) != 1:
        raise ValueError('runtime schema must be a single integer literal')
    return versions[0]


def build_manifest(release, layout=None):
    layout = layout or load_layout()
    files = {}
    for path, item in release['tree'].items():
        classification = selected(path, layout)
        if classification:
            files[path] = dict(sha256=digest(item['raw']), mode=item['mode'],
                               platform=classification[0], kind=classification[1])
    missing = required(layout) - files.keys()
    if missing:
        raise ValueError('missing required release resources: ' + ', '.join(sorted(missing)))
    for platform in layout['platforms']:
        schema = runtime_schema(release['tree'][platform + '/skills/al/scripts/al_schema.py']['raw'])
        if schema != layout['runtime_schema']:
            raise ValueError('unsupported runtime schema: ' + str(schema))
        if not any(name.startswith(platform + '/skills/al/reference/') for name in files):
            raise ValueError('missing runtime reference resources: ' + platform)
    body = {key: release[key] for key in ('source_identity', 'subdir', 'tag', 'version', 'tag_object', 'commit')}
    body.update(schema_version=1, platforms=layout['platforms'], prerequisites=layout['prerequisites'],
                runtime_schema=layout['runtime_schema'], files=files)
    body['digest'] = digest(canonical(body))
    validate_manifest(body)
    return body


def validate_manifest(manifest):
    body = {key: value for key, value in manifest.items() if key != 'digest'}
    layout = load_layout()
    if manifest.get('schema_version') != 1 or manifest.get('digest') != digest(canonical(body)):
        raise ValueError('release manifest schema/digest mismatch')
    if manifest.get('platforms') != layout['platforms'] or manifest.get('runtime_schema') != layout['runtime_schema'] or manifest.get('prerequisites') != layout['prerequisites']:
        raise ValueError('unsupported release compatibility')
    if sys.version_info < tuple(map(int, manifest['prerequisites']['python'].split('.'))):
        raise ValueError('Python ' + manifest['prerequisites']['python'] + ' or later required')
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', manifest.get('commit', '')):
        raise ValueError('invalid source commit identity')
    files = manifest.get('files')
    if not isinstance(files, dict) or required(layout) - files.keys():
        raise ValueError('release manifest is incomplete')
    folded = set()
    for name, item in files.items():
        if R.relative(name).as_posix() != name:
            raise ValueError('noncanonical release resource path')
        classification = selected(name, layout)
        if not classification or classification != (item.get('platform'), item.get('kind')):
            raise ValueError('unexpected release resource: ' + name)
        if name.casefold() in folded or item.get('mode') not in {0o644, 0o755} or not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', '')):
            raise ValueError('invalid/duplicate release resource: ' + name)
        folded.add(name.casefold())
    return manifest


def verify_cache(path, expected_digest):
    path = R.safe_absolute(path)
    manifest = validate_manifest(read_json(path / 'manifest.json'))
    if manifest['digest'] != expected_digest:
        raise ValueError('cached release identity mismatch')
    expected = {'manifest.json'} | {'payload/' + name for name in manifest['files']}
    actual = set()
    for item in path.rglob('*'):
        R.safe_absolute(item)
        if item.is_file():
            actual.add(item.relative_to(path).as_posix())
        elif not item.is_dir():
            raise ValueError('special cache entry refused')
    if actual != expected:
        raise ValueError('cached release file set mismatch')
    for name, item in manifest['files'].items():
        if T.state(path / 'payload' / name) != {key: item[key] for key in ('sha256', 'mode')}:
            raise ValueError('cached release resource corrupt: ' + name)
    return manifest


def cache_release(release, state_root):
    manifest = build_manifest(release)
    root = R.safe_absolute(state_root) / 'installer/cache'
    path = root / manifest['digest']
    R.safe_absolute(path)
    if path.exists():
        verify_cache(path, manifest['digest'])
        return path, manifest
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.release-', dir=root))
    try:
        for name, item in manifest['files'].items():
            D.atomic(stage / 'payload' / name, release['tree'][name]['raw'], item['mode'])
        write_json(stage / 'manifest.json', manifest)
        verify_cache(stage, manifest['digest'])
        if path.exists():
            verify_cache(path, manifest['digest'])
        else:
            os.rename(stage, path)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return path, manifest
