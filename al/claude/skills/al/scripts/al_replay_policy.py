"""Replay declared policy expectations against an explicitly selected package."""
import json
from pathlib import Path
import subprocess
import sys

TIMEOUT=10
CHILD='''
import contextlib,io,json,sys
from pathlib import Path
request=json.load(sys.stdin);scripts=Path(request['scripts']).resolve();expected=scripts/'al_shell.py'
response={'ok':False,'violations':[]}
try:
    if not expected.is_file():raise ImportError('selected parser is missing')
    sys.path.insert(0,str(scripts))
    with contextlib.redirect_stdout(io.StringIO()):
        import al_shell
        origin=Path(al_shell.__file__).resolve()
        if origin!=expected:raise ImportError('selected parser origin mismatch')
        if not callable(getattr(al_shell,'check',None)):raise ImportError('selected policy interface missing')
        violations=al_shell.check(request['command'],request['policy'])
    if not isinstance(violations,list) or any(not isinstance(v,str) or not v for v in violations):raise ValueError('malformed policy violations')
    response={'ok':True,'violations':violations,'module_origin':str(origin)}
except Exception as exc:response['error']=type(exc).__name__+': '+str(exc)
print(json.dumps(response))
'''


def outcome(policy,command,error=None):
    return {'kind':'policy','policy':policy,'command':command,'expected':'allow',
            'violations':[],'matched':False,'unsupported':bool(error),**({'error':error} if error else {})}


def declared(commands):
    if not isinstance(commands,dict) or not commands:
        return [outcome(None,None,'commands must be a nonempty policy mapping')]
    rows=[]
    for policy,values in commands.items():
        if policy not in {'readonly','shared'}:
            rows.append(outcome(policy,None,'unknown replay policy'));continue
        if not isinstance(values,list) or not values:
            rows.append(outcome(policy,None,'policy commands must be a nonempty list'));continue
        for command in values:
            error=None if isinstance(command,str) and command.strip() else 'policy command must be a nonempty string'
            rows.append(outcome(policy,command,error))
    return rows


def check(package,row,root,env):
    if row['unsupported']:return row
    scripts=(package/'scripts').resolve()
    request={'scripts':str(scripts),'policy':row['policy'],'command':row['command']}
    try:
        result=subprocess.run([sys.executable,'-c',CHILD],input=json.dumps(request),cwd=root,env=env,
                              capture_output=True,text=True,timeout=TIMEOUT)
        if result.returncode:raise ValueError('policy subprocess exited '+str(result.returncode))
        data=json.loads(result.stdout)
        if not isinstance(data,dict) or type(data.get('ok')) is not bool:raise ValueError('malformed policy report')
        if not data['ok']:raise ValueError(data.get('error') or 'selected policy interface unavailable')
        violations=data.get('violations')
        if not isinstance(violations,list) or any(not isinstance(v,str) or not v for v in violations):raise ValueError('malformed policy report violations')
        if data.get('module_origin')!=str(scripts/'al_shell.py'):raise ValueError('selected policy report origin mismatch')
        return dict(row,violations=violations,matched=not violations,unsupported=False,module_origin=data['module_origin'])
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        return dict(row,matched=False,unsupported=True,error=str(exc))


def evaluate(package,case,root,env):
    return [check(package,row,root,env) for row in declared(case['commands'])] if 'commands' in case else []


def counts(outcomes):
    result={}
    for kind in ('action','policy'):
        rows=[r for r in outcomes if r['kind']==kind]
        result[kind]={'total':len(rows),'matched':sum(r['matched'] for r in rows),
                      'failed':sum(not r['matched'] and not r['unsupported'] for r in rows),
                      'unavailable':sum(r['unsupported'] for r in rows)}
    return result
