"""Install a stable independent CLI snapshot from trusted local code."""
import os
import shutil
import tempfile
from pathlib import Path
from .engine import D, R, T
from .registry import canonical, coordination, digest, namespace, read_json, write_json
from .source import git
from .validator_origin import capture_origin, closure_paths, interpreter_identity, snapshot_identity, verify_snapshot, verify_origin_data, capture
from .validator_boot import launcher as validator_launcher
from .validator_anchor import existing_anchor


def _verify(path, expected):
    try:return verify_snapshot(path, expected)
    except ValueError as exc:raise ValueError('CLI snapshot corrupt: '+str(exc)) from exc


def bootstrap_cli(source_root, destination, state_root):
    root = R.safe_absolute(source_root)
    if not (root / 'alctl.py').is_file():
        root = R.safe_absolute(root / 'al')
    destination, state_root = R.safe_absolute(destination), R.safe_absolute(state_root)
    if not (root / 'alctl.py').is_file():
        raise ValueError('bootstrap requires the reviewed local al/ source directory')
    try:
        repository_root = R.safe_absolute(git(root, 'rev-parse', '--show-toplevel').decode().strip())
    except ValueError:
        repository_root = root
    for prohibited in (root, repository_root, destination.parent):
        if state_root == prohibited or prohibited in state_root.parents:
            raise ValueError('CLI state must be outside source and launcher directories')
    paths = closure_paths(root)
    content, files = {}, {}
    for path in paths:
        R.safe_absolute(path)
        name = path.relative_to(root).as_posix()
        raw, state = capture(path)
        content[name] = raw
        files[name] = state
    origin = capture_origin(root, files, interpreter_identity())
    content_digest = snapshot_identity(origin)
    snapshot = namespace(state_root) / 'cli' / 'snapshots' / content_digest
    receipt = {'schema_version': 1, 'kind': 'development', 'content_digest': content_digest,
               'files': files, 'source_root': str(root), 'source_commit_if_available': None, 'dirty': True,
               'origin_digest': origin['origin_digest'], 'closure_digest': origin['closure_digest'],
               'interpreter': origin['interpreter']}
    try:
        receipt['source_commit_if_available'] = git(root, 'rev-parse', 'HEAD').decode().strip()
        receipt['dirty'] = bool(git(root, 'status', '--porcelain', '--untracked-files=normal'))
    except ValueError:
        pass
    observation = {key:receipt[key] for key in ('source_commit_if_available', 'dirty')}
    expected = {'snapshot_identity': content_digest, 'origin_digest': origin['origin_digest'],
                'origin_sha256': digest(canonical(origin) + b'\n'),
                'receipt_sha256': digest(canonical(receipt) + b'\n')}
    with coordination(state_root):
        owner_path = namespace(state_root) / 'cli' / ('launcher-' + digest(str(destination).encode()) + '.json')
        previous = read_json(owner_path) if owner_path.exists() else None
        actual = T.state(destination)
        if actual and (not previous or previous.get('destination') != str(destination) or previous.get('state') != actual):
            raise ValueError('unmanaged or modified CLI launcher collision')
        if previous and previous.get('state') != actual:
            raise ValueError('managed CLI launcher drift')
        if snapshot.exists():
            try:
                receipt, expected = existing_anchor(state_root, snapshot, receipt, expected,
                                                    origin['interpreter']['executable'])
            except ValueError as exc:
                raise ValueError('CLI snapshot corrupt: '+str(exc)) from exc
            _verify(snapshot, expected)
        else:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='.cli-', dir=snapshot.parent))
            try:
                for name, raw in content.items():
                    D.atomic(stage / name, raw, files[name]['mode'])
                write_json(stage / 'origin.json', origin)
                write_json(stage / 'receipt.json', receipt)
                _verify(stage, expected)
                os.rename(stage, snapshot)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        verify_origin_data(origin, content)
        request = {'mode': 'snapshot', 'root': str(snapshot), 'expected': expected, 'interpreter': origin['interpreter']['executable']}
        launcher = validator_launcher(request)
        R.safe_absolute(destination)
        if T.state(destination) != actual:
            raise ValueError('CLI launcher changed before publication')
        D.atomic(destination, launcher, 0o755)
        result = {'schema_version': 1, 'destination': str(destination), 'state': T.state(destination),
                  'snapshot': str(snapshot), 'receipt': receipt, 'validator_binding': expected,
                  'bootstrap_observation': observation}
        write_json(owner_path, result)
        return result
