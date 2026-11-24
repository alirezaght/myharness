"""Structured gate currency; corruption is distinct from ordinary replanning."""
import fnmatch
from pathlib import PurePosixPath
import al_ci_context as CC
import al_artifacts as A
import al_gate_discovery as D
import al_gate_inventory as V
import al_scope as S


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


REQUIREMENTS='task coverage requirements changed; rediscover repository gates'


def _widened(now,planned):
    """Paths the task question gained when that is the projection's only difference, else None.

    An edit to a file outside the planned paths (a unit's nested AGENTS.md reached from a
    planned file) only adds it to the question's changed paths; every requirement stays."""
    import al_coverage_relevance as R
    a=R.semantic_projection(now);b=R.semantic_projection(planned)
    qa=dict(a['question']);qb=dict(b['question'])
    added=sorted(set(qa.pop('changed_paths',[]))-set(qb.pop('changed_paths',[])))
    if not added or qa!=qb or {k:v for k,v in a.items() if k!='question'}!={k:v for k,v in b.items() if k!='question'}:return None
    return added


@S.decision
def check(st):
    """Whether the latest gate manifest still answers the task's coverage question, from one
    stability-checked observation of the working copy (S.decision)."""
    refs=st.get('gate_manifests',[])
    if not refs:return {'current':False,'reasons':['repository gate manifest missing'],'superseded':None,'paths':[],'pathless':True}
    ref=refs[-1];manifest=A.read_json(ref);declaration=manifest.get('service_coverage')
    if declaration is None:return {'current':False,'reasons':['coverage projection missing; supported v2 rediscovery/replanning required'],'superseded':ref,'paths':[],'pathless':True}
    D.validate_manifest(st,manifest,check_sources=False)
    if declaration.get('schema_version')==1:
        return {'current':False,'reasons':['coverage policy changed; supported v2 rediscovery/replanning required'],
                'superseded':ref,'paths':[],'pathless':True}
    context=CC.binding(st,manifest.get('ci_context'),allow_historical=True)
    if not context['current']:
        return {'current':False,'reasons':['CI context belongs to a superseded task revision; declare replacement context'],
                'superseded':ref,'paths':[],'pathless':True}
    # The answer for this manifest and these inventory inputs is recorded with the capture it
    # read; while that capture re-observes unchanged, it is the answer a new inventory gives (#25).
    try:inputs=V.inventory_inputs(st,manifest.get('paths',[]),manifest.get('ci_context'),bounded=manifest.get('discovery_mode')=='bounded')
    except (OSError,ValueError,TypeError,KeyError,SystemExit):inputs=None
    key=V.answer_key({'kind':'gate-currency','manifest':ref.get('sha256'),
                      'context':context},inputs,'al_gate_currency') if inputs is not None else None
    if key is not None:
        recorded=V.verified_answer(key)
        if isinstance(recorded,dict) and isinstance(recorded.get('reasons'),list):return recorded
    result=_current(st,manifest,declaration,ref,inputs)
    if key is not None and not any(r.startswith('coverage capture unavailable') for r in result['reasons']):
        V.record_answer(key,inputs,result)
    return result


def _current(st,manifest,declaration,ref,inputs):
    from al_repository_capture import CaptureDrift,CaptureUnavailable
    reasons=[];subjects=[];pathless=False
    try:current=V.inventory(st,manifest.get('paths',[]),manifest.get('ci_context'),bounded=manifest.get('discovery_mode')=='bounded',_inputs=inputs)
    except (CaptureDrift,CaptureUnavailable) as exc:
        return {'current':False,'reasons':['coverage capture unavailable: '+exc.reason],
                'superseded':ref,'paths':[],'pathless':True,
                **({'drift_paths':list(exc.paths)} if isinstance(exc,CaptureDrift) else {})}
    assessment=current['service_coverage'];projection=current['relevance_projection']
    # The assessment holds only gaps in the change's scope (gaps outside its reach are
    # verification notes); each is named so the refusal says what is uncovered.
    import al_service_coverage as Coverage
    for gap in assessment['gaps']:
        reasons.append('task-relevant coverage incomplete: '+Coverage.describe_gap(gap));pathless=True
    widened=None
    if projection['semantic_sha256']!=declaration['projection']['semantic_sha256']:
        reasons.append(REQUIREMENTS);pathless=True
        widened=_widened(projection,declaration['projection'])
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
            'paths':sorted(set(subjects)),'pathless':pathless,'coverage_gaps':assessment['gaps'],
            **({'widened_paths':widened} if widened else {})}
