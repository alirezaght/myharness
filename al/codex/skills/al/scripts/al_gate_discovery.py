"""Sourced repository obligations; ambiguity remains unresolved."""
import hashlib
import re
import al_store
import al_artifacts as A
import al_gate_inventory as V
import al_discovery_inputs as I
import al_scope as S
import al_sources as Sources
import al_service_coverage as Coverage
import al_review_state as ReviewState
from al_schema import refuse

CATEGORIES=('lint-imports','loc','contracts','pre-commit','ci')

source=V.source

def discover(st,paths,ci_context=None,*,bounded=False):
    # Discover over the expanded task paths the manifest will record: validating that
    # manifest asks the identical question and reuses this inventory (see reuse_inventories).
    expanded=V.discovery_paths(st,paths,ci_context,bounded=bounded)
    def finalize(inventory):
        manifest=_discover_captured(st,inventory,ci_context)
        if bounded:manifest['discovery_mode']='bounded'
        return manifest
    return V.inventory(st,expanded,ci_context,_finalize=finalize,bounded=bounded)


def _discover_captured(st,inventory,ci_context):
    source_by_path={r['path']:r for r in inventory['rules']}
    def source(path,job=None):
        value=source_by_path.get(path)
        if value is None:raise ValueError('gate source absent from discovery capture: '+path)
        return {**value,'job':job}
    paths=inventory['paths'];units=inventory['units'];rules=inventory['rules']
    previous={}
    for ref in st.get('gate_manifests',[]):
        for g in A.read_json(ref)['gates']:previous[g['id']]={'env_names':g.get('env_names',[]),'sources':g.get('sources',[]),'review_state':g.get('review_state')}
    gates=[]
    def gate(id,category,argv,scope,src,unit='.',reason=None):
        gates.append({'id':id,'category':category,'applicability':'APPLICABLE' if argv else 'UNRESOLVED',
                      'env_names':previous.get(id,{}).get('env_names',[]),'sources':previous.get(id,{}).get('sources',[]),'reason':reason,'required':True,'units':[unit],'argv':argv,'cwd':'.','scope':scope,'source':src,'execution_kind':'local'})
        # A planner-declared review-hook state location stays with its gate across rediscovery.
        if previous.get(id,{}).get('review_state'):gates[-1]['review_state']=previous[id]['review_state']
    assessment=inventory['service_coverage']
    for binding in assessment['required_bindings']:
        chain=binding['source_chain'];src={k:chain[0][k] for k in ('path','sha256')} if chain else None
        if binding['category']=='ci':src['job']=chain[0]['selector']
        gate(binding['id'],binding['category'],binding['argv'],binding['required_scope'],src)
        gates[-1].update(units=binding['units'],cwd=binding['cwd'],execution_kind=binding['execution_kind'],
                         obligation_ids=binding['obligation_ids'],execution_signature=binding['execution_signature'])
        if binding.get('remote_source'):gates[-1]['remote_source']=binding['remote_source']
        if binding.get('package_script'):gates[-1]['package_script']=binding['package_script']
        if binding.get('advisory'):gates[-1].update(required=False,advisory=binding['advisory'],reason='informational: '+binding['advisory'])
        gates[-1]['env_names']=sorted(set(gates[-1]['env_names']+binding['env_names']))
        gates[-1]['sources']=sorted(gates[-1]['sources']+[v for v in binding['sources'] if v not in gates[-1]['sources']],key=str)
    required_ids=set(inventory['relevance_projection']['relevant_obligation_ids'])
    for profile in inventory['repository_facts']['checks'].get('package_scripts',[]):
        identifier='package-'+__import__('al_repository_facts').digest([profile['unit'],profile['script']])[:16]
        if identifier not in required_ids or any(g['id']==identifier for g in gates):continue
        primary=next((r for r in profile['manager']['inputs'] if r['path']==__import__('al_package_checks').joined(profile['unit'],'package.json')),None)
        if primary:
            gate(identifier,'ci',[],[profile['unit']],{**primary,'job':'scripts.'+profile['script']},unit=profile['unit'],reason='package execution pending; '+'; '.join(profile['execution']['reasons']))
            gates[-1]['package_script']=profile
    if assessment['outcome']=='incomplete' and Coverage.imports_needed(inventory['repository_facts'],assessment):
        gate('imports-unresolved','lint-imports',[],sorted(set(paths+inventory['import_dependencies'])),rules[0] if rules else None,
             reason='; '.join(g['code']+': '+str(g.get('unit') or g.get('path'))+' '+g['reason'] for g in assessment['gaps']))
        gates[-1]['units']=units
    def not_applicable(id,category,reason):
        gates.append({'id':id,'category':category,'applicability':'NOT_APPLICABLE','required':False,'reason':reason,'units':units,'argv':[],'scope':paths,'cwd':'.','source':rules[0] if rules else None,'execution_kind':'local'})
    if not any(g['category']=='lint-imports' for g in gates):
        not_applicable('imports-na','lint-imports','Independently enumerated no affected Python architecture service')
    # A repository opts into a LOC limit or a pre-commit hook by committing one; without one there is
    # nothing to run, so the category is not applicable rather than unverified.
    if inventory['auxiliary']['loc']:
        # The checker runs as the repository defines it (its own scan roots, exclusions and allowlist);
        # passing the task's directories would scan generated or tooling code it never covers.
        gate('loc','loc',['python3',V.LOC_CHECKER],['.'],source(V.LOC_CHECKER))
    else:not_applicable('loc','loc','The repository commits no LOC checker')
    hook=inventory['auxiliary']['hook']
    if hook:gate('hook','pre-commit',['sh',hook],['.'],source(hook))
    else:not_applicable('hook','pre-commit','The repository commits no pre-commit hook')
    for job in inventory['ci']['obligations']:
        if __import__('al_coverage_relevance').ci_record_id(job) not in inventory['relevance_projection']['relevant_ci_ids']:continue
        if any(g.get('source',{}).get('job')==job['job'] and g['source'].get('path')==job['source_path'] and g['category']=='ci' for g in gates if g.get('source')):continue
        src={'path':job['source_path'],'sha256':job['source_sha256'],'job':job['job']}
        scope=sorted(set(job['inputs']+[d['path'] for d in job['dependencies']]))
        execution=job.get('execution') if job['applicability']=='APPLICABLE' else None
        resolution='' if execution else ('; the pipeline verifies it (unverified locally): record its result with `gates ci-evidence`'
                                         +(', or plan this gate as its verbatim script (`ci_recipe`)' if job.get('local_recipe') else ''))
        gate('ci-'+hashlib.sha256((job['source_path']+'\0'+job['job']).encode()).hexdigest()[:12],'ci',execution['argv'] if execution else [],scope,src,reason=job['reason']+resolution)
        gates[-1]['units']=job['units']
        if job.get('local_recipe') and not execution:
            # Offered, not applied: the gate stays UNRESOLVED until a planner maps it (#17).
            gates[-1]['ci_recipe']=job['local_recipe']
            gates[-1]['env_names']=sorted(set(gates[-1]['env_names'])|set(job['local_recipe']['env_names']))
        if job.get('advisory'):gates[-1].update(required=False,advisory=job['advisory'])
        if execution:
            gates[-1]['env_names']=sorted(set(gates[-1]['env_names']+execution['env_names']))
            gates[-1]['execution_kind']=execution['execution_kind']
            if execution.get('remote_source'):gates[-1]['remote_source']=execution['remote_source']
    if inventory['auxiliary']['contracts']:
        declarations=inventory['auxiliary']['contract_declarations']
        primary=declarations[0]
        details='; '.join(row['path']+': '+(row['reason'] or ', '.join('scripts.'+key for key in row['scripts'])) for row in declarations)
        scope=sorted(set(paths+[row['path'] for row in declarations]))
        gate('contracts','contracts',[],scope,source(primary['path']),reason='Resolve the exact contract generation/drift check from sourced rules; '+details)
    else:not_applicable('contracts','contracts','No package script generates or builds contracts')
    if not inventory['ci']['obligations']:
        other=inventory['auxiliary']['other_ci']
        # Unsupported providers are recorded as unknown inventory in the coverage declaration.
        if other:gate('ci','ci',[],paths,rules[-1] if rules else None,reason='CI defined in '+', '.join(other)+' cannot establish complete local check inventory')
        else:not_applicable('ci','ci',('No task-applicable CI obligation under projection '+inventory['relevance_projection']['semantic_sha256']) if inventory['ci'].get('deferred') else 'The repository defines no CI')
    rows=S.source_rows()
    for item in gates:
        if item['applicability']=='NOT_APPLICABLE':continue
        item['sources']=sorted(item.get('sources',[])+Sources.default_parent_entries(item['scope'],item.get('sources',[]),rows),key=str)
        gaps=Sources.capability_gaps(item['scope'],item.get('sources',[]),rows,item['cwd'],root=al_store.REPO_ROOT)
        if gaps:
            item.update(applicability='UNRESOLVED',reason='; '.join(gaps))
    manifest={'schema_version':1,'id':'G1','revision':1,'base_oid':st['identity']['merge_base'],'units':units,'paths':paths,'rules':rules,'gates':gates}
    manifest['service_coverage']=Coverage.declaration(inventory['repository_facts'],assessment,gates)
    if ci_context is not None:manifest['ci_context']=ci_context
    return manifest

def validate_manifest(st,m,check_sources=True):
    if m.get('discovery_mode',None) not in (None,'bounded'):refuse('unsupported gate discovery mode')
    if m.get('schema_version')!=1 or m.get('base_oid')!=st['identity']['merge_base']: refuse('gate manifest schema/base mismatch')
    if not isinstance(m.get('rules'),list) or not isinstance(m.get('units'),list):refuse('manifest rule/unit arrays required')
    for unit in m['units']:
        if not isinstance(unit,str) or not unit:refuse('corrupt manifest unit')
        if unit!='.':S.safe_path(unit)
    for rule in m.get('rules',[]):
        p=al_store.REPO_ROOT/S.safe_path(rule['path'])
        if not re.fullmatch(r'[0-9a-f]{64}',str(rule.get('sha256',''))):refuse('corrupt source rule hash')
        if check_sources and (not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=rule.get('sha256')): refuse('repository rule changed: '+rule['path'])
    gates=m.get('gates'); ids=[]; safe_scopes=set()
    if not isinstance(gates,list): refuse('manifest needs gates')
    for g in gates:
        ids.append(g.get('id'))
        try:g['sources']=Sources.validate_sources(g.get('sources',[]))
        except Sources.SourceError as exc:refuse('invalid gate sources: '+str(exc))
        if any(not Sources.intersects(g.get('scope',[]),item['path']) for item in g['sources']):refuse('gate source declaration outside scope')
        names=g.setdefault('env_names',[])
        if not isinstance(names,list) or any(not isinstance(n,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',n) for n in names) or len(names)!=len(set(names)):refuse('invalid gate env_names')
        if 'env' in g:refuse('gate environment dependencies use env_names')
        if 'hermetic' in g and not isinstance(g['hermetic'],bool):refuse('gate hermetic must be true or false')
        if 'review_state' in g:
            try:g['review_state']=ReviewState.validate_declaration(g['review_state'])
            except ValueError as exc:refuse('invalid gate review_state: '+str(exc))
            if g.get('applicability')!='APPLICABLE' or g.get('execution_kind')!='local':refuse('review_state requires an applicable local gate')
        if not isinstance(g.get('scope'),list) or not isinstance(g.get('units'),list): refuse('gate scope/units arrays required')
        for path in g['scope']:
            # Gates share their (often repository-sized) scopes: check each path once.
            if path!='.' and path not in safe_scopes: S.safe_path(path);safe_scopes.add(path)
        if g.get('cwd')!='.': S.safe_path(g.get('cwd',''))
        if g.get('category') not in CATEGORIES or g.get('applicability') not in {'APPLICABLE','NOT_APPLICABLE','UNRESOLVED'}: refuse('invalid gate category/applicability')
        if 'advisory' in g and (not isinstance(g['advisory'],str) or not g['advisory'] or g.get('required') is not False
                                or g['applicability']=='NOT_APPLICABLE' or not (g.get('obligation_ids') or g['category']=='ci')):
            refuse('advisory gate needs a sourced advisory CI step and required=false')
        if 'ci_recipe' in g:
            recipe=g['ci_recipe']
            if (g.get('category')!='ci' or not isinstance(recipe,dict) or set(recipe)!={'argv','cwd','execution_kind','env_names','semantics'}
                    or not isinstance(recipe['argv'],list) or not recipe['argv'] or any(not isinstance(v,str) for v in recipe['argv'])
                    or recipe['execution_kind']!='local' or recipe['cwd']!='.'):
                refuse('invalid CI recipe: it must be the verbatim sourced job script offered by discovery')
            if g.get('applicability')=='APPLICABLE' and (g.get('argv')!=recipe['argv'] or g.get('execution_kind')!='local' or g.get('cwd')!='.'):
                refuse('a gate mapped to its CI recipe must run exactly the recipe argv locally')
        if g['applicability']=='NOT_APPLICABLE':
            if not g.get('reason') or g.get('required') or (m.get('rules') and not g.get('source')): refuse('N/A needs reason and required=false')
        elif g['applicability']=='APPLICABLE':
            if not g.get('argv') or not isinstance(g['argv'],list) or not g.get('scope') or not g.get('source'): refuse('executable gate needs argv/scope/source')
            if g.get('execution_kind') not in {'local','remote'}: refuse('gate execution kind required')
            if g['execution_kind']=='remote':
                remote=g.get('remote')
                if g.get('remote_source',{}).get('provider')=='github':
                    try:
                        if remote is not None:__import__('al_remote_github').selection(g)
                        elif g['argv']!=['gh','api']:raise ValueError('GitHub lookup argv required')
                    except (ValueError,KeyError,TypeError) as exc:refuse('invalid GitHub selection: '+str(exc))
                else:
                    if not isinstance(remote,dict) or remote.get('provider')!='gitlab' or not remote.get('project_id') or not remote.get('job_name'): refuse('remote gate requires GitLab project/job provenance')
                    if g['source'].get('job')!=remote['job_name']:refuse('remote job identity must match source.job')
                    if len(g['argv'])!=3 or g['argv'][:2]!=['glab','api'] or not g['argv'][2].startswith('projects/'+str(remote['project_id'])+'/jobs/'): refuse('remote gate must query a real GitLab job endpoint')
        if 'package_script' in g:
            try:__import__('al_package_checks').validate_profile(g['package_script'])
            except (ValueError,TypeError,KeyError) as exc:refuse('invalid package-script profile: '+str(exc))
        if g.get('source'):
            src=g['source']; p=al_store.REPO_ROOT/S.safe_path(src['path'])
            if not re.fullmatch(r'[0-9a-f]{64}',str(src.get('sha256',''))):refuse('corrupt gate source hash')
            unavailable=src['sha256']=='0'*64
            if unavailable:
                job=src.get('job')
                if not (g['category']=='ci' and g['applicability']=='UNRESOLVED' and g.get('required') is True
                        and g.get('argv')==[] and g.get('execution_kind')=='local'
                        and all(key not in g for key in ('remote','remote_source','package_script','review_state','execution_signature','obligation_ids','runner_resource'))
                        and isinstance(job,str) and re.fullmatch(r'<unresolved-(?:definition-[0-9]+|workflows?)>',job)):
                    refuse('unavailable CI source cannot authorize execution or waive its obligation')
                # This is unknown inventory identity, never a content hash. The
                # current inventory is independently reconstructed below.
            elif check_sources and (not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=src.get('sha256')): refuse('gate source rule changed: '+src['path'])
    if len(ids)!=len(set(ids)) or any(not i for i in ids): refuse('duplicate/missing gate IDs')
    if set(CATEGORIES)-{g['category'] for g in gates}: refuse('manifest omits repository gate categories')
    if 'service_coverage' in m:
        try:Coverage.validate_declaration(m['service_coverage'],gates)
        except (ValueError,TypeError,KeyError) as exc:refuse('corrupt service coverage: '+str(exc))
    if check_sources:V.validate(st,m)
