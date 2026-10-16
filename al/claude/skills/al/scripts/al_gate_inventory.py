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
import al_ci_inventory as CI
import al_ci_context as CC
import al_gate_coverage as C
from al_schema import refuse

PATTERNS=('AGENTS.md','CLAUDE.md','.husky/pre-commit','.githooks/pre-commit','Makefile','package.json','.gitlab-ci.yml',
          'ci/**/*.yml','ci/import-review-baseline.json','ci/scripts/import_review/*.py','scripts/check-loc.py','scripts/check-loc.allowlist')


def source(path,job=None):
    return {'path':path,'job':job,'sha256':hashlib.sha256((al_store.REPO_ROOT/path).read_bytes()).hexdigest()}


def inventory(st,anticipated=(),ci_context=None):
    context=CC.profile(st,ci_context)
    paths=set(anticipated)
    for ref in st.get('gate_manifests',[]):paths.update(A.read_json(ref).get('paths',[]))
    paths.update(S.changed_paths(st['identity']['merge_base']))
    opaque={r['path'] for r in S.source_rows() if G.opaque(r)}
    def inside(path):return any(Sources.contains(child,path) for child in opaque)
    paths=C.expand(sorted('.' if p=='.' else S.safe_path(p).as_posix() for p in paths))
    paths=[p for p in paths if not any(p!=child and Sources.contains(child,p) for child in opaque)]
    units=set('/'.join(p.split('/')[:2]) if p.startswith(('services/','apps/','packages/')) and not inside('/'.join(p.split('/')[:2])) else '.' for p in paths)
    services,unknown,dependencies=al_affected_services.select(al_store.REPO_ROOT,paths,opaque)
    units.update(services);names={p for p in dependencies if not al_affected_services.implementation_input(p)}
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
    names.update(str(Path(u)/name) for u in units for name in ('package.json','pyproject.toml') if not inside(str(Path(u)/name)) and (al_store.REPO_ROOT/u/name).is_file())
    ci=CI.inventory(al_store.REPO_ROOT,paths,context,opaque)
    names.update(d['path'] for d in ci['dependencies'])
    for pattern in PATTERNS:names.update(q.relative_to(al_store.REPO_ROOT).as_posix() for q in al_store.REPO_ROOT.glob(pattern) if not inside(q.relative_to(al_store.REPO_ROOT).as_posix()) and q.is_file())
    return {'paths':paths,'units':sorted(units),'rules':[source(p) for p in sorted(names)],'services':services,'unknown_imports':unknown,'import_dependencies':dependencies,'ci':ci}


def gaps(st,m,anticipated=()):
    expected=inventory(st,[*m.get('paths',[]),*anticipated],m.get('ci_context'));out=[]
    declared={r['path']:r['sha256'] for r in m['rules']}
    for r in expected['rules']:
        if declared.get(r['path'])!=r['sha256']:out.append('missing/current repository source: '+r['path'])
    if set(expected['units'])-set(m['units']):out.append('manifest omits selected or anticipated units')
    if any(not C.covered(m.get('paths',[]),p) for p in expected['paths']):out.append('manifest omits selected or anticipated paths')
    gates=m['gates'];root=al_store.REPO_ROOT
    contracts=False
    for file in {root/'package.json',*(root/u/'package.json' for u in expected['units'])}:
        if not file.is_file():continue
        try:scripts=json.loads(file.read_text()).get('scripts',{})
        except (ValueError,AttributeError):contracts=True;continue
        contracts=contracts or any('contract' in k and ('gen' in k or 'build' in k) for k in scripts)
    for category,needed in (('lint-imports',bool(expected['services']) or expected['unknown_imports']),('loc',(root/'scripts/check-loc.py').is_file()),('pre-commit',(root/'.husky/pre-commit').is_file() or (root/'.githooks/pre-commit').is_file()),('contracts',contracts),('ci',bool(expected['ci']['obligations']))):
        active=[g for g in gates if g['category']==category and g['applicability']!='NOT_APPLICABLE']
        if needed and not active:out.append('N/A contradicts known '+category+' obligation')
        if category=='lint-imports':
            for gate in active:
                if any(not any(scope=='.' or path==scope or path.startswith(scope.rstrip('/')+'/') for scope in gate['scope']) for path in expected['import_dependencies']):out.append('import gate omits consumer discovery inputs: '+gate['id'])
            for service in expected['services']:
                if not any(service in g['units'] and any(p=='.' or p==service or p.startswith(service+'/') for p in g['scope']) for g in active):out.append('missing affected-service imports: '+service)
            if expected['unknown_imports'] and not any(g['applicability']=='UNRESOLVED' for g in active):out.append('shared Python applicability remains unresolved')
    out.extend(CI.gaps(expected['ci'],gates))
    return sorted(set(out))


def validate(st,m):
    missing=gaps(st,m)
    if missing:refuse('incomplete repository gate inventory: '+str(missing))
