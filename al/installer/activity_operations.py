"""Closed historical reconciliation receipts never waive uncertain cleanup."""
import hashlib
import math
from pathlib import Path

from .engine import R


def setup_checks(entries):
    """Dependency installers are owned children, including failed/unfinished setup."""
    checks=[]
    required={'schema_version','kind','manager','argv','cwd','inputs','stage','status','started_at','hook'}
    optional={'executable','pid','cleanup','exit','python_interpreter','reason','ended_at'}
    for entry in entries:
        rows=entry.get('setup',[])
        if not isinstance(rows,list):raise ValueError('unknown dependency setup registry')
        for index,row in enumerate(rows):
            if (not isinstance(row,dict) or not required<=set(row) or set(row)-required-optional
                    or type(row['schema_version']) is not int or row['schema_version']!=1
                    or row['kind']!='check-setup-v1' or row['stage']!='setup'
                    or row['status'] not in {'RUNNING','PASS','INFRA'}
                    or row['manager'] not in {'uv','npm','pnpm'} or not finite(row['started_at'])):
                raise ValueError('unknown dependency setup activity profile')
            cleanup=row.get('cleanup')
            if cleanup is not None and (not isinstance(cleanup,dict) or type(cleanup.get('complete')) is not bool or not isinstance(cleanup.get('errors'),list)):
                raise ValueError('unknown dependency setup cleanup profile')
            pid=row.get('pid')
            if pid is not None and (type(pid) is not int or pid<=0):
                raise ValueError('invalid dependency setup PID')
            uncertain=bool(pid is not None and (cleanup is None or not cleanup['complete']
                           or cleanup.get('unresolved') is not False
                           or cleanup.get('group_absent') is not True))
            checks.append({'id':entry['id']+'-setup-'+str(index+1),
                           'status':row['status'],'pid':pid,'started_at':row['started_at'],
                           'ended_at':row.get('ended_at'),
                           'cleanup':{'exit':row.get('exit'),
                                      'errors':cleanup.get('errors',[]) if cleanup else [],
                                      'unresolved':uncertain},
                           'process':{'pid':pid} if pid else {}})
    return checks


def finite(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def reconciliation_validation(entry, state, provenance, state_sha256):
    result = {'profile': 'guarded-check-reconciliation-receipt-v1', 'supported': False,
              'state_sha256': state_sha256, 'session_id': state['session_id'], 'run_id': state.get('run_id'),
              'evidence_id': entry['id'], 'artifact_sha256': None}
    receipt = entry.get('reconciliation')
    if receipt is None:return result
    try:
        if (not isinstance(receipt, dict) or set(receipt) != {'reason', 'artifact'}
                or not isinstance(receipt['reason'], str) or not receipt['reason'].strip()
                or entry['status'] != 'INFRA' or entry.get('note') != 'interrupted check explicitly reconciled'):
            raise ValueError('unsupported guarded check reconciliation profile')
        ref = receipt['artifact']
        if (not isinstance(ref, dict) or set(ref) != {'id', 'kind', 'sha256', 'stored_path', 'source', 'captured_at'}
                or ref['kind'] != 'check-reconciliation' or ref['id'] != ref['sha256']
                or not isinstance(ref['sha256'], str) or len(ref['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in ref['sha256'])
                or not isinstance(ref['source'], str) or not ref['source']):
            raise ValueError('unsupported immutable check reconciliation reference')
        path = R.safe_absolute(ref['stored_path']);runtime = Path(provenance['runtime_root'])
        relative = path.relative_to(runtime).as_posix();member = provenance['members'].get(relative)
        if not member or member['type'] != 'file' or member['sha256'] != ref['sha256']:
            raise ValueError('check reconciliation snapshot not inventoried')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:raise ValueError('check reconciliation snapshot bytes changed')
        times = [entry.get('started_at'), entry.get('ended_at'), ref['captured_at'], state.get('updated_at')]
        if not all(finite(t) for t in times) or times != sorted(times):
            raise ValueError('unsupported check reconciliation chronology')
        result.update(supported=True, artifact_sha256=ref['sha256'],
                      payload_semantics='Hash-bound supporting bytes retained under existing guarded check-reconcile; no descendant cleanup waiver.')
    except (OSError, ValueError, TypeError, KeyError) as exc:result['reason'] = str(exc)
    return result


def check_blockers(check, validation=None):
    reasons = []
    if check['status'] == 'RUNNING':reasons.append('active/unresolved evidence reservation')
    if check.get('interrupted') or check.get('reconciliation') is not None:
        if not validation or not validation['supported']:reasons.append('unsupported/unresolved check reconciliation')
    cleanup = check.get('cleanup')
    if cleanup is not None:
        if (not isinstance(cleanup, dict) or set(cleanup) != {'exit', 'errors', 'unresolved'}
                or type(cleanup['unresolved']) is not bool or not isinstance(cleanup['errors'], list)
                or any(not isinstance(e, str) for e in cleanup['errors'])
                or cleanup['exit'] is not None and type(cleanup['exit']) is not int):
            reasons.append('unsupported owned-process cleanup metadata')
        elif cleanup['unresolved'] or cleanup['errors']:
            reasons.append('unresolved owned-process cleanup')
    return reasons


def operation_reasons(record, root, observation):
    """Common operational blockers; acknowledgements never waive any of them."""
    reasons = []
    facts = record['operation_facts']
    if any(w['state'] in {'ACTIVE', 'UNRESOLVED'} for w in facts.get('writers', {}).values()):
        reasons.append('active/unresolved writer')
    for check in facts.get('checks', []):
        reasons.extend(check_blockers(check, facts.get('check_reconciliations', {}).get(check['id'])))
    identity = record['identity']
    reasons.extend(o['reason'] for o in observation['operations'] if o['identity'] == identity and o['result'] != 'quiescent')
    reasons.extend('live or unknown relevant process' for o in observation['processes'] if o['record_identity'] == identity and o['result'] not in {'absent', 'different-process'})
    return reasons
