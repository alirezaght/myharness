"""Explicit rollout and rendered-interface review obligations."""
import re
import posixpath
from pathlib import PurePosixPath
import al_artifacts as A
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

def validate(report,candidate):
    rollout,ui=triggers(candidate)
    assessments=report.get('assessments')
    if not isinstance(assessments,dict): refuse('explicit rollout and UI assessments required')
    for name,applicable in [('rollout',rollout),('ui',ui)]:
        item=assessments.get(name)
        if not isinstance(item,dict) or not item.get('reason'): refuse(name+' assessment needs applicability rationale')
        if type(item.get('applicable')) is not bool or (applicable and not item['applicable']): refuse('required '+name+' assessment omitted')
        if not item['applicable']: continue
        if name=='rollout':
            for dimension in ROLLOUT:
                d=item.get(dimension)
                if not isinstance(d,dict) or not d.get('reason') or d.get('status') not in {'EVIDENCED','NOT_APPLICABLE'}: refuse('missing rollout dimension '+dimension)
                if d['status']=='EVIDENCED':
                    if not d.get('artifacts'): refuse('rollout evidence missing '+dimension)
                    for ref in d['artifacts']: A.read(ref)
        else:
            if not item.get('browser') or not item.get('viewport') or not item.get('functional_checks') or not item.get('alignment_observations') or not item.get('ux_observations'): refuse('rendered UI metadata/checks missing')
            if not item.get('artifacts'): refuse('rendered screenshot/artifact required')
            for ref in item['artifacts']:
                raw=A.read(ref)
                if not raw: refuse('empty rendered artifact')
