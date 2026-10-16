"""Concrete anticipated-path containment and prospective writer obligations."""
from pathlib import PurePosixPath
import al_artifacts as A
import al_scope as S
import al_store
from al_schema import refuse


def contains(directory,path):
    parent=PurePosixPath(directory).parts;child=PurePosixPath(path).parts
    return child[:len(parent)]==parent


def covered(paths,path):
    return any(p==path or p=='.' or (al_store.REPO_ROOT/p).is_dir() and contains(p,path) for p in paths)


def expand(paths):
    result=set(paths);directories=[p for p in paths if (al_store.REPO_ROOT/p).is_dir()]
    if directories:
        known=[p.decode() for p in S.git_bytes('ls-files','--cached','--others','--exclude-standard','-z').split(b'\0') if p]
        known += [p.relative_to(al_store.REPO_ROOT).as_posix() for kind in ('apps','services','packages') for p in (al_store.REPO_ROOT/kind).glob('*') if p.is_dir() and not p.is_symlink()]
        result.update(p for p in known if any(contains(d,p) for d in directories))
    return sorted(result)


def prospective_gaps(st,paths):
    if not st.get('gate_manifests'):return ['repository gate manifest missing']
    import al_gate_inventory as V
    m=A.read_json(st['gate_manifests'][-1])
    out=['writer path is not anticipated: '+p for p in paths if not covered(m.get('paths',[]),p)]
    return sorted(set(out+V.gaps(st,m,paths)))


def writer_ready(st,paths):
    from al_gate_recovery import ready
    ready(st)
    missing=prospective_gaps(st,paths)
    if missing:refuse('writer needs extended gate coverage: '+str(missing))
