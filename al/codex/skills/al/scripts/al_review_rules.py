"""Capture repository-owned hook instructions without executing repository code."""
import hashlib
import re
from pathlib import Path
import al_artifacts as A
import al_store

ROOTS=('AGENTS.md','CLAUDE.md','.pre-commit-config.yaml','.pre-commit-config.yml',
       '.husky/pre-commit','.githooks/pre-commit')
TOKEN=re.compile(r'(?<![\w/])(?:\.?\.?/)?[\w.@-]+(?:/[\w.@+-]+)*\.(?:md|txt|sh|bash|py|js|mjs|cjs|ts|yaml|yml|toml)')


def capture(st,sid):
    root=al_store.REPO_ROOT.resolve();pending=set(ROOTS);seen=set();sources=[];omissions=[]
    for ref in st.get('gate_manifests',[]):
        manifest=A.read_json(ref)
        pending.update(row['path'] for row in manifest.get('rules',[]) if isinstance(row.get('path'),str))
        for binding in manifest.get('repository_facts',{}).get('checks',{}).get('bindings',[]):
            pending.update(row['path'] for row in binding.get('source_chain',[]) if isinstance(row.get('path'),str))
    total=0
    while pending:
        name=min(pending);pending.remove(name)
        if name in seen:continue
        seen.add(name);path=root/name
        try:
            if not path.exists():continue
            if (path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file()
                or any(p in {'.git','node_modules','.venv','.aws','.ssh'} for p in Path(name).parts)):
                omissions.append({'path':name,'reason':'ordinary repository file required'});continue
            size=path.stat().st_size
            if len(sources)>=64 or size>131072 or total+size>1048576:
                omissions.append({'path':name,'reason':'review instruction capture limit'});continue
            with path.open('rb') as stream:raw=stream.read(131073)
            if len(raw)>131072:
                omissions.append({'path':name,'reason':'review instruction capture limit'});continue
            content=raw.decode('utf-8')
        except (OSError,UnicodeError):
            omissions.append({'path':name,'reason':'unreadable text instruction source'});continue
        total+=len(raw)
        artifact=A.snapshot_bytes(sid,raw,'repository-review-rules',name)
        sources.append({'path':name,'sha256':hashlib.sha256(raw).hexdigest(),
                        'artifact':artifact,'content':content})
        for token in TOKEN.findall(content):
            for relative in (Path(token),Path(name).parent/token):
                candidate=root/relative
                if '..' not in relative.parts and candidate.is_file():
                    pending.add(relative.as_posix())
    return {'sources':sources,'omissions':omissions,
            'discovery':'Repository instructions, gate rule sources, and static local file references; no hook execution.'}
