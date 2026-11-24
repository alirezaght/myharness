"""Contextual Python import graphs; unresolved dynamic mechanisms broaden impact."""
import ast
import copy
import hashlib
import re
from al_analysis_cache import AnalysisCache, parse_python
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
    def issue(record,path,node,target,reason):
        uncertainties.append({'code':'uncertain-import','unit':record['unit'],'path':path,'location':getattr(node,'lineno',None),'target':target,'reason':reason})
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
    def sources():
        for snapshot,files in [('base',scan.base),('current',scan.files)]:
            paths=[path for which,path in records if which==snapshot]
            for path,data in files.content_items(paths):yield snapshot,path,records[(snapshot,path)],data
    for count,(snapshot,path,record,data) in enumerate(sources(),1):
        progress.report('inventory-analysis',count=count,total=len(records))
        # A source and its AST are local to one iteration, not a repository-sized memo.
        try:tree=parse_python(data)
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


def _trie_pattern(tokens):
    """A regex matching, at one position, the longest token that starts there.

    Shared prefixes are factored so the engine does O(token length) work per
    position instead of trying every token; single-child chains stay literal so
    nesting depth is the number of branch points, not the token length."""
    trie={}
    for token in tokens:
        node=trie
        for byte in token:node=node.setdefault(byte,{})
        node[None]=True
    def emit(node):
        branches=[]
        for byte in sorted(k for k in node if k is not None):
            run=bytes([byte]);child=node[byte]
            while None not in child and len(child)==1:
                (next_byte,child),=child.items();run+=bytes([next_byte])
            branches.append(re.escape(run)+emit(child))
        if not branches:return b''
        body=branches[0] if len(branches)==1 else b'(?:'+b'|'.join(branches)+b')'
        # Greedy optional continuation: the longest token at a position wins over its prefixes.
        return b'(?:'+body+b')?' if None in node else body
    return re.compile(emit(trie))


class TokenFinder:
    """Every token occurring (as a substring) in a byte string, in one regex scan.

    Equivalent to `{t for t in tokens if t in data}`: each search reports the
    longest token at the leftmost remaining start position, and every token that
    is a prefix of a reported match also occurs there."""
    def __init__(self,tokens):
        self.tokens=frozenset(t for t in tokens if t)
        self.pattern=_trie_pattern(sorted(self.tokens)) if self.tokens else None

    def present(self,data):
        if self.pattern is None:return set()
        found=set();position=0;search=self.pattern.search
        while True:
            match=search(data,position)
            if match is None:return found
            longest=match.group(0);start=match.start()
            found.update(longest[:k] for k in range(1,len(longest)+1) if longest[:k] in self.tokens)
            position=start+1


def _referencing_many(scan,paths,stage='inventory-references'):
    """Source files (other than the artifact itself) naming each artifact or its directory,
    from one pass over source content.

    Per-artifact scans re-read and re-verify every repository source once per
    changed artifact (changed files x repository files); this reads each source once.
    Keep this the only definition: a second, per-token rescan once shadowed it (#23)."""
    wanted={path:_reference_tokens(path) for path in paths}
    if not wanted:return {}
    batches=[(files,[p for p in files if p.endswith(SOURCE_SUFFIXES)]) for files in (scan.files,scan.base)]
    total=sum(len(sources) for _,sources in batches)
    # The result is a function of the tokens and the captured source hashes alone, so a
    # later command that captured the same sources (gates plan after discover) reuses it;
    # any changed source, token or runtime is a different key and is recomputed.
    from al_analysis_cache import DerivedStore
    from al_runtime_resources import implementation_digest
    store=DerivedStore('literal-references')
    key={'schema':1,'implementation':implementation_digest(),
         'wanted':{path:[t.hex() for t in tokens] for path,tokens in sorted(wanted.items())},
         'sources':[[files.snapshot,p,files.records[p]['sha256']] for files,sources in batches for p in sources]}
    cached=store.load(key)
    if isinstance(cached,dict) and set(cached)==set(wanted) and all(isinstance(v,list) for v in cached.values()):
        progress.report(stage,count=total,total=total,force=True)
        return cached
    finder=TokenFinder({t for tokens in wanted.values() for t in tokens})
    holders=defaultdict(set)
    count=0
    for files,sources in batches:
        for p,data in files.content_items(sources):
            for token in finder.present(data):holders[token].add(p)
            count+=1
            progress.report(stage,count=count,total=total)
    progress.report(stage,count=count,total=total,force=True)
    result={path:sorted({p for t in tokens for p in holders.get(t,()) if p!=path}) for path,tokens in wanted.items()}
    store.store(key,result)
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
