"""Structured gate currency; corruption is distinct from ordinary replanning."""
import fnmatch
from pathlib import PurePosixPath
import al_ci_context as CC
import al_artifacts as A
import al_gate_discovery as D
import al_gate_inventory as V


def rule_source(path):
    name=PurePosixPath(path).name
    if name in {'AGENTS.md','CLAUDE.md','package.json','pyproject.toml','.gitignore','pnpm-workspace.yaml'}:return True
    # fnmatch's `*` crosses directories, so `ci/**/*.yml` also matches files directly under ci/.
    return any(fnmatch.fnmatchcase(path,pattern.replace('**/','*')) for pattern in V.PATTERNS)


def _execution_semantics(bindings):
    def semantic(value):
        if isinstance(value,dict):
            return {k:semantic(v) for k,v in value.items() if k not in {'sha256','source_sha256','execution_signature'}}
        if isinstance(value,list):return [semantic(v) for v in value]
        return value
    return [semantic(b) for b in bindings]


def check(st):
    from al_repository_capture import CaptureDrift,CaptureUnavailable
    refs=st.get('gate_manifests',[])
    if not refs:return {'current':False,'reasons':['repository gate manifest missing'],'superseded':None,'paths':[],'pathless':True}
    ref=refs[-1];manifest=A.read_json(ref);declaration=manifest.get('service_coverage')
    if declaration is None:return {'current':False,'reasons':['coverage projection missing; supported v2 rediscovery/replanning required'],'superseded':ref,'paths':[],'pathless':True}
    D.validate_manifest(st,manifest,check_sources=False)
    if declaration.get('schema_version')==1:
        return {'current':False,'reasons':['coverage policy changed; supported v2 rediscovery/replanning required'],
                'superseded':ref,'paths':[],'pathless':True}
    context=CC.binding(st,manifest.get('ci_context'),allow_historical=True)
    reasons=[];subjects=[];pathless=False
    if not context['current']:
        return {'current':False,'reasons':['CI context belongs to a superseded task revision; declare replacement context'],
                'superseded':ref,'paths':[],'pathless':True}
    try:current=V.inventory(st,manifest.get('paths',[]),manifest.get('ci_context'))
    except (CaptureDrift,CaptureUnavailable) as exc:
        return {'current':False,'reasons':['coverage capture unavailable: '+exc.reason],
                'superseded':ref,'paths':[],'pathless':True}
    assessment=current['service_coverage'];projection=current['relevance_projection']
    if assessment['gaps']:
        reasons.append('task-relevant coverage incomplete; rediscover repository gates');pathless=True
    if projection['semantic_sha256']!=declaration['projection']['semantic_sha256']:
        reasons.append('task coverage requirements changed; rediscover repository gates');pathless=True
    if current['capture']['implementation_binding']!=declaration['capture']['implementation_binding']:
        reasons.append('coverage implementation policy changed; rediscover repository gates');pathless=True
    if _execution_semantics(assessment['required_bindings'])!=_execution_semantics(declaration['assessment']['required_bindings']):
        reasons.append('sourced execution requirements changed; rediscover repository gates');pathless=True
    sources={r['path']:r['sha256'] for r in current['rules']}
    for source in manifest['rules']+[g['source'] for g in manifest['gates'] if g.get('source')]:
        if source['path'] not in sources:
            reasons.append('source rule missing: '+source['path']);subjects.append(source['path'])
        elif sources[source['path']]!=source['sha256']:
            reasons.append('source rule changed: '+source['path']);subjects.append(source['path'])
    declared={r['path'] for r in manifest['rules']}
    for source in current['rules']:
        if source['path'] not in declared:
            reasons.append('new repository rule source: '+source['path']);subjects.append(source['path'])
    for unit in set(current['units'])-set(manifest['units']):
        reasons.append('new touched unit requires replanning: '+unit);pathless=True
    return {'current':not reasons,'reasons':sorted(set(reasons)),'superseded':ref,
            'paths':sorted(set(subjects)),'pathless':pathless}
