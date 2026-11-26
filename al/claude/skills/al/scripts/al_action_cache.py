"""AL action cache: check results reused across runs, tasks and worktrees (issue #26).

A check (`evidence run`, `gates run --stage base|candidate`) is a pure function of its
inputs when it is hermetic. Its result is stored under a key that hashes everything it
depends on:

  * argv, cwd relative to the repository, the check kind and its declared scope/sources;
  * the declared environment (value digests, never values) plus the environment policy
    (inherited for `evidence run`, scrubbed installation switches for gates);
  * tool identity: the resolved executable and its content hash, plus the hashes of the
    toolchain lockfiles (`uv.lock`, `pnpm-lock.yaml`, ...) from the repository root down
    to the check's cwd;
  * the input tree: the git tree identity of the check's declared scope plus its package
    (the #21 rule), or the whole repository tree when the package is the root.

Any change to any of these is a different key, so a miss. The store lives in the git
common directory, so every worktree of the repository shares it. Records are immutable
and content-addressed (the full log is a blob named by its SHA-256), written lock-free
with an atomic rename, and LRU-bounded (a hit refreshes the record's mtime).

Only exit status, counts, note and the log are reused. INFRA, TORN, timed-out, interrupted
or cleanup-uncertain results are never stored; FAIL and EMPTY are, so a failing base gate
is not re-run. `--no-cache`, a non-hermetic check (`--non-hermetic`, or a gate with
`"hermetic": false`) and `AL_ACTION_CACHE=0` disable lookups and writes.

A cache hit never establishes more than an execution would: it creates an ordinary ledger
entry (reserved, writer-admitted and fingerprinted like a run) whose log is the original
log and which records `action_cache.origin` — the run, session and entry that executed it.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import al_store
from al_verification_policy import execution_policy

SCHEMA = 1
STOREABLE = frozenset({'PASS', 'FAIL', 'EMPTY'})
DEFAULT_MAX_RECORDS = 4096
# Files that pin a toolchain or its dependency closure.
LOCKFILES = ('uv.lock', 'poetry.lock', 'Pipfile.lock', 'pdm.lock', 'package-lock.json', 'npm-shrinkwrap.json',
             'pnpm-lock.yaml', 'yarn.lock', 'bun.lock', 'bun.lockb', 'Cargo.lock', 'go.sum', 'Gemfile.lock',
             'composer.lock', 'mix.lock', 'pubspec.lock', 'Package.resolved', 'gradle.lockfile', 'flake.lock',
             '.tool-versions', '.python-version', '.nvmrc', '.node-version', 'rust-toolchain', 'rust-toolchain.toml')
RESULT_FIELDS = ('status', 'exit', 'note', 'counts')
GATE_EXTRA_FIELDS = ('setup', 'hook_status', 'execution_stage')


class Uncacheable(Exception):
    """The check's inputs cannot be bounded exactly; it runs without the cache."""


def enabled():
    return os.environ.get('AL_ACTION_CACHE', '1') != '0'


def _git(*args, env=None, cwd=None, text=False):
    try:
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd or al_store.REPO_ROOT,
                                capture_output=True, env=env, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Uncacheable('git ' + args[0] + ' failed: ' + type(exc).__name__) from exc
    if result.returncode != 0:
        raise Uncacheable('git ' + args[0] + ' exited ' + str(result.returncode))
    return result.stdout.decode(errors='surrogateescape') if text else result.stdout


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# ---------------------------------------------------------------------------------------
# Input tree: the #25 snapshot layer (al_snapshot) captures the working copy as a git tree
# through a private per-worktree index; this module only consumes `input_tree(st, paths)`.
# ---------------------------------------------------------------------------------------

def _minimal(paths):
    """Paths without the ones already covered by an ancestor; '.' covers everything."""
    ordered = sorted({os.path.normpath(p) for p in paths})
    if '.' in ordered:
        return ['.']
    out = []
    for path in ordered:
        if not any(path == kept or path.startswith(kept + '/') for kept in out):
            out.append(path)
    return out


def _bounded(tree):
    """The tree may vouch for the check's inputs only when nothing in it leads elsewhere:
    a nested repository's working copy or a symlink out of the repository is not in it."""
    import al_snapshot
    root = al_store.REPO_ROOT.resolve()
    for path, (mode, _, _, _) in al_snapshot.entries(root, tree).items():
        if mode == '160000':
            raise Uncacheable('a nested repository is part of the inputs')
        if mode == '120000':
            target = (root / path).resolve()
            if target != root and root not in target.parents:
                raise Uncacheable('a symlink in the inputs leads outside the repository')
    return tree


def input_tree(st, paths):
    """Git tree hash of the working copy under `paths` (al_snapshot.input_tree; every tracked
    file is a member, wherever it lives). Raises Uncacheable when it cannot be taken or
    bounded exactly."""
    import al_snapshot
    try:
        return _bounded(al_snapshot.input_tree(st, _minimal(paths)))
    except (al_snapshot.Unavailable, OSError, ValueError) as exc:
        raise Uncacheable('input snapshot unavailable: ' + str(exc)) from exc


def commit_tree(oid, paths):
    """The same tree hash for a commit's content under `paths` (a base gate runs on the base
    commit). Every tracked file is a snapshot member, so an unchanged candidate shares the
    base's key."""
    import al_snapshot
    paths = _minimal(paths)
    root = al_store.REPO_ROOT
    try:
        listing = al_snapshot._run(root, ['ls-tree', '-r', '-z', '--full-tree', oid,
                                          *([] if paths == ['.'] else ['--', *paths])])
        rows = [row + b'\0' for row in listing.split(b'\0') if row]
        target = al_snapshot.index_path(root).with_name(al_snapshot.INDEX + '.commit.' + str(os.getpid()))
        try:
            env = {'GIT_INDEX_FILE': str(target)}
            al_snapshot._run(root, ['read-tree', '--empty'], env=env)
            al_snapshot._run(root, ['update-index', '-z', '--index-info'], env=env, data=b''.join(rows))
            return _bounded(al_snapshot._run(root, ['write-tree'], env=env).decode().strip())
        finally:
            if target.exists():
                target.unlink()
    except (al_snapshot.Unavailable, OSError, ValueError) as exc:
        raise Uncacheable('base tree unavailable: ' + str(exc)) from exc


# ---------------------------------------------------------------------------------------
# Tool identity
# ---------------------------------------------------------------------------------------

_FILE_DIGESTS = {}


def _file_digest(path):
    info = os.stat(path)
    identity = (path, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if identity not in _FILE_DIGESTS:
        h = hashlib.sha256()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                h.update(chunk)
        _FILE_DIGESTS[identity] = h.hexdigest()
    return _FILE_DIGESTS[identity]


def program(argv):
    """The executable an argv runs: `env NAME=VALUE ... PROGRAM` names PROGRAM."""
    tokens = list(argv)
    if tokens[:1] == ['env']:
        tokens = tokens[1:]
        while tokens and ('=' in tokens[0] or tokens[0].startswith('-')):
            tokens = tokens[1:]
    return tokens[0] if tokens else None


def tool_identity(argv, cwd, path=None):
    name = program(argv)
    if not name:
        return {'program': None}
    if os.sep in name:
        candidate = name if os.path.isabs(name) else os.path.join(str(al_store.REPO_ROOT / cwd), name)
        resolved = candidate if os.path.isfile(candidate) else None
    else:
        resolved = shutil.which(name, path=path if path is not None else os.environ.get('PATH', os.defpath))
    if not resolved:
        return {'program': name, 'resolved': None}
    real = os.path.realpath(resolved)
    try:
        return {'program': name, 'resolved': real, 'sha256': _file_digest(real)}
    except OSError as exc:
        raise Uncacheable('cannot read the executable ' + real) from exc


def lockfiles(cwd):
    """Hashes of the toolchain lockfiles from the repository root down to cwd."""
    root = al_store.REPO_ROOT
    parts = Path(cwd or '.').parts
    out = {}
    for depth in range(len(parts) + 1):
        directory = Path(*parts[:depth]) if depth else Path('.')
        for name in LOCKFILES:
            target = root / directory / name
            if target.is_file():
                try:
                    out[(directory / name).as_posix()] = _file_digest(str(target))
                except OSError as exc:
                    raise Uncacheable('cannot read ' + str(target)) from exc
    return out


# ---------------------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------------------

class Plan:
    """One check's cache key, computed before it runs."""

    def __init__(self, mode, key, inputs, tree_of):
        self.mode, self.key, self.inputs, self._tree_of = mode, key, inputs, tree_of
        self.digest = digest(key)

    def inputs_unchanged(self):
        try:
            return self._tree_of() == self.key['inputs']['tree']
        except Uncacheable:
            return False


def _env(names):
    from al_environment_observation import value_digest
    return {name: value_digest(name) for name in sorted(set(names or ()))}


def _execution_environment(names):
    """The policy an execution receipt binds: declared names plus CI, PATH, HOME, LANG, TZ, PYTHON* and LC_*.

    It is part of the key (a different interpreter setup is a miss) and is recorded on a hit, so a later
    reader (terminal validation) refuses the reused result when the caller's environment changed."""
    from al_verification_inputs import execution_environment
    declared = sorted(set(names or ()))
    return {'kind': 'declared-execution-v1', 'declared_names': declared, 'inputs': execution_environment(declared)}


def environment_gaps(entry):
    """Current-environment gaps of a reused result (none for an entry without a recorded policy)."""
    policy = (entry.get('action_cache') or {}).get('environment')
    if policy is None:
        return []
    from al_verification_inputs import environment_gaps as gaps
    return gaps(policy)


def _input_paths(scope, cwd):
    import al_ledger as L
    return _minimal([L.inputs_root(cwd or '.'), *(scope or ())])


def evidence_plan(st, spec, *, no_cache=False):
    """(Plan, None) for a cacheable `evidence run`, else (None, reason)."""
    if not enabled():
        return None, 'AL_ACTION_CACHE=0'
    if no_cache:
        return None, '--no-cache'
    if spec.get('hermetic') is False:
        return None, 'the check is declared non-hermetic'
    if spec.get('recipe') is not None:
        return None, 'recipe checks are not cached'
    import al_evidence_storage
    storage = al_evidence_storage.normalize_declaration(spec.get('storage'))
    if storage is not None and (storage.get('outputs') or storage.get('operands')):
        return None, 'the check declares managed outputs'
    try:
        paths = _input_paths(spec['scope'], spec.get('cwd'))
        tree_of = lambda: input_tree(st, paths)  # noqa: E731
        key = {'schema': SCHEMA, 'mode': 'evidence', 'argv': list(spec['argv']), 'cwd': spec.get('cwd') or '.',
               'kind': spec.get('kind'), 'scope': sorted(spec['scope']), 'sources': spec.get('sources', []),
               'storage': storage, 'env': _env(spec.get('env')), 'env_policy': {'mode': 'inherited'},
               'execution_environment': _execution_environment(spec.get('env')),
               'execution_policy': execution_policy(),
               'tool': tool_identity(spec['argv'], spec.get('cwd') or '.'), 'lockfiles': lockfiles(spec.get('cwd')),
               'inputs': {'paths': paths, 'tree': tree_of()}}
    except Uncacheable as exc:
        return None, str(exc)
    return Plan('evidence', key, paths, tree_of), None


def gate_plan(st, m, g, stage, *, no_cache=False):
    """(Plan, None) for a cacheable gate execution, else (None, reason)."""
    if not enabled():
        return None, 'AL_ACTION_CACHE=0'
    if no_cache:
        return None, '--no-cache'
    if g.get('hermetic') is False:
        return None, 'the gate is declared non-hermetic'
    if g.get('execution_kind') != 'local' or g.get('package_script') or g.get('review_state'):
        return None, 'remote, package-script and review-state gates are not cached'
    import al_pytest_policy as PytestPolicy
    if PytestPolicy.gate_requirements(m, g):
        return None, 'pytest guard proof needs its own execution'
    from al_gate_process import INSTALL_ENV, gate_environment
    try:
        paths = _input_paths(g['scope'], g['cwd'])
        if stage == 'base':
            tree_of = lambda: commit_tree(m['base_oid'], paths)  # noqa: E731
        else:
            tree_of = lambda: input_tree(st, paths)  # noqa: E731
        environment, scrubbed = gate_environment(g['env_names'])
        key = {'schema': SCHEMA, 'mode': 'gate', 'argv': list(g['argv']), 'cwd': g['cwd'], 'kind': 'check',
               'category': g.get('category'), 'variant': stage if g.get('category') == 'loc' else None,
               'scope': sorted(g['scope']), 'sources': g.get('sources', []), 'env': _env(g['env_names']),
               'env_policy': {'mode': 'scrubbed', 'removed_names': sorted(INSTALL_ENV), 'removed': scrubbed},
               'execution_environment': _execution_environment(g['env_names']),
               'execution_policy': execution_policy(),
               'tool': tool_identity(g['argv'], g['cwd'], environment.get('PATH')), 'lockfiles': lockfiles(g['cwd']),
               'inputs': {'paths': paths, 'tree': tree_of()}}
    except Uncacheable as exc:
        return None, str(exc)
    return Plan('gate', key, paths, tree_of), None


# ---------------------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------------------

def store_root():
    override = os.environ.get('AL_ACTION_CACHE_DIR')
    if override:
        return Path(override)
    common = _git('rev-parse', '--path-format=absolute', '--git-common-dir', text=True).strip()
    return Path(common) / 'al-action-cache'


def _max_records():
    try:
        return max(1, int(os.environ.get('AL_ACTION_CACHE_MAX', DEFAULT_MAX_RECORDS)))
    except ValueError:
        return DEFAULT_MAX_RECORDS


def _log_sha(path):
    """The ledger's own log digest (content plus executable bit), so a restored log
    verifies exactly like the original receipt."""
    import al_ledger as L
    return L.file_sha(path)


def _record_path(root, key_digest):
    return root / 'actions' / key_digest[:2] / (key_digest + '.json')


def _blob_path(root, sha):
    return root / 'blobs' / sha[:2] / sha


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name[:16] + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def lookup(plan):
    """The stored record for this key, or None. A foreign, corrupt or incomplete record
    (its log blob gone or altered) is a miss."""
    if plan is None or not enabled():
        return None
    try:
        root = store_root()
        path = _record_path(root, plan.digest)
        if path.is_symlink() or not path.is_file():
            return None
        record = json.loads(path.read_bytes())
        if (not isinstance(record, dict) or record.get('schema') != SCHEMA or record.get('key_digest') != plan.digest
                or record.get('key') != plan.key or record.get('status') not in STOREABLE):
            return None
        blob = _blob_path(root, record['log_sha256'])
        if blob.is_symlink() or not blob.is_file() or _log_sha(blob) != record['log_sha256']:
            return None
        os.utime(path)  # LRU: a hit keeps the record young.
        record['blob'] = str(blob)
        return record
    except (OSError, ValueError, TypeError, KeyError, Uncacheable, RuntimeError):
        return None


def save(plan, entry, log, origin, extra=None, replace=False):
    """Record a finished execution. Returns True when it was stored. A record is never
    rewritten, except by an explicit re-execution (`--fresh`, `--retry-failed`), whose
    newer result replaces it."""
    if plan is None or not enabled() or entry.get('status') not in STOREABLE or entry.get('interrupted'):
        return False
    cleanup = entry.get('cleanup') or {}
    if cleanup.get('unresolved') or cleanup.get('errors') or 'execution_association' in entry or 'reuse' in entry:
        return False
    if 'timed out' in (entry.get('note') or '') or 'timeout' in (entry.get('note') or ''):
        return False
    if not plan.inputs_unchanged():
        return False  # The inputs moved while it ran: the result describes no single tree.
    try:
        root = store_root()
        path = _record_path(root, plan.digest)
        if path.is_file() and not replace:
            return False  # Immutable: the first record for a key stays.
        sha = _log_sha(log)
        if sha != entry.get('log_sha256'):
            return False
        blob = _blob_path(root, sha)
        if not blob.is_file():
            _atomic_write(blob, Path(log).read_bytes())
            if _log_sha(blob) != sha:
                blob.unlink(missing_ok=True)
                return False
        import al_runtime_resources as Resources
        started, ended = entry.get('started_at'), entry.get('ended_at')
        record = {'schema': SCHEMA, 'key_digest': plan.digest, 'key': plan.key,
                  **{name: entry.get(name) for name in RESULT_FIELDS},
                  'extra': {name: entry[name] for name in (extra or ()) if name in entry},
                  'log_sha256': sha, 'origin': origin,
                  'timing': {'started_at': started, 'ended_at': ended,
                             'seconds': round(ended - started, 3) if started and ended else None},
                  'al_version': Resources.implementation_digest(), 'recorded_at': time.time()}
        _atomic_write(path, json.dumps(record, sort_keys=True, separators=(',', ':')).encode())
        _prune(root)
        return True
    except (OSError, ValueError, TypeError, KeyError, Uncacheable, RuntimeError):
        return False


def _prune(root):
    """Keep the most recently used records; drop logs no record references."""
    limit = _max_records()
    records = list((root / 'actions').glob('*/*.json'))
    if len(records) <= limit:
        return
    def age(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0
    records.sort(key=age, reverse=True)
    keep = max(1, int(limit * 0.9))
    for stale in records[keep:]:
        stale.unlink(missing_ok=True)
    referenced = set()
    for path in records[:keep]:
        try:
            referenced.add(json.loads(path.read_bytes())['log_sha256'])
        except (OSError, ValueError, TypeError, KeyError):
            continue
    for blob in (root / 'blobs').glob('*/*'):
        if blob.name not in referenced and not blob.name.startswith('.'):
            blob.unlink(missing_ok=True)


def origin(st, sid, entry):
    return {'run': entry.get('run') or st.get('run_id'), 'session': sid, 'entry': entry['id'],
            'area': entry.get('area'), 'worktree': str(al_store.REPO_ROOT)}


def restore_log(record, destination):
    """Copy the original log; returns its SHA-256 (verified against the record)."""
    shutil.copyfile(record['blob'], destination)
    sha = _log_sha(destination)
    if sha != record['log_sha256']:
        raise OSError('cached log changed while it was copied')
    return sha


def provenance(plan, record):
    return {'key': plan.digest, 'origin': record['origin'], 'recorded_at': record.get('recorded_at'),
            'al_version': record.get('al_version'), 'original_seconds': (record.get('timing') or {}).get('seconds'),
            'tree': plan.key['inputs']['tree'], 'environment': plan.key['execution_environment']}


def reused_text(entry):
    cached = entry.get('action_cache')
    if not cached:
        return ''
    source = cached.get('origin') or {}
    tree = cached.get('tree')
    return ('reused from run ' + str(source.get('run')) + ' (' + str(source.get('entry')) + ')'
            + (' on the same input tree ' + tree[:12] if tree else ''))


def annotate_packet(st, data):
    """The review packet shows every reused result with the run where it executed."""
    reused = reused_results(st)
    if reused:
        data['reused_results'] = reused
    return data


def reused_results(st):
    """Ledger entries whose result came from the action cache, for the review packet."""
    import al_ledger as L
    return [{'id': e['id'], 'area': e['area'], 'status': e['status'], 'argv': e['argv'], 'cwd': e['cwd'],
             'reused_from': e['action_cache']['origin'], 'key': e['action_cache']['key']}
            for e in L.ledger(st)['entries'] if e.get('action_cache')]
