"""Contextual Python import graphs; unresolved dynamic mechanisms broaden impact."""
import ast
import copy
import hashlib
from al_analysis_cache import AnalysisCache, parse_python
from al_profile import measured
from collections import defaultdict
from pathlib import PurePosixPath
import sys
from al_repository_facts import within,SOURCE_SUFFIXES,gap

# Reviewed module/distribution identities, never inferred from installed packages.
EXTERNAL={'elasticsearch':'elasticsearch'}
STDLIB=frozenset({'json','os','sys','pathlib','collections','re','math','importlib'})
TARGETS={None,'>=3.11','>=3.12','>=3.13','>=3.14','==3.11.*','==3.12.*','==3.13.*','==3.14.*'}


def _analyze(facts,scan):
    units={u['id']:u for u in facts['units']};potential=sorted(u['id'] for u in facts['units'] if ('python' in u['languages'] or facts['adapter']['id']=='python') and (u['roles'] or u['id']=='.' or u['id'].startswith(('services/','apps/'))))
    records={};owners=defaultdict(set);edges=set();uncertainties=[];resolved=set();import_targets=set()
    for path in sorted(set(scan.files)|set(scan.base)|set(scan.paths)):
        if path.endswith('.py') or not path.endswith(SOURCE_SUFFIXES) or not any(within(p,path) or within(path,p) for p in scan.paths):continue
        owners_for_path=[u for u in units.values() if within(u['path'],path)]
        if not owners_for_path:continue  # enumeration reports the unsupported root
        unit=max(owners_for_path,key=lambda u:len(u['path']))
        if facts['adapter']['id']=='contract':
            if unit.get('independent'):continue
            facts['enumeration']['gaps'].append(gap('unsupported-shared-language',path,'the repository contract cannot prove impact for this non-Python source class',unit['id']))
        else:
            uncertainties.append({'code':'uncertain-language-impact','unit':unit['id'],'path':path,'location':None,'target':None,'reason':'non-Python dependency analysis is unsupported; all enumerated services require verification'})
    def owner(path):
        candidates=[u for u in units.values() if any(within(r,path) for r in u['source_roots']) and within(u['path'],path)]
        return max(candidates,key=lambda u:len(u['path'])) if candidates else None
    for snapshot,files in [('base',scan.base),('current',scan.files)]:
        for path,data in files.items():
            if not path.endswith('.py'):continue
            unit=owner(path)
            if unit is None:continue
            root=max((r for r in unit['source_roots'] if within(r,path)),key=len)
            relative=path if root=='.' else path[len(root)+1:]
            # Test/cache code does not supply installed module identities.
            if any(p in {'tests','__pycache__','.venv'} for p in PurePosixPath(relative).parts):continue
            module=relative[:-3].replace('/','.');package=module.endswith('.__init__') or module=='__init__'
            module=module.removesuffix('.__init__') if module!='__init__' else ''
            key=(snapshot,path);records[key]={'unit':unit['id'],'module':module,'package':package,'data':data};owners[module].add(key)
    def issue(record,path,node,target,reason):
        uncertainties.append({'code':'uncertain-import','unit':record['unit'],'path':path,'location':getattr(node,'lineno',None),'target':target,'reason':reason})
    def targets(record,name):
        own=record['unit'];visible={own}
        visible.update(d for c in units[own]['module_contexts'] for d in c['local_dependencies'])
        candidates={key for module,keys in owners.items() if module and (module==name or name.startswith(module+'.')) for key in keys}
        contextual={key for key in candidates if records[key]['unit'] in visible}
        if contextual:return contextual
        # Cross-unit imports remain conservative when a local dependency declaration
        # is absent; shared roots are possible, isolated service roots are not.
        return {key for key in candidates if not records[key]['unit'].startswith('services/')}
    trees={}
    for (snapshot,path),record in records.items():
        try:tree=parse_python(record['data'],trees)
        except (SyntaxError,UnicodeError,ValueError,RecursionError):
            issue(record,path,ast.Pass(),None,'source AST unavailable');continue
        if sum(1 for _ in ast.walk(tree))>100000:
            issue(record,path,tree,None,'source AST complexity limit');continue
        def resolve(name,node,dynamic=False):
            if not isinstance(name,str) or not name:
                issue(record,path,node,None,'dynamic target is not a literal module');return
            import_targets.add((snapshot,path,name,'dynamic' if dynamic else 'static'))
            hits=targets(record,name)
            for key in hits:edges.add((path,key[1]))
            distinct={records[key]['unit'] for key in hits}
            if len(distinct)>1:issue(record,path,node,name,'ambiguous visible module ownership')
            if dynamic and not hits:
                root=name.split('.')[0]
                # A source directory/namespace anywhere can shadow external imports.
                shadow=any(module==root or module.startswith(root+'.') for module in owners)
                if not shadow and root in STDLIB and (units[record['unit']]['python_requirement'] in TARGETS):resolved.add((path,name,'stdlib'))
                elif not shadow and EXTERNAL.get(root) in units[record['unit']]['external_distributions']:resolved.add((path,name,EXTERNAL[root]))
                else:issue(record,path,node,name,'literal dynamic import identity is not established')
        class Visitor(ast.NodeVisitor):
            def __init__(self):self.aliases={};self.tainted=set()
            def scope(self,node):
                saved=self.aliases,self.tainted;self.aliases=dict(self.aliases);self.tainted=set(self.tainted)
                body=node.body if hasattr(node,'body') else []
                # Binding analysis is deliberately conservative across branches/order.
                imports={};writes=defaultdict(int);alias_dependencies={}
                def bindings(nodes,conditional=False):
                    for n in nodes:
                        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):
                            if hasattr(n,'name'):writes[n.name]+=1
                            continue
                        if isinstance(n,(ast.Assign,ast.AnnAssign)):
                            value=n.value
                            alias=None
                            if isinstance(value,ast.Name) and (value.id=='__import__' or self.aliases.get(value.id) in {'function','builtin'} or imports.get(value.id) in {'function','builtin'}):alias='builtin' if value.id=='__import__' else imports.get(value.id,self.aliases.get(value.id))
                            elif isinstance(value,ast.Attribute) and value.attr=='import_module':
                                alias='function' if isinstance(value.value,ast.Name) and imports.get(value.value.id,self.aliases.get(value.value.id))=='module' else 'unknown'
                            for target in (n.targets if isinstance(n,ast.Assign) else [n.target]):
                                if alias and isinstance(target,ast.Name):
                                    imports[target.id]=alias
                                    alias_dependencies[target.id]=value.id if isinstance(value,ast.Name) else value.value.id if isinstance(value.value,ast.Name) else None
                                if isinstance(target,ast.Attribute) and target.attr=='path':issue(record,path,n,None,'runtime search path assignment')
                        if isinstance(n,ast.Import):
                            for a in n.names:
                                name=a.asname or a.name.split('.')[0];writes[name]+=1
                                if a.name=='importlib':imports[name]='module'
                                if conditional:writes[name]+=2
                        elif isinstance(n,ast.ImportFrom):
                            for a in n.names:
                                name=a.asname or a.name;writes[name]+=1
                                if n.module=='importlib' and a.name=='import_module':imports[name]='function'
                                if n.module=='builtins' and a.name=='__import__':imports[name]='builtin'
                                if conditional:writes[name]+=2
                        elif isinstance(n,ast.Name) and isinstance(n.ctx,(ast.Store,ast.Del)):writes[n.id]+=1
                        elif isinstance(n,(ast.Global,ast.Nonlocal)):
                            for name in n.names:writes[name]+=2
                        elif isinstance(n,ast.Attribute) and isinstance(n.ctx,(ast.Store,ast.Del)) and isinstance(n.value,ast.Name):writes[n.value.id]+=2
                        if conditional and isinstance(n,ast.Name) and isinstance(n.ctx,(ast.Store,ast.Del)):writes[n.id]+=2
                        bindings(list(ast.iter_child_nodes(n)),conditional or isinstance(n,(ast.If,ast.Try,ast.For,ast.While,ast.With,ast.Match)))
                bindings(body)
                if hasattr(node,'args'):
                    for arg in [*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs]:writes[arg.arg]+=2
                    for arg in (node.args.vararg,node.args.kwarg):
                        if arg:writes[arg.arg]+=2
                self.tainted.update(name for name,count in writes.items() if count>1 or name not in imports)
                self.tainted.difference_update(name for name,count in writes.items() if count==1 and name in imports)
                self.aliases.update(imports)
                while True:
                    tainted={name for name,dependency in alias_dependencies.items() if dependency in self.tainted or imports[name]=='unknown'}
                    if tainted<=self.tainted:break
                    self.tainted.update(tainted)
                for n in body:self.visit(n)
                self.aliases,self.tainted=saved
            visit_Module=scope
            visit_FunctionDef=scope
            visit_AsyncFunctionDef=scope
            visit_ClassDef=scope
            def visit_Lambda(self,node):issue(record,path,node,None,'lambda dynamic binding context unsupported') if any(isinstance(n,ast.Call) for n in ast.walk(node)) else None
            def visit_Import(self,node):
                for a in node.names:resolve(a.name,node)
            def visit_ImportFrom(self,node):
                base=node.module or ''
                if node.level:
                    package=record['module'].split('.') if record['package'] else record['module'].split('.')[:-1]
                    if node.level>len(package):issue(record,path,node,base,'relative import escapes package');return
                    base='.'.join(package[:len(package)-node.level+1]+([base] if base else []))
                resolve(base,node)
                for a in node.names:
                    if a.name!='*':
                        name=base+'.'+a.name if base else a.name
                        if targets(record,name):resolve(name,node)
            def primitive(self,node):
                return isinstance(node,ast.Name) and (node.id=='__import__' or self.aliases.get(node.id) in {'function','builtin','module','unknown'}) or isinstance(node,ast.Attribute) and node.attr in {'import_module','__import__'}
            def visit_Assign(self,node):
                if isinstance(node.value,(ast.List,ast.Tuple,ast.Set,ast.Dict)) and any(self.primitive(n) for n in ast.walk(node.value)):issue(record,path,node,None,'import primitive escapes through a container')
                self.generic_visit(node)
            def visit_Return(self,node):
                if self.primitive(node.value):issue(record,path,node,None,'import primitive escapes through a return value')
                self.generic_visit(node)
            def visit_Call(self,node):
                if isinstance(node.func,ast.Name) and node.func.id=='getattr' and node.args and (self.primitive(node.args[0]) or len(node.args)>1 and isinstance(node.args[1],ast.Constant) and node.args[1].value in {'import_module','__import__'}):issue(record,path,node,None,'reflective import primitive binding is unsupported')
                if any(self.primitive(n) for n in [*node.args,*(k.value for k in node.keywords)]):issue(record,path,node,None,'import primitive escapes as a call argument')
                fn=node.func;recognized=False;suspected=False
                if isinstance(fn,ast.Name):
                    kind=self.aliases.get(fn.id)
                    recognized=(fn.id=='__import__' or kind in {'function','builtin'}) and fn.id not in self.tainted
                    suspected=fn.id=='__import__' or kind in {'function','builtin','unknown'}
                elif isinstance(fn,ast.Attribute):
                    recognized=isinstance(fn.value,ast.Name) and self.aliases.get(fn.value.id)=='module' and fn.value.id not in self.tainted and fn.attr=='import_module'
                    suspected=fn.attr in {'import_module','__import__','exec_module','load_module','spec_from_file_location'}
                    if fn.attr in {'append','insert','extend'} and isinstance(fn.value,ast.Attribute) and fn.value.attr=='path':issue(record,path,node,None,'runtime search path mutation')
                if recognized:
                    target=node.args[0].value if node.args and isinstance(node.args[0],ast.Constant) else None
                    if isinstance(target,str) and target.startswith('.'):
                        package=node.args[1].value if len(node.args)>1 and isinstance(node.args[1],ast.Constant) else next((k.value.value for k in node.keywords if k.arg=='package' and isinstance(k.value,ast.Constant)),None)
                        if isinstance(package,str):
                            level=len(target)-len(target.lstrip('.'));parts=package.split('.')
                            target='.'.join(parts[:len(parts)-level+1]+[target.lstrip('.')]) if level<=len(parts) else None
                        else:target=None
                    if isinstance(fn,ast.Name) and (fn.id=='__import__' or self.aliases.get(fn.id)=='builtin'):
                        level=next((k.value.value if isinstance(k.value,ast.Constant) else None for k in node.keywords if k.arg=='level'),0)
                        if len(node.args)>4:level=node.args[4].value if isinstance(node.args[4],ast.Constant) else None
                        if level!=0 or len(node.args)>1 or any(k.arg in {'globals','locals'} for k in node.keywords):
                            issue(record,path,node,target,'builtin import context/relative level is unsupported');target=None
                    resolve(target,node,True)
                elif suspected:issue(record,path,node,None,'dynamic import binding is shadowed or unsupported')
                if isinstance(fn,ast.Name) and fn.id in {'eval','exec'}:issue(record,path,node,None,'runtime code execution can introduce imports')
                self.generic_visit(node)
        Visitor().visit(tree)
    affected={p for _,p in records if any(within(changed,p) or within(p,changed) for changed in scan.paths)}
    artifacts,unclassified=artifact_consumers(scan,records,units)
    for row in artifacts:
        # An artifact owned by a service with no literal reference elsewhere is that service's input.
        if not row['consumers'] and row['kind']!='generated-code' and row['owner'] not in potential:
            uncertainties.append({'code':'uncertain-artifact-consumer','unit':None,'path':row['path'],'location':None,'target':None,
                                  'reason':row['kind']+' has no literal consumer reference; all enumerated services require verification'})
        affected.update(c for c in row['consumers'] if c.endswith('.py'))
    while True:
        consumers={a for a,b in edges if b in affected}
        if consumers<=affected:break
        affected|=consumers
    known={record['unit'] for (_,p),record in records.items() if p in affected and record['unit'] in potential}
    for row in artifacts:
        for consumer in row['consumers']:
            owners_for_path=[u for u in units.values() if within(u['path'],consumer)]
            if owners_for_path:
                unit=max(owners_for_path,key=lambda u:len(u['path']))['id']
                if unit in potential:known.add(unit)
    for u in potential:
        roots=units[u]['source_roots']
        if any((u!='.' and (within(u,p) or within(p,u))) or (u=='.' and (p in {'.','pyproject.toml','uv.lock'} or any(within(r,p) or within(p,r) for r in roots))) for p in scan.paths):known.add(u)
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
    deferred=[u for u in uncertainties if deferrable(u)]
    uncertainties=[u for u in uncertainties if u not in deferred]
    order=lambda u:(u['path'],u['location'] or 0,str(u['target']),u['reason'])
    return {'edges':[list(edge) for edge in sorted(edges)],'known_affected':sorted(known),'potential_services':potential,
            'uncertainties':sorted(uncertainties,key=order),'deferred_uncertainties':sorted(deferred,key=order),'artifact_consumers':artifacts, 'resolved_dynamic':[list(r) for r in sorted(resolved)],'import_targets':[list(r) for r in sorted(import_targets)],'source_membership':[[snapshot,path,r['unit'],r['module']] for (snapshot,path),r in sorted(records.items())],'policy_selection':facts.pop('policy_selection',[])}


DATA_SUFFIXES=('.json','.yaml','.yml')
GENERATED_MARKERS=(b'@generated',b'do not edit',b'code generated')


def artifact_kind(path,data):
    """Closed artifact classes: OpenAPI schemas, other data fixtures, generated Python code."""
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


def _referencing(scan,path,exclude):
    """Source files naming the artifact or its directory (directory loads and globs over-select)."""
    pure=PurePosixPath(path);tokens=[pure.name.encode()]
    if pure.parent.name:
        directory=pure.parent.name.encode()
        tokens+=[directory+b'/',b'"'+directory,b"'"+directory]
    return sorted({p for files in (scan.files,scan.base) for p,data in files.items()
                   if p!=exclude and p.endswith(SOURCE_SUFFIXES) and any(token in data for token in tokens)})


MANIFESTS=frozenset({'package.json','package-lock.json','tsconfig.json','composer.json','pnpm-lock.yaml','pnpm-workspace.yaml'})


def artifact_consumers(scan,records,units):
    """Changed data artifacts and the source files that literally reference them.

    A literal path or file-name reference identifies a consumer (over-approximating on
    shared names); generated code records the schema files that produced it."""
    rows=[];unclassified=False
    changed=sorted({p for files in (scan.files,scan.base) for p in files if any(within(c,p) for c in scan.paths)})
    for path in changed:
        # Configuration, manifests and rule sources keep their existing unit/rule handling.
        if path in scan.rules or PurePosixPath(path).name in MANIFESTS or any(part.startswith('.') for part in PurePosixPath(path).parts):continue
        owners=[u for u in units.values() if within(u['path'],path)]
        if not owners:continue
        data=scan.files.get(path,scan.base.get(path,b''))
        kind=artifact_kind(path,data)
        if kind is None:
            # A changed non-module file of no supported artifact class keeps the exhaustive fallback.
            unclassified=unclassified or not path.endswith(SOURCE_SUFFIXES)
            continue
        if kind=='generated-code':
            header=b'\n'.join(data.splitlines()[:8])
            producers=sorted(p for p in set(scan.files)|set(scan.base) if p.endswith(DATA_SUFFIXES) and PurePosixPath(p).name.encode() in header)
            rows.append({'path':path,'kind':kind,'owner':max(owners,key=lambda u:len(u['path']))['id'],'producers':producers,'consumers':[]})
            continue
        rows.append({'path':path,'kind':kind,'owner':max(owners,key=lambda u:len(u['path']))['id'],'producers':[],
                     'consumers':_referencing(scan,path,path)})
    # Changed paths of kinds the snapshot never reads (e.g. .csv, .sql, templates) are unclassified too.
    for path in scan.paths:
        parts=PurePosixPath(path).parts
        if path=='.' or any(within(path,p) for files in (scan.files,scan.base) for p in files) or any(part.startswith('.') for part in parts):continue
        if any(within(u['path'],path) for u in units.values()) and not path.endswith(SOURCE_SUFFIXES):unclassified=True
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
           'files': {p: hashlib.sha256(v).hexdigest() for p, v in scan.files.items()},
           'base_files': {p: hashlib.sha256(v).hexdigest() for p, v in scan.base.items()},
           'modes': scan.modes, 'base_modes': scan.base_modes}
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
