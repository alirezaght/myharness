"""Trusted isolated approval query bound to canonical or protected snapshot code."""
import os
import re
from pathlib import Path
import subprocess
import sys
from .validator_origin import (canonical, capture_canonical, digest, decode, safe,
                               verify_origin_data, verify_snapshot)
from .validator_boot import CHILD
from .validator_observation import target_inventory,verify_read_set
from .validator_run_pin import capture_run_pin
from .validator_environment import capture_environment


def _context():
    root = Path(__file__).resolve().parents[1]
    trusted = sys.modules.get('_al_validator_context')
    if trusted is not None:
        request = trusted.request
        if request['mode'] != 'snapshot' or Path(request['root']) != root:
            raise ValueError('foreign protected validator context')
        captured = verify_snapshot(root, request['expected'])
        captured['mode'] = 'snapshot'
    else:
        if (root/'origin.json').exists():
            raise ValueError('installed validator requires protected launcher context')
        captured = capture_canonical(root)
    verify_origin_data(captured['origin'], captured['bytes'])
    return captured


def _request(captured):
    value = {'mode':captured['mode'], 'root':captured['snapshot'], 'expected':captured['expected']}
    if captured['mode'] == 'canonical':value['origin'] = captured['origin']
    return value


def _binding(captured):
    origin = captured['origin']
    return dict(captured['expected'], closure_digest=origin['closure_digest'],
                interpreter_identity=origin['interpreter'])


def validator_binding():return _binding(_context())


def query_environment():
    environment = {k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','AL_','GIT_','SSH_','DYLD_','LD_','CLAUDE_','CODEX_'))}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1',
                       GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat', GIT_OPTIONAL_LOCKS='0',
                       GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='core.hooksPath', GIT_CONFIG_VALUE_0=os.devnull,
                       GIT_CONFIG_KEY_1='core.fsmonitor', GIT_CONFIG_VALUE_1='false')
    return environment

def terminal_source(repository, *, session, run, platform):
    repository = safe(Path(repository).resolve())
    if platform not in {'codex','claude'} or not session or not run:
        raise ValueError('explicit terminal source identity required')
    safe(repository/('.'+platform)/'al-runtime'/(session+'.json'))
    captured = _context()
    before = _binding(captured)
    environment = query_environment()
    pin_context=capture_run_pin(captured,repository,session,run,platform)
    caller_environment=capture_environment() if pin_context else None
    request=dict(_request(captured),run_pin_context=pin_context,caller_environment=caller_environment)
    inventory_before=target_inventory(repository,environment)
    argv = [sys.executable,'-I','-S','-B','-c',CHILD,'--repository',str(repository),
            '--session',session,'--run',run,'--platform',platform]
    result = subprocess.run(argv, input=canonical(request), cwd=repository,
                            env=environment, capture_output=True, timeout=120)
    if len(result.stdout)>16*1024*1024 or len(result.stderr)>65536:
        raise ValueError('terminal validator output exceeded bounds')
    try:value = decode(result.stdout)
    except (ValueError,UnicodeError) as exc:raise ValueError('invalid terminal validator response') from exc
    if result.returncode or not isinstance(value,dict) or value.get('kind')!='terminal-workspace-source-proof':
        raise ValueError('terminal approval refused: '+str(value.get('error','validator failed') if isinstance(value,dict) else 'invalid response'))
    if (value.get('schema_version'),value.get('repository'),value.get('session_id'),value.get('run_id'),value.get('platform')) != (2,str(repository),session,run,platform):
        raise ValueError('terminal validator response identity mismatch')
    if any(value.get('validator',{}).get(key)!=item for key,item in before.items()):
        raise ValueError('terminal validator closure/origin response mismatch')
    _validate_proof(value,captured,platform,pin_context,caller_environment)
    if validator_binding()!=before:raise ValueError('trusted terminal validator changed during query')
    if capture_run_pin(captured,repository,session,run,platform)!=pin_context:
        raise ValueError('protected terminal run pin changed during query')
    if pin_context and capture_environment()!=caller_environment:
        raise ValueError('terminal caller environment changed during query')
    verify_read_set(value['validator'].get('read_set'))
    if target_inventory(repository,environment)!=inventory_before:
        raise ValueError('terminal query candidate/Git membership changed')
    verify_read_set(value['validator']['read_set'])
    return value


def _validate_proof(value,captured,platform,pin_context=None,caller_environment=None):
    if set(value)!={'schema_version','kind','repository','session_id','run_id','platform','state_sha256','approval','source','validator'}:
        raise ValueError('unknown terminal proof fields')
    for digest_value in (value['state_sha256'],value['source'].get('fingerprint'),value['approval'].get('task_hash')):
        if not isinstance(digest_value,str) or not re.fullmatch('[0-9a-f]{64}',digest_value):
            raise ValueError('invalid terminal proof digest')
    validate_runtime(value['validator'],captured,platform,pin_context)
    if value['validator'].get('caller_environment_digest')!=(digest(canonical(caller_environment)) if caller_environment else None):
        raise ValueError('terminal caller input proof mismatch')


def validate_runtime(validator,captured,platform,pin_context=None):
    origin=captured['origin'];loaded=validator.get('loaded_modules')
    if not isinstance(loaded,dict) or not loaded or digest(canonical(loaded))!=validator.get('loaded_module_origins_digest'):
        raise ValueError('invalid terminal loaded-module proof')
    for name,item in loaded.items():
        relative=origin['platforms'][platform]['modules'].get(name)
        if name=='installer':relative='installer/__init__.py'
        elif name.startswith('installer.'):relative='installer/'+name.split('.',1)[1]+'.py'
        if relative is None or item!={'origin':str(Path(captured['snapshot'])/relative),'sha256':digest(captured['bytes'][relative])}:
            raise ValueError('terminal loaded module outside closure')
    runner=origin['platforms'][platform]['resources']['al_architecture_check.py']
    identity=pin_context['resource_identities']['al_architecture_check.py'] if pin_context else str(Path(origin['source']['al_root'])/runner)
    expected={'al_architecture_check.py':{'identity':identity,'sha256':digest(captured['bytes'][runner])}}
    if validator.get('run_pin_context_digest')!=(digest(canonical(pin_context)) if pin_context else None):
        raise ValueError('terminal original pin data proof mismatch')
    if validator.get('platform')!=platform or validator.get('resource_bindings')!=expected:
        raise ValueError('terminal resource identity proof mismatch')
