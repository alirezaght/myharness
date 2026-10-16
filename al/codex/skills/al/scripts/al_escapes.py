"""Local user-reported or reproduced post-approval defects."""
import json
import time
from pathlib import Path
import al_artifacts as A
import al_ledger as L
import al_store
from al_schema import refuse,valid_id

def cmd_escape(a,sid):
    st=al_store.load(sid)
    if a.action=='relevant': print(json.dumps(st.get('escapes',[]),indent=2)); return
    data=json.loads(Path(a.file).read_bytes())
    if not valid_id(data.get('id')) or any(x['id']==data['id'] for x in st.get('escapes',[])): refuse('duplicate/invalid escape ID')
    if not data.get('observed_defect') or not data.get('source') or not data.get('areas') or data.get('provenance') not in {'user-reported','reproduced'}: refuse('escape requires defect/source/areas/provenance')
    if data.get('regression_evidence'):
        for eid in data['regression_evidence']:
            if not L.find(st,eid): refuse('unknown regression evidence')
    elif data['provenance']=='reproduced': refuse('reproduced escape needs recorded evidence')
    data.update(at=time.time(),artifact=A.snapshot_artifact(sid,Path(a.file),'escape-record'))
    st.setdefault('escapes',[]).append(data); al_store.save(sid,st)

def register(sub):
    children=sub.add_parser('escape').add_subparsers(dest='action',required=True)
    p=children.add_parser('record'); p.add_argument('--file',required=True); p.set_defaults(fn=cmd_escape)
    children.add_parser('relevant').set_defaults(fn=cmd_escape)
