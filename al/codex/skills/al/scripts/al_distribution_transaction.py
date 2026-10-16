"""Recoverable multi-file installation with explicit transaction boundaries."""
import hashlib
import json
import time
from pathlib import Path
import al_distribution_registry as R


def state(path):
    from al_distribution import sha
    path=Path(path)
    if path.is_symlink():raise ValueError('symlink resource refused')
    if not path.exists():return None
    if not path.is_file():raise ValueError('resource must be a regular file')
    return {'sha256':sha(path),'mode':path.stat().st_mode & 0o777}


def write_json(path,data):
    from al_distribution import atomic
    atomic(path,json.dumps(data,sort_keys=True,indent=2).encode(),0o600)


def validate_target(target,registry):
    from al_distribution import managed,repo_identity,instructions
    root=R.safe_absolute(target['path']);source=R.safe_absolute(target['source'])
    if R.stable(repo_identity(root))!=R.stable(target['identity']):raise ValueError('repository identity changed')
    if instructions(root,target['identity'])!=target['instructions'] or target['instruction_review'].get('instructions')!=target['instructions'] or not target['instruction_review'].get('reviewed'):raise ValueError('target instructions/review changed')
    if managed(source)!=target['source_files']:raise ValueError('source package changed')
    R.assert_generation(registry,target)
    if hashlib.sha256(json.dumps(target['source_files'],sort_keys=True).encode()).hexdigest()!=target['source_manifest_sha256']:raise ValueError('source manifest hash mismatch')
    record,_=R.load(registry,target['path'],target['platform'])
    expected_owned=record['files'] if record else {}
    if target.get('bootstrap'):
        # Revalidate both trusted provenance artifacts and actual inventory.
        row=target['bootstrap']
        artifact={'schema_version':1,'targets':[dict(row,target=target['path'],platform=target['platform'],identity=R.stable(target['identity']))]}
        adopted,_=R.bootstrap(artifact,target,managed(root))
        expected_owned=R.merge_owned(expected_owned,adopted,row)
    if expected_owned!=target['prior_owned']:raise ValueError('prior ownership manifest differs from registry/provenance')
    ids=[a['path'] for a in target['actions']]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate installation action')
    for a in target['actions']:
        if a['action'] not in {'create','replace','delete'}:raise ValueError('unknown installation action')
        if a['action']=='delete':
            if a['path'] in target['source_files'] or expected_owned.get(a['path'])!=a['target'] or a['source'] is not None:raise ValueError('delete lacks prior ownership')
        elif a['source']!=target['source_files'].get(a['path']) or (a['action']=='create')!=(a['target'] is None):raise ValueError('invalid create/replace action')
    if target.get('result_owned')!=R.result_owned(expected_owned,target['source_files'],target['actions']):raise ValueError('result ownership differs from validated provenance/actions')
    obsolete=set(expected_owned)-set(target['source_files'])
    if obsolete!={a['path'] for a in target['actions'] if a['action']=='delete'}|set(target['already_absent']):raise ValueError('missing obsolete ownership disposition')
    for name in target['already_absent']:
        if state(root/R.relative(name)) is not None:raise ValueError('obsolete resource no longer absent')
    for a in target['actions']:
        rel=R.relative(a['path']);path=root/rel
        R.safe_absolute(path)
        if state(path)!=a['target']:raise ValueError('target drift: '+a['path'])
    verify_unmanaged(target)


def verify_unmanaged(target):
    for name,item in target['unmanaged'].items():
        path=Path(target['path'])/R.relative(name)
        if state(path)!=item:raise ValueError('unmanaged resource changed: '+name)
    for name,text in target.get('unmanaged_symlinks',{}).items():
        path=Path(target['path'])/R.relative(name)
        if not path.is_symlink() or __import__('os').readlink(path)!=text:raise ValueError('unmanaged symlink changed')


def desired(manifest):
    from al_distribution_roles import verify_roles
    for target in manifest['targets']:
        for name,item in target['source_files'].items():
            if state(Path(target['path'])/R.relative(name))!=item:raise ValueError('installed hash/mode mismatch: '+name)
        for a in target['actions']:
            if a['action']=='delete' and state(Path(target['path'])/R.relative(a['path'])) is not None:raise ValueError('obsolete owned resource still present')
        for name in target.get('already_absent',[]):
            if state(Path(target['path'])/R.relative(name)) is not None:raise ValueError('obsolete resource was recreated')
        verify_unmanaged(target)
    verify_roles(manifest.get('role_changes',[]))


def actions(manifest,backup):
    out=[]
    for target in manifest['targets']:
        tag=R.key(target['path'],target['platform'])
        for a in target['actions']:
            out.append(dict(a,target_path=str(Path(target['path'])/a['path']),source_path=str(Path(target['source'])/a['path']) if a['source'] else None,
                            backup=str(backup/tag/a['path']) if a['target'] else None,kind='skill',status='PENDING'))
    for role in manifest.get('role_changes',[]):
        pre={'sha256':role['target_sha256'],'mode':role['target_mode']} if role['target_sha256'] else None
        out.append({'action':'replace' if pre else 'create','target_path':role['target'],'source_path':role['source'],
                    'source':{'sha256':role['source_sha256'],'mode':role['source_mode']},'target':pre,
                    'backup':str(backup/'roles'/hashlib.sha256(role['target'].encode()).hexdigest()) if pre else None,'kind':'role','status':'PENDING'})
    return out


def execute_action(action,journal,path):
    from al_distribution import atomic
    target=Path(action['target_path']);R.safe_absolute(target)
    if state(target)!=action['target']:raise ValueError('target drift immediately before mutation: '+str(target))
    if action['target']:
        raw=target.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=action['target']['sha256'] or state(target)!=action['target']:raise ValueError('preimage moved during read')
        backup=Path(action['backup'])
        if backup.exists():raise ValueError('backup identity already exists')
        atomic(backup,raw,action['target']['mode'])
        if state(backup)!=action['target']:raise ValueError('backup hash/mode mismatch')
        action['status']='BACKED_UP';write_json(path,journal)
    else:action['status']='READY';write_json(path,journal)
    if state(target)!=action['target']:raise ValueError('target drift after backup; manual recovery required')
    if action['action']=='delete':target.unlink()
    else:
        source=Path(action['source_path']);R.safe_absolute(source);raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=action['source']['sha256'] or state(source)!=action['source']:raise ValueError('source moved during application')
        atomic(target,raw,action['source']['mode'])
    if state(target)!=action['source']:raise ValueError('postimage verification failed')
    action['status']='APPLIED';write_json(path,journal)


def apply(manifest,backup):
    from al_distribution_roles import validate_roles
    if manifest.get('schema_version')!=2:raise ValueError('distribution requires version 2 plan')
    targets=manifest['targets'];registry=R.external(manifest['registry'],targets)
    for t in targets:R.external(registry,targets,t['source'])
    backup=R.external(backup,targets)
    for t in targets:R.external(backup,targets,t['source'])
    for role in manifest.get('role_changes',[]):R.external(registry,[{'path':role['target'],'identity':role['identity']}])
    role_locks=[{'path':r['target'],'platform':'role'} for r in manifest.get('role_changes',[])]
    with R.locks(registry,targets+role_locks):
        for target in targets:validate_target(target,registry)
        validate_roles(manifest.get('role_changes',[]))
        for role in manifest.get('role_changes',[]):
            sentinel=registry/('role-'+R.key(role['target'],'role')+'.json')
            if sentinel.exists():
                saved=json.loads(sentinel.read_bytes())
                if saved.get('status')!='COMPLETE' or json.loads(Path(saved['journal']).read_bytes()).get('status')!='COMPLETE':raise ValueError('unresolved role transaction; manual recovery required')
        for role in manifest.get('role_changes',[]):R.external(backup,[{'path':role['target'],'identity':role['identity']}])
        backup.mkdir(parents=True,exist_ok=True);path=backup/'distribution-journal.json'
        if path.exists():raise ValueError('distribution backup already used')
        journal={'schema_version':2,'status':'PREPARED','manifest':manifest,'actions':actions(manifest,backup),
                 'registry_preimages':{R.key(t['path'],t['platform']):t.get('prior_registry_record') for t in targets},'created_at':time.time()}
        write_json(path,journal)
        try:
            for target in targets:
                record=target.get('prior_registry_record') or {'schema_version':1,'target':target['path'],'platform':target['platform'],'identity':R.stable(target['identity']),
                         'generation':1,'files':target['prior_owned'],'provenance':dict(target.get('bootstrap') or {'kind':'install','source_manifest_sha256':target['source_manifest_sha256']},journal=str(path))}
                write_json(R.record_path(registry,target['path'],target['platform']),dict(record,pending_transaction=str(path)))
            for role in manifest.get('role_changes',[]):
                write_json(registry/('role-'+R.key(role['target'],'role')+'.json'),{'target':role['target'],'journal':str(path),'status':'PENDING'})
            for action in journal['actions']:execute_action(action,journal,path)
            desired(manifest)
            # Publish all new generations still pending, then complete the journal,
            # and only then clear pending pointers. A crash always remains visible.
            for target in targets:
                record={'schema_version':1,'target':target['path'],'platform':target['platform'],'identity':R.stable(target['identity']),
                        'generation':target['registry']['generation']+1,'files':target['result_owned'],
                        'provenance':{'kind':'install','source_manifest_sha256':target['source_manifest_sha256'],'journal':str(path)},'pending_transaction':str(path)}
                write_json(R.record_path(registry,target['path'],target['platform']),record)
            journal['status']='COMPLETE';write_json(path,journal)
            for role in manifest.get('role_changes',[]):
                write_json(registry/('role-'+R.key(role['target'],'role')+'.json'),{'target':role['target'],'journal':str(path),'status':'COMPLETE'})
            for target in targets:
                record_path=R.record_path(registry,target['path'],target['platform']);record=json.loads(record_path.read_bytes());record['pending_transaction']=None;record['provenance']['journal_sha256']=hashlib.sha256(path.read_bytes()).hexdigest();write_json(record_path,record)
        except BaseException as exc:
            journal.update(status='FAILED',error=str(exc),recovery='Do not retry. Inspect action preimages/backups and registry_preimages; manually reconcile each target and registry pending pointer.')
            write_json(path,journal);raise
    verify(manifest)
    return [dict(a,target=a['target_path'],sha256=a['source']['sha256'] if a['source'] else None) for a in journal['actions']]


def verify(manifest):
    if manifest.get('schema_version')!=2:raise ValueError('distribution requires version 2 plan')
    registry=R.external(manifest['registry'],manifest['targets'])
    role_locks=[{'path':r['target'],'platform':'role'} for r in manifest.get('role_changes',[])]
    with R.locks(registry,manifest['targets']+role_locks):
        for target in manifest['targets']:
            record,_=R.load(registry,target['path'],target['platform'])
            if target.get('result_owned')!=R.result_owned(target['prior_owned'],target['source_files'],target['actions']):raise ValueError('result ownership derivation mismatch')
            if not record or record['files']!=target['result_owned'] or record['generation']!=target['registry']['generation']+1 or record['identity']!=R.stable(target['identity']) or record['provenance'].get('source_manifest_sha256')!=target['source_manifest_sha256']:raise ValueError('registry ownership/generation mismatch')
            journal=json.loads(Path(record['provenance']['journal']).read_bytes())
            if journal.get('status')!='COMPLETE' or journal.get('manifest')!=manifest or any(a['status']!='APPLIED' for a in journal['actions']):raise ValueError('transaction not complete')
            expected=actions(manifest,Path(record['provenance']['journal']).parent)
            if [dict(a,status='PENDING') for a in journal['actions']]!=expected:raise ValueError('transaction action accounting mismatch')
            for action in journal['actions']:
                if action['target'] and state(Path(action['backup']))!=action['target']:raise ValueError('backup integrity/mode mismatch')
        for role in manifest.get('role_changes',[]):
            sentinel=json.loads((registry/('role-'+R.key(role['target'],'role')+'.json')).read_bytes())
            if sentinel.get('status')!='COMPLETE' or json.loads(Path(sentinel['journal']).read_bytes()).get('manifest')!=manifest:raise ValueError('role transaction not complete')
        desired(manifest)
    return {'verified':True,'targets':len(manifest['targets'])}
