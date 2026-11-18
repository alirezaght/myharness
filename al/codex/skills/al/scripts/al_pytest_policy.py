"""Versioned pytest qualification. Only the guarded launcher supplies execution proof.

This module never executes a source predicate or accepts CI status/JUnit as proof.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib
from al_ci_yaml import Unsupported

STRICT='strict-v1'
PLATFORM='platform-aware-v1'
PLATFORMS={'linux':'posix','darwin':'posix','win32':'nt'}
CONTEXT_ENV='AL_PYTEST_GUARD_CONTEXT'
RESOURCE_NAMES=('al_pytest_profile.py','al_pytest_sources.py','al_ci_yaml.py','al_pytest_runner.py','al_pytest_policy.py')


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def selection(read):
    data=tomllib.loads(read('pyproject.toml').decode()).get('tool',{}).get('al',{}).get('pytest-verification')
    if data is None:return {'version':STRICT}
    if not isinstance(data,dict) or set(data)!={'policy','platforms'} or data['policy']!=PLATFORM:
        raise Unsupported('unsupported pytest outcome policy declaration')
    matrix=data['platforms']
    if not isinstance(matrix,list) or not matrix or any(not isinstance(p,str) or p not in PLATFORMS for p in matrix) or len(matrix)!=len(set(matrix)):
        raise Unsupported('literal unique supported pytest platforms required')
    return {'version':PLATFORM,'platforms':sorted(matrix),'source':'pyproject.toml'}


def observed_platform():
    # Capture in the trusted runner before test imports; callers cannot supply this.
    executable=Path(sys.executable).resolve(strict=True)
    return {'sys_platform':sys.platform,'os_name':os.name,'implementation':sys.implementation.name,
            'version':list(sys.version_info[:3]),'executable':str(executable),
            'executable_sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}


def predicate(node,observed):
    """A closed AST grammar, with no eval, string conditions or function calls."""
    if not isinstance(node,ast.Compare) or len(node.ops)!=1 or len(node.comparators)!=1:return None
    left,right=node.left,node.comparators[0]
    if not isinstance(left,ast.Attribute) or not isinstance(left.value,ast.Name) or not isinstance(right,ast.Constant):return None
    key={('sys','platform'):'sys_platform',('os','name'):'os_name'}.get((left.value.id,left.attr))
    allowed=set(PLATFORMS) if key=='sys_platform' else {'posix','nt'}
    if key is None or type(right.value) is not str or right.value not in allowed or not isinstance(node.ops[0],(ast.Eq,ast.NotEq)):return None
    value=observed[key]==right.value
    if isinstance(node.ops[0],ast.NotEq):value=not value
    return {'expression':ast.unparse(node),'key':key,'operator':'==' if isinstance(node.ops[0],ast.Eq) else '!=','literal':right.value,'value':value}


def source_skip_proof(raw,item,observed):
    """Resolve direct module/class/function skipif declarations, never marker aliases.

    Runtime marks must match the complete static skip declaration list. Unknown,
    indirect or mutated declarations fail closed even if the reason mentions OS.
    """
    tree=ast.parse(raw);imports=set();declarations=[]
    for node in tree.body:
        if isinstance(node,ast.Import):
            imports.update(a.name for a in node.names if a.name in {'sys','os','pytest'} and a.asname is None)
    for node in ast.walk(tree):
        if isinstance(node,(ast.Name,ast.Attribute)) and isinstance(node.ctx,(ast.Store,ast.Del)):
            base=node
            while isinstance(base,ast.Attribute):base=base.value
            if isinstance(base,ast.Name) and base.id in {'sys','os','pytest'}:return None
        if isinstance(node,ast.ImportFrom) and any(a.asname in {'sys','os','pytest'} or a.name in {'sys','os','pytest'} for a in node.names):return None
        if isinstance(node,ast.Import) and any(a.asname in {'sys','os','pytest'} or (a.name in {'sys','os','pytest'} and a.asname) for a in node.names):return None
    def marks(value):
        values=value.elts if isinstance(value,(ast.List,ast.Tuple)) else [value]
        for mark in values:
            call=mark.func if isinstance(mark,ast.Call) else mark
            # Other literal marks (native/browser/parametrize) do not prove skips.
            if isinstance(call,ast.Attribute) and isinstance(call.value,ast.Attribute) and isinstance(call.value.value,ast.Name) and call.value.value.id=='pytest' and call.value.attr=='mark':
                if call.attr in {'skip','skipif','xfail'}:declarations.append(mark)
            elif isinstance(mark,ast.Name):declarations.append(mark)
    scope=tree.body
    for node in scope:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pytestmark' for t in node.targets):marks(node.value)
    if item.cls is not None:
        classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==item.cls.__name__]
        if len(classes)!=1:return None
        for mark in classes[0].decorator_list:marks(mark)
        scope=classes[0].body
        for node in scope:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pytestmark' for t in node.targets):marks(node.value)
    name=getattr(item,'originalname',None) or item.name
    functions=[n for n in scope if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
    if len(functions)!=1:return None
    for mark in functions[0].decorator_list:marks(mark)
    runtime=list(item.iter_markers('skipif'))
    if list(item.iter_markers('skip')) or list(item.iter_markers('xfail')) or len(runtime)!=len(declarations) or not declarations:return None
    proven=[]
    for mark in declarations:
        if not isinstance(mark,ast.Call) or not isinstance(mark.func,ast.Attribute) or mark.func.attr!='skipif' or len(mark.args)!=1:return None
        if 'pytest' not in imports:return None
        condition=predicate(mark.args[0],observed)
        if condition is None or mark.args[0].left.value.id not in imports:return None
        if len(mark.keywords)!=1 or mark.keywords[0].arg!='reason' or not isinstance(mark.keywords[0].value,ast.Constant) or type(mark.keywords[0].value.value) is not str:return None
        expected=(condition['value'],mark.keywords[0].value.value)
        index=next((i for i,m in enumerate(runtime) if len(m.args)==1 and type(m.args[0]) is bool and m.args[0] is expected[0] and m.kwargs=={'reason':expected[1]}),None)
        if index is None:return None
        runtime.pop(index);proven.append({**condition,'line':mark.lineno})
    return proven if any(p['value'] for p in proven) else None


def accounting(collected,reports,proofs,policy):
    """Exact per-node stages; a successful call alone is insufficient."""
    errors=[];nodes=[]
    if len(set(collected))!=len(collected):errors.append('duplicate collected test identity')
    if any(r['nodeid'] not in collected for r in reports):errors.append('unexpected pytest outcome identity')
    for node in collected:
        rows=[r for r in reports if r['nodeid']==node];stages={r['when']:r for r in rows}
        state='UNVERIFIED';reason='missing, duplicate or unsupported pytest outcomes'
        if len(stages)==len(rows) and set(stages)<={'setup','call','teardown'} and all(r['outcome'] in {'passed','failed','skipped'} for r in rows):
            if any(r['xfail'] for r in rows):reason='expected-failure outcome is insufficient'
            elif any(r['outcome']=='failed' for r in rows):state='FAIL';reason='pytest stage failed'
            elif set(stages)=={'setup','call','teardown'} and all(r['outcome']=='passed' for r in rows):state='PASS';reason='all required stages passed'
            elif policy['version']==PLATFORM and set(stages)=={'setup','teardown'} and stages['setup']['outcome']=='skipped' and stages['teardown']['outcome']=='passed' and proofs.get(node):state='NOT_APPLICABLE';reason='source-proven literal OS skipif'
        nodes.append({'nodeid':node,'status':state,'reason':reason,**({'predicate_proof':proofs[node]} if state=='NOT_APPLICABLE' else {})})
    return nodes,errors



def collection_gaps(proof,collected,deselected):
    """A survivor list cannot prove collection: every actual collector must pass."""
    if not isinstance(proof,dict) or set(proof)!={'schema_version','finished','reports'} or proof['schema_version']!=1 or proof['finished'] is not True:
        return ['guarded pytest collection proof missing or unfinished']
    reports=proof['reports']
    if not isinstance(reports,list) or not reports:return ['guarded pytest collector outcomes missing']
    collectors={}
    for report in reports:
        if not isinstance(report,dict) or set(report)!={'nodeid','outcome','children'} or not isinstance(report['nodeid'],str) or not isinstance(report['children'],list) or any(not isinstance(n,str) for n in report['children']):
            return ['unsupported guarded pytest collector report']
        if report['outcome']!='passed':return ['skipped, failed or unsupported pytest collection remains unverified']
        if report['nodeid'] in collectors:return ['duplicate guarded pytest collector identity']
        collectors[report['nodeid']]=report['children']
    inventory=collected+deselected
    if len(set(inventory))!=len(inventory) or set(inventory)&set(collectors):return ['duplicate or ambiguous guarded pytest item inventory']
    if '' not in collectors:return ['guarded pytest session collector missing']
    # pytest's session identity is ""; a selected root directory "." is a
    # separate collector. Empty reported collectors are legitimate, not items.
    pending=[''];visited=set();parents={};leaves=[]
    while pending:
        node=pending.pop()
        if node in visited:return ['cyclic guarded pytest collector graph']
        visited.add(node)
        for child in collectors[node]:
            if child in parents or child=='':return ['duplicate or cyclic guarded pytest collection child']
            parents[child]=node
            if child in collectors:pending.append(child)
            elif child in inventory:leaves.append(child)
            else:return ['guarded pytest collection child has no collector report or accounted item']
    if visited!=set(collectors):return ['unreachable guarded pytest collector report']
    if set(leaves)!=set(inventory):return ['guarded pytest collection inventory is incomplete']
    return []

def qualification(policy,collected,reports,proofs,observed,context,bindings):
    nodes,errors=accounting(collected,reports,proofs,policy)
    if policy['version']==PLATFORM:errors.extend(collection_gaps(bindings.get('collection_proof'),collected,bindings.get('deselected',[])))
    matrix=policy.get('platforms',[observed['sys_platform']]);current=observed['sys_platform']
    if policy['version']==PLATFORM and (current not in matrix or PLATFORMS.get(current)!=observed['os_name']):errors.append('observed platform outside supported matrix')
    if policy['version']==PLATFORM and (not isinstance(context,dict) or set(context)!={'run','task','manifest','gate','stage','base','candidate','invocation'} or any(not isinstance(v,str) or not v for v in context.values())):
        errors.append('protected task/candidate/base launch context missing')
    obligations=[{'nodeid':n['nodeid'],'platform':p,'status':n['status'] if p==current else 'UNVERIFIED'} for n in nodes for p in matrix]
    complete=bool(nodes) and not errors and all(r['status'] in {'PASS','NOT_APPLICABLE'} for r in obligations)
    return {'policy':policy,'observed_platform':observed,'context':context,'binding_sha256':digest(bindings),
            'nodes':nodes,'obligations':obligations,'errors':errors,'complete':complete,
            'verdict':'UNVERIFIED' if policy['version']==PLATFORM and errors else 'VERIFIED' if complete else 'FAIL' if any(n['status']=='FAIL' for n in nodes) else 'UNVERIFIED',
            'passed':sum(n['status']=='PASS' for n in nodes),'not_applicable':sum(n['status']=='NOT_APPLICABLE' for n in nodes)}


def gate_requirements(manifest,gate):
    """Use the independently sourced binding, never fields supplied by a test."""
    bindings=manifest.get('service_coverage',{}).get('assessment',{}).get('required_bindings',[])
    matches=[b for b in bindings if b.get('execution_signature')==gate.get('execution_signature') and gate.get('execution_signature')]
    if len(matches)>1:raise ValueError('ambiguous guarded pytest binding')
    chain=matches[0].get('verification_chain',{}) if matches else gate.get('remote_source',{}).get('verification_chain',{})
    return [r for r in chain.get('requirements',[]) if 'pytest_profile' in r]


def launch_context(st,manifest_sha,gate,stage,base,candidate,invocation):
    from al_task import task_hash
    return {'run':st['run_id'],'task':task_hash(st),'manifest':manifest_sha,'gate':gate['id'],
            'stage':stage,'base':base,'candidate':candidate,'invocation':invocation}


def result_gaps(requirements,entry):
    """Validate only an owned gate's source-bound guarded transcript.

    A parsed report by itself has no authority. The caller additionally verifies
    the immutable log hash, owned process completion and gate execution identity.
    """
    if not requirements:return []
    if entry.get('remote_provenance') or entry.get('execution_kind')=='remote':return ['remote guarded pytest attestation unavailable']
    if entry.get('exit')!=0 or not entry.get('pytest_guard_completion',{}).get('complete'):return ['owned guarded pytest execution incomplete']
    context=entry.get('pytest_guard_context')
    if not context:return ['protected pytest launch context missing']
    try:
        raw=Path(entry['log']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry.get('log_sha256'):return ['guarded pytest log changed']
        lines=[line[len('AL_PYTEST_EXECUTION '):] for line in raw.decode().splitlines() if line.startswith('AL_PYTEST_EXECUTION ')]
        if len(lines)!=len(requirements):return ['guarded pytest transcript missing or duplicated']
        from al_runtime_resources import resource_bytes,resource_identity
        expected={name:{'path':resource_identity(name),'sha256':hashlib.sha256(resource_bytes(name)).hexdigest()} for name in RESOURCE_NAMES}
        for requirement,line in zip(requirements,lines):
            report=json.loads(line);q=report['qualification']
            if requirement.get('guard_resources')!=expected or report.get('resources')!=expected:return ['guarded pytest policy/resource version changed']
            if report.get('schema_version')!=2 or report['profile']!=requirement['pytest_profile'] or q['context']!=context:return ['guarded pytest source/profile/context differs']
            if q['policy']!=report['profile']['outcome_policy']:return ['guarded pytest policy differs']
            collection=report['collection'];summary=report['outcomes']
            if q['policy']['version']==PLATFORM:
                gaps=collection_gaps(report.get('collection_proof'),collection,summary.get('deselected',[]))
                if gaps:return gaps
            bindings={'profile':report['profile'],'environment':report['environment'],'inputs':report['inputs'],'resources':expected,'context':context,'platform':q['observed_platform'],'collection':collection,'deselected':summary.get('deselected',[])}
            if 'collection_proof' in report:bindings['collection_proof']=report['collection_proof']
            if q['binding_sha256']!=digest(bindings):return ['guarded pytest inventory binding differs']
            proofs={n['nodeid']:n['predicate_proof'] for n in q['nodes'] if n['status']=='NOT_APPLICABLE'}
            recomputed=qualification(q['policy'],collection,report['reports'],proofs,q['observed_platform'],context,bindings)
            if q!=recomputed or not q['complete'] or summary['errors']:return ['guarded pytest obligations remain unverified']
    except (OSError,ValueError,TypeError,KeyError,UnicodeError):return ['invalid guarded pytest transcript']
    return []
