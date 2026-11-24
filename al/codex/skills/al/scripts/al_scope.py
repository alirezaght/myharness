"""Content-bound repository identity and complete candidate capture."""
import hashlib
import contextlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
import al_store
import al_gitlinks as G
from al_profile import measured
from al_schema import refuse

def git_bytes(*args, root=None):
    from al_analysis_cache import CURRENT
    context = CURRENT.get()
    options = ['-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null']
    if context and context.readonly_git:options += ['-c', 'diff.autoRefreshIndex=false']
    if args and args[0]=='diff':args=(args[0],'--no-ext-diff','--no-textconv',*args[1:])
    r = subprocess.run(['git', '--no-optional-locks', '--literal-pathspecs', *options, *args], cwd=root or al_store.REPO_ROOT, capture_output=True)
    if r.returncode: refuse('git failed: ' + r.stderr.decode(errors='replace').strip())
    return r.stdout

_IDENTITIES = {}


def _identity_key(root, target):
    """Identity answers for an exact target and HEAD object id are fixed while metadata and replacement refs are."""
    metadata = G.plain_metadata(root)
    if metadata is None or not G.HEX.fullmatch(target.encode()):
        return None
    head = G._head(metadata)
    if head is None:
        return None
    state = metadata.lstat()
    return str(root), str(metadata), (state.st_dev, state.st_ino), target, head, G.replacement_state(metadata)


def identity(target='HEAD'):
    root = al_store.REPO_ROOT.resolve()
    key = _identity_key(root, target)
    if key is not None and key in _IDENTITIES:
        common, git_dir, oid, merge_base = _IDENTITIES[key]
    else:
        values = git_bytes('rev-parse', '--git-common-dir', '--git-dir', '--end-of-options', target + '^{commit}', 'HEAD^{commit}').decode().splitlines()
        if len(values) != 5 or values[2] != '--end-of-options' or not all(values): refuse('invalid repository identity response')
        common, git_dir, _, oid, head = values
        if len(oid) not in {40, 64} or len(head) != len(oid) or not all(re.fullmatch(r'[0-9a-f]+', value) for value in (oid, head)):
            refuse('invalid repository identity response')
        merge_base = oid if oid == head else git_bytes('merge-base', 'HEAD', oid).decode().strip()
        if key is not None and key[4] == head and _identity_key(root, target) == key:
            if len(_IDENTITIES) >= 8:_IDENTITIES.clear()
            _IDENTITIES[key] = (common, git_dir, oid, merge_base)
    return {'root': str(root), 'cwd': str(Path.cwd().resolve()),
            'common_dir': str((root / common).resolve()),
            'git_dir': str((root / git_dir).resolve()),
            'target': target, 'target_oid': oid,
            'merge_base': merge_base}

def validate_identity(st):
    expected = st['identity']
    actual = identity(expected['target_oid'])
    for key in ('root','cwd','common_dir','git_dir'):
        if actual[key] != expected[key]: refuse('pinned repository/cwd mismatch: ' + key)

def safe_path(raw):
    p = Path(os.fsdecode(raw))
    if p.is_absolute() or '..' in p.parts or not p.parts: refuse('unsafe candidate path')
    return p

def source_rows(root=None, base='HEAD'):
    return _observed('source-rows', base, lambda: _source_rows(root, base), root)


def _source_rows(root=None, base='HEAD'):
    root=root or al_store.REPO_ROOT
    try:
        index=G.index_entries(root);head=G.tree_entries(root,'HEAD')
        baseline=head if base=='HEAD' else G.tree_entries(root,base)
        _validate_conflict_types(root, index, baseline, head)
        out=[]
        for raw in sorted(G.paths(baseline,head,index)):
            obs=G.observe(root,raw,baseline.get(raw),head.get(raw),index.get(raw))
            out.append({'path':os.fsdecode(raw),'base':obs['base'],'head':obs['head'],'index':obs['index'],'child':obs['child'],
                        'worktree':obs['effective'] or {'type':'replaced'}})
        return out
    except G.GitlinkError as exc:refuse(str(exc))


_BLOB_SHA256 = {}


def _blob_hashes(root, oids):
    """Content SHA-256 per blob id; a blob id names immutable content, so one process hashes it once."""
    from al_git_cache import object_binding
    try:
        binding = tuple(object_binding(root))
    except (OSError,ValueError,subprocess.SubprocessError) as exc:refuse('indexed blob identity unavailable: '+str(exc))
    oids = list(dict.fromkeys(oids))
    known = {oid: _BLOB_SHA256[(binding, oid)] for oid in oids if (binding, oid) in _BLOB_SHA256}
    # A blob's sha256 is also a fact of the repository's shared store (#25).
    import al_analysis_index as index
    from al_snapshot import BLOB_SHA256
    pending = [oid for oid in oids if oid not in known]
    stored = index.lookup('git-blob-sha256', BLOB_SHA256, pending, root=root) if pending else {}
    known.update({pending[i]: v for i, v in stored.items() if isinstance(v, str) and len(v) == 64})
    computed = _blob_hashes_uncached(root, [oid for oid in oids if oid not in known])
    if computed:
        index.record('git-blob-sha256', BLOB_SHA256, list(computed.items()), root=root)
    for oid, value in computed.items():
        _BLOB_SHA256[(binding, oid)] = value
    return {**known, **computed}


@measured('indexed-blob-hashing')
def _blob_hashes_uncached(root, oids):
    """Consume bounded cat-file frames; never buffer the request or blob set."""
    result = {}
    unique = list(dict.fromkeys(oids))
    if not unique: return result
    from al_git_cache import object_binding, lookup, store
    try:
        binding=object_binding(root)
        misses=[]
        for oid in unique:
            value=lookup(binding,oid)
            if value is None:misses.append(oid)
            else:result[oid]=hashlib.sha256(value).hexdigest()
    except (OSError,ValueError,subprocess.SubprocessError) as exc:refuse('indexed blob identity unavailable: '+str(exc))
    if not misses:return result
    from al_analysis_cache import CURRENT
    observation = CURRENT.get()
    errors_context = contextlib.nullcontext(subprocess.DEVNULL) if observation and observation.readonly_git else tempfile.TemporaryFile()
    with errors_context as errors:
        try:
            proc = subprocess.Popen(['git', '--no-optional-locks', 'cat-file', '--batch'],
                                    cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:refuse('indexed blob batch unavailable: '+str(exc))
        try:
            for oid in misses:
                proc.stdin.write(oid.encode('ascii') + b'\n'); proc.stdin.flush()
                frame = proc.stdout.readline(256)
                header = frame.split()
                if not frame.endswith(b'\n') or len(header) != 3 or header[0] != oid.encode('ascii') or header[1] != b'blob' or not header[2].isdigit():
                    refuse('invalid indexed blob batch frame')
                remaining = int(header[2]); digest = hashlib.sha256()
                object_digest=hashlib.new(binding[2],b'blob '+header[2]+b'\0'); cached=[] if remaining<=2*1024*1024 else None
                while remaining:
                    chunk = proc.stdout.read(min(remaining, 1 << 20))
                    if not chunk: refuse('truncated indexed blob batch frame')
                    digest.update(chunk); object_digest.update(chunk); remaining -= len(chunk)
                    if cached is not None:cached.append(chunk)
                if proc.stdout.read(1) != b'\n': refuse('invalid indexed blob batch terminator')
                if object_digest.hexdigest()!=oid:refuse('indexed blob object identity mismatch')
                if cached is not None:store(binding,oid,b''.join(cached))
                result[oid] = digest.hexdigest()
            proc.stdin.close()
            if proc.stdout.read(1) or proc.wait() != 0: refuse('indexed blob batch process failed')
        except OSError as exc:refuse('indexed blob batch failed: '+str(exc))
        finally:
            if proc.poll() is None: proc.kill()
            proc.wait()
            with contextlib.suppress(OSError):
                if not proc.stdin.closed: proc.stdin.close()
            with contextlib.suppress(OSError): proc.stdout.close()
    return result


def _observed(namespace, key, compute, root=None):
    """Within a decision/inspection window, reuse one read of the worktree; elsewhere read fresh."""
    from al_analysis_cache import CURRENT
    context = CURRENT.get()
    if context is None or (root is not None and Path(root) != al_store.REPO_ROOT):
        return compute()
    return context.get_or_compute(namespace, [str(al_store.REPO_ROOT), key], compute)


def _decision_base(st):
    return st['identity']['merge_base'] if st.get('review_mode') == 'whole-mr' else st['baseline_head']


def decision(fn):
    """Evaluate a read-only decision against one worktree observation.

    The worktree is read once before and once after; inside, repeated reads reuse the first
    observation. If the worktree moved (or cannot be observed) the decision is evaluated again
    exactly as without memoization, so a stable decision is identical to a fresh one. Decorated
    functions must be read-only: an unstable window runs their body twice."""
    import functools

    @functools.wraps(fn)
    def wrapper(st, *args, **kwargs):
        from al_analysis_cache import CURRENT, ObservationContext
        if CURRENT.get() is not None:
            return fn(st, *args, **kwargs)
        base = _decision_base(st)
        import al_attribution_tree as tree
        try:
            opening = tree.open_observation(base)
        except SystemExit:
            opening = None
        if opening is not None:
            done, result = _observed_decision(fn, opening, base, st, args, kwargs, tree.chained(opening))
            if done:
                return result
            # Something moved since the previous bracket closed: one window from a fresh observation.
            try:
                opening = tree.fresh_observation(base)
            except SystemExit:
                opening = None
            if opening is not None:
                return _observed_decision(fn, opening, base, st, args, kwargs, False)[1]
            return fn(st, *args, **kwargs)
        try:
            before = _records(base, al_store.REPO_ROOT)
        except (OSError, ValueError, SystemExit):
            return fn(st, *args, **kwargs)
        failure = None
        with ObservationContext({'decision': [str(al_store.REPO_ROOT), base, json.dumps(before, sort_keys=True)]}) as context:
            context.get_or_compute('records', [str(al_store.REPO_ROOT), base], lambda: before)
            try:
                result = fn(st, *args, **kwargs)
            except (Exception, SystemExit) as exc:
                failure = exc
        try:
            stable = _records(base, al_store.REPO_ROOT) == before
        except (OSError, ValueError, SystemExit):
            stable = False
        if not stable:
            return fn(st, *args, **kwargs)
        if failure is not None:
            raise failure
        return result
    return wrapper


def _observed_decision(fn, opening, base, st, args, kwargs, retry):
    """A decision window bracketed by two observations of the working copy (snapshot tree plus
    the state a tree does not record, al_attribution_tree). Equal observations prove nothing the
    decision read moved; otherwise it is evaluated again exactly as without memoization.
    Returns (done, result); with `retry` an unstable window is not re-run here (done False)."""
    import al_attribution_tree as tree
    from al_analysis_cache import ObservationContext
    failure = None
    binding = [str(al_store.REPO_ROOT), base, opening.key()]
    with ObservationContext({'decision': binding}) as context:
        # Windows of this command that observed exactly this working copy (equal opening and
        # closing observations) answered their questions about it: those answers carry over.
        carried = _WINDOWS.get(json.dumps(binding))
        if carried is not None:
            context.local.update(carried)
        with tree.bracket(opening) as state:
            try:
                result = fn(st, *args, **kwargs)
            except (Exception, SystemExit) as exc:
                failure = exc
    try:
        stable = tree.stable(opening, base, (), state)
    except SystemExit:
        stable = False
    if not stable:
        return (False, None) if retry else (True, fn(st, *args, **kwargs))
    _WINDOWS.clear()
    # Only answers that are functions of what the observation saw carry over; a membership
    # listing or an inventory also reads ignored paths, which an observation does not record.
    _WINDOWS[json.dumps(binding)] = {k: v for k, v in context.local.items() if k[0] in _CARRIED}
    if failure is not None:
        raise failure
    return True, result


# The answers of the latest stable decision window, by the observation it opened and closed on.
_WINDOWS = {}
_CARRIED = ('records', 'task-classification', 'candidate', 'candidate-fingerprint', 'source-rows')


def records(base,root=None):
    def compute():
        import al_attribution_tree as tree
        return tree.records(str(Path(root) if root else al_store.REPO_ROOT), base, lambda reuse: _records(base, root, reuse))
    return _observed('records', base, compute, root)


# Process-local worktree content digests keyed by full stat identity. One command observes
# the worktree several times (decision windows read it before and after); unchanged files are
# hashed once. Recently modified files are never reused (racy-clean, as in Git's index).
_DIGESTS = {}
_DIGEST_RACY_NS = 2_000_000_000


def _file_digest(p):
    import time
    before = p.stat()
    key = (str(p), before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    cached = _DIGESTS.get(key)
    if cached is not None:
        return before.st_mode, cached
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    after = p.stat()
    stable = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns) == key[1:]
    if stable and time.time_ns() - max(after.st_mtime_ns, after.st_ctime_ns) > _DIGEST_RACY_NS:
        if len(_DIGESTS) > 200_000:
            _DIGESTS.clear()
        _DIGESTS[key] = sha
    return before.st_mode, sha


def _worktree_digest(p, vouched, settled):
    """(mode, sha256) of a worktree file: the snapshot's blob hash when the file is a regular
    file of the blob's size unchanged since before the racy window, else its bytes."""
    if vouched is not None:
        import stat as stat_module
        value = os.lstat(p)
        if (stat_module.S_ISREG(value.st_mode) and value.st_size == vouched[1]
                and max(value.st_mtime_ns, value.st_ctime_ns) < settled):
            return value.st_mode, vouched[0]
    return _file_digest(p)


def _records(base,root=None,reuse=None):
    """Candidate records of every member. `reuse` (records by path, changed paths) takes over the
    record of each path whose observed state did not change (al_attribution_tree.records)."""
    import al_writer_progress as progress
    progress.report('scope-index', count=0)
    root = root or al_store.REPO_ROOT
    try:
        index = G.index_entries(root)
        base_entries = G.tree_entries(root, base)
        head = G.tree_entries(root, 'HEAD')
        _validate_conflict_types(root, index, base_entries, head)
        observations = {raw: G.observe(root, raw, base_entries.get(raw), head.get(raw), index.get(raw))
                        for raw in G.paths(base_entries, head, index)}
    except G.GitlinkError as exc:
        refuse(str(exc))
    reused, changed = reuse if reuse is not None and not observations else ({}, set())
    def taken(raw):
        path = os.fsdecode(raw.rstrip(b'/'))
        return path in reused and path not in changed
    needed = {raw: entry for raw, entry in index.items() if not taken(raw)}
    hashes = _blob_hashes(root, (e['oid'] for entry in needed.values() for e in entry.get('stages', [entry]) if e['mode'] != '160000'))
    indexed = {}
    for count,(raw, entry) in enumerate(needed.items(),1):
        if 'stages' in entry:
            indexed[raw] = {'stages': [{**e, 'sha256': hashes[e['oid']]} for e in entry['stages']]}
        else:
            indexed[raw] = dict(entry)
            if entry['mode'] != '160000':
                indexed[raw]['sha256'] = hashes[entry['oid']]
        progress.report('scope-index', count=count, total=len(index))
    progress.report('scope-index', count=len(index), force=True)
    opaque = {raw for raw, obs in observations.items() if G.opaque(obs)}
    raw_paths = set(indexed) | set(base_entries) | set(head)
    raw_paths.update(x for x in git_bytes('ls-files','--others','--exclude-standard','-z',root=root).split(b'\0') if x)
    raw_paths = {raw.rstrip(b'/') for raw in raw_paths}
    out = []
    symlinked = {}
    def linked(parts):
        # Each directory is checked once per observation, not once per file beneath it.
        key = parts
        if key not in symlinked:symlinked[key] = (root / Path(*parts)).is_symlink()
        return symlinked[key]
    # Working-copy content the private snapshot tree vouches for is not re-read (#25).
    import time
    settled = time.time_ns() - _DIGEST_RACY_NS
    from al_snapshot import vouched as snapshot_vouched
    vouched = snapshot_vouched(root, (lambda path: path not in reused or path in changed) if reused else (lambda path: True))
    progress.report('scope-worktree', count=0, total=len(raw_paths))
    for raw in sorted(raw_paths):
        if any(raw.rstrip(b'/') != parent and raw.startswith(parent + b'/') for parent in opaque):
            continue
        if taken(raw):
            out.append(reused[os.fsdecode(raw.rstrip(b'/'))])
            continue
        raw = raw.rstrip(b'/')
        rel = safe_path(raw); p = root / rel
        if any(linked(rel.parts[:i]) for i in range(1,len(rel.parts))):
            refuse('candidate path traverses symlink')
        row = {'path': os.fsdecode(raw), 'index': indexed.get(raw)}
        obs = observations.get(raw)
        if obs:
            row.update(base=obs['base'], head=obs['head'], child=obs['child'])
        if obs and obs['effective'] is not None:
            row['worktree'] = obs['effective']
        elif p.is_symlink(): row['worktree'] = {'type':'symlink','text':os.readlink(p)}
        elif p.is_file():
            mode, sha = _worktree_digest(p, vouched.get(rel.as_posix()), settled)
            row['worktree'] = {'type':'file','executable':bool(mode & 0o111),'sha256':sha}
        elif not p.exists() or (obs and p.is_dir()): row['worktree'] = {'type':'deleted'}
        else: refuse('unsupported candidate file type: ' + str(rel))
        if row['index'] and 'stages' in row['index'] and row['worktree']['type'] not in {'file', 'deleted'}:
            refuse('unmerged worktree type is unsupported')
        out.append(row)
        progress.report('scope-worktree', count=len(out), total=len(raw_paths))
    progress.report('scope-worktree', count=len(out), force=True)
    return out


def _validate_conflict_types(root, index, baseline, head):
    """Only ordinary file stages and an ordinary/deleted parent path are representable."""
    for raw, entry in index.items():
        if 'stages' not in entry:
            continue
        rel = safe_path(raw)
        ancestors = {os.fsencode(str(Path(*rel.parts[:i]))) for i in range(1, len(rel.parts))}
        if any(tree.get(parent, {}).get('mode') in {'120000', '160000'}
               for tree in (baseline, head, index) for parent in ancestors):
            refuse('unmerged index beneath opaque or symlink type boundary is unsupported')
        if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts))):
            refuse('unmerged candidate path traverses symlink')
        if any(e and e.get('mode') not in {'100644', '100755'} for e in (baseline.get(raw), head.get(raw))):
            refuse('unmerged index type transition is unsupported')
        path = root / rel
        if path.is_symlink() or (path.exists() and not path.is_file()):
            refuse('unmerged worktree type is unsupported')


def unresolved_index_gaps(capture):
    """Observable conflict inputs are never a resolved final candidate."""
    return ['unresolved index conflict: ' + row['path'] for row in capture['files']
            if row.get('index') and 'stages' in row['index']]


def parent_representation(rows):
    return {r['path']: r['worktree'] for r in rows if r['worktree']['type'] != 'deleted'}


def child_content_gaps(capture):
    return ['uncaptured submodule work at ' + r['path'] + ': ' + (r['child'].get('error') or 'dirty child contents require an AL run rooted in that child')
            for r in capture['files'] if r.get('child') and (r['child'].get('dirty') or r['child']['state'] == 'inspection-failed')]


def parent_diff(base, *args, root=None, rows=()):
    failed=any(r.get('child',{}).get('state')=='inspection-failed' for r in rows)
    policy='all' if failed else 'none'
    return git_bytes('diff','--ignore-submodules='+policy,*args,base,'--',root=root)


def changed_paths(base, root=None):
    rows=source_rows(root,base)
    opaque={r['path'] for r in rows if G.opaque(r)}
    changed={os.fsdecode(x).rstrip('/') for args in (
        ('diff','--ignore-submodules=none','--name-only','-z',base,'--'),
        ('ls-files','--others','--exclude-standard','-z'))
        for x in (parent_diff(base,'--name-only','-z',root=root,rows=rows) if args[0]=='diff' else git_bytes(*args,root=root)).split(b'\0') if x}
    changed={p for p in changed if not any(p!=parent and p.startswith(parent+'/') for parent in opaque)}
    for row in rows:
        effective=row['worktree']
        entry={k:effective[k] for k in ('mode','oid')} if effective['type']=='gitlink' else None
        if row.get('base')!=entry or row.get('index')!=row.get('base') or row.get('head')!=row.get('base') or row['child'].get('dirty') or row['child']['state']=='inspection-failed':
            changed.add(row['path'])
    return sorted(changed)

def _candidate_key(st):
    return [_decision_base(st), st['identity']['target_oid'], st.get('preexisting_changes', [])]


@measured('candidate-hashing')
def candidate_fingerprint(st):
    """The exact candidate digest without a discarded diff or untracked byte payload."""
    key = _candidate_key(st)
    return _observed('candidate-fingerprint', key, lambda: _candidate_fingerprint(st))


def _candidate_content(st):
    base = _decision_base(st)
    return {'base': base, 'target': st['identity']['target_oid'], 'files': records(base)}


def _candidate_fingerprint(st):
    from al_writer_progress import report
    report('candidate-fingerprint', force=True)
    return hashlib.sha256(json.dumps(_candidate_content(st),sort_keys=True).encode()).hexdigest()


@measured('candidate-hashing')
def capture_candidate(st):
    key = _candidate_key(st)
    return _observed('candidate', key, lambda: _capture_candidate(st))


def _capture_candidate(st):
    content = _candidate_content(st)
    base, rows = content['base'], content['files']
    sha = hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()
    from al_writer_progress import report
    report('candidate-materialization', force=True)
    unresolved = unresolved_index_gaps({'files': rows})
    # Git's combined unmerged diff can refresh the source index. These inputs are
    # recorded exactly but intentionally have no resolved patch to materialize.
    diff = '' if unresolved else parent_diff(base,'--binary',rows=rows).decode(errors='replace')
    diff_limitations = [r['path']+': '+r['child']['error'] for r in rows if r.get('child',{}).get('state')=='inspection-failed']
    diff_limitations += unresolved
    import base64
    untracked = {}
    for row in rows:
        if row['index'] is None and row['worktree']['type'] in {'file', 'symlink'}:
            path = al_store.REPO_ROOT / safe_path(os.fsencode(row['path']))
            untracked[row['path']] = {'encoding':'base64','content':base64.b64encode(os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()).decode()}

    return {'fingerprint':sha,'base':base,'files':rows,'diff':diff,
            'preexisting_changes':st.get('preexisting_changes',[]), 'untracked':untracked, 'diff_limitations':diff_limitations}

def parent_status(root=None):
    rows=source_rows(root)
    ordinary=git_bytes('status','--ignore-submodules=all','--porcelain',root=root).decode(errors='replace').splitlines()
    paths={row['path'] for row in rows}
    lines=[line for line in ordinary if line[3:] not in paths]
    for row in rows:
        index=row['index'];head=row['head'];effective=row['worktree']
        x=' ' if index==head else ('D' if index is None else 'A' if head is None else 'M')
        current={k:effective[k] for k in ('mode','oid')} if effective['type']=='gitlink' else None
        y=' ' if current==index else 'M'
        if row['child'].get('dirty') or row['child']['state'] in {'inspection-failed','replaced'}:y='M'
        if x+y!='  ':lines.append(x+y+' '+row['path'])
    return sorted(lines)


def tree_fingerprint():
    base = git_bytes('rev-parse','HEAD').decode().strip()
    rows = records(base)
    return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(), len(parent_status())


# --- Whole-MR target selection -------------------------------------------------------------

def _probe(*args):
    """A read-only Git answer, or None when Git cannot give one (never a refusal)."""
    try:
        r = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                            *args], cwd=al_store.REPO_ROOT, capture_output=True)
    except OSError:
        return None
    return r.stdout.decode(errors='replace').strip() if r.returncode == 0 else None


def _short(ref):
    for prefix in ('refs/heads/', 'refs/remotes/'):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def _full_name(name):
    """The full ref a revision name denotes (refs/heads/..., refs/remotes/...), or None."""
    if not name or name.startswith('-'):
        return None
    value = _probe('rev-parse', '--symbolic-full-name', name)
    return value.splitlines()[-1] if value else None


def _upstream(branch_ref):
    value = _probe('for-each-ref', '--format=%(upstream)', branch_ref)
    value = value.splitlines()[0] if value else None
    return value if value and value.startswith(('refs/heads/', 'refs/remotes/')) else None


def target_lag(target):
    """How far a target naming a local branch is behind that branch's upstream, else None."""
    if not target or target.startswith('-'):
        return None
    full = _full_name(target)
    if not full or not full.startswith('refs/heads/'):
        return None
    upstream = _upstream(full)
    if not upstream:
        return None
    counts = _probe('rev-list', '--left-right', '--count', full + '...' + upstream)
    try:
        ahead, behind = (int(value) for value in (counts or '').split())
    except ValueError:
        return None
    if not behind:
        return None
    return {'target': target, 'branch': _short(full), 'upstream': _short(upstream), 'behind': behind, 'ahead': ahead}


def target_warning(target, *, command='start'):
    """A loud, non-blocking warning for a stale local target branch (behind only never refuses)."""
    lag = target_lag(target)
    if lag is None:
        return None
    retarget = '' if command == 'retarget' else (
        ' If the run already started, retarget it before review-start: retarget --target ' + lag['upstream'] + '.')
    return ('AL · WARNING · --target ' + target + ' is the local branch ' + lag['branch'] + ', '
            + str(lag['behind']) + ' commit(s) behind its upstream ' + lag['upstream']
            + (' (and ' + str(lag['ahead']) + ' ahead)' if lag['ahead'] else '')
            + '. The merge base, review scope and gate scope will include commits that are already on '
            + lag['upstream'] + '. Use --target ' + lag['upstream'] + ' unless the local branch is intended.'
            + retarget)


def _creation(branch_ref):
    """(source, commit) from the oldest reflog entry when it records how the branch was created."""
    log = _probe('reflog', 'show', '--format=%H %gs', branch_ref)
    if not log:
        return None
    commit, _, subject = log.splitlines()[-1].partition(' ')
    match = re.fullmatch(r'branch: Created from (.+)', subject.strip())
    return (match.group(1).strip(), commit) if match else None


def default_target():
    """(target, explanation) for an omitted whole-MR target, or (None, why it is undeterminable).

    The target is the upstream of the branch the current branch was created from (a fresh worktree
    branch records that source in its reflog). A branch created from a bare commit uses the remote
    default branch only when exactly one remote default branch contains that commit."""
    branch = _probe('symbolic-ref', '-q', 'HEAD')
    if not branch:
        return None, 'HEAD is detached'
    created = _creation(branch)
    if created is None:
        return None, 'the reflog of ' + _short(branch) + ' does not record which branch it was created from'
    source, commit = created
    if source != 'HEAD' and not re.fullmatch(r'[0-9a-f]{7,64}(\^\{commit\})?', source):
        full = _full_name(source)
        if full and full.startswith('refs/remotes/'):
            return _short(full), 'the remote branch ' + _short(branch) + ' was created from'
        if full and full.startswith('refs/heads/') and full != branch:
            upstream = _upstream(full)
            if upstream:
                return _short(upstream), ('the upstream of ' + _short(full) + ', the branch '
                                          + _short(branch) + ' was created from')
            return _short(full), 'the branch ' + _short(branch) + ' was created from (it has no upstream)'
    heads = _probe('for-each-ref', '--format=%(refname) %(symref)', 'refs/remotes') or ''
    defaults = sorted({symref for line in heads.splitlines() for name, _, symref in [line.partition(' ')]
                       if name.endswith('/HEAD') and symref.startswith('refs/remotes/')})
    containing = [ref for ref in defaults if _probe('merge-base', '--is-ancestor', commit, ref) is not None]
    if len(containing) == 1:
        return _short(containing[0]), ('the remote default branch ' + _short(containing[0])
                                       + ', which contains the commit ' + _short(branch) + ' was created from')
    return None, _short(branch) + ' was created from ' + source + ' and no single remote default branch contains it'


# --- Retargeting an existing run -------------------------------------------------------------

def retarget_blockers(st):
    """Why the run's target cannot safely change now; an empty list when it can."""
    out = []
    if st.get('review') is not None or st.get('review_iteration'):
        out.append('review has started and its packets are bound to the current merge base')
    if st.get('writers') or st.get('task_attribution', {}).get('contributions'):
        out.append('implementation writers were registered and their contributions are attributed against the current baseline')
    if 'inherited_lineage' in st:
        out.append('a successor run inherits its target and merge base from its predecessor')
    if 'integration' in st:
        out.append('a Harness execution binds its projections and receipts to the current repository identity')
    if st.get('gate_recovery'):
        out.append('a gate recovery is active')
    if st.get('checkpoints'):
        out.append('recorded recovery checkpoints are bound to the current repository identity')
    from al_writers import operation_gaps
    return out + operation_gaps(st)


def cmd_retarget(a, sid):
    """Change the target before review: recompute the merge base and drop only what depends on it."""
    import copy
    import sys
    import time
    st = al_store.load(sid)
    target = (a.target or '').strip()
    if not target:
        refuse('retarget requires --target REF')
    blockers = retarget_blockers(st)
    if blockers:
        refuse('retarget refused: ' + '; '.join(blockers) + '. Start a new run with --target ' + target + ' instead.')
    old = st['identity']
    new = identity(target)
    for key in ('root', 'cwd', 'common_dir', 'git_dir'):
        if new[key] != old[key]:
            refuse("retarget must run from the run's own checkout and working directory (" + key + ' differs)')
    warning = target_warning(target, command='retarget')
    if warning:
        print(warning)
    if all(new[key] == old[key] for key in ('target', 'target_oid', 'merge_base')):
        print('AL · retarget · unchanged · ' + target + ' · merge base ' + new['merge_base'][:12])
        return
    record = {'at': time.time(), 'from': {k: old[k] for k in ('target', 'target_oid', 'merge_base')},
              'to': {k: new[k] for k in ('target', 'target_oid', 'merge_base')}, 'invalidated': {}}
    # Gate plans, their runs and decisions are bound to the merge base; keep them only as history.
    for key in ('gate_manifests', 'gate_runs', 'gate_decisions', 'gate_refreshes'):
        if key in st:
            record['invalidated'][key] = st.pop(key)
    st['identity'] = new
    if 'rework_recovery' in st:
        # Its origin may change only together with this recorded retarget (see its prepare_save).
        st['rework_recovery']['origin']['identity'] = copy.deepcopy(new)
    if 'task_attribution' in st:
        # No writer has contributed yet, so the baseline capture can be taken again.
        from al_task_attribution import capture_attribution
        record['invalidated']['task_attribution'] = st.pop('task_attribution')
        capture_attribution(st)
    admission = st.get('proof_admission')
    replanned = bool(admission and admission.get('status') == 'admitted')
    if replanned:
        # Admitted obligations bind the repository identity, so planning admission is reopened;
        # the specification gate, classification and recorded evidence are kept.
        from al_new_origin import FIELDS, occurrence
        if st['phase'] != 'PLANNING':
            al_store.set_phase(st, 'PLANNING', ['al-architect'])
        st['proof_admission'] = {key: admission[key] for key in FIELDS}
        st['proof_admission'].update(status='pending', planning_occurrence=occurrence(st),
                                     invalidation_generation=admission['invalidation_generation'] + 1)
        record['invalidated']['proof_admission'] = copy.deepcopy(admission)
    if st.get('implementation_readiness') is not None:
        st['implementation_readiness']['obligations'] = sorted(set(
            st['implementation_readiness']['obligations'] + ['target changed; rediscover gates and reissue planning']))
    st.setdefault('retargets', []).append(record)
    al_store.save(sid, st)
    print('AL · retarget · ' + old['target'] + ' -> ' + new['target'] + ' · merge base '
          + old['merge_base'][:12] + ' -> ' + new['merge_base'][:12])
    print('  kept: specification gate, classification, recorded evidence')
    print('  invalidated: gate plans, gate runs and decisions, review scope, task baseline (recaptured)'
          + (', planning admission' if replanned else ''))
    print('  next: gates discover and gates plan; then planning packet, report and admit'
          + (' (admission was reopened)' if replanned else ' if planning is not yet admitted'))


def register(sub):
    p = sub.add_parser('retarget', help='change the target of a run before review starts')
    p.add_argument('--target', required=True)
    p.set_defaults(fn=cmd_retarget)
