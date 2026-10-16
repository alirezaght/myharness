"""External, provenance-bound managed-resource ownership registry."""
import contextlib
import fcntl
import hashlib
import json
from pathlib import Path


def safe_absolute(path):
    path=Path(path).expanduser().absolute()
    for ancestor in (path,*path.parents):
        if ancestor.is_symlink():raise ValueError('symlink path refused: '+str(ancestor))
    return path.resolve()


def relative(path):
    p=Path(path)
    if not isinstance(path,str) or not path or p.is_absolute() or '..' in p.parts or p==Path('.'):
        raise ValueError('unsafe managed path')
    return p


def stable(identity):return {'root':identity['root'],'git_dir':identity.get('git_dir')}


def key(target,platform):return hashlib.sha256((str(target)+'\0'+platform).encode()).hexdigest()


def record_path(registry,target,platform):return registry/(key(target,platform)+'.json')


def external(path,targets,source=None):
    path=safe_absolute(path)
    prohibited=[Path(t['identity']['root']).resolve() for t in targets]+[Path(t['path']).resolve() for t in targets]
    if source:prohibited.append(Path(source).resolve())
    for root in prohibited:
        if path==root or root in path.parents:raise ValueError('registry/backup must be outside package/repository trees')
    return path


def load(registry,target,platform):
    from al_distribution import sha
    path=record_path(registry,target,platform)
    if path.is_symlink():raise ValueError('symlink registry record refused')
    if not path.exists():return None,None
    record=json.loads(path.read_bytes())
    if record.get('schema_version')!=1 or record.get('target')!=str(target) or record.get('platform')!=platform:
        raise ValueError('registry identity/schema mismatch')
    if type(record.get('generation')) is not int or record['generation']<1 or not isinstance(record.get('files'),dict):raise ValueError('invalid registry record')
    for name,item in record['files'].items():
        relative(name)
        if not isinstance(item.get('sha256'),str) or type(item.get('mode')) is not int:raise ValueError('invalid registry resource')
    if record.get('pending_transaction'):raise ValueError('unresolved transaction; recover manually from journal/backups: '+record['pending_transaction'])
    provenance=record.get('provenance',{})
    if provenance.get('kind') not in {'install','bootstrap'} or not isinstance(provenance.get('source_manifest_sha256'),str) or len(provenance['source_manifest_sha256'])!=64 or not provenance.get('journal'):raise ValueError('registry ownership provenance missing')
    if provenance.get('journal'):
        journal_path=safe_absolute(provenance['journal'])
        if provenance.get('journal_sha256') and sha(journal_path)!=provenance['journal_sha256']:raise ValueError('unresolved transaction journal integrity; manual recovery required')
        if not journal_path.is_file() or json.loads(journal_path.read_bytes()).get('status')!='COMPLETE':raise ValueError('unresolved transaction journal; manual recovery required: '+str(journal_path))
    return record,sha(path)


@contextlib.contextmanager
def locks(registry,targets):
    registry.mkdir(parents=True,exist_ok=True)
    with contextlib.ExitStack() as stack:
        for target in sorted(targets,key=lambda x:key(x['path'],x['platform'])):
            path=registry/(key(target['path'],target['platform'])+'.lock')
            if path.is_symlink():raise ValueError('symlink registry lock refused')
            handle=stack.enter_context(path.open('a+'));fcntl.flock(handle,fcntl.LOCK_EX)
        yield


def referenced(ref):
    from al_distribution import sha
    path=safe_absolute(ref['path'])
    if sha(path)!=ref['sha256']:raise ValueError('bootstrap provenance artifact changed')
    return json.loads(path.read_bytes())


def bootstrap(prior,target,existing):
    if prior is None:return {},None
    data=json.loads(Path(prior).read_bytes()) if isinstance(prior,(str,Path)) else prior
    if data.get('schema_version')!=1 or not isinstance(data.get('targets'),list):raise ValueError('invalid prior-managed artifact')
    rows=[x for x in data['targets'] if x.get('target')==target['path'] and x.get('platform')==target['platform']]
    if not rows:return {},None
    if len(rows)!=1:raise ValueError('duplicate bootstrap target')
    row=rows[0]
    if not row.get('reviewed') or not row.get('reason') or stable(row['identity'])!=stable(target['identity']):raise ValueError('bootstrap review/identity missing')
    baseline=referenced(row['source_manifest']);inventory=referenced(row['inventory'])
    entries=inventory if isinstance(inventory,list) else inventory['targets']
    matches=[x for x in entries if (x.get('target') or x.get('path'))==target['path'] and x.get('platform')==target['platform']]
    if len(matches)!=1:raise ValueError('bootstrap inventory target mismatch')
    observed=matches[0].get('hashes') or {p:v['sha256'] for p,v in matches[0]['files'].items()}
    owned={}
    for name,item in row['files'].items():
        relative(name)
        if name not in baseline or not item.get('ownership_source'):raise ValueError('bootstrap path has no baseline ownership provenance')
        if observed.get(name)!=item.get('sha256') or existing.get(name)!={'sha256':item.get('sha256'),'mode':item.get('mode')}:raise ValueError('bootstrap inventory/hash/mode mismatch')
        if baseline[name]!=item['sha256'] and not item.get('local_difference_reason'):raise ValueError('local legacy difference needs explicit rationale')
        owned[name]={'sha256':item['sha256'],'mode':item['mode']}
    return owned,{'kind':'bootstrap','source_manifest_sha256':row['source_manifest']['sha256'],'source_manifest':row['source_manifest'],'inventory':row['inventory'],'reviewed':True,'reason':row['reason'],'files':row['files']}


def merge_owned(registered,adopted,provenance):
    for name,item in adopted.items():
        if name in registered and item!=registered[name] and not provenance['files'][name].get('local_difference_reason'):raise ValueError('local ownership adoption requires explicit per-file rationale')
    return dict(registered,**adopted)


def prior(registry,target,existing,prior_managed):
    record,sha=load(registry,target['path'],target['platform'])
    if record:
        if record['identity']!=stable(target['identity']):raise ValueError('registry repository identity changed')
        if prior_managed is not None:
            adopted,provenance=bootstrap(prior_managed,target,existing)
            if provenance:
                return merge_owned(record['files'],adopted,provenance),provenance,record,sha
        return record['files'],None,record,sha
    owned,provenance=bootstrap(prior_managed,target,existing)
    return owned,provenance,None,None


def assert_generation(registry,target):
    record,sha=load(registry,target['path'],target['platform'])
    if sha!=target['registry']['sha256'] or (record['generation'] if record else 0)!=target['registry']['generation']:
        raise ValueError('registry generation/hash changed; replan')


def result_owned(prior,desired,actions):
    written={a['path'] for a in actions if a['action'] in {'create','replace'}}
    return {name:item for name,item in desired.items() if name in prior or name in written}
