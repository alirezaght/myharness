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

def discover(st,paths,ci_context=None):
    inventory=V.inventory(st,paths,ci_context)
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
        gates[-1]['env_names']=sorted(set(gates[-1]['env_names']+binding['env_names']))
        gates[-1]['sources']=sorted(gates[-1]['sources']+[v for v in binding['sources'] if v not in gates[-1]['sources']],key=str)
    if assessment['outcome']=='incomplete':
        gate('imports-unresolved','lint-imports',[],sorted(set(paths+inventory['import_dependencies'])),rules[0] if rules else None,
             reason='; '.join(g['code']+': '+str(g.get('unit') or g.get('path'))+' '+g['reason'] for g in assessment['gaps']))
        gates[-1]['units']=units
    if not any(g['category']=='lint-imports' for g in gates):
        gates.append({'id':'imports-na','category':'lint-imports','applicability':'NOT_APPLICABLE','required':False,'reason':'Independently enumerated no affected Python architecture service','units':units,'argv':[],'scope':paths,'cwd':'.','source':rules[0] if rules else None,'execution_kind':'local'})
    checker=al_store.REPO_ROOT/'scripts/check-loc.py'
    if checker.is_file() and I.loc_supported(checker):
        # The checker runs as the repository defines it (its own scan roots, exclusions and allowlist);
        # passing the task's directories would scan generated or tooling code it never covers.
        gate('loc','loc',['python3','scripts/check-loc.py'],['.'],source('scripts/check-loc.py'))
    else: gate('loc','loc',[],paths,source('scripts/check-loc.py') if checker.is_file() else None,reason='Resolve supported repository LOC checker interface')
    if (al_store.REPO_ROOT/'.husky/pre-commit').is_file(): gate('hook','pre-commit',['sh','.husky/pre-commit'],['.'],source('.husky/pre-commit'))
    else: gate('hook','pre-commit',[],paths,None,reason='Resolve repository pre-commit obligation')
    for job in inventory['ci']['obligations']:
        if any(g.get('source',{}).get('job')==job['job'] and g['source'].get('path')==job['source_path'] and g['category']=='ci' for g in gates if g.get('source')):continue
        src={'path':job['source_path'],'sha256':job['source_sha256'],'job':job['job']}
        scope=sorted(set(job['inputs']+[d['path'] for d in job['dependencies']]))
        execution=job.get('execution') if job['applicability']=='APPLICABLE' else None
        gate('ci-'+hashlib.sha256((job['source_path']+'\0'+job['job']).encode()).hexdigest()[:12],'ci',execution['argv'] if execution else [],scope,src,reason=job['reason']+'; map the sourced job to explicit local/remote verification')
        gates[-1]['units']=job['units']
        if execution:gates[-1]['env_names']=sorted(set(gates[-1]['env_names']+execution['env_names']))
    for category in ('contracts',) if inventory['ci']['obligations'] else ('contracts','ci'):
        gate(category,category,[],paths,rules[-1] if rules else None,reason='Resolve exact generation/drift or touched-unit CI job applicability from sourced rules')
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
    if m.get('schema_version')!=1 or m.get('base_oid')!=st['identity']['merge_base']: refuse('gate manifest schema/base mismatch')
    if not isinstance(m.get('rules'),list) or not isinstance(m.get('units'),list):refuse('manifest rule/unit arrays required')
    for unit in m['units']:
        if not isinstance(unit,str) or not unit:refuse('corrupt manifest unit')
        if unit!='.':S.safe_path(unit)
    for rule in m.get('rules',[]):
        p=al_store.REPO_ROOT/S.safe_path(rule['path'])
        if not re.fullmatch(r'[0-9a-f]{64}',str(rule.get('sha256',''))):refuse('corrupt source rule hash')
        if check_sources and (not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=rule.get('sha256')): refuse('repository rule changed: '+rule['path'])
    gates=m.get('gates'); ids=[]
    if not isinstance(gates,list): refuse('manifest needs gates')
    for g in gates:
        ids.append(g.get('id'))
        try:g['sources']=Sources.validate_sources(g.get('sources',[]))
        except Sources.SourceError as exc:refuse('invalid gate sources: '+str(exc))
        if any(not Sources.intersects(g.get('scope',[]),item['path']) for item in g['sources']):refuse('gate source declaration outside scope')
        names=g.setdefault('env_names',[])
        if not isinstance(names,list) or any(not isinstance(n,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',n) for n in names) or len(names)!=len(set(names)):refuse('invalid gate env_names')
        if 'env' in g:refuse('gate environment dependencies use env_names')
        if 'review_state' in g:
            try:g['review_state']=ReviewState.validate_declaration(g['review_state'])
            except ValueError as exc:refuse('invalid gate review_state: '+str(exc))
            if g.get('applicability')!='APPLICABLE' or g.get('execution_kind')!='local':refuse('review_state requires an applicable local gate')
        if not isinstance(g.get('scope'),list) or not isinstance(g.get('units'),list): refuse('gate scope/units arrays required')
        for path in g['scope']:
            if path!='.': S.safe_path(path)
        if g.get('cwd')!='.': S.safe_path(g.get('cwd',''))
        if g.get('category') not in CATEGORIES or g.get('applicability') not in {'APPLICABLE','NOT_APPLICABLE','UNRESOLVED'}: refuse('invalid gate category/applicability')
        if g['applicability']=='NOT_APPLICABLE':
            if not g.get('reason') or g.get('required') or (m.get('rules') and not g.get('source')): refuse('N/A needs reason and required=false')
        elif g['applicability']=='APPLICABLE':
            if not g.get('argv') or not isinstance(g['argv'],list) or not g.get('scope') or not g.get('source'): refuse('executable gate needs argv/scope/source')
            if g.get('execution_kind') not in {'local','remote'}: refuse('gate execution kind required')
            if g['execution_kind']=='remote':
                remote=g.get('remote')
                if not isinstance(remote,dict) or remote.get('provider')!='gitlab' or not remote.get('project_id') or not remote.get('job_name'): refuse('remote gate requires GitLab project/job provenance')
                if g['source'].get('job')!=remote['job_name']:refuse('remote job identity must match source.job')
                if len(g['argv'])!=3 or g['argv'][:2]!=['glab','api'] or not g['argv'][2].startswith('projects/'+str(remote['project_id'])+'/jobs/'): refuse('remote gate must query a real GitLab job endpoint')
        if g.get('source'):
            src=g['source']; p=al_store.REPO_ROOT/S.safe_path(src['path'])
            if not re.fullmatch(r'[0-9a-f]{64}',str(src.get('sha256',''))):refuse('corrupt gate source hash')
            if check_sources and (not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=src.get('sha256')): refuse('gate source rule changed: '+src['path'])
    if len(ids)!=len(set(ids)) or any(not i for i in ids): refuse('duplicate/missing gate IDs')
    if set(CATEGORIES)-{g['category'] for g in gates}: refuse('manifest omits repository gate categories')
    if 'service_coverage' in m:
        try:Coverage.validate_declaration(m['service_coverage'],gates)
        except (ValueError,TypeError,KeyError) as exc:refuse('corrupt service coverage: '+str(exc))
    if check_sources:V.validate(st,m)
