"""Bounded containment checks for disposable materialization paths."""
import os
from pathlib import Path
import stat

class MaterializationError(ValueError):pass


def resolve(root,path,allow_missing=False):
    root=Path(root).resolve();path=Path(path)
    if not path.is_absolute():path=root/path
    try:parts=path.relative_to(root).parts
    except ValueError:raise MaterializationError('path outside disposable checkout')
    steps=[0]
    def walk(tokens,current=(),trail=(),missing=False):
        current=list(current)
        for index,part in enumerate(tokens):
            if part in {'','.'}:continue
            if part=='..':
                if not current:raise MaterializationError('symlink traversal escapes disposable checkout')
                current.pop();continue
            if part=='.git':raise MaterializationError('disposable Git metadata path refused')
            target=root.joinpath(*current,part)
            try:mode=target.lstat().st_mode
            except FileNotFoundError:
                if not missing:raise MaterializationError('dangling/missing disposable path: '+str(target.relative_to(root)))
                current.append(part);continue
            except OSError as exc:raise MaterializationError('unsupported disposable path: '+str(exc)) from exc
            if stat.S_ISLNK(mode):
                steps[0]+=1
                if steps[0]>64 or target in trail:raise MaterializationError('cyclic/excessive disposable symlink resolution')
                text=os.readlink(target)
                if Path(text).is_absolute():raise MaterializationError('absolute disposable symlink refused: '+str(target.relative_to(root)))
                resolved=walk(Path(text).parts,current,trail+(target,),False)
                current=list(resolved.relative_to(root).parts)
                if index<len(tokens)-1 and not resolved.is_dir():raise MaterializationError('non-directory disposable path component')
            else:
                if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):raise MaterializationError('unsupported disposable target type')
                if index<len(tokens)-1 and not stat.S_ISDIR(mode):raise MaterializationError('non-directory disposable path component')
                current.append(part)
        return root.joinpath(*current)
    return walk(parts,missing=allow_missing)


def validate(root):
    root=Path(root).resolve()
    for directory,dirs,files in os.walk(root,followlinks=False):
        if Path(directory)==root:dirs[:]=[d for d in dirs if d!='.git'];files=[f for f in files if f!='.git']
        for name in dirs+files:resolve(root,Path(directory)/name)


def overlay(root,path):
    parent=resolve(root,Path(path).parent,allow_missing=True)
    parent.mkdir(parents=True,exist_ok=True)
    return parent/Path(path).name


def cwd(root,path):
    result=resolve(root,path)
    if not result.is_dir():raise MaterializationError('gate cwd must be an existing contained directory')
    return result
