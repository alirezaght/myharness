"""Integrity-bound, data-only CI planning scenarios; never pipeline evidence."""
import json
import re
from pathlib import Path
import al_artifacts as A
import al_store
import al_task as T
from al_schema import refuse

PROFILE='merge_request_quality'


def binding(st,declaration,allow_historical=False):
    if declaration is None:return {'profile':None,'current':True}
    if not isinstance(declaration,dict) or set(declaration)!={'profile','source'}:refuse('invalid CI context declaration')
    name=declaration['profile'];ref=declaration['source']
    if not isinstance(name,str) or not name:refuse('CI context profile required')
    if not isinstance(ref,dict) or ref.get('kind')!='ci-context' or not re.fullmatch(r'[0-9a-f]{64}',str(ref.get('sha256',''))) or ref.get('id')!=ref['sha256']:refuse('typed CI context artifact required')
    if not isinstance(ref.get('stored_path'),str) or not ref['stored_path']:refuse('CI context artifact path required')
    expected=al_store.RUNTIME_DIR/st['session_id']/'artifacts'/ref['sha256']
    if Path(ref.get('stored_path','')).resolve()!=expected.resolve():refuse('CI context artifact belongs to another run')
    try:
        raw=A.read(ref);data=json.loads(raw)
    except (OSError,ValueError,KeyError) as exc:refuse('invalid CI context artifact: '+str(exc))
    if not isinstance(data,dict) or set(data)!={'schema_version','profile','session_id','run_id','task_hash','selection'}:refuse('CI context artifact data fields required')
    if type(data['schema_version']) is not int or data['schema_version']!=1 or type(data['run_id']) is not type(st['run_id']) or data['profile']!=name or data['session_id']!=st['session_id'] or data['run_id']!=st['run_id']:refuse('CI context task/run/profile binding mismatch')
    selection=data['selection']
    if not isinstance(selection,dict) or set(selection)-{'label','source_sha256'} or not isinstance(selection.get('label'),str) or not 0<len(selection['label'].strip())<=200:refuse('CI context selection provenance required')
    if 'source_sha256' in selection and not re.fullmatch(r'[0-9a-f]{64}',str(selection['source_sha256'])):refuse('CI context selection provenance hash invalid')
    hashes=T.revision_hashes(st)
    if data['task_hash'] not in hashes:refuse('CI context task binding is not a recognized task revision')
    current=data['task_hash']==hashes[-1]
    if not current and not allow_historical:refuse('CI context task/run/profile binding mismatch: superseded task revision')
    return {'profile':name if name==PROFILE else None,'current':current}


def profile(st,declaration):return binding(st,declaration)['profile']
