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
    options = ['-c', 'diff.autoRefreshIndex=false'] if context and context.readonly_git else []
    if context and context.readonly_git and args and args[0]=='diff':args=(args[0],'--no-ext-diff','--no-textconv',*args[1:])
    r = subprocess.run(['git', '--no-optional-locks', *options, *args], cwd=root or al_store.REPO_ROOT, capture_output=True)
    if r.returncode: refuse('git failed: ' + r.stderr.decode(errors='replace').strip())
    return r.stdout

def identity(target='HEAD'):
    root = al_store.REPO_ROOT.resolve()
    values = git_bytes('rev-parse', '--git-common-dir', '--git-dir', '--end-of-options', target + '^{commit}', 'HEAD^{commit}').decode().splitlines()
    if len(values) != 5 or values[2] != '--end-of-options' or not all(values): refuse('invalid repository identity response')
    common, git_dir, _, oid, head = values
    if len(oid) not in {40, 64} or len(head) != len(oid) or not all(re.fullmatch(r'[0-9a-f]+', value) for value in (oid, head)):
        refuse('invalid repository identity response')
    return {'root': str(root), 'cwd': str(Path.cwd().resolve()),
            'common_dir': str((root / common).resolve()),
            'git_dir': str((root / git_dir).resolve()),
            'target': target, 'target_oid': oid,
            'merge_base': oid if oid == head else git_bytes('merge-base', 'HEAD', oid).decode().strip()}

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
    root=root or al_store.REPO_ROOT
    try:
        index=G.index_entries(root);head=G.tree_entries(root,'HEAD')
        baseline=head if base=='HEAD' else G.tree_entries(root,base)
        out=[]
        for raw in sorted(G.paths(baseline,head,index)):
            obs=G.observe(root,raw,baseline.get(raw),head.get(raw),index.get(raw))
            out.append({'path':os.fsdecode(raw),'base':obs['base'],'head':obs['head'],'index':obs['index'],'child':obs['child'],
                        'worktree':obs['effective'] or {'type':'replaced'}})
        return out
    except G.GitlinkError as exc:refuse(str(exc))


@measured('indexed-blob-hashing')
def _blob_hashes(root, oids):
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


def records(base,root=None):
    root = root or al_store.REPO_ROOT
    try:
        index = G.index_entries(root)
        base_entries = G.tree_entries(root, base)
        head = G.tree_entries(root, 'HEAD')
        observations = {raw: G.observe(root, raw, base_entries.get(raw), head.get(raw), index.get(raw))
                        for raw in G.paths(base_entries, head, index)}
    except G.GitlinkError as exc:
        refuse(str(exc))
    hashes = _blob_hashes(root, (e['oid'] for e in index.values() if e['mode'] != '160000'))
    indexed = {}
    for raw, entry in index.items():
        indexed[raw] = dict(entry)
        if entry['mode'] != '160000':
            indexed[raw]['sha256'] = hashes[entry['oid']]
    opaque = {raw for raw, obs in observations.items() if G.opaque(obs)}
    raw_paths = set(indexed) | set(base_entries) | set(head)
    raw_paths.update(x for x in git_bytes('ls-files','--others','--exclude-standard','-z',root=root).split(b'\0') if x)
    raw_paths = {raw.rstrip(b'/') for raw in raw_paths}
    out = []
    for raw in sorted(raw_paths):
        if any(raw.rstrip(b'/') != parent and raw.startswith(parent + b'/') for parent in opaque):
            continue
        raw = raw.rstrip(b'/')
        rel = safe_path(raw); p = root / rel
        if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1,len(rel.parts))):
            refuse('candidate path traverses symlink')
        row = {'path': os.fsdecode(raw), 'index': indexed.get(raw)}
        obs = observations.get(raw)
        if obs:
            row.update(base=obs['base'], head=obs['head'], child=obs['child'])
        if obs and obs['effective'] is not None:
            row['worktree'] = obs['effective']
        elif p.is_symlink(): row['worktree'] = {'type':'symlink','text':os.readlink(p)}
        elif p.is_file(): row['worktree'] = {'type':'file','executable':bool(p.stat().st_mode & 0o111),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        elif not p.exists() or (obs and p.is_dir()): row['worktree'] = {'type':'deleted'}
        else: refuse('unsupported candidate file type: ' + str(rel))
        out.append(row)
    return out


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

@measured('candidate-hashing')
def capture_candidate(st):
    base = st['identity']['merge_base'] if st.get('review_mode') == 'whole-mr' else st['baseline_head']
    rows = records(base)
    content = {'base':base,'target':st['identity']['target_oid'],'files':rows}
    sha = hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()
    diff = parent_diff(base,'--binary',rows=rows).decode(errors='replace')
    diff_limitations = [r['path']+': '+r['child']['error'] for r in rows if r.get('child',{}).get('state')=='inspection-failed']
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
