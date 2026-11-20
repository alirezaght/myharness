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
    from al_repository_facts import Snapshot
    from al_repository_capture import capture_discovery
    from al_coverage_relevance import project_expansion
    def attempt(scan):
        requested=[]
        for path in paths:
            observed=scan.observe_path(path)
            if observed['state']=='opaque':refuse('anticipated-path membership incomplete: opaque requested path '+path)
            if observed['state']=='directory':requested.append(path)
        projection=project_expansion(scan.listing_observation,requested)
        if projection['gaps']:refuse('anticipated-path membership incomplete: '+str(projection['gaps']))
        result=set(paths)
        result.update(p.rstrip('/') for p in scan.members if any(contains(d,p.rstrip('/')) for d in requested))
        return {'paths':sorted(result)}
    return capture_discovery(lambda:Snapshot(al_store.REPO_ROOT,None,paths,[]),attempt)['paths']


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
