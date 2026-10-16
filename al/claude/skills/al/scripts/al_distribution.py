#!/usr/bin/env python3
"""Plan and atomically apply managed skill resources with external backups."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

PRUNE={'node_modules','.git','.venv','venv','__pycache__','.pytest_cache','al-runtime','.cache','dist','build'}
PLATFORMS=('codex','cl'+'aude')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def managed(root):
    return {p.relative_to(root).as_posix(): {'sha256':sha(p),'mode':p.stat().st_mode & 0o777}
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()
            and not PRUNE.intersection(p.relative_to(root).parts)
            and p.suffix not in {'.pyc','.log'} and p.name not in {'.DS_Store','current'}}


def repo_identity(path):
    r=subprocess.run(['git','rev-parse','--show-toplevel'],cwd=path,capture_output=True,text=True)
    if r.returncode: return {'root':str(path),'git_dir':None,'head':None}
    root=Path(r.stdout.strip()).resolve()
    def git(*args):
        result=subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,check=True)
        return result.stdout.strip()
    return {'root':str(root),'git_dir':str((root/git('rev-parse','--git-dir')).resolve()),'head':git('rev-parse','HEAD')}


def instructions(path,repo):
    out=[]; current=path
    stop=Path(repo['root'])
    while True:
        for name in ('AGENTS.md','CL'+'AUDE.md'):
            p=current/name
            if p.is_file(): out.append({'path':str(p),'sha256':sha(p)})
        if current==stop or current.parent==current: break
        current=current.parent
    return out


def discover(roots):
    found=[]; seen=set()
    for start in roots:
        if not start.exists() or start.is_symlink(): continue
        for directory,dirs,files in os.walk(start,followlinks=False):
            dirs[:]=[x for x in dirs if x not in PRUNE and not (Path(directory)/x).is_symlink()]
            path=Path(directory)
            for platform in PLATFORMS:
                if path.name=='al' and path.parent.name=='skills' and path.parent.parent.name=='.'+platform:
                    resolved=path.resolve()
                    if resolved in seen: continue
                    seen.add(resolved); repo=repo_identity(path)
                    found.append({'path':str(resolved),'platform':platform,'identity':repo,'instructions':instructions(path,repo)})
    return found


def plan(source,targets,reviews,*,registry,prior_managed=None):
    import al_distribution_registry as R
    from al_distribution_transaction import state
    source=R.safe_absolute(source);files=managed(source);result=[]
    registry=R.external(registry,targets,source)
    if len({R.key(x['path'],x['platform']) for x in targets})!=len(targets):raise ValueError('duplicate distribution target')
    with R.locks(registry,targets):
        for target in targets:
            root=R.safe_absolute(target['path']);target=dict(target,path=str(root))
            if R.stable(repo_identity(root))!=R.stable(target['identity']):raise ValueError('target identity changed')
            existing=managed(root);review=reviews.get(str(root))
            if not review or not review.get('reviewed') or review.get('instructions')!=target['instructions'] or instructions(root,target['identity'])!=target['instructions']:
                raise ValueError('instruction review record missing/mismatched: '+str(root))
            owned,bootstrap,record,record_sha=R.prior(registry,target,existing,prior_managed)
            actions=[];absent=[]
            for name,item in files.items():
                R.relative(name);R.safe_absolute(root/name);previous=state(root/name)
                if previous!=item:actions.append({'path':name,'action':'replace' if previous else 'create','source':item,'target':previous})
            for name,item in owned.items():
                if name in files:continue
                R.safe_absolute(root/name);previous=state(root/name)
                if previous is None:absent.append(name)
                elif previous!=item:raise ValueError('obsolete managed resource drift; explicit adoption required: '+name)
                else:actions.append({'path':name,'action':'delete','source':None,'target':previous})
            result_owned=R.result_owned(owned,files,actions)
            unmanaged={name:item for name,item in existing.items() if name not in result_owned and name not in owned}
            links={p.relative_to(root).as_posix():os.readlink(p) for p in root.rglob('*') if p.is_symlink() and not PRUNE.intersection(p.relative_to(root).parts)}
            result.append(dict(target,source=str(source),source_files=files,actions=actions,unmanaged=unmanaged,unmanaged_symlinks=links,
                               already_absent=absent,prior_owned=owned,result_owned=result_owned,bootstrap=bootstrap,prior_registry_record=record,
                               registry={'path':str(R.record_path(registry,str(root),target['platform'])),'sha256':record_sha,'generation':record['generation'] if record else 0},
                               source_manifest_sha256=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
                               local_differences=list(unmanaged),instruction_review=review))
    return {'schema_version':2,'created_at':time.time(),'registry':str(registry),'targets':result,'role_changes':[]}


def atomic(path,raw,mode):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.al-install-')
    try:
        with os.fdopen(fd,'wb') as out: out.write(raw); out.flush(); os.fsync(out.fileno())
        os.chmod(tmp,mode); os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def apply(manifest,backup):
    from al_distribution_transaction import apply as transaction_apply
    return transaction_apply(manifest,backup)


def verify(manifest):
    from al_distribution_transaction import verify as transaction_verify
    return transaction_verify(manifest)


def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='action',required=True)
    p=sub.add_parser('discover'); p.add_argument('--root',action='append'); p.add_argument('--output',required=True)
    p=sub.add_parser('plan'); p.add_argument('--registry',required=True); p.add_argument('--prior-managed'); p.add_argument('--roles-file'); p.add_argument('--source',required=True); p.add_argument('--inventory',required=True); p.add_argument('--reviews',required=True); p.add_argument('--platform',choices=PLATFORMS,required=True); p.add_argument('--output',required=True)
    for name in ('apply','verify'):
        p=sub.add_parser(name); p.add_argument('--manifest',required=True); p.add_argument('--output',required=True)
        if name=='apply':p.add_argument('--backup',required=True)
    a=ap.parse_args()
    if a.action=='discover': result=discover([Path(x).expanduser() for x in a.root or ['~/Documents','~/personal']])
    elif a.action=='plan':
        targets=json.loads(Path(a.inventory).read_text()); reviews=json.loads(Path(a.reviews).read_text())
        result=plan(Path(a.source),[x for x in targets if x['platform']==a.platform],reviews,registry=Path(a.registry),prior_managed=a.prior_managed)
        if a.roles_file:
            from al_distribution_roles import plan_roles
            result['role_changes']=plan_roles(json.loads(Path(a.roles_file).read_text()))
    else:
        manifest=json.loads(Path(a.manifest).read_text())
        result=apply(manifest,Path(a.backup)) if a.action=='apply' else verify(manifest)
    Path(a.output).write_text(json.dumps(result,indent=2)); print(json.dumps(result,indent=2))

if __name__=='__main__':main()
