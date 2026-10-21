"""Invocation-local execution identity and independently checked reuse proof."""
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
import al_checkouts as C
import al_artifacts as A
import al_ledger as L


def execution_key(st, manifest, manifest_sha, gate, stage, capture, env_fingerprint, timeout):
    return {'run':st['run_id'], 'manifest':manifest_sha, 'revision':manifest['revision'],
            'stage':stage, 'base':manifest['base_oid'], 'candidate':capture['fingerprint'],
            'argv':gate['argv'], 'cwd':gate['cwd'], 'execution_kind':gate['execution_kind'],
            'scope':gate['scope'], 'sources':gate.get('sources',[]),
            'env':sorted(gate['env_names']), 'env_fingerprint':env_fingerprint, 'timeout':timeout}


def key_digest(key):
    return hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()


def _tree(root, metadata=False):
    result={'.':['dir',stat.S_IMODE(root.lstat().st_mode)]}
    def visit(directory):
        for p in sorted(directory.iterdir()):
            rel=p.relative_to(root).as_posix()
            if not metadata and rel=='.git':continue
            mode=p.lstat().st_mode
            if metadata and rel=='index':
                if not stat.S_ISREG(mode):raise C.MaterializationError('scratch index is not a regular file')
                result[rel]=['index',stat.S_IMODE(mode)]
                continue
            if metadata and stat.S_ISLNK(mode):raise C.MaterializationError('scratch metadata symlink refused')
            if stat.S_ISLNK(mode):value=['link',os.readlink(p)]
            elif stat.S_ISDIR(mode):value=['dir',stat.S_IMODE(mode)]
            elif stat.S_ISREG(mode):value=['file',stat.S_IMODE(mode),hashlib.sha256(p.read_bytes()).hexdigest()]
            else:raise C.MaterializationError('unsupported scratch state: '+rel)
            result[rel]=value
            if stat.S_ISDIR(mode):visit(p)
    visit(root)
    return result


def scratch_state(root, expected=None, *, post_execution=False):
    if not post_execution:C.validate(root)
    try:
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise C.MaterializationError('scratch root replaced')
        if (root/'.git').is_symlink() or not (root/'.git').is_dir():
            raise C.MaterializationError('scratch metadata directory replaced')
        metadata=_tree(root/'.git',True)
        if expected is not None and metadata!=expected['metadata']:
            return dict(expected,metadata=metadata)
        if post_execution and expected is None:
            raise C.MaterializationError('post-execution inspection requires initial state')
        state={'work':_tree(root), 'metadata':metadata,
               'index':C.run(root,'ls-files','--stage','-z'),
               'index_flags':C.run(root,'ls-files','-v','-z')}
        # Complete work/index comparison avoids Git traversing generated directory links.
        if post_execution:return dict(expected,**state)
        state.update(diff=C.run(root,'diff','--ignore-submodules=none','--binary','HEAD'),
                     untracked=C.run(root,'ls-files','--others','--exclude-standard','-z'))
        return state
    except OSError as exc:raise C.MaterializationError('unreadable scratch state') from exc


def drift(before, after, root):
    if any(before[k]!=after[k] for k in ('metadata','index','index_flags','diff','untracked')):return 'FAIL'
    if before['work']!=after['work']:
        tracked=[item.split(b'\t',1)[1].decode(errors='surrogateescape') for item in before['index'].split(b'\0') if item]
        if any(before['work'].get(p)!=after['work'].get(p) for p in tracked):return 'FAIL'
        changed={p for p in before['work'].keys() | after['work'].keys() if before['work'].get(p)!=after['work'].get(p)}
        check=subprocess.run(['git','check-ignore','--no-index','-z','--stdin'],cwd=root,
                             input=b''.join(os.fsencode(p)+b'\0' for p in sorted(changed)),capture_output=True)
        if check.returncode not in {0,1}:raise C.MaterializationError('cannot classify scratch output')
        ignored={os.fsdecode(p) for p in check.stdout.split(b'\0') if p}
        for p in changed-ignored:
            value=after['work'].get(p,before['work'].get(p))
            if value[0]!='dir' or (p in before['work'] and p in after['work']):return 'FAIL'
            leaves={q for q in changed if q.startswith(p+'/') and after['work'].get(q,before['work'].get(q))[0]!='dir'}
            if not leaves or not leaves<=ignored:return 'FAIL'
        return 'REBUILD'
    return None


def reuse_gaps(st, entry):
    try:return _reuse_gaps(st,entry)
    except (KeyError,TypeError,ValueError,OSError,L.FingerprintError,SystemExit):
        return ['unreadable or malformed reuse provenance']


def _reuse_gaps(st, entry):
    proof=entry.get('reuse')
    if not isinstance(proof,dict):return ['malformed reuse provenance']
    if entry.get('status')!='PASS' or entry.get('exit')!=0 or entry.get('interrupted') or not entry.get('ended_at'):return ['reused execution incomplete']
    original=L.find(st,proof.get('evidence'))
    if not original or original is entry or original.get('reuse'):return ['missing or chained original execution']
    key=entry.get('execution_key')
    if not isinstance(key,dict) or key!=original.get('execution_key') or key.get('execution_kind')!='local':return ['execution identity differs']
    if not entry.get('invocation') or entry.get('invocation')!=original.get('invocation'):return ['foreign execution invocation']
    if entry.get('run')!=st['run_id'] or original.get('run')!=st['run_id']:return ['foreign execution run']
    if not entry.get('reservation') or not original.get('reservation'):return ['missing evidence reservation']
    if entry.get('pid') is not None:return ['reused evidence claims a process']
    if entry.get('reservation')==original.get('reservation') or entry.get('id')==original.get('id'):return ['shared evidence reservation']
    for field in ('argv','cwd','scope','sources','env','fp_before','fp_after','gate_stage','gate_manifest'):
        if entry.get(field)!=original.get(field):return ['execution dependency differs: '+field]
    if original.get('status')!='PASS' or original.get('interrupted') or not original.get('ended_at') or not original.get('pid'):return ['original execution incomplete']
    if original.get('exit')!=0 or original.get('fp_before')!=original.get('fp_after') or original.get('fingerprint_error'):return ['original execution was not stable']
    if key.get('stage')=='base':
        if not original.get('log') or L.file_sha(original['log'])!=original.get('log_sha256'):return ['original base log invalid']
    elif L.entry_verdict(st,original)[0]!='VALID':return ['original evidence invalid']
    if proof.get('log_sha256')!=original.get('log_sha256') or proof.get('execution_sha256')!=key_digest(key):return ['original proof differs']
    if key.get('run')!=st['run_id'] or key.get('manifest')!=entry.get('gate_manifest') or key.get('stage')!=entry.get('gate_stage'):return ['execution binding differs']
    ref=st['gate_manifests'][-1]
    if ref['sha256']!=key['manifest']:return ['execution manifest changed']
    manifest=A.read_json(ref)
    marker='execution_sha256: '+key_digest(key)+'\ninvocation: '+entry['invocation']+'\n'
    if marker.encode() not in Path(original['log']).read_bytes():return ['original log execution binding differs']
    associations=st.get('gate_runs',[])
    for e in (original,entry):
        matches=[r for r in associations if r.get('evidence')==e['id'] and r.get('manifest')==key['manifest'] and r.get('stage')==key['stage'] and e.get('area')=='gate-'+key['stage']+'-'+r.get('gate','') and r.get('execution_kind')=='local' and r.get('env_fingerprint')==key['env_fingerprint'] and r.get('sources',[])==e.get('sources',[])]
        if len(matches)!=1:return ['missing execution association']
        gate=next((g for g in manifest['gates'] if g['id']==matches[0]['gate']),None)
        if not gate or gate['applicability']!='APPLICABLE':return ['execution obligation missing']
        expected=execution_key(st,manifest,ref['sha256'],gate,key['stage'],{'fingerprint':key['candidate']},L.scope_fingerprint([],gate['env_names']),key['timeout'])
        if expected!=key:return ['execution differs from manifest dependencies']
        if matches[0].get('fingerprint')!=(key['candidate'] if key['stage']=='candidate' else key['base']):return ['execution candidate differs']
    return []
