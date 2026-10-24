"""Recognized repository gate interfaces and their actual dependency scopes."""
import re
from pathlib import Path
from al_schema import refuse


def recipe(text,target):
    lines=text.splitlines();result=[];active=False
    for line in lines:
        if line and not line[0].isspace() and not line.startswith('#'):
            active=bool(re.match(r'^'+re.escape(target)+r'\s*:',line))
            continue
        if active and line.startswith('\t'):result.append(line)
    return '\n'.join(result)


def loc_directories(root,paths):
    root=root.resolve();result=set()
    for raw in paths:
        p=Path(raw)
        if p.is_absolute() or '..' in p.parts:refuse('LOC path escapes repository')
        target=(root/p).resolve()
        if target!=root and root not in target.parents:refuse('LOC path escapes repository')
        if not target.is_dir():target=target.parent
        while not target.is_dir():target=target.parent
        if target!=root and root not in target.parents:refuse('LOC scan escapes repository')
        result.add(target.relative_to(root))
    kept=[]
    for p in sorted(result,key=lambda x:(len(x.parts),str(x))):
        if not any(q==Path('.') or q==p or q in p.parents for q in kept):kept.append(p)
    return [p.as_posix() for p in kept]


from al_service_coverage import validate_check_binding  # public binding validator
