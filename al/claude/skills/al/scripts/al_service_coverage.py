"""Repository-independent coverage decisions and manifest execution bindings."""
from copy import deepcopy
from al_repository_facts import digest,semantic_requirements,within,validate_facts


def assess(facts):
    validate_facts(facts);deps=facts['dependencies'];uncertain=deps['uncertainties'];potential=deps['potential_services']
    selected=sorted(set(potential if uncertain else deps['known_affected'])|set(deps.get('policy_selection',[])))
    enum=[*facts['enumeration']['gaps'],*facts['unsupported_boundaries']]
    if not facts['scan']['stable']:enum.append({'code':'torn-scan','path':'.','reason':'repository changed during fact collection','unit':None})
    obligations=[o for o in facts['checks']['obligations'] if set(o['units'])&set(selected)]
    bindings=[b for b in facts['checks']['bindings'] if set(b['units'])&set(selected)]
    gaps=[g for g in facts['checks']['gaps'] if g.get('unit') is None or g['unit'] in selected]
    bound={oid for b in bindings for oid in b['obligation_ids']}
    for o in obligations:
        if o['id'] not in bound and not any(g.get('unit') in o['units'] for g in gaps):gaps.append({'code':'missing-check','unit':o['units'][0] if o['units'] else None,'path':'.','category':o['category'],'reason':'required obligation has no executable binding: '+o['id']})
    return {'schema_version':1,'outcome':'incomplete' if enum or gaps else 'exhaustive' if uncertain else 'identified',
            'known_affected':deps['known_affected'],'potential_services':potential,'selected_services':selected,'dependency_uncertainties':uncertain,
            'enumeration':{'status':'incomplete' if enum else 'complete','boundary_ids':facts['enumeration']['boundaries'],'gap_ids':[g['code'] for g in enum]},
            'check_discovery':{'status':'incomplete' if gaps else 'complete','obligation_ids':[o['id'] for o in obligations],'gap_ids':[g['code'] for g in gaps]},'required_bindings':bindings,'gaps':enum+gaps}


def validate_assessment(value):
    if value.get('schema_version')!=1 or value.get('outcome') not in {'identified','exhaustive','incomplete'}:raise ValueError('invalid service coverage assessment')
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
    if gate.get('required') is not True or gate.get('applicability')!='APPLICABLE':gaps.append('required executable check was disabled: '+expected['id'])
    for key in ('units','env_names'):
        if not set(expected[key])<=set(gate.get(key,[])):gaps.append('check omits '+key+': '+expected['id'])
    if not all(any(within(scope,p) for scope in gate.get('scope',[])) for p in expected['required_scope']):gaps.append('check omits required scope: '+expected['id'])
    if not all(item in gate.get('sources',[]) for item in expected['sources']):gaps.append('check omits source capability: '+expected['id'])
    chain=expected['source_chain']
    if chain:
        source={k:chain[0][k] for k in ('path','sha256')}
        if expected['category']=='ci':source['job']=chain[0]['selector']
        if gate.get('source')!=source:gaps.append('check source differs from sourced chain: '+expected['id'])
    if gate.get('obligation_ids')!=expected['obligation_ids'] or gate.get('execution_signature')!=expected['execution_signature']:gaps.append('check obligation/signature binding differs: '+expected['id'])
    return gaps


def declaration(facts,assessment,gates):
    semantics=semantic_requirements(facts)
    provenance={'scan_inputs':facts['scan_inputs'],'rule_sources':facts['rule_sources'],'membership':facts['enumeration']['observed_members'],'absent_paths':facts['enumeration']['absent_paths']}
    provenance['digest']=digest(provenance)
    return {'schema_version':1,'adapter':facts['adapter'],'policy_version':facts['policy_version'],'repository':facts['repository'], 'assessment':assessment,'semantic_requirements':semantics,'semantic_sha256':digest(semantics),'provenance':provenance,
            'bindings':[{'obligation_id':oid,'gate_id':g['id'],'execution_signature':g['execution_signature']} for g in gates if g.get('execution_signature') for oid in g.get('obligation_ids',[])]}


def validate_declaration(value,gates):
    if not isinstance(value,dict) or value.get('schema_version')!=1 or value.get('policy_version')!=1 or value.get('adapter') not in ({'id':'python','version':1},{'id':'contract','version':1}):raise ValueError('unsupported coverage declaration')
    validate_assessment(value['assessment']);semantics=value['semantic_requirements']
    if digest(semantics)!=value['semantic_sha256']:raise ValueError('corrupt coverage semantic hash')
    for key in ('adapter','policy_version','repository'):
        if value[key]!=semantics[key]:raise ValueError('inconsistent coverage '+key)
    reconstructed={**semantics,'scan':{'stable':semantics['stable']}}
    expected=assess(reconstructed)
    normalized=deepcopy(value['assessment'])
    normalized['dependency_uncertainties']=[{k:v for k,v in u.items() if k!='location'} for u in normalized['dependency_uncertainties']]
    if normalized!=expected:raise ValueError('coverage assessment contradicts semantic facts')
    deps=semantics['dependencies'];assessment=value['assessment']
    for key in ('known_affected','potential_services'):
        if assessment[key]!=deps[key]:raise ValueError('assessment differs from semantic requirements: '+key)
    selected=sorted(set(deps['potential_services'] if deps['uncertainties'] else deps['known_affected'])|set(deps.get('policy_selection',[])))
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


def current_gaps(facts,assessment,manifest):
    value=manifest.get('service_coverage');out=[]
    relevant=bool(facts['units'] or assessment['gaps'] or assessment['dependency_uncertainties'])
    if not value:return ['service coverage declaration required; rediscover repository gates'] if relevant else []
    if value['semantic_sha256']!=digest(semantic_requirements(facts)):out.append('service coverage requirements changed; rediscover repository gates')
    old=value['assessment']
    for key in ('outcome','known_affected','potential_services','selected_services','enumeration','check_discovery'):
        if old[key]!=assessment[key]:out.append('service coverage assessment changed: '+key)
    if assessment['outcome']=='incomplete' and not any(g['category']=='lint-imports' and g['applicability']=='UNRESOLVED' and g.get('required') is True for g in manifest['gates']):out.append('coverage incomplete requires a blocking unresolved coverage gate')
    for binding in assessment['required_bindings']:
        matches=[g for g in manifest['gates'] if g.get('obligation_ids')==binding['obligation_ids']]
        if not matches:out.append('missing sourced check binding: '+binding['id'])
        else:out.extend(validate_check_binding(binding,matches[0]))
    return out
