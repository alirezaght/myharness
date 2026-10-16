"""Frozen accepted task packages and explicit user amendments."""
import json
import re
import time
from pathlib import Path
import al_artifacts as A
import al_store
from al_schema import refuse, valid_id

def substantive(text):
    return isinstance(text,str) and len(text.strip()) >= 20 and not re.fullmatch(r'(yes|ok|okay|approve|approved|do that)[.! ]*',text.strip(),re.I)

def package(sid,a):
    if a.package_file:
        raw=Path(a.package_file).read_bytes(); data=json.loads(raw)
        required=('original_request','accepted_spec','acceptance','constraints','ticket','criteria','invariants')
        if any(k not in data for k in required): refuse('task package lacks required provenance fields')
        if not substantive(data['accepted_spec']) or not isinstance(data['original_request'],str) or not data['original_request'].strip(): refuse('materially missing accepted task package')
        if not isinstance(data['acceptance'],dict) or not data['acceptance'].get('source') or not data['acceptance'].get('text'): refuse('acceptance requires text and source')
        if not isinstance(data['constraints'],list) or any(not isinstance(x,str) for x in data['constraints']): refuse('constraints must be supplied text entries')
        if not isinstance(data['ticket'],dict) or type(data['ticket'].get('present')) is not bool: refuse('ticket must explicitly record presence/absence')
        if data['ticket']['present'] and not data['ticket'].get('content'): refuse('present ticket needs supplied content')
        source=a.package_file
    else:
        text=Path(a.spec_file).read_text() if a.spec_file else a.spec
        if not substantive(text): refuse('assent alone needs --package-file with the accepted proposal')
        ids=re.findall(r'\bAC-\d+\b',text)
        data={'original_request':text,'accepted_spec':text,'acceptance':{'text':'substantive legacy specification','source':'CLI --spec-file' if a.spec_file else 'CLI --spec'},
              'constraints':[],'ticket':{'present':False,'reason':'not supplied'},'criteria':list(dict.fromkeys(ids)) or ['AC-01'],'invariants':[]}
        raw=json.dumps(data,sort_keys=True).encode(); source=a.spec_file or 'CLI --spec'
    for key in ('criteria','invariants'):
        if not isinstance(data[key],list) or any(not isinstance(i,str) or not i for i in data[key]) or len(data[key])!=len(set(data[key])): refuse('invalid '+key+' IDs')
    if not data['criteria']: refuse('task package needs stable criteria IDs')
    return data,A.snapshot_bytes(sid,raw,'task-package',source)

def revision_hashes(st):
    A.read(st['task_package']);amendments=[];hashes=[]
    def current():return A.digest(json.dumps({'original':st['task_package']['sha256'],'amendments':amendments},sort_keys=True).encode())
    hashes.append(current())
    for amendment in st['amendments']:
        data=A.read_json(amendment['artifact'])
        if not isinstance(data,dict) or any(data.get(k)!=amendment.get(k) for k in ('id','text','source','affected_criteria')):refuse('amendment artifact provenance mismatch')
        amendments.append(amendment['artifact']['sha256']);hashes.append(current())
    return hashes


def task_hash(st):return revision_hashes(st)[-1]

def cmd_amend(a,sid):
    st=al_store.load(sid)
    if st['status']!='RUNNING': refuse('resume before amendment')
    data=json.loads(Path(a.file).read_bytes())
    if not valid_id(data.get('id')) or any(x['id']==data['id'] for x in st['amendments']): refuse('invalid/duplicate amendment ID')
    if not substantive(data.get('text')) or not data.get('source'): refuse('amendment needs substantive user text and source')
    criteria=A.read_json(st['task_package'])['criteria']
    affected=data.get('affected_criteria')
    if affected is not None and (not isinstance(affected,list) or any(x not in criteria for x in affected)): refuse('unknown affected criterion')
    from al_gate_recovery import amendment_binding,amend
    recovery=amendment_binding(st) if 'gate_recovery' in st else None
    data.update(at=time.time(),artifact=A.snapshot_artifact(sid,Path(a.file),'user-amendment'))
    st['amendments'].append(data); st['review']=None; st['review_status']=None
    st['evidence']['invalidated_at']=time.time()
    al_store.set_phase(st,'PLANNING'); st['spec_status']=None
    if recovery:amend(st,sid,data,recovery)
    al_store.save(sid,st)

def cmd_conflict(a,sid):
    st=al_store.load(sid)
    if st['phase']!='PLAN_CONFLICT' or not a.reason.strip(): refuse('resolution requires PLAN_CONFLICT and Architect rationale')
    if not Path(a.file).read_bytes().strip():refuse('conflict resolution artifact must be nonempty')
    st['conflict_resolution']={'role':'al-architect','reason':a.reason,'artifact':A.snapshot_artifact(sid,Path(a.file),'conflict-resolution'),'occurrence':len(st['phase_occurrences'])-1}
    al_store.save(sid,st)

def register(sub):
    p=sub.add_parser('task').add_subparsers(dest='action',required=True).add_parser('amend')
    p.add_argument('--file',required=True); p.set_defaults(fn=cmd_amend)
    p=sub.add_parser('conflict-resolved'); p.add_argument('--file',required=True); p.add_argument('--reason',required=True); p.set_defaults(fn=cmd_conflict)
