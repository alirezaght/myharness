"""Fault injection around real old public workflow, never positive authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from al.installer.validator_origin import canonical


def readiness(flow,**options):
    values=['--expected-manifest',flow.replacement_ref['sha256'],'--replan-proof',flow.replan,
            '--replan-proof-sha256',hashlib.sha256(flow.replan.read_bytes()).hexdigest()]
    return flow.proof('compatibility-readiness',*values,allow=True,**options)


def before_approval(flow):
    path=flow.runtime/(flow.session+'.json');original=path.read_bytes();index=(flow.repo/'.git/index').read_bytes()
    # The complete replan+actual checks already passed. These faults must never
    # make readiness/terminal eligibility by transplanting an older receipt.
    for kind in ('old-receipts','budget','task','base','missing-coverage','changed-signature','other-running-check','unresolved-writer'):
        state=json.loads(original)
        if kind=='old-receipts':
            first=state['gate_manifests'][0]['sha256'];latest=state['gate_manifests'][-1]['sha256']
            state['gate_runs']=[dict(row,manifest=latest) for row in state['gate_runs'] if row['manifest']==first]
        elif kind=='other-running-check':
            other=deepcopy(state['evidence']['entries'][-1]);other.update(id='E999',status='RUNNING')
            state['evidence']['entries'].append(other)
        elif kind=='unresolved-writer':state['writers']['old-engineer'].update(state='ACTIVE',outcome=None)
        elif kind=='budget':state['rework']['used']+=1
        elif kind=='task':
            ref=state['task_package'];task=json.loads(Path(ref['stored_path']).read_bytes());task['accepted_spec']+=' changed task'
            raw=canonical(task);sha=hashlib.sha256(raw).hexdigest();forged=Path(ref['stored_path']).with_name(sha)
            forged.write_bytes(raw);ref.update(id=sha,sha256=sha,stored_path=str(forged))
        elif kind=='base':state['identity']['merge_base']='0'*40
        else:
            ref=state['gate_manifests'][-1];manifest=json.loads(Path(ref['stored_path']).read_bytes())
            if kind=='missing-coverage':manifest.pop('service_coverage')
            else:
                gate=next(g for g in manifest['gates'] if g.get('execution_signature'));gate['execution_signature']='0'*64
            raw=canonical(manifest);sha=hashlib.sha256(raw).hexdigest();forged=Path(ref['stored_path']).with_name(sha)
            forged.write_bytes(raw);ref.update(id=sha,sha256=sha,stored_path=str(forged))
        injected=canonical(state);path.write_bytes(injected)
        try:
            refused=readiness(flow)
            assert refused.returncode!=0,kind+': readiness accepted a changed protected binding'
            assert path.read_bytes()==injected and (flow.repo/'.git/index').read_bytes()==index
        finally:path.write_bytes(original)
    source=flow.repo/'svc/app.py';body=source.read_bytes();source.write_text('VALUE = 3\n')
    try:
        refused=readiness(flow)
        assert refused.returncode!=0 and 'candidate' in refused.stdout.lower(),refused.stdout[-2000:]
    finally:source.write_bytes(body)
    binding_inside=flow.runtime/'target-selected-issuer.json';binding_inside.write_bytes(flow.binding_file.read_bytes())
    args=['--issuer-binding',binding_inside,'--issuer-binding-sha256',flow.binding_sha]
    refused=flow.candidate('replan-proof',*args,allow=True)
    assert refused.returncode!=0 and 'external' in refused.stdout,refused.stdout[-2000:]
    forged=deepcopy(flow.binding);forged['run']['run_id']='foreign'
    target=flow.artifacts/'foreign-issuer.json';target.write_bytes(canonical(forged))
    refused=flow.candidate('replan-proof','--issuer-binding',target,'--issuer-binding-sha256',hashlib.sha256(target.read_bytes()).hexdigest(),allow=True)
    assert refused.returncode!=0,'foreign issuer substitution accepted'
    # The actual public workflow is nonterminal here, independently of the
    # passing current-policy readiness check.
    refused=flow.candidate('source-proof',allow=True)
    assert refused.returncode!=0,'nonterminal current-policy readiness conferred approval'
    assert path.read_bytes()==original and (flow.repo/'.git/index').read_bytes()==index


def terminal_refusals(flow):
    state=flow.state();path=flow.runtime/(flow.session+'.json');original=path.read_bytes()
    index=(flow.repo/'.git/index').read_bytes()
    evidence=next(e for e in state['evidence']['entries'] if e['area']=='actual-pytest')
    targets={'stale-review':Path(state['review']['comparison']['stored_path']),
             'stale-evidence':Path(evidence['log']),'candidate':flow.repo/'svc/app.py',
             'issuer-capture':Path(flow.binding['root'])/'provenance.json'}
    for kind,target in targets.items():
        before=target.read_bytes()
        try:
            target.write_bytes(before+b'\n')
            if kind=='issuer-capture':result=readiness(flow)
            else:result=flow.candidate('source-proof',allow=True)
            assert result.returncode!=0,kind+': stale proof accepted'
            assert path.read_bytes()==original and (flow.repo/'.git/index').read_bytes()==index
        finally:target.write_bytes(before)
