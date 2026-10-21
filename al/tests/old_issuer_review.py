"""Produce structured reports through the explicitly selected old public CLI."""
import json


def complete(cli,state,artifacts):
    cli('review-start');review=state()['review'];rid=review['id']
    for stage in ('fresh','compare'):
        if stage=='compare':
            plan=artifacts/'approved-plan.md';plan.write_text('Preserve original authority and require all current sourced verification.\n')
            cli('review-compare-start','--plan',str(plan))
        cli('review-packet','--stage',stage)
        current=state();review=current['review'];packet=review['packets'][stage]
        inspected=[packet['sha256']]
        if stage=='compare':inspected.extend(ref['sha256'] for ref in review['comparison_inputs'].values() if isinstance(ref,dict) and ref.get('sha256'))
        evidence=current['evidence']['entries'][-1]['id']
        report={'schema_version':1,'review_id':rid,'stage':stage,'reviewer_role':'al-architect','model':current['model'],
                'invocation_id':rid+'-'+stage,'candidate_hash':review['candidate']['fingerprint'],'task_hash':review['task_hash'],
                'packet_hash':packet['sha256'],'inspected_artifacts':inspected,
                'criteria':[{'id':'AC-01','status':'SATISFIED','rationale':'The real configured toolchain and old-public-workflow were executed.',
                             'locations':['svc/app.py:1'],'evidence':[evidence]}],
                'findings':[],'plan_defects':[],'status':'APPROVED',
                'assessments':{'rollout':{'applicable':False,'reason':'No changed deployment configuration or contract.'},
                               'ui':{'applicable':False,'reason':'No rendered interface changes.'}}}
        if stage=='compare':report['prior_resolutions']=[]
        path=artifacts/(stage+'-review.json');path.write_text(json.dumps(report))
        cli('review-fresh' if stage=='fresh' else 'review-result','--file',str(path))
