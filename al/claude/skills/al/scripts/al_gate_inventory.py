"""Independently selected task paths, source rules and concrete obligations."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
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


_MEMO = ContextVar('al_gate_inventory_memo', default=None)


@contextmanager
def reuse_inventories():
    """Within one command, an inventory of identical inputs is computed once.

    Discovery and the validation of its own manifest ask the same question; the
    memo is keyed by every input the inventory reads from run state (base, merged
    task paths, CI context, opaque boundaries, adapter) and lives only for the
    enclosing command, so later commands always observe the repository afresh."""
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def inventory_inputs(st,anticipated=(),ci_context=None,*,bounded=False):
    """Everything an inventory reads from run state, merged and normalized once."""
    context=CC.profile(st,ci_context)
    merged=set()
    required_adapter=None
    for ref in st.get('gate_manifests',[]):
        prior=A.read_json(ref);merged.update(prior.get('paths',[]))
        if prior.get('service_coverage',{}).get('adapter',{}).get('id')=='contract':required_adapter='contract'
    from al_task_attribution import classify_changes
    attributed = classify_changes(st) if st.get('review_mode') != 'whole-mr' else None
    merged.update(attributed['discovery_paths'] if attributed else S.changed_paths(st['identity']['merge_base']))
    opaque={r['path'] for r in S.source_rows() if G.opaque(r)}
    def normalized(values):return sorted({'.' if p=='.' else S.safe_path(p).as_posix() for p in values})
    return {'context':context,'base':st['identity']['merge_base'],'merged':normalized(merged),'bounded':bool(bounded),
            'paths':normalized(set(anticipated)|merged),'opaque':sorted(opaque),'required_adapter':required_adapter,
            'normalize':normalized}


def inputs_key(inputs):
    return digest({k:inputs[k] for k in ('context','base','paths','opaque','required_adapter','bounded')})


_memo_key=inputs_key


def last_inputs_key():
    """Inputs of the latest inventory in the active reuse scope (what a saved result depends on)."""
    memo=_MEMO.get()
    return memo.get('__last__') if memo is not None else None


def expand_task_paths(scan,paths,opaque):
    """Requested directories and the task paths they expand to (opaque interiors excluded)."""
    requested=[p for p in paths if scan.observe_path(p)['state']=='directory']
    # Directory containment by ancestor lookup: members x requested directories is quadratic.
    requested_scope=PathScope(PurePosixPath(d).as_posix() for d in requested)
    expanded=sorted(set(paths)|{p.rstrip('/') for p in scan.members if requested_scope.covers(PurePosixPath(p.rstrip('/')).as_posix())})
    expanded=[p for p in expanded if not any(p!=child and Sources.contains(child,p) for child in opaque)]
    return requested,expanded


def discovery_paths(st,anticipated=(),ci_context=None,*,bounded=False):
    """Task paths with directories expanded exactly as a manifest records them.

    Discovering over this set asks the same question as validating the resulting
    manifest, so the validation reuses the one inventory instead of rebuilding it."""
    inputs=inventory_inputs(st,anticipated,ci_context,bounded=bounded)
    listing=Snapshot(al_store.REPO_ROOT,inputs['base'],inputs['paths'],set(inputs['opaque']),inputs['required_adapter'],listing_only=True)
    return expand_task_paths(listing,inputs['paths'],set(inputs['opaque']))[1]


def inventory(st,anticipated=(),ci_context=None,*,_finalize=None,bounded=False,_inputs=None):
    inputs=_inputs if _inputs is not None else inventory_inputs(st,anticipated,ci_context,bounded=bounded)
    memo=_MEMO.get()
    key=_memo_key(inputs) if memo is not None else None
    if memo is not None:memo['__last__']=key
    def compute():
        # An inventory of these inputs recorded earlier in this worktree run is the answer
        # while its capture re-observes unchanged (#25); otherwise a new capture is analysed.
        recorded_key=answer_key({'kind':'inventory'},inputs)
        # A capture whose re-observation found only content changes is the start of an
        # incremental one (#35): its fresh listing and the unchanged files' hashes are reused.
        reuse={}
        if recorded_key is not None:
            recorded=verified_answer(recorded_key,inputs,reuse)
            if isinstance(recorded,dict) and isinstance(recorded.get('paths'),list):return recorded
        context=inputs['context'];paths=inputs['paths'];opaque=set(inputs['opaque']);required_adapter=inputs['required_adapter']
        scans=[]
        def snapshot():
            # Only the first attempt is incremental: a retry after drift captures afresh.
            prior=reuse.pop('capture',None)
            extra={'listing':prior['listing'],'known':prior['known']} if prior else {}
            scans.append(Snapshot(al_store.REPO_ROOT,inputs['base'],paths,opaque,required_adapter,**extra));return scans[-1]
        value=capture_discovery(snapshot,lambda scan:_inventory_attempt(st,paths,context,opaque,scan,bounded=inputs['bounded']))
        _OBSERVED[inputs_key(inputs)]=_observations(scans[-1])
        if recorded_key is not None:record_answer(recorded_key,inputs,value)
        return value
    from al_analysis_cache import CURRENT
    observation=CURRENT.get()
    if key is not None and key in memo:
        result=copy.deepcopy(memo[key])
    elif key is None and observation is not None:
        # Inside one stability-checked decision window (S.decision), the same inventory
        # question is answered once; a moved worktree re-runs the decision without it.
        result=observation.get_or_compute('gate-inventory',[str(al_store.REPO_ROOT),inputs_key(inputs)],compute)
    else:
        result=compute()
        if key is not None:memo[key]=copy.deepcopy(result)
    return _finalize(result) if _finalize else result


def _inventory_attempt(st,paths,context,opaque,scan,*,bounded=False):
    def inside(path):return any(Sources.contains(child,path) for child in opaque)
    requested,paths=expand_task_paths(scan,paths,opaque)
    expansion=Relevance.project_expansion(scan.listing_observation,requested) if requested else {'gaps':[]}
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
    reached_only=set()
    if bounded:
        # Bounded discovery: a selected unit holding no task path is affected only through
        # dependency/consumer edges. Its CI checks are selected from its affected consumer
        # files (or its unit directory), not from a per-file analysis of the whole unit.
        declared=PathScope(paths)
        consumers=set(facts['dependencies'].get('affected_source_paths',[]))
        reached_only={s for s in services if s!='.' and not declared.overlaps(s)}
        if reached_only:
            reached=PathScope(reached_only)
            service_inputs={p for p in service_inputs if p in consumers or not reached.covers(p)}
    for service in services:
        if service in reached_only:
            service_inputs.update([p for p in consumers if within(service,p)] or [service])
            continue
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
            if job.get('advisory'):b.update(required=False,advisory=job['advisory'])
            if job.get('advisory_steps'):b['advisory_steps']=job['advisory_steps']
            b['execution_signature']=digest(b);facts['checks']['bindings'].append(b)
    names.update(advisory_bindings(scan,facts,ci,inside))
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
    pipeline_verification(facts,services,ci,projected)
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


def pipeline_verification(facts,services,ci,projection):
    """A service whose sourced verification is a CI job AL cannot run locally is verified by the
    pipeline (#17).

    Its `missing-verification-check` gap becomes a recorded `pipeline_verified` obligation: the
    jobs stay UNRESOLVED gates that `gates status` and the review packet list as unverified, and
    review stays reachable. Unknown inventories, advisory and release jobs never qualify, so a
    service whose only jobs are mapped (but do not cover it) keeps the gap."""
    relevant=set(projection['relevant_ci_ids']);records=[]
    for unit in services:
        if not any(g.get('unit')==unit and g['code']=='missing-verification-check' for g in facts['checks']['gaps']):continue
        jobs=[o for o in ci['obligations'] if Relevance.ci_record_id(o) in relevant and (unit in o['units'] or '.' in o['units'])]
        delegated=[o for o in jobs if CI.pipeline_verified(o)]
        if not delegated:continue
        verification='verification-'+unit.replace('/','-').replace('.','root')
        facts['checks']['gaps']=[g for g in facts['checks']['gaps'] if not (g.get('unit')==unit and g['code']=='missing-verification-check')]
        facts['checks']['obligations']=[o for o in facts['checks']['obligations'] if o['id']!=verification]
        records.append({'unit':unit,'obligation_id':verification,'verified_by':'pipeline',
                        'jobs':[{'source_path':o['source_path'],'job':o['job'],'applicability':o['applicability'],'reason':o['reason']}
                                for o in sorted(delegated,key=lambda o:(o['source_path'],o['job']))]})
    if records:facts['checks']['pipeline_verified']=records


def advisory_bindings(scan,facts,ci,inside):
    """A check the repository configures (for example import-linter in pyproject.toml) that its CI
    runs only as an advisory step is informational, not a required gate.

    The CI definitions and every script they run or source are read and bound as rule sources,
    so a recipe change that makes the step blocking invalidates the manifest."""
    import al_ci_advisory as Advisory
    candidates=[b for b in facts['checks']['bindings'] if b['category']!='ci' and b.get('argv') and not b.get('advisory') and not b.get('runner_resource')]
    entries=Advisory.ci_entries(ci) if candidates else []
    bound=set()
    if not entries:return bound
    def read(path):
        if inside(path) or scan.observe_path(path)['state']!='regular':return None
        try:return scan.capture_bytes(path,required=True,rule=True).decode('utf-8')
        except UnicodeError:return None
    complete=Advisory.complete(ci)
    for binding in candidates:
        names=Advisory.tool_names(binding['argv'])
        result=Advisory.classify(entries,names,read) if names else None
        if not result or not result['advisory']:continue
        reasons='; '.join(sorted({m['reason'] for m in result['mentions']}))
        note='' if complete else ' (CI inventory incomplete: unread definitions were not inspected)'
        binding.update(required=False,advisory='CI runs '+'/'.join(sorted(names))+' only as an advisory step: '+reasons+note)
        for path in result['sources']:
            record={'path':path,'sha256':hashlib.sha256(scan.capture_bytes(path,required=True,rule=True)).hexdigest(),
                    'selector':'advisory CI recipe','semantics':'advisory CI recipe'}
            if record not in binding['source_chain']:binding['source_chain'].append(record)
            bound.add(path)
    return bound


def gaps(st,m,anticipated=(),*,admission=True):
    """Inventory gaps of a manifest. A question this worktree run already answered is
    answered again by re-observing the capture it depended on (#25), not by a new inventory."""
    # One reading of the run's inventory inputs serves the key, the inventory and the record.
    try:inputs=_manifest_inputs(st,m,anticipated)
    except (OSError,ValueError,TypeError,KeyError,SystemExit):inputs=None
    key=_verification_key(st,m,anticipated,admission,inputs) if inputs is not None else None
    if key is not None:
        recorded=verified_answer(key)
        if isinstance(recorded,list) and all(isinstance(g,str) for g in recorded):return recorded
    out=_gaps(st,m,anticipated,admission=admission,inputs=inputs)
    if key is not None:record_answer(key,inputs,list(out))
    return out


def _manifest_inputs(st,m,anticipated=()):
    return inventory_inputs(st,[*m.get('paths',[]),*anticipated],m.get('ci_context'),bounded=m.get('discovery_mode')=='bounded')


def _gaps(st,m,anticipated=(),*,admission=True,inputs=None):
    progress.report('inventory-validate',count=len(m.get('paths',[])),force=True)
    expected=inventory(st,[*m.get('paths',[]),*anticipated],m.get('ci_context'),bounded=m.get('discovery_mode')=='bounded',_inputs=inputs);out=[]
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
    # Only an independently reconstructed advisory step can make a gate informational.
    bindings={tuple(b['obligation_ids']):b for b in expected['service_coverage']['required_bindings']}
    for gate in gates:
        if 'advisory' not in gate:continue
        if gate.get('obligation_ids'):
            binding=bindings.get(tuple(gate['obligation_ids']))
            sourced=bool(binding) and binding.get('advisory')==gate['advisory'] and not Coverage.validate_check_binding(binding,gate)
        else:
            source=gate.get('source') or {}
            sourced=any(o.get('advisory')==gate['advisory'] and o['source_path']==source.get('path') and o['job']==source.get('job') for o in expected['ci']['obligations'])
        if not sourced:out.append('advisory gate has no sourced advisory CI step: '+gate['id'])
    return sorted(set(out))


def validate(st,m):
    """Refuse a manifest whose repository inventory has gaps.

    A manifest this worktree already validated (normally by the `gates discover` that
    produced it) is not re-analysed: its recorded capture observations are re-observed
    instead, which is AL's own end-of-capture stability check (#25)."""
    missing=gaps(st,m,admission=False)
    if missing:refuse('incomplete repository gate inventory: '+str(missing))


# Observations of the capture behind each inventory computed in this process, by inputs key.
_OBSERVED={}
_VOLATILE_ENVIRONMENT={'_','OLDPWD','SHLVL','PWD'}


def _observations(scan):
    return {'root':str(scan.root),'base_oid':scan.base_oid,'paths':list(scan.paths),'opaque':sorted(scan.opaque),
            'required_adapter':scan.required_adapter,'capture_started_ns':scan.capture_started_ns,
            'listing':json.loads(json.dumps(scan.listing_observation)),
            'path_observations':json.loads(json.dumps(scan.path_observations)),
            'files':{p:{'sha256':r['sha256'],'identity':list(r['identity']),'size':r['size']} for p,r in scan.files.records.items()},
            'absent':sorted(scan.absent),'rules':sorted(scan.rules),
            'matches':[[pattern,purpose,list(values)] for (pattern,purpose),values in sorted(scan.match_observations.items())]}


def _environment():
    """What an inventory reads outside the repository: the environment and the PATH directories."""
    import sys
    rows=[]
    for directory in os.environ.get('PATH','').split(os.pathsep):
        try:value=os.stat(directory);rows.append([directory,value.st_ino,value.st_mtime_ns])
        except OSError:rows.append([directory,None,None])
    return {'environ':sorted([k,v] for k,v in os.environ.items() if k not in _VOLATILE_ENVIRONMENT),'path':rows,'executable':sys.executable}


def _verification_key(st,m,anticipated,admission,inputs):
    # A manifest's id and revision are assigned when it is saved; the gaps never read them,
    # so the validation `gates discover` recorded answers the saved manifest too.
    return answer_key({'kind':'manifest-gaps','manifest':digest({k:v for k,v in m.items() if k not in ('id','revision')}),
                       'anticipated':sorted(anticipated),'admission':bool(admission)},inputs)


def answer_key(question,inputs,*modules):
    """Key of an answer computed from one inventory of `inputs`: the producing code, the
    question, the inventory inputs and what an inventory reads outside the capture (#25)."""
    import al_analysis_index as index
    if not index.enabled():return None
    try:
        head=S.git_bytes('rev-parse','--verify','--quiet','HEAD').decode().strip()
        return digest({'code':index.code_digest('al_gate_inventory','al_gate_discovery',*modules),'inputs':inputs_key(inputs),
                       'question':question,'rows':digest(S.source_rows()),'head':head,'root':str(al_store.REPO_ROOT),
                       'environment':_environment()})
    except (OSError,ValueError,TypeError,KeyError,SystemExit):
        return None


def _verification_path(key):
    return Path(al_store.RUNTIME_DIR)/'inventory-verifications'/(key+'.json')


def record_answer(key,inputs,answer):
    """Keep, per worktree run, an answer and the capture observations it depended on."""
    observed=_OBSERVED.get(inputs_key(inputs))
    from al_analysis_cache import CURRENT
    context=CURRENT.get()
    # A read-only inspection records nothing.
    if observed is None or (context is not None and context.readonly_git):return
    import tempfile
    path=_verification_path(key)
    try:
        # The runtime directory ignores itself, so a record never becomes a task change.
        al_store._ensure_runtime_dir()
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if path.parent.is_symlink():return
        fd,temporary=tempfile.mkstemp(dir=str(path.parent),prefix='.tmp.',suffix='.json')
        try:
            # One C-encoded string: json.dump streams through the pure-Python encoder.
            with os.fdopen(fd,'w') as handle:handle.write(json.dumps({'key':key,'observations':observed,'answer':answer},sort_keys=True))
            os.replace(temporary,path)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
        rows=sorted(path.parent.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        for stale in rows[8:]:stale.unlink(missing_ok=True)
    except (OSError,ValueError,TypeError):
        return


def verified_answer(key,inputs=None,reuse=None):
    """The recorded answer when the recorded capture is unchanged (listing, path, absence, match
    and source observations re-observed with the capture's own racy-clean rule), else None.

    With a `reuse` dict, a recorded capture that moved only in file content (#35) leaves
    `reuse['capture']`: the fresh listing and the recorded file records, from which the new
    capture starts incrementally. Any structural change leaves nothing (a full capture)."""
    from al_repository_capture import immutable_observation
    path=_verification_path(key)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_uid!=os.getuid():return None
        record=json.loads(path.read_bytes())
        if record.get('key')!=key or 'answer' not in record:return None
        obs=record['observations']
        if obs['root']!=str(al_store.REPO_ROOT):return None
        progress.report('inventory-verify',force=True)
        scan=Snapshot(al_store.REPO_ROOT,obs['base_oid'],obs['paths'],set(obs['opaque']),obs['required_adapter'],listing_only=True)
        fresh=scan.listing_observation
        scan.listing_observation=immutable_observation(obs['listing'])
        scan.path_observations=obs['path_observations']
        scan.files.records={p:{**r,'identity':tuple(r['identity'])} for p,r in obs['files'].items()}
        scan.absent=set(obs['absent'])
        scan.match_observations={(pattern,purpose):tuple(values) for pattern,purpose,values in obs['matches']}
        scan.capture_started_ns=obs['capture_started_ns']
        try:scan.revalidate(fresh)
        except CaptureDrift:
            if reuse is not None and _content_only(scan,fresh,obs):
                reuse['capture']={'listing':fresh,'known':(scan.files.records,obs['capture_started_ns'])}
            raise
        if inputs is not None:_OBSERVED[inputs_key(inputs)]=obs
        return record['answer']
    except (CaptureDrift,CaptureUnavailable,OSError,ValueError,TypeError,KeyError,SystemExit):
        return None


def _content_only(scan,fresh,obs):
    """Whether a recorded capture moved only in the content of files it captured (#35).

    Structural changes answer False: a different listing (added, removed or ignored paths,
    changed declarations), a path whose state or boundary changed, an absent path now present,
    a captured file gone or no longer regular, and a changed rule source, manifest or ignore
    file (they decide layout and rules). Records from before rule sources were kept also
    answer False."""
    import stat
    from al_repository_facts import file_identity,path_name,MANIFESTS,RACY_NS
    if 'rules' not in obs or fresh!=scan.listing_observation:return False
    scan.begin_observation_epoch()
    shape=lambda o:(o['state'],o.get('frontier'),o.get('boundary_kind'))
    for path,old in scan.path_observations.items():
        if shape(scan.path_observation(path))!=shape(old):return False
    for path in scan.absent:
        if scan.path_observation(path)['state']!='absent':return False
    governing=set(obs['rules'])
    settled=obs['capture_started_ns']-RACY_NS
    for path,record in scan.files.records.items():
        try:
            scan.safe_file(path)
            current=os.lstat(os.path.join(str(scan.root),path))
        except (OSError,ValueError):return False
        if not stat.S_ISREG(current.st_mode):return False
        identity=tuple(record['identity'])
        if path in governing or path_name(path) in MANIFESTS:
            # Rule and layout sources must be unchanged: same identity, and a source too recent
            # for the racy-clean rule is read again (the capture's own revalidation rule).
            if file_identity(current)!=identity:return False
            if max(identity[4],identity[5])>=settled:
                try:data,now=scan.read_observed(path)
                except (OSError,ValueError):return False
                if tuple(now)!=identity or hashlib.sha256(data).hexdigest()!=record['sha256']:return False
    return True
