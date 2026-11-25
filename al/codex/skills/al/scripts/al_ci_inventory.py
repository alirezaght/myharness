"""CI obligations identified by sourced concrete job names and affected inputs."""
import copy
import hashlib
import shlex
import re
import al_ci_verification as Verification
import al_ci_advisory as Advisory
from al_ci_github import github_inventory
from pathlib import Path
import al_ci_rules as R
from al_ci_yaml import load,Unsupported
import al_ci_expression as E
import al_sources as Sources


def source(root,path,*,capture=None):
    raw=capture.capture_bytes(path,required=True,rule=True) if capture else (root/path).read_bytes()
    return {'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'job':None}


def rule_decision(value,paths,context=None,directories=()):
    decision=R.evaluate_rules(value,paths,context,directories)
    return decision['selected'],set(decision['inputs']),decision['reason_code']


class Inventory:
    MAX_WORLDS=128
    MAX_EXPANSION_WORK=2000000
    MAX_EXPANDED_BYTES=16*1024*1024

    def __init__(self,root,paths,context=None,opaque=(),*,capture=None):
        self.opaque=opaque;self.context=context;self.capture=capture
        self.root=Path(root).resolve();self.paths=paths
        self.directories={p for p in paths if p=='.' or capture and capture.observe_path(p)['state']=='directory' or p.startswith(('apps/','services/','packages/')) and len(Path(p).parts)==2}
        self.files={};self.visits=0;self.include_decisions=0;self.issues=[];self.deferred=[]
        self.worlds=[{'definitions':{},'origins':{},'globals':{},'environment_context':[],'unknown':False}]
        self.definitions={};self.origins={}

    def issue(self,path,reason):
        item=(path,reason)
        if item not in self.issues:self.issues.append(item)

    def bounded(self,worlds):
        from al_repository_facts import digest
        work=0;size=0;pending=list(worlds)
        while pending:
            value=pending.pop();work+=1
            if work>self.MAX_EXPANSION_WORK:raise Unsupported('CI definition expansion-work limit')
            if isinstance(value,str):
                size+=len(value.encode('utf-8'))
                if size>self.MAX_EXPANDED_BYTES:raise Unsupported('CI definition expanded-byte limit')
            elif isinstance(value,dict):pending.extend(value.values());pending.extend(value.keys())
            elif isinstance(value,list):pending.extend(value)
        unique={digest(w):w for w in worlds}
        if len(unique)>self.MAX_WORLDS:raise Unsupported('CI definition-world limit')
        return list(unique.values())

    def child_dependency(self,pattern):
        """Whether a local include pattern can only resolve inside an opaque child root."""
        literal=[]
        for part in Path(pattern).parts:
            if any(c in part for c in '*?['):break
            literal.append(part)
        head=Path(*literal).as_posix() if literal else '.'
        return any(child!='.' and Sources.contains(child,head) for child in self.opaque)

    def read(self,path):
        if any(Sources.contains(child,path) for child in self.opaque):
            raise Unsupported('unavailable child-content dependency in CI include: '+path)
        if '..' in Path(path).parts or Path(path).is_absolute():raise Unsupported('unsafe local include')
        for index in range(1,len(Path(path).parts)+1):
            target=self.root/Path(*Path(path).parts[:index])
            if target.is_symlink():raise Unsupported('symlink local include')
        if self.capture:
            raw=self.capture.capture_bytes(path,required=True,rule=True)
        else:
            try:raw=(self.root/path).read_bytes()
            except OSError as exc:raise Unsupported('missing/unavailable local include '+path) from exc
        self.files[path]={'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'job':None}
        if not raw:raise Unsupported('empty/unavailable local include '+path)
        try:return load(raw.decode('utf-8'))
        except UnicodeError as exc:raise Unsupported('CI source encoding unsupported') from exc

    def collect(self,path):
        self.worlds=self.collect_worlds(path,self.worlds,())
        self.definitions={name:value for w in self.worlds for name,value in w['definitions'].items()}
        self.origins={name:value for w in self.worlds for name,value in w['origins'].items()}

    def collect_worlds(self,path,worlds,trail):
        if path in trail:raise Unsupported('cyclic local include '+path)
        self.visits+=1
        if self.visits>512 or path not in self.files and len(self.files)>=128:
            raise Unsupported('CI include count limit')
        data=self.read(path)
        includes=data.get('include',[])
        includes=[includes] if isinstance(includes,(str,dict)) else includes
        if not isinstance(includes,list):raise Unsupported('unsupported include definition')
        for include in includes:
            self.include_decisions+=1
            if self.include_decisions>512:raise Unsupported('CI include decision limit')
            include={'local':include} if isinstance(include,str) else include
            if not isinstance(include,dict) or set(include)-{'local','rules'} or not isinstance(include.get('local'),str):
                self.issue(path,'dynamic/nonlocal include');continue
            status,_,_=rule_decision(include['rules'],self.paths,self.context,self.directories) if 'rules' in include else (True,set(),None)
            pattern=include['local'].lstrip('/')
            if '..' in Path(pattern).parts or '$' in pattern:
                self.issue(path,'dynamic/unsafe local include');continue
            # A child repository's content is not part of the parent's capture, so an include
            # into it must be reported as that dependency before any membership lookup.
            if status is not False and self.child_dependency(pattern):
                self.issue(path,'unavailable child-content dependency in CI include: '+pattern);continue
            matches=(self.capture.observe_matches(pattern,purpose='include') if self.capture else
                     tuple(q.relative_to(self.root).as_posix() for q in sorted(self.root.glob(pattern))))
            if status is False:continue
            if not matches:
                self.issue(path,'missing local include '+pattern);continue
            absent=copy.deepcopy(worlds) if status is None else []
            included=copy.deepcopy(worlds)
            for matched in matches:
                try:included=self.collect_worlds(matched,included,trail+(path,))
                except Unsupported as exc:
                    self.issue(path,str(exc))
                    for w in included:w['unknown']=True
            worlds=self.bounded(absent+included)
        for world in worlds:
            workflow=data.get('workflow')
            if workflow is not None:
                selected,_,_=rule_decision(workflow.get('rules') if isinstance(workflow,dict) else None,
                                      self.paths,self.context,self.directories)
                if not isinstance(workflow,dict) or set(workflow)-{'rules','auto_cancel'} or selected is not True:
                    world['unknown']=True
            if 'cache' in data:world['environment_context'].append({'path':path,'key':'cache'})
            for key in ('default','image','services','before_script','after_script','variables'):
                if key in data:
                    old=world['globals'].get(key)
                    world['globals'][key]=R.merge(old,data[key]) if isinstance(old,dict) and isinstance(data[key],dict) else copy.deepcopy(data[key])
            for name,value in data.items():
                if name in R.GLOBAL:continue
                if not isinstance(name,str):raise Unsupported('nonliteral CI job name')
                old=world['definitions'].get(name)
                world['definitions'][name]=R.merge(old,value) if isinstance(old,dict) and isinstance(value,dict) else copy.deepcopy(value)
                world['origins'][name]=path
        return self.bounded(worlds)

    def unit_scope(self):return {R.unit(p) for p in self.paths} or {'.'}

    def obligations(self):
        from al_repository_facts import digest
        result=[];dependencies=[self.files[p] for p in sorted(self.files)]
        for name in sorted(self.origins):
            if name.startswith('.'):continue
            variants=[];inputs=set();reasons=[];executions=[];advisories=[];unknown=bool(self.issues)
            for world in self.worlds:
                if name not in world['definitions']:
                    variants.append({'present':False});continue
                path=world['origins'][name]
                try:
                    used=set();job=R.references(R.inherited(name,world['definitions'],used=used),world['definitions'],used=used)
                    semantics=R.structure(job)
                    if 'only' in job or 'except' in job:raise Unsupported('legacy applicability expression unresolved')
                    selected,matched,reason=rule_decision(job['rules'],self.paths,self.context,self.directories) if 'rules' in job else (True,set(self.paths),'verification job without path restrictions')
                    variants.append({'present':True,'path':path,'job':job,'selected':selected,
                                     'origins':[{'name':n,'path':world['origins'][n]} for n in sorted(used)],
                                     'globals':world['globals'],'universe_complete':not world['unknown']})
                    if selected is False:continue
                    inputs.update(matched);reasons.append(reason)
                    if selected is None or world['unknown']:unknown=True
                    if R.release(name,semantics) is not False:
                        unknown=True;reasons.append('CI release/verification identity unresolved');continue
                    if not any(k in job for k in ('script','trigger','run')):raise Unsupported('job execution definition missing')
                    local=copy.deepcopy(job);defaults=world['globals'].get('default',{})
                    if not isinstance(defaults,dict):local['unsupported_default']=True
                    else:
                        for key,value in defaults.items():local.setdefault(key,value)
                    for key,value in world['globals'].items():
                        if key!='default':local.setdefault(key,value)
                    advisories.append(Advisory.gitlab_job(local))
                    executions.append(execution(local,self.root,self.opaque,world['environment_context'],capture=self.capture))
                except Unsupported as exc:
                    unknown=True;reasons.append(str(exc));inputs.update(self.paths)
                    variants.append({'present':True,'path':path,'unsupported':True})
            path=self.origins[name]
            if not unknown and all(not v['present'] or v.get('selected') is False for v in variants):
                self.deferred.append({'source_path':path,'source_sha256':self.files[path]['sha256'],'job':name,
                                      'reason':'all captured definition variants are path-disjoint or excluded',
                                      'classification':'deferred','definition_variants':variants,
                                      'definition_universe':'complete','dependencies':dependencies})
                continue
            present=all(v['present'] for v in variants)
            status='APPLICABLE' if present and not unknown else 'UNRESOLVED'
            signatures={digest(e) for e in executions}
            mapped=copy.deepcopy(executions[0]) if status=='APPLICABLE' and executions and len(signatures)==1 else None
            # A job that never fails its pipeline (allow_failure, or every script line swallowing its
            # failure) is informational; lines that swallow their failure are not part of the check.
            advisory=advisories[0] if advisories and all(advisories) else None
            advisory_steps=None
            if mapped:
                advisory=advisory or mapped.pop('advisory',None);advisory_steps=mapped.pop('advisory_steps',None)
                inputs.update(mapped.get('input_scope',[]))
                mapped['verification_chain'].update(provider='gitlab',workflow=path,job=name,
                    applicability={'profile':self.context,'status':status},source_chain=dependencies)
            extra={**({'advisory':advisory} if advisory else {}),**({'advisory_steps':advisory_steps} if advisory_steps else {})}
            if not mapped:
                recipe=local_recipe(name,variants)
                if recipe:extra['local_recipe']=recipe
            result.append({**extra,'source_path':path,'source_sha256':self.files[path]['sha256'],'job':name,
                           'units':sorted({R.unit(p) for p in inputs} or self.unit_scope()),
                           'applicability':status,'dependencies':dependencies+(mapped.pop('source_dependencies',[]) if mapped else []),
                           'reason':'; '.join(sorted(set(reasons))) or 'conditional definition applicability unresolved',
                           'inputs':sorted(inputs or set(self.paths)),'execution':mapped,
                           'definition_variants':variants,'definition_universe':'incomplete' if self.issues else 'complete',
                           'impact':{'inventory_complete':not self.issues,'classification':'unknown' if self.issues else 'relevant'}})
        for index,(path,reason) in enumerate(self.issues):
            result.append({'source_path':path,'source_sha256':self.files.get(path,{}).get('sha256','0'*64),
                           'job':'<unresolved-definition-'+str(index)+'>','units':sorted(self.unit_scope()),
                           'applicability':'UNRESOLVED','dependencies':dependencies,'reason':reason,
                           'inputs':sorted(self.paths),'execution':None,
                           'impact':{'inventory_complete':False,'classification':'unknown'}})
        return result


REMOTE_TOOLS={'docker','podman','buildah','kubectl','helm','terraform','tofu','pulumi','aws','gcloud','az','ssh','scp',
              'rsync','curl','wget','twine','gh','glab','firebase','vercel','netlify','flyctl','heroku','ansible-playbook',
              'serverless','sls','wrangler','eb','skaffold','argocd'}
REMOTE_WORDS={'publish','deploy','push','release','upload','apply','destroy'}


def side_effect(tokens):
    """A command line whose effect reaches outside the checkout."""
    name=Path(tokens[0]).name
    return name in REMOTE_TOOLS or any(Path(t).stem.lower() in REMOTE_WORDS or t.lower() in REMOTE_WORDS for t in tokens)


def _selected_definitions(variants):
    """Captured definitions that may run for the task, or None when one could not be read."""
    out=[]
    for variant in variants or ():
        if not variant.get('present') or variant.get('selected') is False:continue
        if not isinstance(variant.get('job'),dict):return None
        out.append(variant)
    return out


def release_job(name,variants):
    """True only when every captured definition that may run is a definite release/deploy job."""
    selected=_selected_definitions(variants)
    if not selected:return False
    for variant in selected:
        try:
            if R.release(name,R.structure(variant['job'])) is not True:return False
        except Unsupported:return False
    return True


def local_recipe(name,variants):
    """The job's own `before_script` + `script` lines, verbatim, as one local command.

    Offered for a sourced GitLab job AL could not map (unknown pipeline context, image,
    variables, services, shell syntax): a planner may map the job's gate to exactly this
    command (`ci_recipe`) so the obligation becomes executable. Every definition that may run
    must agree on the lines; release jobs and lines that reach outside the checkout are never
    offered (#17)."""
    selected=_selected_definitions(variants)
    if not selected or release_job(name,variants):return None
    recipes=[]
    for variant in selected:
        job=variant['job'];scope=variant.get('globals') or {}
        default=scope.get('default') if isinstance(scope.get('default'),dict) else {}
        before=job['before_script'] if 'before_script' in job else default.get('before_script',scope.get('before_script',[]))
        lines=[]
        for value in (before,job.get('script')):
            value=[value] if isinstance(value,str) else value
            if not isinstance(value,list) or any(not isinstance(line,str) for line in value):return None
            lines.extend(value)
        if not job.get('script') or not lines:return None
        for line in lines:
            try:tokens=shlex.split(line)
            except ValueError:return None
            if tokens and side_effect(tokens):return None
        recipes.append(lines)
    if any(lines!=recipes[0] for lines in recipes):return None
    return {'argv':['sh','-e','-c','\n'.join(recipes[0])],'cwd':'.','execution_kind':'local','env_names':['PATH'],
            'semantics':'verbatim sourced job script (before_script + script); runner image, variables and services are not reproduced'}


def pipeline_verified(obligation):
    """A concretely enumerated, task-relevant job AL cannot run locally: the pipeline verifies it.

    Unknown inventories, advisory jobs and definite release jobs never qualify."""
    if str(obligation.get('job','')).startswith('<unresolved-') or obligation.get('impact',{}).get('inventory_complete') is False:return False
    if obligation.get('execution') or obligation.get('advisory'):return False
    return not release_job(obligation['job'],obligation.get('definition_variants'))


def inventory(root,paths,context=None,opaque=(),*,capture=None):
    if capture is None:
        from al_repository_facts import Snapshot
        from al_repository_capture import capture_discovery
        return capture_discovery(lambda:Snapshot(root,None,paths,opaque),lambda scan:inventory(root,paths,context,opaque,capture=scan))
    reader=Inventory(root,paths,context,opaque,capture=capture)
    if (capture.observe_path('.gitlab-ci.yml')['state']!='absent' if capture else (root/'.gitlab-ci.yml').is_file()):
        try:reader.collect('.gitlab-ci.yml')
        except Unsupported as exc:reader.issue('.gitlab-ci.yml',str(exc))
    if reader.files and not reader.issues and not any(not name.startswith('.') for name in reader.definitions):reader.issue('.gitlab-ci.yml','CI job graph is empty or only hidden templates')
    try:
        if capture.observe_path('.github/workflows')['state']=='opaque':
            raise Unsupported('unsafe/unavailable GitHub workflow boundary')
        github=github_inventory(root,paths,context,opaque,directories=reader.directories,capture=capture)
    except Unsupported as exc:
        # Too many workflows, or workflows edited while AL read them: the pipeline verifies them.
        github={'obligations':[{'source_path':'.github/workflows','source_sha256':'0'*64,'job':'<unresolved-workflows>',
                                'units':sorted(reader.unit_scope()),'applicability':'UNRESOLVED','dependencies':[],'reason':str(exc),
                                'inputs':sorted(paths),'execution':None,'impact':{'inventory_complete':False,'classification':'unknown'}}],'dependencies':[]}
    # Retain the trigger semantics needed to independently reconstruct exclusions.
    for item in github.get('deferred',[])+github.get('obligations',[]):
        if item.get('source_sha256')=='0'*64:continue
        path=item['source_path']
        raw=capture.capture_bytes(path,required=True,rule=True) if capture else (root/path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['source_sha256']:
            from al_repository_capture import CaptureDrift
            raise CaptureDrift('delegated-source-conflict',[path])
        definition=load(raw.decode('utf-8'))
        if 'job' in item:item['workflow_definition']=definition
        else:item['trigger_definition']=definition.get('on')
    obligations=reader.obligations()+github['obligations']
    from al_go_checks import container_observation
    for obligation in obligations:
        definitions=[v['job'] for v in obligation.get('definition_variants',[]) if isinstance(v.get('job'),dict)]
        workflow=obligation.get('workflow_definition',{})
        if isinstance(workflow,dict) and isinstance(workflow.get('jobs',{}),dict):
            definition=workflow.get('jobs',{}).get(obligation['job'])
            if isinstance(definition,dict):definitions.append(definition)
        observations=[]
        for definition in definitions:
            try:
                def container_read(path):
                    if capture:return capture.capture_bytes(path,required=True,rule=True)
                    from al_ci_github import read
                    return read(root,path)[0]
                observed=container_observation(definition,root,container_read,observe=capture.observe_path if capture else None)
                if observed and observed not in observations:observations.append(observed)
            except __import__('al_repository_capture').CaptureDrift:raise
            except (Unsupported,OSError,ValueError):continue
        if observations:
            obligation['verification_observations']=observations
            for observed in observations:
                obligation['dependencies'].extend(r for r in observed['sources'] if r not in obligation['dependencies'])
    dependencies={d['path']:d for d in [*reader.files.values(),*github['dependencies'],*(d for o in obligations for d in o['dependencies'])]}
    value={'obligations':obligations,'deferred':reader.deferred+github.get('deferred',[]),'dependencies':[dependencies[p] for p in sorted(dependencies)]}
    if capture:
        def adopt(item,field=None):
            if field=='guard_resources':
                from al_pytest_policy import RESOURCE_NAMES
                from al_runtime_resources import resource_identity,resource_bytes
                expected={name:{'path':resource_identity(name),'sha256':hashlib.sha256(resource_bytes(name)).hexdigest()} for name in RESOURCE_NAMES}
                if item!=expected:raise ValueError('CI guarded pytest runtime resource binding differs')
                return
            if isinstance(item,dict):
                if isinstance(item.get('path'),str) and isinstance(item.get('sha256'),str):capture.adopt_source(item,purpose='ci-reader')
                for key,child in item.items():adopt(child,key)
            elif isinstance(item,list):
                for child in item:adopt(child)
        adopt(value)
    return value


def recipe_mapped(obligation,gate):
    """A required local gate that runs exactly the job's independently reconstructed verbatim script."""
    recipe=obligation.get('local_recipe')
    if not recipe or obligation.get('execution') or gate.get('applicability')!='APPLICABLE' or gate.get('required') is not True:return False
    if any(key in gate for key in ('remote','remote_source','advisory','package_script','obligation_ids')):return False
    return (gate.get('ci_recipe')==recipe and gate.get('argv')==recipe['argv'] and gate.get('cwd')==recipe['cwd']
            and gate.get('execution_kind')==recipe['execution_kind'] and set(recipe['env_names'])<=set(gate.get('env_names',[])))


def covered(scope,path):return any(p=='.' or p==path or path.startswith(p.rstrip('/')+'/') for p in scope)


def gaps(ci,gates,*,projection=None):
    missing=[]
    for obligation in ci['obligations']:
        if projection is not None:
            from al_coverage_relevance import ci_record_id
            if ci_record_id(obligation) not in projection['relevant_ci_ids']:continue
        label=obligation['source_path']+':'+obligation['job']+' units='+','.join(obligation['units'])
        matches=[g for g in gates if g['category']=='ci' and g.get('source') and g['source'].get('path')==obligation['source_path'] and g['source'].get('sha256')==obligation['source_sha256'] and g['source'].get('job')==obligation['job']]
        valid=False
        for gate in matches:
            # An advisory job is satisfied by its informational gate; any other job needs a required one.
            informational=obligation.get('advisory') and gate.get('required') is False and gate.get('advisory')==obligation['advisory']
            if gate['applicability']=='NOT_APPLICABLE' or not (gate.get('required') is True or informational):continue
            execution=obligation.get('execution')
            # A job AL could not map may be planned as its own verbatim script (#17).
            if gate['applicability']=='APPLICABLE' and not execution and recipe_mapped(obligation,gate):pass
            elif gate['applicability']=='APPLICABLE' and (not execution or gate.get('argv')!=execution['argv'] or gate.get('cwd')!='.' or gate.get('execution_kind')!=execution['execution_kind'] or gate.get('remote_source')!=execution.get('remote_source') or not set(execution['env_names'])<=set(gate.get('env_names',[]))):continue
            if obligation['applicability']=='UNRESOLVED' and gate['applicability']!='UNRESOLVED' and not recipe_mapped(obligation,gate):continue
            if set(obligation['units'])-set(gate['units']):continue
            required=obligation['inputs']+[d['path'] for d in obligation['dependencies']]
            from al_repository_facts import PathScope
            scope=PathScope(gate['scope'])
            if not all(scope.covers(p) for p in required):continue
            valid=True;break
        if not valid:missing.append('missing sourced CI job '+label+' ('+obligation['applicability']+')')
    return missing


def execution(job,root,opaque=(),environment_context=(),*,capture=None):
    """Only literal local shell-job semantics; unsupported CI environments stay open."""
    if isinstance(job.get('environment'),dict) and job['environment'].get('action') in {'prepare','access'}:
        from al_ci_environment import execution as isolated
        return isolated(job,root,opaque,environment_context,capture=capture)
    if set(job)-{'script','before_script','stage','rules','allow_failure','interruptible','retry','timeout','tags','needs'}:return None
    if job.get('needs') or any(isinstance(rule,dict) and ('variables' in rule or rule.get('when') in {'manual','delayed'}) for rule in job.get('rules',[])):return None
    lines=[]
    for key in ('before_script','script'):
        value=job.get(key,[]);value=[value] if isinstance(value,str) else value
        if not isinstance(value,list) or any(not isinstance(line,str) for line in value):return None
        lines.extend(value)
    if not lines:return None
    text='\n'.join(lines)
    from al_go_checks import command_candidate
    if command_candidate(lines):
        from al_go_checks import execution as go_execution
        def go_read(path):
            if any(path==o or path.startswith(o+'/') for o in opaque):raise Unsupported('opaque Go recipe input')
            if capture:return capture.capture_bytes(path,required=True,rule=True)
            from al_ci_github import read
            return read(root,path)[0]
        return go_execution(lines,root,go_read,observe=capture.observe_path if capture else None)
    # A line whose failure is swallowed (`cmd || true`) never fails the job: it is recorded as
    # advisory and left out of the check. When every line is advisory the job is advisory and
    # its commands run as an informational check.
    lines,swallowed=Advisory.partition_lines(lines)
    all_swallowed=not lines and bool(swallowed)
    if all_swallowed:lines=[item['command'] for item in swallowed]
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
        tokens=shlex.split(line)
        candidates=tokens[1:]+([tokens[0]] if '/' in tokens[0] else [])
        for token in candidates:
            if token.startswith('-') or not token or token.startswith('/'):continue
            candidate=root/token
            if candidate.is_file():
                if '..' in Path(token).parts or candidate.is_symlink() or root not in candidate.resolve().parents:return None
                normalized=Path(token).as_posix()
                dependencies[normalized]=source(root,normalized,capture=capture)
    # A job that publishes, deploys, pushes or talks to remote infrastructure is left to the pipeline,
    # so a local run never has outside effects; checks and the repository's own scripts run locally.
    if any(side_effect(tokens) for tokens in parsed):return None
    checks=[check for line in lines for check in Verification.observed(shlex.split(line))]
    requirements=[{'selector':'script['+str(i)+']','purpose':Verification.purpose(tokens,Verification.observed(tokens)),'argv':tokens,'cwd':'.','env_names':['PATH'],'targets':Verification.observed(tokens),'dependencies':sorted(dependencies)} for i,tokens in enumerate(parsed)]
    chain={'schema_version':1,'provider':'gitlab','workflow':None,'job':None,'applicability':None,'setup':[],'requirements':requirements,'source_chain':[],'complete':True}
    advisory={'advisory':'every script line swallows its failure (`|| <benign>`)'} if all_swallowed else {'advisory_steps':[item['line'] for item in swallowed]} if swallowed else {}
    return {**advisory,'verification':checks,'verification_chain':chain,'source_dependencies':[dependencies[p] for p in sorted(dependencies)],'argv':['sh','-eu','-c',text],'cwd':'.','execution_kind':'local','env_names':['PATH']}
