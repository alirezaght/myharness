"""Stdlib-only immutable validator closure and original resource identity."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:raise ValueError('duplicate validator metadata key')
            out[key] = value
        return out
    def invalid(value):raise ValueError('nonfinite validator metadata')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def safe(path):
    path = Path(path).absolute()
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():raise ValueError('validator resource symlink refused')
    return path


def capture(path):
    path = safe(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('invalid validator resource: ' + str(path) + ' is not a regular file')
    if before.st_size > 16 * 1024 * 1024:
        raise ValueError('invalid validator resource: ' + str(path) + ' is ' + format(before.st_size / 1048576, '.1f')
                         + ' MiB, over the 16 MiB limit')
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode):
        raise ValueError('validator resource moved during capture')
    return raw, {'sha256': digest(raw), 'mode': stat.S_IMODE(before.st_mode)}


def interpreter_identity():
    path = Path(sys.executable).resolve(strict=True)
    _, state = capture(path)
    return dict(state, executable=sys.executable, canonical_executable=str(path),
                implementation=sys.implementation.name, version=list(sys.version_info[:3]))


def repository_identity(root):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1')
    env = {k:v for k,v in env.items() if not k.startswith(('GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_', 'GIT_DIR', 'GIT_WORK_TREE'))}
    command = ['/usr/bin/git', '--no-optional-locks', '-C', str(root), 'rev-parse', '--show-toplevel', '--git-dir', '--git-common-dir']
    result = subprocess.run(command, env=env, capture_output=True, timeout=10)
    if result.returncode:
        return {'root': str(root), 'git_dir': None, 'common_dir': None}
    lines = result.stdout.decode().splitlines()
    if len(lines) != 3:raise ValueError('invalid validator source repository identity')
    repo = Path(lines[0]).resolve()
    return {'root': str(repo), 'git_dir': str((root / lines[1]).resolve()), 'common_dir': str((root / lines[2]).resolve())}


def closure_paths(root):
    root = safe(root)
    paths = [root/'alctl.py', root/'release-layout.json']
    paths.extend(sorted((root/'installer').glob('*.py')))
    for platform in ('codex','claude'):
        paths.append(root/platform/'skills/al/SKILL.md')
        paths.extend(sorted((root/platform/'agents').glob('al-*')))
        paths.extend(sorted((root/platform/'skills/al/scripts').glob('*.py')))
        paths.extend(sorted(p for p in (root/platform/'skills/al/reference').rglob('*') if p.is_file()))
    return paths


def capture_canonical(root):
    root = safe(root).resolve()
    files, contents = {}, {}
    for path in closure_paths(root):
        raw,state = capture(path)
        name = path.relative_to(root).as_posix()
        files[name],contents[name] = state,raw
    origin = capture_origin(root, files, interpreter_identity())
    expected = {'snapshot_identity':snapshot_identity(origin), 'origin_digest':origin['origin_digest']}
    return {'snapshot':str(root), 'origin':origin, 'bytes':contents, 'expected':expected, 'mode':'canonical'}


def capture_origin(source_root, files, interpreter):
    root = safe(source_root).resolve()
    closure, platforms = {}, {}
    for name, state in sorted(files.items()):
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or path.as_posix() != name:
            raise ValueError('unsafe validator closure name')
        kind = ('runtime' if '/scripts/' in name else 'reference' if '/reference/' in name
                else 'installer' if name.startswith('installer/') else 'bootstrap')
        closure[name] = dict(state, kind=kind)
    if len({name.casefold() for name in closure}) != len(closure):
        raise ValueError('case-colliding validator closure')
    for platform in ('codex', 'claude'):
        scripts = platform + '/skills/al/scripts/'
        reference = platform + '/skills/al/reference/'
        modules = {Path(name).stem: name for name in closure if name.startswith(scripts) and name.endswith('.py')}
        resources = {Path(name).name: name for name in closure if name.startswith(scripts)}
        resources.update({'reference/' + name[len(reference):]:name for name in closure if name.startswith(reference)})
        if not {'al_store', 'al_runtime_resources', 'al_repo_python'} <= modules.keys():
            raise ValueError('incomplete validator runtime closure')
        platforms[platform] = {'scripts_root': str(root / scripts), 'reference_root': str(root / reference),
                               'modules': modules, 'resources': resources}
    repository = repository_identity(root)
    value = {'schema_version': 1, 'kind': 'terminal-validator-origin',
             'source': {'al_root': str(root), 'repository': repository,
                        'source_subdir': root.relative_to(repository['root']).as_posix()},
             'interpreter': interpreter, 'platforms': platforms, 'closure': closure,
             'closure_digest': digest(canonical(closure))}
    value['origin_digest'] = digest(canonical(value))
    return value


def snapshot_identity(origin):
    return digest(canonical({'schema_version':1, 'closure_digest':origin['closure_digest'], 'origin_digest':origin['origin_digest']}))


def verify_origin(origin):
    if origin.get('schema_version') != 1 or origin.get('kind') != 'terminal-validator-origin':
        raise ValueError('unsupported validator origin')
    if digest(canonical({k:v for k,v in origin.items() if k != 'origin_digest'})) != origin.get('origin_digest'):
        raise ValueError('validator origin digest mismatch')
    if digest(canonical(origin['closure'])) != origin['closure_digest']:
        raise ValueError('validator closure digest mismatch')
    files = {name:{k:item[k] for k in ('sha256','mode')} for name,item in origin['closure'].items()}
    reconstructed = capture_origin(Path(origin['source']['al_root']), files, origin['interpreter'])
    if reconstructed != origin:raise ValueError('validator origin mapping/identity mismatch')


def verify_origin_data(origin, captured):
    verify_origin(origin)
    root = safe(origin['source']['al_root'])
    if {p.relative_to(root).as_posix() for p in closure_paths(root)} != set(origin['closure']):
        raise ValueError('original validator closure membership changed')
    for name, item in origin['closure'].items():
        raw, state = capture(root / name)
        if raw != captured[name] or state != {k:item[k] for k in ('sha256','mode')}:
            raise ValueError('original validator resource changed: ' + name)
    if interpreter_identity() != origin['interpreter']:
        raise ValueError('validator interpreter identity changed')
    return origin['source']


def verify_snapshot(snapshot, expected):
    snapshot = safe(snapshot)
    origin_raw, origin_state = capture(snapshot / 'origin.json')
    receipt_raw, receipt_state = capture(snapshot / 'receipt.json')
    if origin_state['mode']!=0o600 or receipt_state['mode']!=0o600:
        raise ValueError('protected validator metadata mode changed')
    if digest(origin_raw) != expected['origin_sha256'] or digest(receipt_raw) != expected['receipt_sha256']:
        raise ValueError('protected validator metadata changed')
    origin = decode(origin_raw)
    # Origin identity is protected before its data can choose any original path.
    if snapshot_identity(origin) != expected['snapshot_identity'] or origin['origin_digest'] != expected['origin_digest']:
        raise ValueError('protected validator origin mismatch')
    names = set(origin['closure']) | {'origin.json', 'receipt.json'}
    actual, captured = set(), {}
    for path in snapshot.rglob('*'):
        safe(path)
        if path.is_file():actual.add(path.relative_to(snapshot).as_posix())
        elif not path.is_dir():raise ValueError('special validator snapshot member')
    if actual != names:raise ValueError('validator snapshot membership changed')
    for name,item in origin['closure'].items():
        raw,state = capture(snapshot/name)
        if state != {k:item[k] for k in ('sha256','mode')}:raise ValueError('validator snapshot resource changed: '+name)
        captured[name] = raw
    return {'snapshot': str(snapshot), 'origin':origin, 'bytes':captured, 'expected':dict(expected)}
