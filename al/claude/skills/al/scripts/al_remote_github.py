"""Closed, bounded, read-only GitHub push-job evidence; no provider logs are retained."""
import hashlib
import json
import re
import subprocess
import os
import time
from pathlib import Path
import al_scope as S
from al_gate_process import stop

LIMIT=2*1024*1024
PAGES=10


def canonical_origin(raw):
    match=re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?',raw.strip())
    if not match or any(part in {'.','..'} for part in match[1].split('/')):raise ValueError('unsupported GitHub origin')
    return match[1]


def source_contract(root,path,name,job,chain):
    try:origin=S.git_bytes('remote','get-url','origin',root=root).decode()
    except (SystemExit,UnicodeError):raise ValueError('GitHub origin unavailable') from None
    repository=canonical_origin(origin)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*',name):raise ValueError('unsupported GitHub job key')
    events=chain['applicability']['events']
    if 'push' not in events or (events['push'] or {}).get('tags'):raise ValueError('remote evidence requires a push workflow')
    steps=[{'number':i+2,'name':'Run '+step.get('uses',step.get('run',''))} for i,step in enumerate(job['steps'])]
    return {'provider':'github','semantics_version':2,'evidence_kind':'CI_JOB_ATTESTATION','pytest_guard_status':'UNAVAILABLE','repository':repository,'workflow_path':path,'job_key':name,'job_name':name,
            'runner_label':job['runs-on'],'steps':steps,'verification_chain':chain,
            'source_sha256':hashlib.sha256(json.dumps(chain,sort_keys=True,separators=(',',':')).encode()).hexdigest()}


def selection(gate):
    source=gate.get('remote_source');remote=gate.get('remote')
    if not isinstance(source,dict) or source.get('provider')!='github':raise ValueError('missing GitHub source contract')
    if source.get('semantics_version')!=2 or source.get('evidence_kind')!='CI_JOB_ATTESTATION' or source.get('pytest_guard_status')!='UNAVAILABLE':raise ValueError('unsupported GitHub evidence semantics')
    if not isinstance(remote,dict) or set(remote)!={'provider','repository_id','run_id','attempt','job_id'} or remote.get('provider')!='github':raise ValueError('typed GitHub selection required')
    if any(type(remote[k]) is not int or remote[k]<=0 for k in ('repository_id','run_id','attempt','job_id')):raise ValueError('positive GitHub identity required')
    if gate.get('argv')!=['gh','api'] or gate.get('source',{}).get('job')!=source['job_key'] or gate['source']['path']!=source['workflow_path']:raise ValueError('GitHub source/selection mismatch')
    if source['runner_label'] not in {'ubuntu-latest','macos-latest'} or source['job_name']!=source['job_key']:raise ValueError('unsupported GitHub runner/name')
    return source,remote


def success(data):
    return data.get('status')=='completed' and data.get('conclusion')=='success'


def repository(data,source,remote):
    if not isinstance(data,dict) or type(data.get('id')) is not int or data.get('id')!=remote['repository_id'] or data.get('full_name')!=source['repository']:raise ValueError('repository mismatch')
    owner,name=source['repository'].split('/')
    if data.get('name')!=name or not isinstance(data.get('owner'),dict) or data['owner'].get('login')!=owner:raise ValueError('repository owner mismatch')


def run_identity(data,source,remote,revision,workflow_id=None):
    if any(type(data.get(k)) is not int for k in ('id','run_attempt')):raise ValueError('invalid run IDs')
    repository(data.get('repository'),source,remote);repository(data.get('head_repository'),source,remote)
    if data.get('id')!=remote['run_id'] or data.get('run_attempt')!=remote['attempt'] or data.get('head_sha')!=revision or data.get('event')!='push' or data.get('path')!=source['workflow_path'] or not success(data):raise ValueError('run identity/status mismatch')
    identifier=data.get('workflow_id')
    if type(identifier) is not int or identifier<=0 or workflow_id is not None and workflow_id!=identifier:raise ValueError('workflow mismatch')
    return identifier


def job_identity(data,source,remote,revision):
    if not isinstance(data,dict) or data.get('id')!=remote['job_id'] or data.get('run_id')!=remote['run_id'] or data.get('run_attempt')!=remote['attempt'] or data.get('head_sha')!=revision or data.get('name')!=source['job_name'] or not success(data):raise ValueError('job identity/status mismatch')
    base='https://api.github.com/repos/'+source['repository']
    if data.get('url')!=base+'/actions/jobs/'+str(remote['job_id']) or data.get('run_url')!=base+'/actions/runs/'+str(remote['run_id']):raise ValueError('job repository mismatch')
    if data.get('labels')!=[source['runner_label']]:raise ValueError('runner mismatch')
    steps=data.get('steps')
    if any(type(data.get(k)) is not int for k in ('id','run_id','run_attempt')):raise ValueError('invalid job IDs')
    if not isinstance(steps,list) or len(steps)>256:raise ValueError('invalid steps')
    numbers=[s.get('number') for s in steps if isinstance(s,dict)]
    if len(numbers)!=len(steps) or any(type(n) is not int for n in numbers) or numbers!=sorted(set(numbers)):raise ValueError('invalid ordered steps')
    # GitHub adds setup/post/complete steps; only these known successful wrappers
    # may accompany the exact source-derived sequence.
    expected=source['steps'];sourced=[s for s in steps if s.get('number') in {e['number'] for e in expected}]
    if [{'number':s['number'],'name':s.get('name')} for s in sourced]!=expected or any(not success(s) for s in sourced):raise ValueError('required step mismatch')
    extras=[s for s in steps if s not in sourced]
    if not extras or extras[0].get('number')!=1 or extras[0].get('name')!='Set up job':raise ValueError('missing job setup')
    allowed={'Set up job','Post Run actions/checkout@v4','Post Run astral-sh/setup-uv@v6','Complete job'}
    if any(s.get('name') not in allowed or not success(s) for s in extras):raise ValueError('unsupported provider step')
    return {'id':data['id'],'run_id':data['run_id'],'run_attempt':data['run_attempt'],'head_sha':data['head_sha'],'name':data['name'],'labels':data['labels'],'steps':[{k:s[k] for k in ('number','name','status','conclusion')} for s in steps]}


def snapshot(source,revision,candidate=True):
    if canonical_origin(S.git_bytes('remote','get-url','origin').decode())!=source['repository']:raise ValueError('origin changed')
    if S.git_bytes('diff','HEAD','--') or S.git_bytes('ls-files','--others','--exclude-standard'):raise ValueError('uncommitted candidate inputs')
    if candidate and S.git_bytes('rev-parse','HEAD').decode().strip()!=revision:raise ValueError('candidate commit changed')
    chain=source['verification_chain']
    directory=Path(S.al_store.REPO_ROOT)/'.github/workflows'
    membership=sorted(p.relative_to(S.al_store.REPO_ROOT).as_posix() for p in directory.glob('*') if p.suffix in {'.yml','.yaml'})
    if membership!=chain['workflow_membership']:raise ValueError('workflow membership changed')
    digest=hashlib.sha256(json.dumps(chain,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if digest!=source['source_sha256']:raise ValueError('source contract changed')
    bound={ref['path'] for ref in chain['source_chain']}
    from al_ci_github import read
    import al_pytest_profile as Pytest
    import al_ci_verification as Verification
    for name in ('.python-version','.ruff.toml','ruff.toml','conftest.py',*Pytest.CONFIGS):
        target=Path(S.al_store.REPO_ROOT)/name
        if (target.exists() or target.is_symlink()) and name not in bound:raise ValueError('unbound effective configuration')
    for requirement in chain['requirements']:
        if 'pytest_profile' in requirement:
            observed=Pytest.profile(S.al_store.REPO_ROOT,Verification.pytest_arguments(requirement['argv']),lambda name:read(S.al_store.REPO_ROOT,name)[0])
            if observed!=requirement['pytest_profile']:raise ValueError('pytest source membership changed')
    for ref in chain['source_chain']:
        try:raw=S.git_bytes('show',revision+':'+ref['path'])
        except SystemExit:raise ValueError('committed source unavailable') from None
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('committed source mismatch')
        path=Path(S.al_store.REPO_ROOT)/ref['path']
        if any(p.is_symlink() for p in [path,*path.parents]) or hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('working source mismatch')
    return S.git_bytes('rev-parse','HEAD').decode().strip(),revision,digest


def execute(st,gate,stage,log,timeout,sid,entry,owner):
    # A launch failure can cancel before get() receives its process handle.
    owner.drain_cancelled_output=False
    hashes=[];deadline=time.monotonic()+min(timeout,120)
    log.write_text('Read-only GitHub quality evidence (provider output redacted).\n')
    try:
        if stage not in {'candidate','base'}:raise ValueError('unknown evidence stage')
        source,remote=selection(gate)
        revision=S.git_bytes('rev-parse','HEAD').decode().strip() if stage=='candidate' else st['identity']['merge_base']
        before=snapshot(source,revision,stage=='candidate')
        prefix='repos/'+source['repository']+'/actions/'
        def get(endpoint):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError('lookup deadline exceeded')
            from al_package_output import Capture
            with open(os.devnull,'wb') as errors:
                proc=None
                try:
                    proc=owner.spawn(['gh','api','--hostname','github.com','--method','GET',endpoint],stdout=subprocess.PIPE,stderr=errors)
                    entry['pid']=proc.pid
                    import al_evidence as E
                    E._commit(sid,entry)
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise ValueError('lookup deadline exceeded')
                    code,raw,capture=Capture().run(proc,remaining)
                finally:
                    proc=proc or owner.proc
                    if proc is not None:
                        try:stop(proc)
                        finally:
                            if proc.stdout is not None:
                                proc.stdout.close();proc.stdout=None
                if code or not capture['complete'] or not owner.completion().get('complete'):raise ValueError('lookup/cleanup unavailable')
            hashes.append({'endpoint':endpoint,'sha256':hashlib.sha256(raw).hexdigest()})
            def unique(pairs):
                result={}
                for key,value in pairs:
                    if key in result:raise ValueError('duplicate JSON key')
                    result[key]=value
                return result
            def invalid_constant(value):raise ValueError('nonfinite JSON number')
            data=json.loads(raw.decode(),object_pairs_hook=unique,parse_constant=invalid_constant)
            if not isinstance(data,dict):raise ValueError('malformed response')
            return data
        run='runs/'+str(remote['run_id']);attempt=run+'/attempts/'+str(remote['attempt'])
        bound_repo=get('repos/'+source['repository'])
        repository(bound_repo,source,remote)
        initial=get(prefix+run);workflow=run_identity(initial,source,remote,revision)
        run_identity(get(prefix+attempt),source,remote,revision,workflow)
        jobs=[];total=None
        for page in range(1,PAGES+1):
            listing=get(prefix+attempt+'/jobs?per_page=100&page='+str(page))
            count=listing.get('total_count');batch=listing.get('jobs')
            if type(count) is not int or not 0<count<=100*PAGES or total is not None and total!=count or not isinstance(batch,list) or len(batch)>100:raise ValueError('invalid job listing')
            total=count;jobs.extend(batch)
            if len(jobs)>=total:break
            if len(batch)!=100:raise ValueError('truncated job listing')
        if len(jobs)!=total or any(not isinstance(j,dict) or type(j.get('id')) is not int or j['id']<=0 or not isinstance(j.get('name'),str) or not j['name'] for j in jobs) or len({j['id'] for j in jobs})!=len(jobs):raise ValueError('incomplete/duplicate job listing')
        if len({j['name'] for j in jobs})!=len(jobs):raise ValueError('duplicate display name')
        chosen=[j for j in jobs if j.get('name')==source['job_name']]
        if len(chosen)!=1:raise ValueError('missing/ambiguous job')
        listed=job_identity(chosen[0],source,remote,revision)
        direct=job_identity(get(prefix+'jobs/'+str(remote['job_id'])),source,remote,revision)
        if direct!=listed:raise ValueError('job listing/direct mismatch')
        run_identity(get(prefix+run),source,remote,revision,workflow)
        repository(get('repos/'+source['repository']),source,remote)
        if snapshot(source,revision,stage=='candidate')!=before:raise ValueError('source changed during lookup')
        proof={'provider':'github','repository':source['repository'],'repository_id':remote['repository_id'],'workflow_id':workflow,'workflow_path':source['workflow_path'],'commit':revision,'run_id':remote['run_id'],'attempt':remote['attempt'],'job_id':remote['job_id'],'job':direct,'source_sha256':source['source_sha256'],'responses':hashes}
        proof.update(semantics_version=2,kind='CI_JOB_ATTESTATION',status='PASS',pytest_guard_status='UNAVAILABLE')
        provenance={'provider':'github','semantics_version':2,'ci_job_attestation':proof,'pytest_guard_status':'UNAVAILABLE','reason':'Successful CI job has no authenticated guarded pytest qualification'}
        log.write_text(json.dumps(provenance,sort_keys=True)+'\n')
        return 'INFRA',126,provenance
    except (ValueError,KeyError,TypeError,UnicodeError,OSError,RecursionError,subprocess.SubprocessError):
        # No exception text, stderr, remote messages, tokens or raw bodies.
        return 'INFRA',126,{'provider':'github','reason':'GitHub evidence unavailable or provenance mismatch','responses':hashes}


def result_gaps(gate,evidence):
    """Provider-only evidence, including historical PASS, cannot qualify pytest."""
    github=(gate.get('remote_source',{}).get('provider')=='github' or
            gate.get('remote',{}).get('provider')=='github' or
            (evidence or {}).get('remote_provenance',{}).get('provider')=='github')
    if gate.get('execution_kind')=='remote' and github:
        # No supplemental proof transport exists. Self-asserted guard fields,
        # changed status labels and v1 receipts cannot substitute for one.
        return ['GitHub CI job attestation lacks authenticated guarded pytest qualification']
    return []
