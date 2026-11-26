"""Explicit rollout and rendered-interface review obligations."""
import re
import posixpath
from pathlib import PurePosixPath
import al_proof_json as J
from al_schema import refuse
from al_ui_paths import rendered_path

ROLLOUT=('expand_contract','deployment_order','aggregator_environment','consumer_deployment','dlq_redrive')

def triggers(candidate):
    changed=[posixpath.normpath(p) for p in candidate.get('changed_paths',[])]
    alembic=any(len(parts:=PurePosixPath(p).parts)>=5 and parts[0]=='services' and parts[2:4]==('alembic','versions') for p in changed)
    diff=candidate['diff']
    rollout=alembic or any(re.search(r'(migration|terraform|infra/|\.tf$|\.env|event|pubsub|config)',p,re.I) for p in changed) or bool(re.search(r'^\+.*(os\.environ|process\.env|import\.meta\.env|Pub/Sub|pubsub)',diff,re.M))
    ui=any(rendered_path(p) for p in changed)
    return rollout,ui

def artifact_bytes(refs,label,required=False):
    if not isinstance(refs,list) or (required and not refs):refuse(label+' artifacts must be '+('a nonempty array' if required else 'an array'))
    return [J.reference(ref) for ref in refs]


def validate(report,candidate):
    rollout,ui=triggers(candidate)
    assessments=report.get('assessments')
    if not isinstance(assessments,dict): refuse('explicit rollout and UI assessments required')
    for name,applicable in [('rollout',rollout),('ui',ui)]:
        item=assessments.get(name)
        if not isinstance(item,dict) or not item.get('reason'): refuse(name+' assessment needs applicability rationale')
        if type(item.get('applicable')) is not bool or (applicable and not item['applicable']): refuse('required '+name+' assessment omitted')
        J.text(item['reason'],name+' assessment reason')
        if not item['applicable']:
            if name=='ui' and 'artifacts' in item:artifact_bytes(item['artifacts'],'rendered UI')
            if name=='rollout':
                for dimension in ROLLOUT:
                    d=item.get(dimension)
                    if isinstance(d,dict) and 'artifacts' in d:artifact_bytes(d['artifacts'],'rollout '+dimension)
            continue
        if name=='rollout':
            for dimension in ROLLOUT:
                d=item.get(dimension)
                if not isinstance(d,dict) or not d.get('reason') or d.get('status') not in {'EVIDENCED','NOT_APPLICABLE'}: refuse('missing rollout dimension '+dimension)
                J.text(d['reason'],'rollout '+dimension+' reason')
                if d['status']=='EVIDENCED':
                    artifact_bytes(d.get('artifacts'),'rollout '+dimension,required=True)
                elif 'artifacts' in d:artifact_bytes(d['artifacts'],'rollout '+dimension)
        else:
            if not item.get('browser') or not item.get('viewport') or not item.get('functional_checks') or not item.get('alignment_observations') or not item.get('ux_observations'): refuse('rendered UI metadata/checks missing')
            for raw in artifact_bytes(item.get('artifacts'),'rendered UI',required=True):
                if not raw: refuse('empty rendered artifact')
