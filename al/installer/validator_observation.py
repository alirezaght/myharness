"""Fresh parent-side target membership and child-read-set validation."""
import os
from pathlib import Path
import re
import subprocess

from .validator_origin import capture, digest, safe
from .validator_readonly import Readonly


def git(repository, environment, *args):
    argv=['/usr/bin/git','--no-optional-locks','-c','core.hooksPath='+os.devnull,
          '-c','core.fsmonitor=false','-c','diff.autoRefreshIndex=false','-C',str(repository),*args]
    result=subprocess.run(argv,cwd=repository,env=environment,capture_output=True,timeout=30)
    if result.returncode or len(result.stdout)>16*1024*1024 or len(result.stderr)>65536:
        raise ValueError('parent terminal Git observation failed')
    return result.stdout


def target_inventory(repository,environment,*,depth=0,budget=None):
    budget=budget if budget is not None else [1024]
    budget[0]-=1
    if depth>8 or budget[0]<0:raise ValueError('parent terminal Gitlink observation bound exceeded')
    root=safe(repository)
    lines=git(root,environment,'rev-parse','--show-toplevel','--git-dir','--git-common-dir','HEAD^{commit}').decode().splitlines()
    if len(lines)!=4 or Path(lines[0]).resolve()!=root or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',lines[3]):
        raise ValueError('invalid parent terminal repository identity')
    gitdir=safe((root/lines[1]).resolve());common=safe((root/lines[2]).resolve())
    index=gitdir/'index'
    index_state=capture(index)[1] if index.exists() else None
    members=git(root,environment,'ls-files','--cached','--others','--exclude-standard','-z')
    entries=git(root,environment,'ls-files','--stage','-z')
    children={}
    for entry in entries.split(b'\0'):
        if not entry:continue
        metadata,name=entry.split(b'\t',1);mode,oid,stage=metadata.split()
        if stage!=b'0':raise ValueError('unmerged parent terminal index')
        if mode!=b'160000':continue
        relative=Path(os.fsdecode(name))
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('unsafe parent terminal Gitlink')
        path=safe(root/relative)
        if not (path/'.git').exists():
            if path.exists() and (not path.is_dir() or any(path.iterdir())):
                raise ValueError('unobserved uninitialized Gitlink contents')
            children[str(relative)]={'state':'uninitialized','exists':path.exists()}
            continue
        status=git(path,environment,'status','--porcelain=v1','-z','--untracked-files=all','--ignore-submodules=none')
        if status:raise ValueError('parent terminal Gitlink is dirty')
        children[str(relative)]=target_inventory(path,environment,depth=depth+1,budget=budget)
    return {'root':str(root),'git_dir':str(gitdir),'common_dir':str(common),'head':lines[3],
            'index':index_state,'members_sha256':digest(members),'entries_sha256':digest(entries),'children':children}


def verify_read_set(value):
    if not isinstance(value,dict) or not value or len(value)>100000:
        raise ValueError('incomplete terminal read-set proof')
    reader=Readonly()
    for name,state in value.items():
        if not isinstance(name,str) or not Path(name).is_absolute():raise ValueError('invalid terminal read-set path')
        path=safe(name)
        observed=reader.fingerprint(path)
        expected=None if observed is None else {'metadata':list(observed[0]),'sha256':observed[1]}
        if expected!=state:raise ValueError('terminal query input changed: '+name)
