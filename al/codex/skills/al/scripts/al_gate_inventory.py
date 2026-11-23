"""Independently selected task paths, source rules and concrete obligations."""
import hashlib
import json
import os
import al_gitlinks as G
import al_sources as Sources
from pathlib import Path, PurePosixPath
import al_artifacts as A
import al_scope as S
import al_store
import al_affected_services
import al_service_coverage as Coverage
import al_ci_verification as Verification
from al_repository_facts import digest,within,gap,Snapshot,collect_from_snapshot, unit_depth, PathScope
from al_repository_capture import capture_discovery,CaptureDrift,CaptureUnavailable
import al_coverage_relevance as Relevance
import al_ci_inventory as CI
import al_ci_context as CC
import al_gate_coverage as C
from al_schema import refuse
import al_writer_progress as progress

PATTERNS=('AGENTS.md','CLAUDE.md','.husky/pre-commit','.githooks/pre-commit','Makefile','package.json','.gitlab-ci.yml',
          'ci/**/*.yml','.al/repository.json','scripts/check-loc.py','scripts/check-loc.allowlist')


LOC_CHECKER='scripts/check-loc.py'
HOOKS=('.husky/pre-commit','.githooks/pre-commit')
OTHER_CI=('Jenkinsfile','.circleci/config.yml','azure-pipelines.yml','bitbucket-pipelines.yml','.travis.yml',
          '.buildkite/pipeline.yml','.drone.yml','.woodpecker.yml','.woodpecker/*.yml','buildspec.yml')


def hook():
    """The repository's committed pre-commit hook, if it has one."""
    return next((p for p in HOOKS if (al_store.REPO_ROOT/p).is_file()),None)


def contracts_needed(units):
    """A package script generates or builds contracts: the drift check needs a sourced rule."""
    root=al_store.REPO_ROOT
    for file in {root/'package.json',*(root/u/'package.json' for u in units)}:
        if not file.is_file():continue
        try:scripts=json.loads(file.read_text()).get('scripts',{})
        except (ValueError,AttributeError):return True
        if any('contract' in k and ('gen' in k or 'build' in k) for k in scripts):return True
    return False


def source(path,job=None):
    return {'path':path,'job':job,'sha256':hashlib.sha256((al_store.REPO_ROOT/path).read_bytes()).hexdigest()}


def compact(paths,universe):
    """The same coverage in fewer entries: a directory replaces its files once every scanned file
    under it is selected (it then also covers its other files, which is conservative). Top-level
    directories are the widest entry, never `.`."""
    paths=set(paths);covered_files=paths|set(universe)
    totals={};selected={}
    for path in covered_files:
        parts=path.split('/')
        for i in range(1,len(parts)):
            directory='/'.join(parts[:i])
            totals[directory]=totals.get(directory,0)+1
            if path in paths:selected[directory]=selected.get(directory,0)+1
    full={d for d,n in totals.items() if selected.get(d)==n}
    result=set()
    for path in paths:
        parts=path.split('/')
        top=next(('/'.join(parts[:i]) for i in range(1,len(parts)) if '/'.join(parts[:i]) in full),None)
        result.add(top or path)
    return sorted(result)


# A directory holding one of these is a project of its own, whatever its ecosystem or location.
MANIFESTS=('package.json','pyproject.toml','setup.py','go.mod','Cargo.toml','pom.xml','build.gradle','build.gradle.kts',
           'Gemfile','composer.json','mix.exs','Package.swift','CMakeLists.txt','pubspec.yaml','deno.json')


def manifest_unit(directory):
    """The nearest directory below the root, at or above `directory`, that holds a project manifest."""
    current=Path(directory)
    while current!=Path('.'):
        try:names={e.name for e in os.scandir(al_store.REPO_ROOT/current) if e.is_file()}
        except OSError:names=set()
        if names&set(MANIFESTS) or any(n.endswith(('.csproj','.fsproj')) for n in names):return current.as_posix()
        current=current.parent
    return None


def classify_unit(path,opaque,facts=None):
    if facts:
        candidates=[u['path'] for u in facts['units'] if within(u['path'],path)]
        if candidates and max(candidates,key=unit_depth)!='.':return max(candidates,key=unit_depth)
    if facts is not None:return '/'.join(path.split('/')[:2]) if path.startswith(('services/','apps/','packages/')) else '.'
    folder=path if (al_store.REPO_ROOT/path).is_dir() else str(Path(path).parent)
    unit=manifest_unit(folder) or ('/'.join(path.split('/')[:2]) if path.startswith(('services/','apps/','packages/')) else '.')
    return '.' if any(Sources.contains(child,unit) for child in opaque) else unit


def _walk_reaches(parent, child):
    """A parent walk does not traverse nested .git or symlink directory roots."""
    parts=Path(child).parts
    for length in range(len(Path(parent).parts)+1,len(parts)+1):
        if parts[length-1]=='.git' or (al_store.REPO_ROOT/Path(*parts[:length])).is_symlink():
            return False
    return True


def _instruction_frontier(paths):
    """Cover each ordinary directory once, preserving separately declared walk boundaries."""
    selected=set()
    for path in sorted(set(paths),key=lambda p:(len(Path(p).parts),p)):
        parts=Path(path).parts
        ancestors=[Path(*parts[:length]).as_posix() for length in range(len(parts))]
        if any(parent in selected and _walk_reaches(parent,path) for parent in ancestors):
            continue
        selected.add(path)
    return sorted(selected)


def inventory(st,anticipated=(),ci_context=None,*,_finalize=None):
    context=CC.profile(st,ci_context)
    paths=set(anticipated)
    required_adapter=None
    for ref in st.get('gate_manifests',[]):
        prior=A.read_json(ref);paths.update(prior.get('paths',[]))
        if prior.get('service_coverage',{}).get('adapter',{}).get('id')=='contract':required_adapter='contract'
    from al_task_attribution import classify_changes
    attributed = classify_changes(st) if st.get('review_mode') != 'whole-mr' else None
    paths.update(attributed['discovery_paths'] if attributed else S.changed_paths(st['identity']['merge_base']))
    opaque={r['path'] for r in S.source_rows() if G.opaque(r)}
    def inside(path):return any(Sources.contains(child,path) for child in opaque)
    paths=sorted('.' if p=='.' else S.safe_path(p).as_posix() for p in paths)
    return capture_discovery(
        lambda:Snapshot(al_store.REPO_ROOT,st['identity']['merge_base'],paths,opaque,required_adapter),
        lambda scan:_finalize(_inventory_attempt(st,paths,context,opaque,scan)) if _finalize else _inventory_attempt(st,paths,context,opaque,scan))


def _inventory_attempt(st,paths,context,opaque,scan):
    def inside(path):return any(Sources.contains(child,path) for child in opaque)
    original_paths=list(paths)
    requested=[p for p in paths if scan.observe_path(p)['state']=='directory']
    expansion=Relevance.project_expansion(scan.listing_observation,requested) if requested else {'gaps':[]}
    # Directory containment by ancestor lookup: members x requested directories is quadratic.
    requested_scope=PathScope(PurePosixPath(d).as_posix() for d in requested)
    paths=sorted(set(paths)|{p.rstrip('/') for p in scan.members if requested_scope.covers(PurePosixPath(p.rstrip('/')).as_posix())})
    paths=[p for p in paths if not any(p!=child and Sources.contains(child,p) for child in opaque)]
    # Known CI/rule identities are policy inputs, not anonymous runtime data.
    # Literal runtime consumers are still retained by artifact analysis.
    for pattern in PATTERNS+('.github/workflows/*.yml','.github/workflows/*.yaml'):
        for path in scan.observe_matches(pattern,purpose='instruction-frontier'):
            if scan.observe_path(path)['state']=='regular':scan.capture_bytes(path,required=True,rule=True)
    facts=collect_from_snapshot(scan)
    progress.report('inventory-coverage',force=True)
    facts['enumeration']['gaps'].extend(expansion['gaps'])
    facts['question'].update(changed_paths=paths,ci_context_binding=context)
    assessment=Coverage.assess(facts)
    services=assessment['selected_services']
    dependencies=sorted({p['path'] for p in facts['scan_inputs']}|{p['path'] for p in facts['rule_sources']})
    units={classify_unit(p,opaque,facts) for p in paths};units.update(services)
    names={r['path'] for r in facts['rule_sources'] if len(Path(r['path']).parts)==1 or r['path']=='.al/repository.json' or any(within(u,r['path']) for u in services)}
    def observed_rule(path):
        observed=scan.observe_path(path)
        if observed['state']=='absent':return False
        if observed['state']!='regular':
            facts['enumeration']['gaps'].append(gap('required-rule-boundary',path,'governing repository source is opaque'))
            return False
        scan.capture_bytes(path,required=True,rule=True);names.add(path);return True
    progress.report('inventory-rules',force=True)
    directories=set(requested)
    # Every ancestor directory is visited once, not once per changed file beneath it.
    visited=set()
    for path in paths+services:
        if inside(path):continue
        current=Path(path) if scan.observe_path(path)['state']=='directory' else Path(path).parent
        while current not in visited:
            visited.add(current)
            for name in ('AGENTS.md','CLAUDE.md'):observed_rule((current/name).as_posix())
            if current==Path('.'):break
            current=current.parent
    directory_scope=PathScope(directories)
    for member in scan.members:
        if Path(member).name in {'AGENTS.md','CLAUDE.md'} and directory_scope.covers(member):observed_rule(member)
    for unit in units:
        for name in MANIFESTS:observed_rule((Path(unit)/name).as_posix())
    # Observe delegated CI/profile inputs before readers run, including absences.
    for unit in {'.',*(u['path'] for u in facts['units'])}:
        for name in ('.python-version','pytest.ini','tox.ini','setup.cfg','.coveragerc','ruff.toml','.ruff.toml','tsconfig.json','requirements.txt'):
            observed_rule((Path(unit)/name).as_posix())
    for pattern in ('.github/workflows/*.yml','.github/workflows/*.yaml'):
        scan.observe_matches(pattern,purpose='workflow-membership')
    service_inputs=set(facts['dependencies'].get('coverage_inputs',[]))
    for service in services:
        members=[p['path'] for p in facts['scan_inputs'] if p['snapshot']=='current' and (service=='.' or within(service,p['path']))]
        members.extend(p['path'] for p in facts['rule_sources'] if service=='.' or within(service,p['path']))
        service_inputs.update(members or [service])
    progress.report('inventory-ci',count=len(set(paths)|service_inputs),force=True)
    ci=CI.inventory(al_store.REPO_ROOT,sorted(set(paths)|service_inputs),context,opaque,capture=scan)
    for job in ci['obligations']:
        if job.get('impact',{}).get('inventory_complete') is False or job['job'].startswith('<unresolved-'):
            facts['enumeration']['gaps'].append(gap('unknown-ci-inventory',job['source_path'],job['reason']))
    other_ci=[]
    for pattern in OTHER_CI:
        matches=scan.observe_matches(pattern,purpose='provider-presence')
        if matches:
            other_ci.extend(matches)
            facts['enumeration']['gaps'].append(gap('unsupported-ci-provider',pattern,'CI job universe cannot be enumerated'))
    import al_package_checks
    al_package_checks.select_ci(facts,ci)
    # CI applicability is monotone over the finite selected service universe.
    for job in ci['obligations']:
        selected=set(job['units'])&set(services)
        if '.' in job['units']:selected.update(services)
        global_required=True
        if not selected:selected=set(job['units']) or {'.'}
        oid='ci-'+digest([job['source_path'],job['job']])[:12]
        if job['applicability']!='APPLICABLE' or not job.get('execution'):
            continue
        else:
            facts['checks']['obligations'].append({'id':oid,'category':'ci','units':sorted(selected),'global_required':global_required})
            execution=job['execution'];src={'path':job['source_path'],'sha256':job['source_sha256'],'selector':job['job'],'semantics':'sourced CI execution'}
            b={'id':oid,'global_required':global_required,'category':'ci','units':sorted(set(job['units'])|selected),'required':True,**execution,'sources':[], 'required_scope':sorted(set(job['inputs']+[d['path'] for d in job['dependencies']])), 'source_chain':[src]+[{'path':d['path'],'sha256':d['sha256'],'selector':job['job'],'semantics':'sourced CI dependency'} for d in job['dependencies'] if d['path']!=src['path']],'obligation_ids':[oid]}
            b['execution_signature']=digest(b);facts['checks']['bindings'].append(b)
    facts['ci_observations']=ci
    progress.report('inventory-relevance',count=len(ci['obligations']),force=True)
    projected=Relevance.project_task(facts)
    relevant_ids=set(projected['relevant_gap_ids'])
    projected_gaps=[row['record'] for row in projected['records'] if row['record_id'] in relevant_ids and row['kind']=='enumeration']
    for unit in services:
        verification='verification-'+unit.replace('/','-').replace('.','root')
        unit_fact=next(u for u in facts['units'] if u['id']==unit)
        proof=Verification.conventional_verification(unit_fact,ci['obligations'],facts['checks']['bindings'],
                                                     {'gaps':projected_gaps},projection=projected)
        if proof['satisfied'] and facts['scan']['stable'] and any(o['id']==verification for o in facts['checks']['obligations']):
            chosen=next(b for b in sorted(facts['checks']['bindings'],key=lambda b:b['id']) if b['id'] in proof['binding_ids'])
            chosen['obligation_ids'].append(verification)
            chosen['conventional_verification']=proof
            facts['checks']['gaps']=[g for g in facts['checks']['gaps'] if not (g.get('unit')==unit and g['code']=='missing-verification-check')]
    # Only checks of the selected services run in this task; others are verified by the pipeline.
    # Keep every observed binding; the common projection chooses required ones.
    names.update(d['path'] for d in ci['dependencies'])
    for pattern in PATTERNS:
        for path in scan.observe_matches(pattern,purpose='instruction-frontier'):
            if not inside(path):observed_rule(path)
    for path in sorted(names):observed_rule(path)
    hooks=[path for path in HOOKS if observed_rule(path)]
    loc=observed_rule(LOC_CHECKER)
    contracts=False
    for unit in units|{'.'}:
        path=(Path(unit)/'package.json').as_posix()
        if observed_rule(path):
            try:scripts=json.loads(scan.text(path)).get('scripts',{})
            except (ValueError,AttributeError):contracts=True;continue
            contracts|=any('contract' in key and ('gen' in key or 'build' in key) for key in scripts)
    dependencies=sorted(set(dependencies)|names)
    universe={p['path'] for p in facts['scan_inputs'] if p['snapshot']=='current'}|{p['path'] for p in facts['rule_sources']}
    # Discovery inputs are evidence dependencies for every service execution.
    for binding in facts['checks']['bindings']:
        binding['required_scope']=compact(set(binding['required_scope']+dependencies),universe)
        binding['execution_signature']=digest({k:v for k,v in binding.items() if k!='execution_signature'})
    progress.report('inventory-bindings',count=len(facts['checks']['bindings']),force=True)
    rows=S.source_rows();runnable=[]
    for binding in facts['checks']['bindings']:
        binding['sources']=sorted(binding['sources']+Sources.default_parent_entries(binding['required_scope'],binding['sources'],rows),key=str)
        binding['execution_signature']=digest({k:v for k,v in binding.items() if k!='execution_signature'})
        unavailable=Sources.capability_gaps(binding['required_scope'],binding['sources'],rows,binding['cwd'],root=al_store.REPO_ROOT)
        if unavailable:facts['checks']['gaps'].append(gap('unavailable-check-input','.', '; '.join(unavailable),unit=binding['units'][0],category=binding['category']))
        else:runnable.append(binding)
    facts['checks']['bindings']=runnable
    facts['rule_sources']=[{'path':path,'sha256':scan.files.records[path]['sha256']} for path in sorted(scan.rules) if path in scan.files]
    facts['enumeration']['absent_paths']=sorted(scan.absent)
    facts['question']['governing_source_refs']=[{'path':path,'sha256':scan.files.records[path]['sha256']} for path in sorted(names) if path in scan.files]
    projection=Relevance.project_task(facts);assessment=Coverage.assess(facts)
    return {'paths':paths,'units':sorted(units),'rules':[{'path':path,'job':None,'sha256':scan.files.records[path]['sha256']} for path in sorted(names) if path in scan.files],
            'services':services,'import_dependencies':dependencies,'ci':ci,'repository_facts':facts,
            'service_coverage':assessment,'relevance_projection':projection,'global_diagnostics':projection['records'],
            'auxiliary':{'loc':loc,'hook':hooks[0] if hooks else None,'contracts':contracts,'other_ci':sorted(other_ci)}}


def gaps(st,m,anticipated=(),*,admission=True):
    progress.report('inventory-validate',count=len(m.get('paths',[])),force=True)
    expected=inventory(st,[*m.get('paths',[]),*anticipated],m.get('ci_context'));out=[]
    declared={r['path']:r['sha256'] for r in m['rules']}
    for r in expected['rules']:
        if declared.get(r['path'])!=r['sha256']:out.append('missing/current repository source: '+r['path'])
    if set(expected['units'])-set(m['units']):out.append('manifest omits selected or anticipated units')
    declared_paths=C.Coverage(m.get('paths',[]))
    if any(not declared_paths(p) for p in expected['paths']):out.append('manifest omits selected or anticipated paths')
    gates=m['gates'];root=al_store.REPO_ROOT
    for category,needed in (('lint-imports',Coverage.imports_needed(expected['repository_facts'],expected['service_coverage'])),('loc',expected['auxiliary']['loc']),('pre-commit',bool(expected['auxiliary']['hook'])),('contracts',expected['auxiliary']['contracts']),('ci',bool(expected['relevance_projection']['relevant_ci_ids']))):
        active=[g for g in gates if g['category']==category and g['applicability']!='NOT_APPLICABLE']
        if needed and not active:out.append('N/A contradicts known '+category+' obligation')
    out.extend(Coverage.current_gaps(expected['repository_facts'],expected['service_coverage'],m,admission=admission))
    out.extend(CI.gaps(expected['ci'],gates,projection=expected['relevance_projection']))
    return sorted(set(out))


def validate(st,m):
    missing=gaps(st,m,admission=False)
    if missing:refuse('incomplete repository gate inventory: '+str(missing))
