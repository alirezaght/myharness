"""Structured gate currency; corruption is distinct from ordinary replanning."""
import hashlib
import al_ci_context as CC
import al_artifacts as A
import al_gate_discovery as D
import al_scope as S
import al_store
import al_gate_inventory as V


def check(st):
    refs=st.get('gate_manifests',[])
    if not refs:return {'current':False,'reasons':['repository gate manifest missing'],'superseded':None}
    ref=refs[-1];m=A.read_json(ref);D.validate_manifest(st,m,check_sources=False)
    context=CC.binding(st,m.get('ci_context'),allow_historical=True)
    reasons=V.gaps(st,m) if context['current'] else ['CI context belongs to a superseded task revision; declare replacement context']
    sources=m['rules']+[g['source'] for g in m['gates'] if g.get('source')]
    for source in sources:
        p=al_store.REPO_ROOT/S.safe_path(source['path'])
        if not p.is_file():reasons.append('source rule missing: '+source['path'])
        elif hashlib.sha256(p.read_bytes()).hexdigest()!=source['sha256']:reasons.append('source rule changed: '+source['path'])
    changed=S.git_bytes('diff','--name-only','-z',st['identity']['merge_base']).split(b'\0')
    changed+=S.git_bytes('ls-files','--others','--exclude-standard','-z').split(b'\0')
    for raw in filter(None,changed):
        p=raw.decode();unit='/'.join(p.split('/')[:2]) if p.startswith(('services/','apps/','packages/')) else '.'
        if unit not in m['units']:reasons.append('new touched unit requires replanning: '+unit)
    return {'current':not reasons,'reasons':sorted(set(reasons)),'superseded':ref}
