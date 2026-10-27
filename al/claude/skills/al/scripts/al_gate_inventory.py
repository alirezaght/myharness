"""Independently selected task paths, source rules and concrete obligations."""
import hashlib
import json
import os
import al_gitlinks as G
import al_sources as Sources
from pathlib import Path
import al_artifacts as A
import al_scope as S
import al_store
import al_affected_services
import al_service_coverage as Coverage
import al_ci_verification as Verification
from al_repository_facts import digest,within,gap
import al_ci_inventory as CI
import al_ci_context as CC
import al_gate_coverage as C
from al_schema import refuse

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
        if candidates and max(candidates,key=len)!='.':return max(candidates,key=len)
    folder=path if (al_store.REPO_ROOT/path).is_dir() else str(Path(path).parent)
    unit=manifest_unit(folder) or ('/'.join(path.split('/')[:2]) if path.startswith(('services/','apps/','packages/')) else '.')
    return '.' if any(Sources.contains(child,unit) for child in opaque) else unit


def inventory(st,anticipated=(),ci_context=None):
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
    expansion_gaps=[]
    if any((al_store.REPO_ROOT/p).is_dir() for p in paths):
        from al_repository_facts import Snapshot
        expanded=Snapshot(al_store.REPO_ROOT,None,paths,opaque)
        expansion_gaps=expanded.gaps
        paths=sorted(set(paths)|{p.rstrip('/') for p in expanded.members if any(C.contains(d,p.rstrip('/')) for d in paths if (al_store.REPO_ROOT/d).is_dir())})
    paths=sorted({'.' if p=='.' else S.safe_path(p).as_posix() for p in paths})
    paths=[p for p in paths if not any(p!=child and Sources.contains(child,p) for child in opaque)]
    units={classify_unit(p,opaque) for p in paths}
    facts=al_affected_services.select(al_store.REPO_ROOT,paths,opaque,base_oid=st['identity']['merge_base'],required_adapter=required_adapter)
    facts['enumeration']['gaps'].extend(expansion_gaps)
    assessment=Coverage.assess(facts)
    services=assessment['selected_services']
    dependencies=sorted({p['path'] for p in facts['scan_inputs']}|{p['path'] for p in facts['rule_sources']})
    units={classify_unit(p,opaque,facts) for p in paths};units.update(services);names={p['path'] for p in facts['rule_sources']}
    for p in paths+services:
        if inside(p):continue
        current=Path(p) if (al_store.REPO_ROOT/p).is_dir() else Path(p).parent
        while True:
            names.update(str(current/name) for name in ('AGENTS.md','CLAUDE.md') if (al_store.REPO_ROOT/current/name).is_file())
            if current==Path('.'):break
            current=current.parent
        target=al_store.REPO_ROOT/p
        if target.is_dir():
            for directory,dirs,files in os.walk(target,followlinks=False):
                dirs[:]=[d for d in dirs if d!='.git' and not inside((Path(directory)/d).relative_to(al_store.REPO_ROOT).as_posix())]
                names.update((Path(directory)/name).relative_to(al_store.REPO_ROOT).as_posix() for name in ('AGENTS.md','CLAUDE.md') if name in files)
    names.update(str(Path(u)/name) for u in units for name in MANIFESTS if not inside(str(Path(u)/name)) and (al_store.REPO_ROOT/u/name).is_file())
    service_inputs=set()
    for service in services:
        members=[p['path'] for p in facts['scan_inputs'] if p['snapshot']=='current' and (service=='.' or within(service,p['path']))]
        members.extend(p['path'] for p in facts['rule_sources'] if service=='.' or within(service,p['path']))
        service_inputs.update(members or [service])
    ci=CI.inventory(al_store.REPO_ROOT,sorted(set(paths)|service_inputs),context,opaque)
    for job in ci['obligations']:
        if job.get('impact',{}).get('inventory_complete') is False or job['job'].startswith('<unresolved-'):
            facts['enumeration']['gaps'].append(gap('unknown-ci-inventory',job['source_path'],job['reason']))
    for pattern in OTHER_CI:
        if any(al_store.REPO_ROOT.glob(pattern)):
            facts['enumeration']['gaps'].append(gap('unsupported-ci-provider',pattern,'CI job universe cannot be enumerated'))
    # CI applicability is monotone over the finite selected service universe.
    for job in ci['obligations']:
        selected=set(job['units'])&set(services)
        if '.' in job['units']:selected.update(services)
        if not selected:continue
        oid='ci-'+digest([job['source_path'],job['job']])[:12]
        if job['applicability']!='APPLICABLE' or not job.get('execution'):
            continue
        else:
            facts['checks']['obligations'].append({'id':oid,'category':'ci','units':sorted(selected)})
            execution=job['execution'];src={'path':job['source_path'],'sha256':job['source_sha256'],'selector':job['job'],'semantics':'sourced CI execution'}
            b={'id':oid,'category':'ci','units':sorted(set(job['units'])|selected),'required':True,**execution,'sources':[], 'required_scope':sorted(set(job['inputs']+[d['path'] for d in job['dependencies']])), 'source_chain':[src]+[{'path':d['path'],'sha256':d['sha256'],'selector':job['job'],'semantics':'sourced CI dependency'} for d in job['dependencies'] if d['path']!=src['path']],'obligation_ids':[oid]}
            b['execution_signature']=digest(b);facts['checks']['bindings'].append(b)
    for unit in services:
        verification='verification-'+unit.replace('/','-').replace('.','root')
        unit_fact=next(u for u in facts['units'] if u['id']==unit)
        proof=Verification.conventional_verification(unit_fact,ci['obligations'],facts['checks']['bindings'],
                                                     {'gaps':[*facts['enumeration']['gaps'],*facts['unsupported_boundaries'],*facts['scan'].get('gaps',[])]})
        if proof['satisfied'] and facts['scan']['stable'] and any(o['id']==verification for o in facts['checks']['obligations']):
            chosen=next(b for b in sorted(facts['checks']['bindings'],key=lambda b:b['id']) if b['id'] in proof['binding_ids'])
            chosen['obligation_ids'].append(verification)
            chosen['conventional_verification']=proof
            facts['checks']['gaps']=[g for g in facts['checks']['gaps'] if not (g.get('unit')==unit and g['code']=='missing-verification-check')]
    # Only checks of the selected services run in this task; others are verified by the pipeline.
    facts['checks']['bindings']=[b for b in facts['checks']['bindings'] if set(b['units'])&set(services)]
    universe={p['path'] for p in facts['scan_inputs'] if p['snapshot']=='current'}|{p['path'] for p in facts['rule_sources']}
    # Discovery inputs are evidence dependencies for every service execution.
    for binding in facts['checks']['bindings']:
        binding['required_scope']=compact(set(binding['required_scope']+dependencies),universe)
        binding['execution_signature']=digest({k:v for k,v in binding.items() if k!='execution_signature'})
    rows=S.source_rows();runnable=[]
    for binding in facts['checks']['bindings']:
        binding['sources']=sorted(binding['sources']+Sources.default_parent_entries(binding['required_scope'],binding['sources'],rows),key=str)
        binding['execution_signature']=digest({k:v for k,v in binding.items() if k!='execution_signature'})
        unavailable=Sources.capability_gaps(binding['required_scope'],binding['sources'],rows,binding['cwd'],root=al_store.REPO_ROOT)
        if unavailable:facts['checks']['gaps'].append(gap('unavailable-check-input','.', '; '.join(unavailable),unit=binding['units'][0],category=binding['category']))
        else:runnable.append(binding)
    facts['checks']['bindings']=runnable
    assessment=Coverage.assess(facts)
    names.update(d['path'] for d in ci['dependencies'])
    for pattern in PATTERNS:names.update(q.relative_to(al_store.REPO_ROOT).as_posix() for q in al_store.REPO_ROOT.glob(pattern) if not inside(q.relative_to(al_store.REPO_ROOT).as_posix()) and q.is_file())
    return {'paths':paths,'units':sorted(units),'rules':[source(p) for p in sorted(names)],'services':services,'import_dependencies':dependencies,'ci':ci,'repository_facts':facts,'service_coverage':assessment}


def gaps(st,m,anticipated=(),*,admission=True):
    expected=inventory(st,[*m.get('paths',[]),*anticipated],m.get('ci_context'));out=[]
    declared={r['path']:r['sha256'] for r in m['rules']}
    for r in expected['rules']:
        if declared.get(r['path'])!=r['sha256']:out.append('missing/current repository source: '+r['path'])
    if set(expected['units'])-set(m['units']):out.append('manifest omits selected or anticipated units')
    if any(not C.covered(m.get('paths',[]),p) for p in expected['paths']):out.append('manifest omits selected or anticipated paths')
    gates=m['gates'];root=al_store.REPO_ROOT
    for category,needed in (('lint-imports',any(o['category']=='lint-imports' and set(o['units'])&set(expected['services']) for o in expected['repository_facts']['checks']['obligations']) or expected['service_coverage']['outcome']=='incomplete'),('loc',(root/LOC_CHECKER).is_file()),('pre-commit',bool(hook())),('contracts',contracts_needed(expected['units'])),('ci',bool(expected['ci']['obligations']))):
        active=[g for g in gates if g['category']==category and g['applicability']!='NOT_APPLICABLE']
        if needed and not active:out.append('N/A contradicts known '+category+' obligation')
    out.extend(Coverage.current_gaps(expected['repository_facts'],expected['service_coverage'],m,admission=admission))
    out.extend(CI.gaps(expected['ci'],gates))
    return sorted(set(out))


def validate(st,m):
    missing=gaps(st,m,admission=False)
    if missing:refuse('incomplete repository gate inventory: '+str(missing))
