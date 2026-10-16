"""CI obligations identified by sourced concrete job names and affected inputs."""
import hashlib
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
        self.root=root.resolve();self.paths=paths;self.files={};self.definitions={};self.origins={};self.provenance={};self.visits=0;self.issues=[];self.visiting=[];self.workflow_unknown=False

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
        def append(path,job,status,inputs,reason):
            result.append({'source_path':path,'source_sha256':self.files[path]['sha256'],'job':job,
                           'units':sorted({R.unit(p) for p in inputs}),'applicability':status,
                           'dependencies':dependencies,'reason':reason,'inputs':sorted(inputs)})
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
                append(path,name,'APPLICABLE',inputs,reason)
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
    return {'obligations':reader.obligations(),'dependencies':list(reader.files.values())}


def covered(scope,path):return any(p=='.' or p==path or path.startswith(p.rstrip('/')+'/') for p in scope)


def gaps(ci,gates):
    missing=[]
    for obligation in ci['obligations']:
        label=obligation['source_path']+':'+obligation['job']+' units='+','.join(obligation['units'])
        matches=[g for g in gates if g['category']=='ci' and g.get('source') and g['source'].get('path')==obligation['source_path'] and g['source'].get('sha256')==obligation['source_sha256'] and g['source'].get('job')==obligation['job']]
        valid=False
        for gate in matches:
            if gate['applicability']=='NOT_APPLICABLE':continue
            if obligation['applicability']=='UNRESOLVED' and gate['applicability']!='UNRESOLVED':continue
            if set(obligation['units'])-set(gate['units']):continue
            required=obligation['inputs']+[d['path'] for d in obligation['dependencies']]
            if not all(covered(gate['scope'],p) for p in required):continue
            valid=True;break
        if not valid:missing.append('missing sourced CI job '+label+' ('+obligation['applicability']+')')
    return missing
