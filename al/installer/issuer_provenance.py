"""Immutable issuer provenance data; never an executable policy selection."""
import os
from pathlib import Path
import shutil
from .validator_origin import capture, canonical, decode, digest, safe, interpreter_identity, repository_identity

EXCLUDED={'__pycache__','.pytest_cache','.ruff_cache','.DS_Store'}
ROLES={'al-architect','al-architect-fallback','al-designer','al-designer-fallback','al-engineer','al-builder','al-scout'}


def inventory(installation,platform):
    root=safe(installation).resolve(strict=True)
    if root.name!='al' or root.parent.name!='skills' or root.parent.parent.name!='.'+platform:
        raise ValueError('issuer canonical installation layout mismatch')
    paths={};total=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        if len(Path(directory).relative_to(root).parts)>32:raise ValueError('issuer depth bound exceeded')
        for name in dirs:
            if (Path(directory)/name).is_symlink():raise ValueError('issuer directory symlink refused')
        dirs[:]=sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted(files):
            if name in EXCLUDED or name.endswith(('.pyc','.pyo')):continue
            path=Path(directory)/name;raw,state=capture(path);total+=len(raw)
            rel=path.relative_to(root).as_posix()
            paths['skill/'+rel]=(path,raw,dict(state,kind='runtime' if rel.startswith('scripts/') else 'reference' if rel.startswith('reference/') else 'package'))
    roles=root.parent.parent/'agents';suffix='.toml' if platform=='codex' else '.md'
    safe(roles)
    for path in sorted(roles.glob('al-*'+suffix)):
        if path.stem not in ROLES:raise ValueError('unknown issuer AL role resource')
        raw,state=capture(path);total+=len(raw);paths['roles/'+path.name]=(path,raw,dict(state,kind='role'))
    if len(paths)>2000 or total>64*1024*1024:raise ValueError('issuer closure bound exceeded')
    for name in ('skill/scripts/al_state.py','skill/scripts/al_store.py','skill/SKILL.md'):
        if name not in paths:raise ValueError('incomplete issuer entrypoint closure')
    return root,paths


def invocation(reference,root,platform,repository,session,run,paths):
    if not isinstance(reference,dict) or set(reference)!={'path','sha256'}:raise ValueError('explicit supervisor invocation reference required')
    path=safe(reference['path']).resolve(strict=True)
    if path.is_relative_to(repository) or path.is_relative_to(root):raise ValueError('issuer invocation provenance must be external')
    raw,source_state=capture(path)
    if digest(raw)!=reference['sha256']:raise ValueError('issuer invocation provenance changed')
    source=decode(raw)
    if source.get('schema_version')!=1 or source.get('kind') not in {'complete-issuer-input-preparation-not-protected-capture','al-issuer-invocation-provenance'}:
        raise ValueError('unsupported supervisor issuer provenance')
    known=source['known_supervisor_invocation'];subject=source['run']
    if subject['repository']!=str(repository) or subject['session']!=session or subject['run']!=run:
        raise ValueError('issuer invocation belongs to another run')
    if source['platforms'][platform]['original_installation']!=str(root):raise ValueError('foreign issuer installation provenance')
    selected=source['platforms'][platform]
    expected={'skill/'+name:{k:row[k] for k in ('sha256','mode')} for name,row in selected['files'].items()}
    expected.update({'roles/'+name:{k:row[k] for k in ('sha256','mode')} for name,row in selected['roles'].items()})
    if expected!={name:{k:row[2][k] for k in ('sha256','mode')} for name,row in paths.items()}:
        raise ValueError('issuer differs from independently reviewed invocation closure')
    argv=known['argv']
    if not isinstance(argv,list) or len(argv)!=4 or argv[1:]!=[str(root/'scripts/al_state.py'),'--session',session]:
        raise ValueError('issuer invocation entrypoint/session mismatch')
    executable=shutil.which(argv[0]) if isinstance(argv[0],str) else None
    identity=interpreter_identity()
    if not executable or str(Path(executable).resolve())!=identity['canonical_executable']:
        raise ValueError('issuer invocation interpreter mismatch')
    return {'argv':argv,'interpreter_identity':identity,'supervisor_provenance_artifact_digest':reference['sha256']},raw,source_state


def capture_issuer(installation,*,platform,repository,session,run,invocation_provenance,execution_profile=False,previous_binding=None):
    if platform not in {'codex','claude'}:raise ValueError('unknown issuer platform')
    repository=safe(repository).resolve(strict=True)
    root,paths=inventory(installation,platform)
    if root.is_relative_to(repository):raise ValueError('target repository cannot supply issuing runtime')
    known,source_raw,source_state=invocation(invocation_provenance,root,platform,repository,session,run,paths)
    if previous_binding is not None:
        verify_issuer(previous_binding['root'],previous_binding)
        prior=decode(capture(Path(previous_binding['root'])/'provenance.json')[0])
        if prior['run']!={'repository_identity':repository_identity(repository),'session_id':session,'run_id':run} or prior['installation']!={'canonical_root':str(root),'platform':platform} or prior['invocation']['interpreter_identity']!=known['interpreter_identity'] or prior['invocation']['argv']!=known['argv']:
            raise ValueError('previous issuer capture belongs to another invocation')
        if not execution_profile:raise ValueError('prior binding requires explicit new execution profile')
    if execution_profile:
        from .interpreter_execution import capture_profile
        known['execution_profile']=capture_profile(shutil.which(known['argv'][0]),known['interpreter_identity'],known['argv'][0])
    closure={name:state for name,(_,_,state) in paths.items()}
    closure['provenance/supervisor-invocation.json']=dict(source_state,kind='provenance')
    data={'schema_version':2 if execution_profile else 1,'kind':'al-issuing-runtime-provenance','installation':{'canonical_root':str(root),'platform':platform},
          'invocation':known,'run':{'repository_identity':repository_identity(repository),'session_id':session,'run_id':run},
          'closure':closure,'closure_digest':digest(canonical(closure)),
          'resource_inventory':{'members':sorted(closure),'roles':sorted(n for n in closure if n.startswith('roles/'))}}
    if previous_binding is not None:data['previous_capture']={k:previous_binding[k] for k in ('capture_digest','provenance_sha256','receipt_sha256')}
    data['capture_digest']=digest(canonical(data))
    again_root,again=inventory(root,platform)
    if again_root!=root or {n:v[2] for n,v in again.items()}!={n:v for n,v in closure.items() if not n.startswith('provenance/')}:raise ValueError('issuer changed during complete capture')
    blobs={state['sha256']:raw for _,raw,state in paths.values()}
    blobs[source_state['sha256']]=source_raw
    return {'provenance':data,'blobs':blobs}


def retain_issuer(captured,storage):
    """Authorized preparation writes once; its returned binding must be protected."""
    data=captured['provenance'];root=safe(storage)/data['capture_digest']
    if root.exists():raise ValueError('issuer retained capture already exists')
    root.mkdir(parents=True,mode=0o700)
    blobs=root/'blobs';blobs.mkdir(mode=0o700)
    def write(path,raw):
        with path.open('xb') as file:
            os.fchmod(file.fileno(),0o600);file.write(raw);file.flush();os.fsync(file.fileno())
    for sha,raw in captured['blobs'].items():
        if digest(raw)!=sha:raise ValueError('issuer blob digest mismatch')
        write(blobs/sha,raw)
    raw=canonical(data)+b'\n';write(root/'provenance.json',raw)
    receipt={'schema_version':1,'kind':'al-issuer-storage-receipt','capture_digest':data['capture_digest'],
             'provenance_sha256':digest(raw),'run':data['run'],'members':sorted(captured['blobs'])}
    receipt_raw=canonical(receipt)+b'\n';write(root/'receipt.json',receipt_raw)
    binding={'root':str(root),'capture_digest':data['capture_digest'],'provenance_sha256':digest(raw),
             'receipt_sha256':digest(receipt_raw),'run':data['run'],'installation':data['installation']}
    verify_issuer(root,binding)
    return binding


def verify_issuer(captured,expected_binding,*,observe_live=False):
    root=safe(captured).resolve(strict=True)
    if str(root)!=expected_binding['root']:raise ValueError('foreign retained issuer capture')
    raw,state=capture(root/'provenance.json');receipt_raw,receipt_state=capture(root/'receipt.json')
    if state!={'sha256':expected_binding['provenance_sha256'],'mode':0o600} or receipt_state!={'sha256':expected_binding['receipt_sha256'],'mode':0o600}:
        raise ValueError('retained issuer metadata changed')
    data=decode(raw);receipt=decode(receipt_raw);unsigned=dict(data);claimed=unsigned.pop('capture_digest')
    if claimed!=expected_binding['capture_digest'] or digest(canonical(unsigned))!=claimed or digest(canonical(data['closure']))!=data['closure_digest']:
        raise ValueError('retained issuer digest mismatch')
    if data['schema_version'] not in {1,2} or data['kind']!='al-issuing-runtime-provenance' or data['run']!=expected_binding['run'] or data['installation']!=expected_binding['installation']:
        raise ValueError('retained issuer run/origin mismatch')
    if data['schema_version']==2:
        from .interpreter_execution import validate_profile
        invocation=data['invocation'];profile=invocation.get('execution_profile')
        validate_profile(profile,invocation['interpreter_identity'])
        if profile['requested_argv0']!=invocation['argv'][0]:raise ValueError('issuer execution invocation differs')
    elif 'execution_profile' in data['invocation']:raise ValueError('v1 issuer cannot supply execution profile')
    members=sorted({row['sha256'] for row in data['closure'].values()})
    wanted={'schema_version':1,'kind':'al-issuer-storage-receipt','capture_digest':claimed,'provenance_sha256':digest(raw),'run':data['run'],'members':members}
    if receipt!=wanted:raise ValueError('retained issuer receipt mismatch')
    observed=list(root.rglob('*'))
    if any(p.is_symlink() for p in observed):raise ValueError('retained issuer symlink refused')
    actual=sorted(p.relative_to(root).as_posix() for p in observed if not p.is_dir())
    if actual!=sorted(['provenance.json','receipt.json',*('blobs/'+sha for sha in members)]):raise ValueError('retained issuer membership changed')
    for sha in members:
        _,row=capture(root/'blobs'/sha)
        if row!={'sha256':sha,'mode':0o600}:raise ValueError('retained issuer blob changed')
    if observe_live:
        _,live=inventory(data['installation']['canonical_root'],data['installation']['platform'])
        if {n:v[2] for n,v in live.items()}!={n:v for n,v in data['closure'].items() if not n.startswith('provenance/')}:raise ValueError('live issuer no longer matches retained provenance')
    return {'schema_version':1,'kind':'al-issuer-provenance-observation','capture_digest':claimed,'live_observed':observe_live,
            'run':data['run'],'installation':data['installation']}
