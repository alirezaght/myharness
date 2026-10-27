"""Structured gate currency; corruption is distinct from ordinary replanning."""
import fnmatch
import hashlib
from pathlib import PurePosixPath
import al_ci_context as CC
import al_artifacts as A
import al_gate_discovery as D
import al_scope as S
import al_store
import al_gate_inventory as V


def rule_source(path):
    name=PurePosixPath(path).name
    if name in {'AGENTS.md','CLAUDE.md','package.json','pyproject.toml','.gitignore','pnpm-workspace.yaml'}:return True
    # fnmatch's `*` crosses directories, so `ci/**/*.yml` also matches files directly under ci/.
    return any(fnmatch.fnmatchcase(path,pattern.replace('**/','*')) for pattern in V.PATTERNS)


def check(st):
    refs=st.get('gate_manifests',[])
    if not refs:return {'current':False,'reasons':['repository gate manifest missing'],'superseded':None}
    ref=refs[-1];m=A.read_json(ref);D.validate_manifest(st,m,check_sources=False)
    context=CC.binding(st,m.get('ci_context'),allow_historical=True)
    # The full inventory runs at discovery, planning and review start; between them rule hashes
    # and touched units decide currency.
    reasons=[] if context['current'] else ['CI context belongs to a superseded task revision; declare replacement context']
    # The path behind each path-specific reason: a refresh may rediscover them when all are planned.
    subjects=[];pathless=bool(reasons)
    sources=m['rules']+[g['source'] for g in m['gates'] if g.get('source')]
    for source in sources:
        p=al_store.REPO_ROOT/S.safe_path(source['path'])
        if not p.is_file():reasons.append('source rule missing: '+source['path']);subjects.append(source['path'])
        elif hashlib.sha256(p.read_bytes()).hexdigest()!=source['sha256']:reasons.append('source rule changed: '+source['path']);subjects.append(source['path'])
    changed=S.git_bytes('diff','--name-only','-z',st['identity']['merge_base']).split(b'\0')
    changed+=S.git_bytes('ls-files','--others','--exclude-standard','-z').split(b'\0')
    paths=[raw.decode() for raw in filter(None,changed)]
    changed_paths=list(paths)
    if 'task_attribution' in st and st.get('review_mode') != 'whole-mr':
        from al_task_attribution import classify_changes
        paths=classify_changes(st)['discovery_paths']
    for p in paths:
        unit=V.classify_unit(p,[],m.get('service_coverage',{}).get('semantic_requirements'))
        if unit not in m['units']:reasons.append('new touched unit requires replanning: '+unit);subjects.append(p)
    # A new instruction, hook, CI or manifest file anywhere is a rule source the plan never consulted.
    declared={r['path'] for r in m['rules']}
    for p in changed_paths:
        if p not in declared and (al_store.REPO_ROOT/p).is_file() and rule_source(p):
            reasons.append('new repository rule source: '+p);subjects.append(p)
    semantics=m.get('service_coverage',{}).get('semantic_requirements',{})
    expected=semantics.get('enumeration',{}).get('membership_sha256')
    if expected:
        from al_repository_facts import Snapshot,membership_digest
        opaque=[r['path'] for r in S.source_rows() if V.G.opaque(r)]
        scan=Snapshot(al_store.REPO_ROOT,st['identity']['merge_base'],m.get('paths',[]),opaque)
        if scan.gaps:
            reasons.append('repository membership incomplete; rediscover repository gates');pathless=True
        elif membership_digest(scan.members)!=expected:
            reasons.append('repository membership changed; rediscover repository gates')
            # Only removal of an already declared rule may explain membership
            # drift during automatic refresh. New source members need a new plan.
            missing_rules={r['path'] for r in m['rules'] if not (al_store.REPO_ROOT/r['path']).is_file()}
            restored=sorted(set(scan.members)|missing_rules)
            if missing_rules and membership_digest(restored)==expected:
                subjects.extend(missing_rules)
            else:pathless=True
    return {'current':not reasons,'reasons':sorted(set(reasons)),'superseded':ref,'paths':sorted(set(subjects)),'pathless':pathless}
