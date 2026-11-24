"""Contextual Python import graphs; unresolved dynamic mechanisms broaden impact."""
import ast
import copy
import hashlib
import re
from al_analysis_cache import AnalysisCache
from al_blob_analysis import TokenFinder, _trie_pattern  # noqa: F401  (re-exported)
import al_blob_analysis as blob
from al_profile import measured
from collections import defaultdict
from pathlib import PurePosixPath
import sys
import al_writer_progress as progress
from al_repository_facts import within,SOURCE_SUFFIXES,gap, unit_depth, PathScope

# Reviewed module/distribution identities, never inferred from installed packages.
EXTERNAL={'elasticsearch':'elasticsearch'}
STDLIB=frozenset({'json','os','sys','pathlib','collections','re','math','importlib'})
TARGETS={None,'>=3.11','>=3.12','>=3.13','>=3.14','==3.11.*','==3.12.*','==3.13.*','==3.14.*'}


def _targets_digest(targets):
    """Every import of both snapshots, bound by digest: a changed import changes the facts without listing them."""
    return hashlib.sha256('\n'.join('\0'.join(row) for row in sorted(targets)).encode()).hexdigest()


def _analyze(facts,scan):
    units={u['id']:u for u in facts['units']};potential=sorted(u['id'] for u in facts['units'] if ('python' in u['languages'] or facts['adapter']['id'] in {'python','conventional'}) and (u['roles'] or u['id']=='.' or u['id'].startswith(('services/','apps/'))))
    records={};owners=defaultdict(set);edges=set();uncertainties=[];resolved=set();import_targets=set()
    scope=PathScope(scan.paths)
    for path in sorted(set(scan.files)|set(scan.base)|set(scan.paths)):
        if path.endswith('.py') or not path.endswith(SOURCE_SUFFIXES) or not scope.overlaps(path):continue
        owners_for_path=[u for u in units.values() if within(u['path'],path)]
        if not owners_for_path:continue  # enumeration reports the unsupported root
        unit=max(owners_for_path,key=lambda u:unit_depth(u['path']))
        if path.endswith('.go') and 'go' in unit:continue
        if facts['adapter']['id']=='contract':
            if unit.get('independent'):continue
            facts['enumeration']['gaps'].append(gap('unsupported-shared-language',path,'the repository contract cannot prove impact for this non-Python source class',unit['id']))
        else:
            uncertainties.append({'code':'uncertain-language-impact','unit':unit['id'],'path':path,'location':None,'target':None,'reason':'non-Python dependency analysis is unsupported; all enumerated services require verification'})
    def owner(path):
        candidates=[u for u in units.values() if any(within(r,path) for r in u['source_roots']) and within(u['path'],path)]
        return max(candidates,key=lambda u:unit_depth(u['path'])) if candidates else None
    for snapshot,files in [('base',scan.base),('current',scan.files)]:
        for path in files:
            if not path.endswith('.py'):continue
            unit=owner(path)
            if unit is None:continue
            root=max((r for r in unit['source_roots'] if within(r,path)),key=unit_depth)
            relative=path if root=='.' else path[len(root)+1:]
            # Test/cache code does not supply installed module identities.
            if any(p in {'tests','__pycache__','.venv'} for p in PurePosixPath(relative).parts):continue
            module=relative[:-3].replace('/','.');package=module.endswith('.__init__') or module=='__init__'
            module=module.removesuffix('.__init__') if module!='__init__' else ''
            key=(snapshot,path);records[key]={'unit':unit['id'],'module':module,'package':package};owners[module].add(key)
    def issue(record,path,line,target,reason):
        uncertainties.append({'code':'uncertain-import','unit':record['unit'],'path':path,'location':line,'target':target,'reason':reason})
    def targets(record,name):
        own=record['unit'];visible={own}
        visible.update(d for c in units[own]['module_contexts'] for d in c['local_dependencies'])
        # A module owns an import exactly when it is a dotted prefix of the imported name.
        parts=name.split('.')
        candidates={key for i in range(1,len(parts)+1) if parts[0] for key in owners.get('.'.join(parts[:i]),())}
        contextual={key for key in candidates if records[key]['unit'] in visible}
        if contextual:return contextual
        # Cross-unit imports remain conservative when a local dependency declaration
        # is absent; shared roots are possible, isolated service roots are not.
        return {key for key in candidates if not records[key]['unit'].startswith('services/')}
    # Each source's import observations are a per-blob fact (al_blob_analysis); they are
    # replayed here against the repository's module ownership, in source order.
    def resolve(record,snapshot,path,name,line,dynamic=False):
        if not isinstance(name,str) or not name:
            issue(record,path,line,None,'dynamic target is not a literal module');return
        import_targets.add((snapshot,path,name,'dynamic' if dynamic else 'static'))
        hits=targets(record,name)
        for key in hits:edges.add((path,key[1]))
        distinct={records[key]['unit'] for key in hits}
        if len(distinct)>1:issue(record,path,line,name,'ambiguous visible module ownership')
        if dynamic and not hits:
            root=name.split('.')[0]
            # A source directory/namespace anywhere can shadow external imports.
            shadow=any(module==root or module.startswith(root+'.') for module in owners)
            if not shadow and root in STDLIB and (units[record['unit']]['python_requirement'] in TARGETS):resolved.add((path,name,'stdlib'))
            elif not shadow and EXTERNAL.get(root) in units[record['unit']]['external_distributions']:resolved.add((path,name,EXTERNAL[root]))
            else:issue(record,path,line,name,'literal dynamic import identity is not established')
    for count,((snapshot,path),events) in enumerate(source_events(scan,records),1):
        progress.report('inventory-analysis',count=count,total=len(records))
        record=records[(snapshot,path)]
        for event in events:
            if event[0]=='i':issue(record,path,event[1],event[2],event[3])
            elif event[0]=='r':resolve(record,snapshot,path,event[1],event[2],event[3])
            elif targets(record,event[1]):resolve(record,snapshot,path,event[1],event[2])
    progress.report('inventory-analysis',count=len(records),force=True)
    affected={p for _,p in records if scope.overlaps(p)}
    changed_sources=set(affected)
    artifacts,unclassified=artifact_consumers(scan,records,units)
    if unclassified:
        uncertainties.append({'code':'uncertain-artifact-classification','unit':None,'path':scan.paths[0] if len(scan.paths)==1 else '.', 'location':None,'target':None,'reason':'changed runtime source/data has no complete consumer classification'})
    for row in artifacts:
        # An artifact owned by a service with no literal reference elsewhere is that service's input.
        if not row['consumers'] and row['kind'] not in {'generated-code','documentation'} and row['owner'] not in potential and not any(u['id']==row['owner'] and 'go' in u for u in units.values()):
            uncertainties.append({'code':'uncertain-artifact-consumer','unit':None,'path':row['path'],'location':None,'target':None,
                                  'reason':row['kind']+' has no literal consumer reference; all enumerated services require verification'})
        affected.update(c for c in row['consumers'] if c.endswith('.py'))
    importers=defaultdict(set)
    for a,b in edges:importers[b].add(a)
    pending=list(affected)
    while pending:
        for consumer in importers.get(pending.pop(),()):
            if consumer not in affected:affected.add(consumer);pending.append(consumer)
    known={record['unit'] for (_,p),record in records.items() if p in affected and record['unit'] in potential}
    def owner_of(path):
        owners=[u for u in units.values() if within(u['path'],path)]
        return max(owners,key=lambda u:unit_depth(u['path']))['id'] if owners else None
    # A consumer outside the unit layout belongs to no service, so no service check can verify
    # it: it widens coverage only through the units that literally reference it. Its literal
    # dependents are observed once; an unowned dependent keeps the conservative fallback.
    outside=sorted({c for row in artifacts for c in row['consumers'] if owner_of(c) is None})
    outside_dependents=_referencing_many(scan,outside)
    leaf=set()
    for consumer in outside:
        dependents=outside_dependents[consumer]
        if all(owner_of(d) is not None or d in outside_dependents for d in dependents):
            leaf.add(consumer)
            known.update(o for o in map(owner_of,dependents) if o in potential)
    for row in artifacts:
        for consumer in row['consumers']:
            owners_for_path=[u for u in units.values() if within(u['path'],consumer)]
            if owners_for_path:
                unit=max(owners_for_path,key=lambda u:unit_depth(u['path']))['id']
                if unit in potential:known.add(unit)
            else:
                facts['enumeration']['gaps'].append(gap('unclassified-runtime-consumer',consumer,
                    'literal runtime consumer is outside the supported unit layout'))
            if consumer in leaf:continue
            if not consumer.endswith('.py') and not (consumer.endswith('.go') and owners_for_path and 'go' in units[unit]):
                uncertainties.append({'code':'uncertain-language-impact','unit':unit if owners_for_path else None,
                    'path':consumer,'location':None,'target':None,
                    'reason':'runtime consumer dependency analysis is unavailable for this source language'})
    for u in potential:
        roots=units[u]['source_roots']
        if (u!='.' and scope.overlaps(u)) or (u=='.' and (bool(scope.raw&{'.','pyproject.toml','uv.lock'}) or any(scope.overlaps(r) for r in roots))):known.add(u)
    # Deduplicate base/current diagnostics without source bytes or incidental order.
    uncertainties=list({(u['unit'],u['path'],u['location'],u['target'],u['reason']):u for u in uncertainties}.values())
    # Import-analysis uncertainty describes who may import a module. It is deferred (kept for
    # audit, never a blocker) only when it provably cannot reach the affected modules: nothing
    # importable is affected, or its literal target cannot name an affected module. Unknown
    # targets, uncertainty inside affected files, unclassified data changes and every other
    # uncertainty class keep blocking.
    affected_modules={records[key]['module'] for key in records if key[1] in affected}
    def reaches(target):
        return any(m==target or m.startswith(target+'.') or target.startswith(m+'.') for m in affected_modules if m)
    def deferrable(u):
        # Uncertainty inside an affected file concerns the changed code itself and always counts.
        if u['code']!='uncertain-import' or unclassified or u['path'] in affected:return False
        if not affected:return True
        return isinstance(u['target'],str) and bool(u['target']) and not reaches(u['target'])
    flags=[deferrable(u) for u in uncertainties]
    deferred=[u for u,flag in zip(uncertainties,flags) if flag]
    uncertainties=[u for u,flag in zip(uncertainties,flags) if not flag]
    order=lambda u:(u['path'],u['location'] or 0,str(u['target']),u['reason'])
    return {'edges':[list(edge) for edge in sorted(edges)],'known_affected':sorted(known),'potential_services':potential,
            'uncertainties':sorted(uncertainties,key=order),'deferred_uncertainties':sorted(deferred,key=order),'artifact_consumers':artifacts, 'resolved_dynamic':[list(r) for r in sorted(resolved)],'import_targets_digest':_targets_digest(import_targets),'import_targets_count':len(import_targets),'task_import_targets_digest':_targets_digest({row for row in import_targets if row[1] in affected or records[(row[0],row[1])]['unit'] in (set(potential) if uncertainties else known|set(facts.get('policy_selection',[])))}),'affected_source_paths':sorted(affected|{consumer for row in artifacts for consumer in row['consumers']}),'source_membership':[[snapshot,path,r['unit'],r['module']] for (snapshot,path),r in sorted(records.items())],'policy_selection':facts.pop('policy_selection',[]),
            'runtime_consumer_dependents':outside_dependents,
            'runtime_consumer_reach':_consumer_reach(scan,units,edges,importers,changed_sources,artifacts,outside)}


def _consumer_reach(scan,units,edges,importers,changed_sources,artifacts,outside):
    """Whether each consumer outside the unit layout literally reaches the change.

    Consumer discovery over-approximates on shared file and directory names; a script that
    only shares a name is no consumer of the change. It reaches the change when its bytes
    name a changed path (or the unit directory owning one) in full, or when it imports a
    module in the changed sources' importer closure."""
    reached=set(changed_sources);pending=list(changed_sources)
    while pending:
        for consumer in importers.get(pending.pop(),()):
            if consumer not in reached:reached.add(consumer);pending.append(consumer)
    changed=set(scan.paths)|{row['path'] for row in artifacts}
    owners=set()
    for path in changed:
        found=[u['path'] for u in units.values() if u['path'] not in {'.',''} and within(u['path'],path)]
        if found:owners.add(max(found,key=unit_depth))
    tokens=sorted({t.rstrip('/').encode() for t in changed if t not in {'.',''}}|{(o.rstrip('/')+'/').encode() for o in owners})
    imports=defaultdict(set)
    for a,b in edges:imports[a].add(b)
    out={}
    for consumer in outside:
        data=scan.files.get(consumer) if consumer in scan.files else scan.base.get(consumer,b'')
        out[consumer]=bool(imports.get(consumer,set())&reached) or any(token in data for token in tokens)
    return out


DATA_SUFFIXES=('.json','.yaml','.yml')
DOCUMENT_SUFFIXES=('.md','.markdown','.mdx')
GENERATED_MARKERS=(b'@generated',b'do not edit',b'code generated')


def artifact_kind(path,data):
    """Closed artifact classes: documentation, schemas, data fixtures and generated code."""
    if path.endswith(DOCUMENT_SUFFIXES):return 'documentation'
    if path.endswith('.py'):
        header=b'\n'.join(data.splitlines()[:8]).lower()
        return 'generated-code' if any(marker in header for marker in GENERATED_MARKERS) else None
    if not path.endswith(DATA_SUFFIXES):return None
    head=data[:65536]
    if path.endswith('.json'):
        import json
        try:value=json.loads(data)
        except (ValueError,UnicodeError,RecursionError):value=None
        if isinstance(value,dict) and ('openapi' in value or 'swagger' in value):return 'openapi-schema'
    elif any(line.startswith((b'openapi:',b'swagger:')) for line in head.splitlines()):return 'openapi-schema'
    return 'data-fixture'


def _reference_tokens(path):
    pure=PurePosixPath(path);tokens=[pure.name.encode()]
    if pure.parent.name:
        directory=pure.parent.name.encode()
        tokens+=[directory+b'/',b'"'+directory,b"'"+directory]
    return tokens


def _root(scan):
    return getattr(scan,'root',None)


# Per-blob recomputation runs in at most this many worker processes per command (#25);
# AL_ANALYSIS_JOBS bounds it (1 disables parallelism). Small batches stay in process.
PARALLEL_MINIMUM=256
BATCH=2048


def _jobs():
    import os
    try:value=int(os.environ.get('AL_ANALYSIS_JOBS',''))
    except ValueError:value=0
    return value if value>0 else max(1,min(4,(os.cpu_count() or 1)))


def _call(name,args):
    return getattr(blob,name)(*args)


def _run_many(calls):
    """[al_blob_analysis.<name>(*args)] in order: forked workers (which share this process's
    exact code) for large batches, in process otherwise or when workers are unavailable."""
    jobs=_jobs()
    if jobs>1 and len(calls)>=PARALLEL_MINIMUM:
        import multiprocessing
        import warnings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',DeprecationWarning)
                context=multiprocessing.get_context('fork')
                with context.Pool(jobs) as pool:
                    return pool.starmap(_call,calls,chunksize=max(16,len(calls)//(jobs*8)))
        except (OSError,ValueError,RuntimeError,ImportError):
            pass
    return [_call(name,args) for name,args in calls]


def _blob_facts(scan,namespace,rows,call,finish=lambda value:value,*,compress=False):
    """Per-blob facts for `rows` of (files, path, key): stored ones are reused; the missing
    ones read each distinct blob once (verified against its captured hash, in this process)
    and are computed by `al_blob_analysis` functions, in parallel when many are missing.
    `call(key, data)` names the function and its arguments; `finish` converts its result."""
    import al_analysis_index as index
    keys=[];first={}
    for files,path,key in rows:
        identity=index.encode(key)
        if identity not in first:first[identity]=len(keys);keys.append((key,files,path))
    def missing(positions):
        out={};pending=[]
        def flush():
            for position,value in zip([p for p,_ in pending],_run_many([c for _,c in pending])):out[position]=finish(value)
            pending.clear()
        for files in {id(keys[i][1]):keys[i][1] for i in positions}.values():
            wanted=[i for i in positions if keys[i][1] is files]
            by_path={keys[i][2]:i for i in wanted}
            for path,data in files.content_items(list(by_path)):
                pending.append((by_path[path],call(keys[by_path[path]][0],data)))
                if len(pending)>=BATCH:flush()
        flush()
        return out
    values=index.facts(namespace,index.code_digest('al_blob_analysis'),[k for k,_,_ in keys],missing,root=_root(scan),compress=compress)
    return {identity:values[position] for identity,position in first.items()}


def source_events(scan,records):
    """((snapshot, path), import events) for every recorded Python source, base first."""
    import al_analysis_index as index
    order=[key for snapshot in ('base','current') for key in records if key[0]==snapshot]
    files={'base':scan.base,'current':scan.files}
    rows=[(files[s],p,[files[s].records[p]['sha256'],records[(s,p)]['module'],records[(s,p)]['package']]) for s,p in order]
    events=_blob_facts(scan,'python-import-events',rows,lambda key,data:('import_events',(data,key[1],key[2])))
    for (s,p),(_,_,key) in zip(order,rows):yield (s,p),events[index.encode(key)]


def _reference_runs(scan,rows):
    """{sha256: runs} for (files, path, sha256) rows: al_blob_analysis.reference_runs per blob."""
    found=_blob_facts(scan,'reference-runs',[(f,p,[sha]) for f,p,sha in rows],
                      lambda key,data:('reference_runs',(data,)),lambda runs:runs.decode('latin-1'),compress=True)
    import al_analysis_index as index
    return {sha:found[index.encode([sha])].encode('latin-1') for _,_,sha in rows}


def _referencing_many(scan,paths,stage='inventory-references'):
    """Source files (other than the artifact itself) naming each artifact or its directory,
    from one pass over source content.

    Per-artifact scans re-read and re-verify every repository source once per
    changed artifact (changed files x repository files); this reads each source once.
    Keep this the only definition: a second, per-token rescan once shadowed it (#23).

    Facts (#25): the whole answer is keyed by the tokens and every captured source hash;
    otherwise each distinct blob is answered from its stored reference runs, so only new
    or changed blobs are read. Both are keyed by their producing code's digest."""
    import al_analysis_index as index
    wanted={path:_reference_tokens(path) for path in paths}
    if not wanted:return {}
    batches=[(files,[p for p in files if p.endswith(SOURCE_SUFFIXES)]) for files in (scan.files,scan.base)]
    total=sum(len(sources) for _,sources in batches)
    key={'wanted':{path:[t.hex() for t in tokens] for path,tokens in sorted(wanted.items())},
         'sources':[[files.snapshot,p,files.records[p]['sha256']] for files,sources in batches for p in sources]}
    def compute():
        tokens={t for values in wanted.values() for t in values}
        finder=TokenFinder(tokens)
        holders=defaultdict(set)
        count=0
        if all(blob.token_safe(t) for t in tokens):
            rows=[(files,p,files.records[p]['sha256']) for files,sources in batches for p in sources]
            runs=_reference_runs(scan,rows)
            present={}
            for files,p,sha in rows:
                if sha not in present:present[sha]=finder.present(runs[sha])
                for token in present[sha]:holders[token].add(p)
                count+=1
                progress.report(stage,count=count,total=total)
        else:
            for files,sources in batches:
                for p,data in files.content_items(sources):
                    for token in finder.present(data):holders[token].add(p)
                    count+=1
                    progress.report(stage,count=count,total=total)
        return {path:sorted({p for t in values for p in holders.get(t,()) if p!=path}) for path,values in wanted.items()}
    result=index.fact('literal-references',index.code_digest('al_python_imports'),key,compute,root=_root(scan))
    progress.report(stage,count=total,total=total,force=True)
    return result


def _referencing(scan,path,exclude):
    """Source files naming the artifact or its directory (directory loads and globs over-select)."""
    found=_referencing_many(scan,[path])[path]
    return [p for p in found if p!=exclude]


MANIFESTS=frozenset({'package.json','package-lock.json','tsconfig.json','composer.json','pnpm-lock.yaml','pnpm-workspace.yaml'})


def artifact_consumers(scan,records,units):
    """Changed data artifacts and the source files that literally reference them.

    A literal path or file-name reference identifies a consumer (over-approximating on
    shared names); generated code records the schema files that produced it."""
    rows=[];unclassified=False
    scope=PathScope(scan.paths)
    changed=sorted({p for files in (scan.files,scan.base) for p in files if scope.covers(p)} |
                   {p for p in scan.paths if p.endswith(DOCUMENT_SUFFIXES)} |
                   {p for p in scan.members if not p.endswith('/') and scope.covers(p)})
    # Configuration, manifests and rule sources keep their existing unit/rule handling.
    candidates=[path for path in changed if path in scan.rules or not (PurePosixPath(path).name in MANIFESTS or any(part.startswith('.') for part in PurePosixPath(path).parts))]
    # Every artifact's literal consumers come from one shared pass over repository sources.
    references=_referencing_many(scan,candidates)
    def referencing(path):return [p for p in references[path] if p!=path]
    data_names=defaultdict(list)
    for p in sorted(set(scan.files)|set(scan.base)):
        if p.endswith(DATA_SUFFIXES):data_names[PurePosixPath(p).name.encode()].append(p)
    data_finder=None
    for path in candidates:
        policy=path in scan.rules
        if policy:
            consumers=referencing(path)
            if not consumers:continue
        else:consumers=None
        owners=[u for u in units.values() if within(u['path'],path)]
        owner=max(owners,key=lambda u:unit_depth(u['path']))['id'] if owners else None
        data=scan.files[path] if path in scan.files else scan.base.get(path,b'')
        kind=artifact_kind(path,data)
        if policy and kind is None:kind='runtime-source'
        if kind is None:
            # A changed non-module file of no supported artifact class keeps the exhaustive fallback.
            unclassified=unclassified or not path.endswith(SOURCE_SUFFIXES) or not owners
            continue
        if kind=='generated-code':
            header=b'\n'.join(data.splitlines()[:8])
            if data_finder is None:data_finder=TokenFinder(data_names)
            producers=sorted(p for name in data_finder.present(header) for p in data_names[name])
            rows.append({'path':path,'kind':kind,'owner':owner,'producers':producers,'consumers':[]})
            continue
        rows.append({'path':path,'kind':kind,'owner':owner,'producers':[],
                     'consumers':consumers if consumers is not None else referencing(path)})
    # Changed paths of kinds the snapshot never reads (e.g. .csv, .sql, templates) are unclassified too.
    captured=PathScope([p for files in (scan.files,scan.base) for p in files])
    for path in scan.paths:
        parts=PurePosixPath(path).parts
        if path=='.' or captured.contained(path) or any(part.startswith('.') for part in parts):continue
        if not path.endswith(DOCUMENT_SUFFIXES) and not (path in scan.rules):unclassified=True
    return rows,unclassified


from al_runtime_resources import implementation_digest,binding_identity
IMPLEMENTATION = implementation_digest()
# Whole-repository graphs get their own small process-local cache so per-file
# entries elsewhere cannot evict them between identical gate inventory calls.
GRAPHS = AnalysisCache(max_entries=4)


@measured('import-analysis')
def analyze(facts, scan):
    key = {'implementation': IMPLEMENTATION, 'runtime_binding': binding_identity(), 'callable': id(_analyze), 'facts': facts,
           'repository': str(scan.root), 'base': scan.base_oid, 'paths': scan.paths,
           'opaque': scan.opaque, 'required_adapter': scan.required_adapter,
           'files': scan.files.hashes(),
           'base_files': scan.base.hashes(),
           'modes': scan.modes, 'base_modes': scan.base_modes,'rules':sorted(scan.rules)}
    def compute():
        fresh = copy.deepcopy(facts)
        count = len(fresh['enumeration']['gaps'])
        dependencies = _analyze(fresh, scan)
        return {'dependencies': dependencies, 'enumeration_gap_delta': fresh['enumeration']['gaps'][count:],
                'consumed_policy_selection': 'policy_selection' in facts and 'policy_selection' not in fresh}
    result = GRAPHS.get_or_compute('import-graph', key, compute)
    facts['enumeration']['gaps'].extend(result['enumeration_gap_delta'])
    if result['consumed_policy_selection']:
        facts.pop('policy_selection', None)
    return result['dependencies']
