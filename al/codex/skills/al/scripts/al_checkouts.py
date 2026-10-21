"""Disposable effective-parent materialization; never writes source metadata."""
import contextlib
import hashlib
import os
import shutil
import subprocess
from al_profile import record
import tempfile
import time
from pathlib import Path
import al_gitlinks as G
import al_scope as S
import al_store
from al_checkout_paths import MaterializationError,validate,overlay,cwd as gate_cwd


def run(root,*args):
    r=subprocess.run(['git',*args],cwd=root,capture_output=True)
    if r.returncode: raise MaterializationError('disposable git failed: '+r.stderr.decode(errors='replace'))
    return r.stdout


def overlay_candidate(root, capture, *, stage_files=True):
    validate(root)
    rows=capture['files']
    for row in rows:
        kind=row['worktree']['type']
        if kind=='unknown': raise MaterializationError('unknown effective submodule state: '+row['path'])
        path=overlay(root,root/S.safe_path(row['path']))
        if path.is_symlink() or path.is_file():path.unlink()
        elif path.is_dir():shutil.rmtree(path)
        if kind=='deleted':continue
        if kind=='gitlink':path.mkdir();continue
        if kind=='symlink':path.symlink_to(row['worktree']['text'])
        else:
            raw=(al_store.REPO_ROOT/row['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['worktree']['sha256']:raise MaterializationError('candidate moved during materialization')
            path.write_bytes(raw);path.chmod(0o755 if row['worktree']['executable'] else 0o644)
    validate(root)
    if stage_files:run(root,'add','--all')
    # Ordinary isolation overlays retain their prior index; typed transitions cannot.
    for row in rows:
        if 'child' not in row:continue
        effective=row['worktree']
        if effective['type']=='gitlink':
            for raw in G.index_entries(root):
                name=os.fsdecode(raw)
                if name.startswith(row['path']+'/'):run(root,'update-index','--force-remove',name)
            run(root,'update-index','--add','--cacheinfo','160000,'+effective['oid']+','+row['path'])
        else:
            indexed=G.index_entries(root).get(os.fsencode(row['path']))
            if indexed and indexed['mode']=='160000':run(root,'update-index','--force-remove',row['path'])
    actual=S.parent_representation(S.records(capture['base'],root=root))
    if actual!=S.parent_representation(rows):raise MaterializationError('disposable candidate representation differs from capture')
    validate(root)


@contextlib.contextmanager
def materialize(st,stage,base=None):
    started=time.monotonic()
    capture=S.capture_candidate(st)
    with tempfile.TemporaryDirectory(prefix='al-gate-') as tmp:
        root=Path(tmp).resolve()/'checkout';root.mkdir()
        revision=capture['base'] if stage=='candidate' else (base or st['identity']['merge_base'])
        objects=run(al_store.REPO_ROOT,'rev-parse','--path-format=absolute','--git-path','objects').decode().rstrip('\n')
        object_path=Path(objects)
        if not object_path.is_absolute() or not object_path.is_dir() or any(c in objects for c in '\n\r\0'):
            raise MaterializationError('unrepresentable source object directory')
        fmt=run(al_store.REPO_ROOT,'rev-parse','--show-object-format').decode().strip()
        if fmt not in {'sha1','sha256'}:raise MaterializationError('unsupported source object format')
        run(root,'init','-q','--object-format='+fmt)
        (root/'.git/objects/info/alternates').write_text(objects+'\n')
        run(root,'checkout','-q','--detach',revision);validate(root)
        if stage=='candidate':
            overlay_candidate(root,capture)
            if S.capture_candidate(st)['fingerprint']!=capture['fingerprint']:raise MaterializationError('candidate changed during materialization')
        validate(root)
        record('checkout-materialization',time.monotonic()-started)
        yield {'root':root,'capture':capture,'stage':stage}
