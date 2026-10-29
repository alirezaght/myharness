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
        from al_repository_facts import Snapshot
        scan=Snapshot(al_store.REPO_ROOT,None,paths,[])
        if scan.gaps:refuse('anticipated-path membership incomplete: '+str(scan.gaps))
        result.update(p.rstrip('/') for p in scan.members if any(contains(d,p.rstrip('/')) for d in directories))
    return sorted(result)


def prospective_gaps(st,paths):
    if not st.get('gate_manifests'):return ['repository gate manifest missing']
    import al_gate_inventory as V
    m=A.read_json(st['gate_manifests'][-1])
    out=['writer path is not anticipated: '+p for p in paths if not covered(m.get('paths',[]),p)]
    opaque={r['path'] for r in S.source_rows() if V.G.opaque(r)}
    semantic=m.get('service_coverage',{}).get('semantic_requirements')
    out+=['writer path is in an unplanned unit: '+p for p in paths if V.classify_unit(p,opaque,semantic) not in m['units']]
    return sorted(set(out))


def writer_ready(st,paths):
    from al_new_origin import require_implementation_ready
    return require_implementation_ready(st, paths=paths)
