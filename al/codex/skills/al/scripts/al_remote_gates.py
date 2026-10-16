"""Remote CI evidence is an actual job lookup for the exact candidate commit."""
import json
import subprocess
import al_scope as S
from al_gate_process import stop


def response(raw):
    data=json.loads(raw.decode('utf-8'))
    if not isinstance(data,dict):raise ValueError('remote job response must be an object')
    for key in ('pipeline','commit'):
        if not isinstance(data.get(key),dict):raise ValueError('remote '+key+' must be an object')
    for label,value in (('job ID',data.get('id')),('pipeline ID',data['pipeline'].get('id'))):
        if type(value) is not int or value<=0:raise ValueError('remote '+label+' must be a positive integer')
    for label,value in (('job name',data.get('name')),('commit ID',data['commit'].get('id')),('status',data.get('status'))):
        if not isinstance(value,str) or not value.strip():raise ValueError('remote '+label+' must be a nonempty string')
    project=data['pipeline'].get('project_id')
    if project is not None and not (type(project) is int and project>0 or isinstance(project,str) and project.strip()):raise ValueError('remote project ID has unsupported type')
    if data.get('web_url') is not None and not isinstance(data['web_url'],str):raise ValueError('remote job URL must be a string or null')
    return data


def execute_remote(st,gate,stage,log,timeout,sid,entry,owner):
    remote=gate['remote']
    if stage=='candidate':
        dirty=S.git_bytes('diff','HEAD','--') or S.git_bytes('ls-files','--others','--exclude-standard')
        if dirty:
            log.write_text('Candidate has uncommitted inputs; a remote job cannot attest this candidate.\n')
            return 'INFRA',126,{'provider':'gitlab','reason':'uncommitted candidate'}
        revision=S.git_bytes('rev-parse','HEAD').decode().strip()
    else:revision=st['identity']['merge_base']
    log.write_text('argv: '+json.dumps(gate['argv'])+'\nrequired_revision: '+revision+'\n')
    try:
        import al_evidence as E
        result=owner.spawn(gate['argv'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        entry['pid']=result.pid; E._commit(sid,entry)
        try: stdout,stderr=result.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop(result); stdout,stderr=result.communicate()
            with log.open('ab') as out: out.write(stdout);out.write(stderr)
            return 'INFRA',126,{'provider':'gitlab','reason':'remote lookup timed out'}
        with log.open('ab') as out:out.write(stdout);out.write(stderr)
    except (OSError,subprocess.TimeoutExpired) as exc:
        with log.open('a') as out:out.write(str(exc)+'\n')
        return 'INFRA',127,{'provider':'gitlab','reason':str(exc)}
    if result.returncode:return 'INFRA',result.returncode,{'provider':'gitlab','reason':'remote lookup failed'}
    try:data=response(stdout)
    except (ValueError,UnicodeError) as exc:return 'INFRA',126,{'provider':'gitlab','reason':'invalid remote job response: '+str(exc)}
    provenance={'provider':'gitlab','project_id':remote['project_id'],'job_id':data.get('id'),'job_name':data.get('name'),
                'pipeline_id':data.get('pipeline',{}).get('id'),'commit':data.get('commit',{}).get('id'),
                'status':data.get('status'),'web_url':data.get('web_url')}
    if not provenance['job_id'] or not provenance['pipeline_id'] or provenance['commit']!=revision or provenance['job_name']!=remote['job_name']:
        return 'INFRA',126,dict(provenance,reason='remote job provenance mismatch')
    if data.get('pipeline',{}).get('project_id') not in {None,remote['project_id']}:
        return 'INFRA',126,dict(provenance,reason='remote project mismatch')
    return ('PASS',0,provenance) if provenance['status']=='success' else ('FAIL',1,provenance)
