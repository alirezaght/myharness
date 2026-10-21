"""One real registered check can exclude only its own unfinished reservation."""
import os
from pathlib import Path
import re
import sys
from .execution_process import process_arguments,observe
from .interpreter_execution import validate_live,validate_profile
from .issuer_provenance import verify_issuer
from .validator_origin import canonical,decode,digest,safe



def current_reservation(repository,session,run,platform,issuer,area):
    from .validator_origin import capture
    raw,_=capture(Path(issuer['root'])/'provenance.json');data=decode(raw)
    verify_issuer(issuer['root'],issuer)
    known=data['invocation']['argv']
    if data['schema_version']!=2:raise ValueError('readiness requires protected interpreter execution profile')
    profile=data['invocation'].get('execution_profile')
    validate_profile(profile,data['invocation']['interpreter_identity'])
    if area is None:
        validate_live(profile,data['invocation']['interpreter_identity'],observe(os.getpid()))
        return None
    root=safe(Path(repository).resolve());path=root/('.'+platform)/'al-runtime'/(session+'.json')
    state=decode(path.read_bytes())
    if state['session_id']!=session or state['run_id']!=run:raise ValueError('foreign readiness reservation run')
    candidates=[e for e in state['evidence']['entries'] if e['area']==area and e['status']=='RUNNING' and e.get('pid')==os.getpid()]
    if len(candidates)!=1:raise ValueError('exact running readiness reservation required')
    entry=candidates[0];spec=state['evidence']['plan'].get(area)
    if not spec or entry.get('run')!=run or entry.get('owner_pid')!=os.getppid() or entry.get('cwd')!='.' or entry.get('kind')!='check' or entry.get('tier')!='broad' or not re.fullmatch('[0-9a-f]{32}',entry.get('reservation','')):
        raise ValueError('readiness reservation lacks registered dispatcher identity')
    owner=observe(os.getppid());caller=observe(os.getpid())
    validate_live(profile,data['invocation']['interpreter_identity'],owner)
    validate_live(profile,data['invocation']['interpreter_identity'],caller)
    if (owner['cwd']!=str(root) or caller['cwd']!=str(root) or caller['parent_pid']!=os.getppid()):
        raise ValueError('readiness invocation kernel parent/cwd differs')
    owner_args=owner['argv'];caller_args=caller['argv']
    if owner_args[1:]!=known[1:]+['evidence','run','--area',area]:raise ValueError('readiness parent is not the known registered issuer dispatcher')
    actual=list(sys.argv)
    expected=[actual,[sys.executable,*actual]]
    if entry['argv'] not in expected:raise ValueError('readiness argv differs from actual invocation')
    if caller_args[1:] not in [entry['argv'][1:],['-I','-S','-B',*entry['argv']]]:
        raise ValueError('readiness kernel argv differs from reservation')
    from .validator_origin import canonical as serialize
    # Mirrors exact existing ledger command matching without importing a runtime.
    fields=('argv','cwd','scope','kind','tier','preflight','needs','sources')
    for key in fields:
        default=[] if key in {'needs','sources'} else None
        if entry.get(key,default)!=spec.get(key,default):raise ValueError('readiness registered command changed')
    if sorted(entry.get('env',[]))!=sorted(spec.get('env',[])):raise ValueError('readiness registered environment changed')
    if observe(os.getppid())!=owner or observe(os.getpid())!=caller:raise ValueError('readiness invocation identity changed')
    return {'entry_id':entry['id'],'reservation':entry['reservation'],'area':area,'entry_digest':digest(serialize(entry)),
            'pid':os.getpid(),'owner_pid':os.getppid(),'caller_identity':caller['identity'],'owner_identity':owner['identity'],'caller_observation':caller,'owner_observation':owner}


def exclude_self(state,receipt):
    if receipt is None:return
    if not isinstance(receipt,dict) or receipt.get('pid')!=os.getppid():raise ValueError('foreign readiness self reservation')
    entries=state['evidence']['entries'];matches=[e for e in entries if e['id']==receipt['entry_id']]
    if len(matches)!=1:raise ValueError('readiness self reservation missing')
    entry=matches[0]
    if entry['status']!='RUNNING' or entry.get('pid')!=receipt['pid'] or entry.get('owner_pid')!=receipt['owner_pid'] or entry.get('reservation')!=receipt['reservation'] or digest(canonical(entry))!=receipt['entry_digest']:
        raise ValueError('readiness self reservation changed')
    state['evidence']['entries']=[e for e in entries if e['id']!=entry['id']]
    state['evidence']['plan'].pop(entry['area'])
