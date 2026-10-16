"""Explicit parent-entry evidence capabilities; child contents need a child run."""
from pathlib import Path


class SourceError(ValueError):
    pass


def validate_sources(sources):
    if not isinstance(sources, (list, tuple)):
        raise SourceError('sources must be an array')
    out=[]
    for item in sources:
        if not isinstance(item,dict) or set(item)!={'path','capability'}:
            raise SourceError('source requires exactly path and capability')
        path=item['path']
        if not isinstance(path,str) or not path or path=='.' or Path(path).is_absolute() or '..' in Path(path).parts or Path(path).as_posix()!=path or any(c in path for c in '*?[:'):
            raise SourceError('source path must be normalized and parent-relative')
        if not isinstance(item['capability'],str) or item['capability'] not in {'parent-entry','child-content'}:
            raise SourceError('unsupported source capability')
        if any(row['path']==path for row in out):raise SourceError('duplicate source path: '+path)
        out.append(dict(item))
    return sorted(out,key=lambda item:item['path'])


def contains(parent,path):
    return parent=='.' or parent==path or Path(parent) in Path(path).parents


def intersects(scope,path):
    return any(contains(p,path) or contains(path,p) for p in scope)


def boundaries(rows):
    return {r['path']:r for r in rows if r.get('child') and r['child']['state']!='replaced'}


def canonical_cwd(root,cwd):
    if not isinstance(cwd,str) or not cwd or Path(cwd).is_absolute() or '..' in Path(cwd).parts:
        raise SourceError('cwd must use the existing repository-relative path grammar')
    try:
        parent=Path(root).resolve();target=(parent/cwd).resolve()
        if target!=parent and parent not in target.parents:
            raise SourceError('cwd escapes parent repository')
        if not target.is_dir():raise SourceError('cwd does not exist or is not a directory')
        return target.relative_to(parent).as_posix()
    except (OSError,RuntimeError) as exc:
        raise SourceError('cannot resolve cwd: '+str(exc)) from exc


def capability_gaps(scope,sources,rows,cwd='.',*,root):
    cwd=canonical_cwd(root,cwd)
    declarations=validate_sources(sources)
    declared={item['path']:item['capability'] for item in declarations}
    links=boundaries(rows);by_path={r['path']:r for r in rows};out=[]
    for path,capability in declared.items():
        if not intersects(scope,path):out.append('source declaration outside scope: '+path)
        if path in by_path and path not in links:out.append('source is not a submodule boundary: '+path)
        if capability=='child-content':out.append('unsupported child-content capability at '+path+'; run AL rooted in that child repository')
    for path,row in links.items():
        if intersects(scope,path):
            if path not in declared:out.append('submodule '+path+' requires an explicit source capability')
            if any(p!=path and contains(path,p) for p in scope):out.append('scope inside submodule '+path+' requires unsupported child-content capability')
            if row['worktree']['type']=='unknown':out.append('submodule inspection failed at '+path)
        if contains(path,cwd):out.append('cwd inside submodule '+path+' requires unsupported child-content capability')
    return sorted(set(out))


def pointer_dependencies(scope,sources,rows):
    declared=validate_sources(sources)
    indexed={row['path']:row for row in rows}
    out=[]
    for source in declared:
        if source['capability']!='parent-entry' or not intersects(scope,source['path']):continue
        row=indexed.get(source['path'])
        entry=row.get('index') if row else None
        out.append({'path':source['path'],'index':{k:entry[k] for k in ('mode','oid')} if entry else None,
                    'effective':row['worktree'] if row else {'type':'deleted'}})
    return out
