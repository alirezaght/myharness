"""Repository-independent coverage decisions and manifest execution bindings."""
from copy import deepcopy
from al_repository_facts import all_within,digest,semantic_requirements,within,validate_facts


def _assess_v1(facts):
    validate_facts(facts);deps=facts['dependencies'];uncertain=deps['uncertainties'];potential=deps['potential_services']
    selected=sorted(set(potential) if uncertain else set(deps['known_affected'])|set(deps.get('policy_selection',[])))
    enum=[*facts['enumeration']['gaps'],*facts['unsupported_boundaries']]
    if not facts['scan']['stable']:enum.append({'code':'torn-scan','path':'.','reason':'repository changed during fact collection','unit':None})
    obligations=[o for o in facts['checks']['obligations'] if set(o['units'])&set(selected)]
    bindings=[b for b in facts['checks']['bindings'] if set(b['units'])&set(selected)]
    gaps=[g for g in facts['checks']['gaps'] if g.get('unit') is None or g['unit'] in selected]
    bound={oid for b in bindings for oid in b['obligation_ids']}
    for o in obligations:
        if o['id'] not in bound:gaps.append({'code':'missing-check','unit':o['units'][0] if o['units'] else None,'path':'.','category':o['category'],'reason':'required obligation has no executable binding: '+o['id']})
    return {'schema_version':1,'outcome':'incomplete' if enum or gaps else 'exhaustive' if uncertain else 'identified',
            'known_affected':deps['known_affected'],'potential_services':potential,'selected_services':selected,'dependency_uncertainties':uncertain,
            'enumeration':{'status':'incomplete' if enum else 'complete','boundary_ids':facts['enumeration']['boundaries'],'gap_ids':[g['code'] for g in enum]},
            'check_discovery':{'status':'incomplete' if gaps else 'complete','obligation_ids':[o['id'] for o in obligations],'gap_ids':[g['code'] for g in gaps]},'required_bindings':bindings,'gaps':enum+gaps}


def validate_assessment(value):
    if value.get('schema_version') not in {1,2} or value.get('outcome') not in {'identified','exhaustive','incomplete'}:raise ValueError('invalid service coverage assessment')
    for key in ('known_affected','potential_services','selected_services','dependency_uncertainties','required_bindings','gaps'):
        if not isinstance(value.get(key),list):raise ValueError('invalid service coverage '+key)
    if not set(value['selected_services'])<=set(value['potential_services']):raise ValueError('selection outside coverage universe')
    expected='incomplete' if value['gaps'] else 'exhaustive' if value['dependency_uncertainties'] else 'identified'
    if expected!=value['outcome']:raise ValueError('inconsistent coverage outcome')
    if value['outcome']=='exhaustive' and set(value['selected_services'])!=set(value['potential_services']):raise ValueError('exhaustive selection omits services')


def validate_check_binding(expected,gate):
    gaps=[]
    for key in ('argv','cwd','execution_kind','category'):
        if gate.get(key)!=expected[key]:gaps.append('check '+key+' differs from sourced execution: '+expected['id'])
    # A check whose sourced CI step is advisory is informational (required=false); any other stays required.
    if gate.get('required') is not expected.get('required',True) or gate.get('applicability')!='APPLICABLE':gaps.append('required executable check was disabled: '+expected['id'])
    if gate.get('advisory')!=expected.get('advisory'):gaps.append('check advisory status differs from sourced CI: '+expected['id'])
    for key in ('units','env_names'):
        if not set(expected[key])<=set(gate.get(key,[])):gaps.append('check omits '+key+': '+expected['id'])
    if not all_within(gate.get('scope',[]),expected['required_scope']):gaps.append('check omits required scope: '+expected['id'])
    if not all(item in gate.get('sources',[]) for item in expected['sources']):gaps.append('check omits source capability: '+expected['id'])
    if expected.get('package_script')!=gate.get('package_script'):gaps.append('package-script profile differs from sourced execution: '+expected['id'])
    if expected.get('remote_source')!=gate.get('remote_source'):gaps.append('remote source differs from sourced execution: '+expected['id'])
    chain=expected['source_chain']
    if chain:
        source={k:chain[0][k] for k in ('path','sha256')}
        if expected['category']=='ci':source['job']=chain[0]['selector']
        if gate.get('source')!=source:gaps.append('check source differs from sourced chain: '+expected['id'])
    if gate.get('obligation_ids')!=expected['obligation_ids'] or gate.get('execution_signature')!=expected['execution_signature']:gaps.append('check obligation/signature binding differs: '+expected['id'])
    return gaps


def _declaration_v1(facts,assessment,gates):
    semantics=semantic_requirements(facts)
    # Per-file scan inputs are bound by digest and count: the manifest stays small on large repositories.
    provenance={'scan_inputs':{'count':len(facts['scan_inputs']),'sha256':digest(facts['scan_inputs'])},'rule_sources':facts['rule_sources'],'membership':facts['enumeration']['observed_members'],'absent_paths':facts['enumeration']['absent_paths'],'ignored_outputs':facts['enumeration'].get('ignored_outputs',[])}
    provenance['digest']=digest(provenance)
    return {'schema_version':1,'adapter':facts['adapter'],'policy_version':facts['policy_version'],'repository':facts['repository'], 'assessment':assessment,'semantic_requirements':semantics,'semantic_sha256':digest(semantics),'provenance':provenance,
            'bindings':[{'obligation_id':oid,'gate_id':g['id'],'execution_signature':g['execution_signature']} for g in gates if g.get('execution_signature') for oid in g.get('obligation_ids',[])]}


def _validate_declaration_v1(value,gates):
    if not isinstance(value,dict) or value.get('schema_version')!=1 or value.get('policy_version')!=1 or value.get('adapter') not in ({'id':'python','version':1},{'id':'contract','version':1}):raise ValueError('unsupported coverage declaration')
    validate_assessment(value['assessment']);semantics=value['semantic_requirements']
    if digest(semantics)!=value['semantic_sha256']:raise ValueError('corrupt coverage semantic hash')
    for key in ('adapter','policy_version','repository'):
        if value[key]!=semantics[key]:raise ValueError('inconsistent coverage '+key)
    reconstructed={**semantics,'scan':{'stable':semantics['stable']}}
    expected=_assess_v1(reconstructed)
    normalized=deepcopy(value['assessment'])
    normalized['dependency_uncertainties']=[{k:v for k,v in u.items() if k!='location'} for u in normalized['dependency_uncertainties']]
    if normalized!=expected:raise ValueError('coverage assessment contradicts semantic facts')
    deps=semantics['dependencies'];assessment=value['assessment']
    for key in ('known_affected','potential_services'):
        if assessment[key]!=deps[key]:raise ValueError('assessment differs from semantic requirements: '+key)
    selected=sorted(set(deps['potential_services']) if deps['uncertainties'] else set(deps['known_affected'])|set(deps.get('policy_selection',[])))
    if assessment['selected_services']!=selected:raise ValueError('assessment selection differs from semantic requirements')
    if [{k:v for k,v in u.items() if k!='location'} for u in assessment['dependency_uncertainties']]!=deps['uncertainties']:raise ValueError('assessment erased dependency uncertainty')
    if not isinstance(value['bindings'],list) or len(value['bindings'])>10000:raise ValueError('invalid coverage binding list')
    proof=deepcopy(value['provenance']);claimed=proof.pop('digest')
    if digest(proof)!=claimed:raise ValueError('corrupt coverage provenance hash')
    by_id={g['id']:g for g in gates};seen=set();expected={oid:b for b in value['assessment']['required_bindings'] for oid in b['obligation_ids']}
    for link in value['bindings']:
        oid=link['obligation_id'];gid=link['gate_id']
        if oid in seen or oid not in expected or gid not in by_id:raise ValueError('duplicate/foreign coverage binding')
        seen.add(oid)
        if link['execution_signature']!=expected[oid]['execution_signature'] or validate_check_binding(expected[oid],by_id[gid]):raise ValueError('corrupt coverage gate binding: '+oid)
    if seen!=set(expected):raise ValueError('coverage declaration omits obligation bindings')


def current_gaps(facts,assessment,manifest,*,admission=True):
    value=manifest.get('service_coverage');out=[]
    relevant=bool(facts['units'] or assessment['gaps'] or assessment['dependency_uncertainties'])
    if not value:return ['service coverage v2 declaration required; rediscover repository gates'] if facts['schema_version']==2 or relevant else []
    if value.get('schema_version')!=facts['schema_version']:return ['coverage policy changed; supported rediscovery/replanning required']
    if facts['schema_version']==2:
        import al_coverage_relevance as R
        previous=value.get('projection',{})
        if previous.get('semantic_sha256')!=R.project_task(facts)['semantic_sha256']:out.append('service coverage requirements changed; rediscover repository gates')
    elif value['semantic_sha256']!=digest(semantic_requirements(facts)):out.append('service coverage requirements changed; rediscover repository gates')
    old=value['assessment']
    for key in ('outcome','known_affected','potential_services','selected_services','enumeration','check_discovery'):
        if old[key]!=assessment[key]:out.append('service coverage assessment changed: '+key)
    for binding in assessment['required_bindings']:
        matches=[g for g in manifest['gates'] if g.get('obligation_ids')==binding['obligation_ids']]
        if not matches:out.append('missing sourced check binding: '+binding['id'])
        else:out.extend(validate_check_binding(binding,matches[0]))
    if admission:out.extend(readiness_gaps(value,manifest['gates']))
    return out


def describe_gap(g):
    return g['code']+': '+str(g.get('unit') or g.get('path'))+' '+g['reason']


# A gap that only means AL could not establish a check for a unit (no executable binding, an
# unmodelable manifest, package script or CI definition, an unclassified directory) is an
# unverified check: listed for review, never blocking (#157). Every other gap blocks - torn
# captures, link/gitlink/opaque boundaries, a changed coverage contract, unreadable or missing
# inputs, unknown impact - and so does any code not listed here (fail closed).
UNVERIFIED_CODES=frozenset({
    'missing-check','missing-verification-check','missing-architecture-check',
    'unsupported-architecture-chain','missing-manifest','package-execution-pending',
    'package-discovery-unavailable','package-script-nonliteral','package-semantic-unverified',
    'unclassified-unit','unknown-ci-inventory','unsupported-ci-provider',
})


def blocking(g):
    return str(g.get('code','')) not in UNVERIFIED_CODES


def readiness_gaps(value,gates):
    """Reconstruct coverage before granting any admission; sentinels confer no authority.

    Only gaps in the change's scope are listed: gaps outside the paths and units the change
    reaches are recorded as the assessment's verification notes and never block."""
    validate_declaration(value,gates)
    a=value['assessment']
    if a['outcome']!='incomplete':return []
    return sorted(set('coverage incomplete: '+describe_gap(g) for g in a['gaps'] if blocking(g)))


def notes(value):
    """Verification notes: coverage gaps outside the change's reach (never blocking)."""
    if not isinstance(value,dict):return []
    assessment=value.get('assessment',{})
    outside={'coverage note (outside the change\'s reach; not verified by this task): '+describe_gap(g)
             for g in assessment.get('coverage_notes',[])}
    inside={'coverage unverified (in the change\'s scope; AL could not establish this check): '+describe_gap(g)
            for g in assessment.get('gaps',[]) if not blocking(g)}
    return sorted(outside|inside)


def coverage_notes(st):
    import al_artifacts as A
    refs=st.get('gate_manifests',[])
    return notes(A.read_json(refs[-1]).get('service_coverage')) if refs else []


def admission_gaps(st,*,current=False):
    import al_artifacts as A
    refs=st.get('gate_manifests',[])
    if not refs:return ['repository gate manifest missing']
    manifest=A.read_json(refs[-1]);value=manifest.get('service_coverage')
    out=readiness_gaps(value,manifest['gates']) if value else []
    if current:
        import al_gate_inventory as V
        out.extend(V.gaps(st,manifest))
    return sorted(set(out))


NEXT_COVERAGE_STEP=('next: repair the named gaps, then rediscover task-relevant gates with '
                    '`gates discover --paths-file <json> --base <pinned-base>` and '
                    '`gates plan --file <manifest>`; `recovery next` names any earlier prerequisite')

BOUNDARY_REPAIR=('AL cannot enumerate this link/gitlink the change reaches: run AL rooted in its target '
                 'repository, or replace the link with ordinary files')
PACKAGE_REPAIR=("make the unit's package manager, workspace members and scripts literal and available "
                '(the reason names what is missing)')
CHECK_REPAIR="declare a supported check for the unit (manifest test/lint/architecture configuration or a CI job)"
REPAIRS={
    'symlink-boundary':BOUNDARY_REPAIR,'gitlink-boundary':BOUNDARY_REPAIR,'opaque-boundary':BOUNDARY_REPAIR,
    'unsafe-boundary':BOUNDARY_REPAIR,'base-symlink':BOUNDARY_REPAIR,
    'package-execution-pending':PACKAGE_REPAIR,'package-discovery-unavailable':PACKAGE_REPAIR,
    'package-script-nonliteral':"replace the nonliteral package script with a literal command",
    'package-semantic-unverified':"give the unit's package script a supported, finite check configuration",
    'missing-check':CHECK_REPAIR,'missing-verification-check':CHECK_REPAIR,'missing-architecture-check':CHECK_REPAIR,
    'unsupported-architecture-chain':CHECK_REPAIR,
    'unclassified-unit':'add a supported manifest (pyproject.toml, package.json or go.mod) to the directory',
    'unknown-ci-inventory':'the CI definition the change edits cannot be enumerated: keep it within the supported definition limits',
    'unsupported-ci-provider':'the changed CI provider is unsupported: verify its jobs in the remote pipeline',
    'torn-capture':'the repository changed during discovery: let concurrent writers finish, then rediscover',
    'torn-scan':'the repository changed during discovery: let concurrent writers finish, then rediscover',
    'required-rule-boundary':'make the governing rule file a regular file',
}


def coverage_guidance(gaps,manifest=None):
    """Name each in-scope gap with its repair and the exact rediscovery commands (#14)."""
    gaps=[g for g in gaps if blocking(g)]
    if not gaps:return NEXT_COVERAGE_STEP
    lines=['next: these coverage gaps are in the change\'s scope (a changed path, its unit, or a unit or',
           'link it literally reaches); rediscovery alone cannot clear them:']
    for g in sorted(gaps,key=lambda g:(g['code'],str(g.get('unit')),str(g.get('path')))):
        where='unit '+g['unit'] if g.get('unit') else 'path '+str(g.get('path'))
        if g.get('unit') and g.get('path') not in {None,'.',g['unit']}:where+=' ('+g['path']+')'
        lines.append('  - '+g['code']+' at '+where+': '+g['reason']+' -> '+REPAIRS.get(g['code'],'repair the named source'))
    bounded=' --bounded' if (manifest or {}).get('discovery_mode')=='bounded' else ''
    lines+=['after the repair, rediscover over the same planned paths and replan:',
            '  gates discover --extend current --paths-file <file containing []>'+bounded+' --output <manifest.json>',
            '  gates plan --file <manifest.json>',
            'coverage gaps outside the change\'s reach, and checks AL cannot establish in it, are verification notes',
            '(`gates status` coverage_notes) and do not block']
    return '\n'.join(lines)


def manifest_guidance(st):
    """Guidance for the latest manifest's in-scope gaps (used where only gap strings are known)."""
    import al_artifacts as A
    refs=st.get('gate_manifests',[])
    if not refs:return NEXT_COVERAGE_STEP
    manifest=A.read_json(refs[-1]);value=manifest.get('service_coverage') or {}
    return coverage_guidance(value.get('assessment',{}).get('gaps',[]),manifest)


def require_ready(st,*,current=False):
    """Final proof, review and approval barriers: incomplete relevant coverage refuses there."""
    from al_schema import refuse
    missing=admission_gaps(st,current=current)
    if missing:refuse('service coverage incomplete (required before final proof, review and approval): '
                      +str(missing)+'\n'+manifest_guidance(st))


def record_obligations(st,*,current=True):
    """Plan admission and scoped writers record incomplete coverage as verification debt.

    Corrupt or tampered declarations still raise (integrity is never debt); every
    reconstructed gap becomes an implementation-readiness obligation that final
    proof, review and approval enforce through require_ready."""
    missing=admission_gaps(st,current=current)
    if not missing:return []
    value=st.get('implementation_readiness')
    if not value:
        from al_implementation import binding
        value=st['implementation_readiness']={'schema_version':1,'binding':binding(st),
                                              'task_id':None,'paths':[],'obligations':[]}
    value['obligations']=sorted(set(value['obligations'])|set(missing))
    return missing


def assess(facts):
    if facts.get('schema_version')==1:return _assess_v1(facts)
    import al_coverage_relevance as R
    validate_facts(facts)
    projection=R.project_task(facts)
    ids=set(projection['relevant_gap_ids'])
    relevant=[row['record'] for row in projection['records'] if row['record_id'] in ids]
    bindings=[b for b in facts['checks']['bindings'] if b['id'] in projection['required_binding_ids']]
    obligations=[o for o in facts['checks']['obligations'] if o['id'] in projection['relevant_obligation_ids']]
    bound={oid for b in bindings for oid in b['obligation_ids']}
    reach=R.change_reach(facts,projection['question'])
    outside={d['record_id'] for d in projection['deferred'] if d['reason']=='outside-change-reach'}
    notes=[row['record'] for row in projection['records'] if row['record_id'] in outside]
    for o in obligations:
        if o['id'] not in bound:
            record={'code':'missing-check','unit':o['units'][0] if o['units'] else None,
                    'path':'.','category':o['category'],'reason':'required obligation has no executable binding: '+o['id']}
            # A selected unit the change does not reach (selected only through dependency
            # uncertainty) has an unverified check: a verification note, not an obligation.
            if not o['units'] or set(o['units'])&reach['units']:relevant.append(record)
            else:notes.append(record)
    enum=[row['record_id'] for row in projection['records'] if row['record_id'] in ids and row['kind']=='enumeration']
    checks=[row['record_id'] for row in projection['records'] if row['record_id'] in ids and row['kind']=='check']
    deps=facts['dependencies'];uncertain=deps['uncertainties']
    return {'schema_version':2,'outcome':'incomplete' if relevant else 'exhaustive' if uncertain else 'identified',
            'known_affected':deps['known_affected'],'potential_services':deps['potential_services'],
            'selected_services':projection['selected_services'],'dependency_uncertainties':uncertain,
            'enumeration':{'status':'incomplete' if enum else 'complete','boundary_ids':facts['enumeration']['boundaries'],'gap_ids':enum},
            'check_discovery':{'status':'incomplete' if relevant and not enum else 'complete','obligation_ids':[o['id'] for o in obligations],'gap_ids':checks},
            'required_bindings':bindings,'gaps':relevant,'global_inventory':projection['global_inventory'],
            'deferred_record_ids':[d['record_id'] for d in projection['deferred']],
            'coverage_notes':notes,
            'projection_sha256':projection['semantic_sha256']}


def declaration(facts,assessment,gates):
    if facts.get('schema_version')==1:return _declaration_v1(facts,assessment,gates)
    import al_coverage_relevance as R
    value=_declaration_v1(facts,assessment,gates)
    value.update(schema_version=2,policy_version=2,projection=R.project_task(facts),
                 global_inventory=assessment['global_inventory'],capture=facts['capture'])
    value['provenance']['capture_sha256']=digest(facts['capture'])
    value['provenance']['digest']=digest({k:v for k,v in value['provenance'].items() if k!='digest'})
    return value


def validate_declaration(value,gates):
    if isinstance(value,dict) and value.get('schema_version')==1:
        return _validate_declaration_v1(value,gates)
    import re
    import al_coverage_relevance as R
    if not isinstance(value,dict) or value.get('schema_version')!=2 or value.get('policy_version')!=2:
        raise ValueError('unsupported coverage declaration')
    semantics=value['semantic_requirements']
    if semantics.get('schema_version')!=2 or semantics.get('policy_version')!=2:
        raise ValueError('mixed coverage policy versions')
    if digest(semantics)!=value['semantic_sha256']:raise ValueError('corrupt coverage semantic hash')
    for key in ('adapter','policy_version','repository'):
        if value[key]!=semantics[key]:raise ValueError('inconsistent coverage '+key)
    capture=value['capture']
    if capture.get('schema_version')!=2 or capture.get('stable') is not True or type(capture.get('attempts')) is not int or capture.get('attempts') not in {1,2}:
        raise ValueError('invalid coverage capture')
    if not isinstance(capture.get('discarded_attempts'),list) or len(capture['discarded_attempts'])!=capture['attempts']-1:raise ValueError('invalid capture retry lineage')
    for key in ('source_count','match_count','absence_count'):
        if type(capture.get(key)) is not int or capture[key]<0:raise ValueError('invalid capture bound summary')
    for key in ('implementation_binding','observations_digest'):
        if not re.fullmatch('[0-9a-f]{64}',capture.get(key,'')):raise ValueError('invalid capture binding')
    facts={**semantics,'scan':{'stable':semantics['stable']}}
    R.validate_projection(value['projection'],facts)
    expected=assess(facts)
    normalized=deepcopy(value['assessment'])
    normalized['dependency_uncertainties']=[{k:v for k,v in u.items() if k!='location'} for u in normalized['dependency_uncertainties']]
    if normalized!=expected:raise ValueError('coverage assessment contradicts semantic facts')
    if value['global_inventory']!=expected['global_inventory']:raise ValueError('corrupt global coverage inventory')
    validate_assessment(value['assessment'])
    provenance=deepcopy(value['provenance']);claimed=provenance.pop('digest')
    if digest(provenance)!=claimed or provenance.get('capture_sha256')!=digest(capture):
        raise ValueError('corrupt coverage provenance')
    summary=provenance.get('scan_inputs')
    if not isinstance(summary,dict) or type(summary.get('count')) is not int or summary['count']<0 or not re.fullmatch('[0-9a-f]{64}',summary.get('sha256','')):
        raise ValueError('invalid coverage input summary')
    expected={oid:b for b in expected['required_bindings'] for oid in b['obligation_ids']}
    by_id={g['id']:g for g in gates};seen=set()
    for link in value['bindings']:
        oid=link['obligation_id'];gid=link['gate_id']
        if oid in seen or oid not in expected or gid not in by_id:raise ValueError('duplicate/foreign coverage binding')
        seen.add(oid)
        if link['execution_signature']!=expected[oid]['execution_signature'] or validate_check_binding(expected[oid],by_id[gid]):
            raise ValueError('corrupt coverage gate binding: '+oid)
    if seen!=set(expected):raise ValueError('coverage declaration omits obligation bindings')


def imports_needed(facts,assessment):
    selected=set(assessment['selected_services'])
    observed=any(o['category']=='lint-imports' and set(o['units'])&selected for o in facts['checks']['obligations'])
    unknown=assessment['outcome']=='incomplete' and any(g['code'] not in {'unknown-ci-inventory','unsupported-ci-provider'} for g in assessment['gaps'])
    return observed or unknown
