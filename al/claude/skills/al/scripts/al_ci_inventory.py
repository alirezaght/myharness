"""CI obligations identified by sourced concrete job names and affected inputs."""
import hashlib
import shlex
import re
import al_ci_verification as Verification
from al_ci_github import github_inventory
from pathlib import Path
import al_ci_rules as R
from al_ci_yaml import load,Unsupported
import al_ci_expression as E
import al_sources as Sources


def source(root,path):return {'path':path,'sha256':hashlib.sha256((root/path).read_bytes()).hexdigest(),'job':None}


class Inventory:
    def __init__(self,root,paths,context=None,opaque=()):
        self.opaque=opaque
        self.context=context
        self.directories={p for p in paths if p=='.' or (root/p).is_dir() or p.startswith(('apps/','services/','packages/')) and len(Path(p).parts)==2}
        self.root=root.resolve();self.paths=paths;self.files={};self.globals={};self.definitions={};self.origins={};self.provenance={};self.visits=0;self.issues=[];self.visiting=[];self.workflow_unknown=False;self.environment_context=[]

    def issue(self,path,reason):self.issues.append((path,reason))

    def collect(self,path,uncertain=False):
        if any(Sources.contains(child,path) for child in self.opaque):raise Unsupported('unavailable child-content dependency in CI include: '+path)
        target=(self.root/path).resolve()
        if self.root not in target.parents or not target.is_file():raise Unsupported('missing/unsafe local include '+path)
        if path in self.visiting:raise Unsupported('cyclic local include '+path)
        self.visits+=1
        if self.visits>512 or path not in self.files and len(self.files)>=128:raise Unsupported('CI include count limit')
        self.visiting.append(path);self.files[path]=source(self.root,path)
        try:data=load(target.read_text())
        except (Unsupported,UnicodeError) as exc:self.issue(path,str(exc));data={}
        workflow=data.get('workflow')
        if workflow is not None:
            status,_,reason=R.rules(workflow.get('rules') if isinstance(workflow,dict) else None,self.paths,self.context,self.directories)
            if not isinstance(workflow,dict) or set(workflow)-{'rules','auto_cancel'} or 'auto_cancel' in workflow and not E.scheduling(workflow['auto_cancel']):status=None
            if status is False:self.issue(path,'declared CI workflow excludes this planning scenario')
            if status is not True:uncertain=True;self.workflow_unknown=True
        includes=data.get('include',[]);includes=[includes] if isinstance(includes,(str,dict)) else includes
        if not isinstance(includes,list):self.issue(path,'unsupported include definition');includes=[]
        for include in includes:
            include={'local':include} if isinstance(include,str) else include
            if not isinstance(include,dict) or set(include)-{'local','rules'} or not isinstance(include.get('local'),str):self.issue(path,'dynamic/nonlocal include');continue
            status,_,_=R.rules(include['rules'],self.paths,self.context,self.directories) if 'rules' in include else (True,set(),None)
            if status is False:continue
            pattern=include['local'].lstrip('/')
            if '..' in Path(pattern).parts or '$' in pattern:self.issue(path,'dynamic/unsafe local include');continue
            matches=sorted(self.root.glob(pattern))
            if not matches:self.issue(path,'missing local include '+pattern)
            for matched in matches:
                try:self.collect(matched.relative_to(self.root).as_posix(),uncertain or status is None)
                except Unsupported as exc:self.issue(path,str(exc))
        if 'cache' in data:self.environment_context.append({'path':path,'key':'cache'})
        for key in ('default','image','services','before_script','after_script','variables'):
            if key in data:self.globals[key]=R.merge(self.globals.get(key,{}),data[key]) if isinstance(data[key],dict) and isinstance(self.globals.get(key,{}),dict) else data[key]
        for name,value in data.items():
            if name in R.GLOBAL:continue
            if not isinstance(name,str):self.issue(path,'nonliteral CI job name');continue
            provenance=self.provenance.setdefault(name,{'sources':set(),'uncertain':False})
            provenance['sources'].update(self.visiting);provenance['uncertain']|=uncertain
            previous=self.definitions.get(name,{})
            self.definitions[name]=R.merge(previous,value) if isinstance(previous,dict) and isinstance(value,dict) else value
            self.origins[name]=path
        self.visiting.pop()

    def obligations(self):
        result=[];dependencies=list(self.files.values())
        def append(path,job,status,inputs,reason,execution=None):
            if execution:
                inputs=set(inputs)|set(execution.get('input_scope',[]))
                execution['verification_chain'].update(provider='gitlab',workflow=path,job=job,applicability={'profile':self.context,'status':status},source_chain=dependencies)
            result.append({'source_path':path,'source_sha256':self.files[path]['sha256'],'job':job,
                           'units':sorted({R.unit(p) for p in inputs}),'applicability':status,
                           'dependencies':dependencies+(execution.pop('source_dependencies',[]) if execution else []),'reason':reason,'inputs':sorted(inputs),'execution':execution})
        for name,path in self.origins.items():
            if name.startswith('.'):continue
            try:
                used=set();job=R.references(R.inherited(name,self.definitions,used=used),self.definitions,used=used)
                uncertain=[self.provenance[n] for n in used if self.provenance[n]['uncertain']]
                if uncertain:
                    contributors=sorted({p for item in uncertain for p in item['sources']})
                    append(path,name,'UNRESOLVED',set(self.paths),'conditional CI definition/inheritance provenance unresolved: '+','.join(contributors));continue
                if self.workflow_unknown:raise Unsupported('include/workflow pipeline context unresolved')
                semantics=R.structure(job)
                if not any(k in job for k in ('script','trigger','run')):raise Unsupported('job execution definition missing')
                if 'only' in job or 'except' in job:raise Unsupported('legacy applicability expression unresolved')
                selected,inputs,reason=R.rules(job['rules'],self.paths,self.context,self.directories) if 'rules' in job else (True,set(self.paths),'verification job without path restrictions')
                if selected is False:continue
                if selected is None:
                    append(path,name,'UNRESOLVED',inputs,reason);continue
                release=R.release(name,semantics)
                if release is None:raise Unsupported('CI release/verification identity unresolved')
                if release:continue
                local=dict(job)
                defaults=self.globals.get('default',{})
                if not isinstance(defaults,dict):local['unsupported_default']=True
                else:
                    for key,value in defaults.items():local.setdefault(key,value)
                for key,value in self.globals.items():
                    if key!='default':local.setdefault(key,value)
                append(path,name,'APPLICABLE',inputs,reason,execution(local,self.root,self.opaque,self.environment_context))
            except Unsupported as exc:append(path,name,'UNRESOLVED',set(self.paths),str(exc))
        for index,(path,reason) in enumerate(self.issues):append(path,'<unresolved-definition-'+str(index)+'>','UNRESOLVED',set(self.paths),reason)
        return result


def inventory(root,paths,context=None,opaque=()):
    reader=Inventory(root,paths,context,opaque)
    if (root/'.gitlab-ci.yml').is_file():
        try:reader.collect('.gitlab-ci.yml')
        except Unsupported as exc:
            if '.gitlab-ci.yml' in reader.files:reader.issue('.gitlab-ci.yml',str(exc))
    if reader.files and not reader.issues and not any(not name.startswith('.') for name in reader.definitions):reader.issue('.gitlab-ci.yml','CI job graph is empty or only hidden templates')
    github=github_inventory(root,paths,context,opaque)
    obligations=reader.obligations()+github['obligations']
    dependencies={d['path']:d for d in [*reader.files.values(),*github['dependencies'],*(d for o in obligations for d in o['dependencies'])]}
    return {'obligations':obligations,'dependencies':[dependencies[p] for p in sorted(dependencies)]}


def covered(scope,path):return any(p=='.' or p==path or path.startswith(p.rstrip('/')+'/') for p in scope)


def gaps(ci,gates):
    missing=[]
    for obligation in ci['obligations']:
        label=obligation['source_path']+':'+obligation['job']+' units='+','.join(obligation['units'])
        matches=[g for g in gates if g['category']=='ci' and g.get('source') and g['source'].get('path')==obligation['source_path'] and g['source'].get('sha256')==obligation['source_sha256'] and g['source'].get('job')==obligation['job']]
        valid=False
        for gate in matches:
            if gate['applicability']=='NOT_APPLICABLE' or gate.get('required') is not True:continue
            execution=obligation.get('execution')
            if gate['applicability']=='APPLICABLE' and (not execution or gate.get('argv')!=execution['argv'] or gate.get('cwd')!='.' or gate.get('execution_kind')!='local' or not set(execution['env_names'])<=set(gate.get('env_names',[]))):continue
            if obligation['applicability']=='UNRESOLVED' and gate['applicability']!='UNRESOLVED':continue
            if set(obligation['units'])-set(gate['units']):continue
            required=obligation['inputs']+[d['path'] for d in obligation['dependencies']]
            if not all(covered(gate['scope'],p) for p in required):continue
            valid=True;break
        if not valid:missing.append('missing sourced CI job '+label+' ('+obligation['applicability']+')')
    return missing


def execution(job,root,opaque=(),environment_context=()):
    """Only literal local shell-job semantics; unsupported CI environments stay open."""
    if isinstance(job.get('environment'),dict) and job['environment'].get('action') in {'prepare','access'}:
        from al_ci_environment import execution as isolated
        return isolated(job,root,opaque,environment_context)
    if set(job)-{'script','before_script','stage','rules','allow_failure','interruptible','retry','timeout','tags','needs'}:return None
    if job.get('needs') or any(isinstance(rule,dict) and ('variables' in rule or rule.get('when') in {'manual','delayed'}) for rule in job.get('rules',[])):return None
    lines=[]
    for key in ('before_script','script'):
        value=job.get(key,[]);value=[value] if isinstance(value,str) else value
        if not isinstance(value,list) or any(not isinstance(line,str) for line in value):return None
        lines.extend(value)
    if not lines:return None
    text='\n'.join(lines)
    # The reviewed local mapping permits literal commands, not expansion/eval or
    # shell control flow whose CI execution context is unavailable locally.
    if any(token in text for token in ('$','`',';','&&','||','|','>','<','\\')):return None
    try:
        parsed=[shlex.split(line) for line in lines]
        if any(not tokens for tokens in parsed):return None
        control={'if','then','else','elif','fi','for','while','until','do','done','case','esac','function','select','in','!','{','}','(',')','exec','exit','return','break','continue','trap','set','eval','source','.','cd','export','unset','alias','unalias','read'}
        if any(tokens[0] in control or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=',tokens[0]) for tokens in parsed):return None
    except ValueError:return None
    dependencies={}
    for line in lines:
        for token in shlex.split(line)[1:]:
            if token.startswith('-') or not token or token.startswith('/'):continue
            candidate=root/token
            if candidate.is_file():
                if '..' in Path(token).parts or candidate.is_symlink() or root not in candidate.resolve().parents:return None
                dependencies[token]=source(root,token)
    checks=[check for line in lines for check in Verification.observed(shlex.split(line))]
    requirements=[{'selector':'script['+str(i)+']','purpose':Verification.purpose(tokens,Verification.observed(tokens)),'argv':tokens,'cwd':'.','env_names':['PATH'],'targets':Verification.observed(tokens),'dependencies':sorted(dependencies)} for i,tokens in enumerate(parsed)]
    chain={'schema_version':1,'provider':'gitlab','workflow':None,'job':None,'applicability':None,'setup':[],'requirements':requirements,'source_chain':[],'complete':True}
    return {'verification':checks,'verification_chain':chain,'source_dependencies':[dependencies[p] for p in sorted(dependencies)],'argv':['sh','-eu','-c',text],'cwd':'.','execution_kind':'local','env_names':['PATH']}
