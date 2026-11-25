"""A planned edit to a rule file refreshes the gate manifest instead of demanding a replan."""
import json
from hook_regression_helpers import invoke
from batch5_helpers import state
from launcher_context import admit_planning


def implementing(cli,tmp,paths):
    invoke(cli,'start','--spec','Edit the planned project file without replanning.')
    invoke(cli,'spec-gate','SPEC_READY')
    invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    file=tmp/'paths.json';file.write_text(json.dumps(paths))
    invoke(cli,'gates','discover','--paths-file',str(file))
    invoke(cli,'gates','run','--stage','base')
    admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','first','--role','al-engineer','--files',','.join(paths))


def test_planned_rule_file_edit_refreshes_and_keeps_base_runs(cli,tmp_path,repo):
    implementing(cli,tmp_path,['svc/app.py','pyproject.toml'])
    before=state(cli);old=before['gate_manifests'][-1]['sha256']
    base={r['gate']:r['evidence'] for r in before.get('gate_runs',[]) if r['stage']=='base' and r['manifest']==old}
    (repo/'pyproject.toml').write_text((repo/'pyproject.toml').read_text()+'# planned edit\n')
    # Writer finish refreshes the planned rule edit once no writer is editing (issue #32).
    invoke(cli,'writer','finish','--id','first','--outcome','success')
    invoke(cli,'writer','start','--id','second','--role','al-engineer','--files','svc/app.py')
    s=state(cli)
    assert s['writers']['second']['state']=='ACTIVE'
    assert 'source rule changed: pyproject.toml' not in s['implementation_readiness']['obligations']
    new=s['gate_manifests'][-1]['sha256']
    assert new!=old and 'gate_recovery' not in s
    refresh=s['gate_refreshes'][-1]
    assert refresh['superseded']==old and refresh['manifest']==new
    assert any('pyproject.toml' in reason for reason in refresh['reasons'])
    carried={r['gate']:r['evidence'] for r in s['gate_runs'] if r['stage']=='base' and r['manifest']==new}
    assert carried and all(base[g]==e for g,e in carried.items())
    import al_gates
    assert not al_gates.base_gaps(s)


def test_unplanned_rule_source_still_needs_a_replan(cli,tmp_path,repo,state_file):
    implementing(cli,tmp_path,['svc/app.py'])
    invoke(cli,'writer','finish','--id','first','--outcome','success')
    (repo/'AGENTS.md').write_text('New repository instructions.\n')
    invoke(cli,'writer','start','--id','second','--role','al-engineer','--files','svc/app.py')
    current=state(cli)
    assert 'new repository rule source: AGENTS.md' in current['implementation_readiness']['obligations']
    assert 'gate_refreshes' not in current
    invoke(cli,'writer','finish','--id','second','--outcome','success')
    invoke(cli,'phase','VERIFICATION')
    before=state_file().read_bytes()
    out=invoke(cli,'review-start',ok=False)
    assert 'new repository rule source: AGENTS.md' in ''.join(out)
    assert state_file().read_bytes()==before and 'gate_refreshes' not in state(cli)


def test_gate_plan_warns_about_a_source_that_is_not_a_rule_file(cli,tmp_path,repo):
    """A hand-planned gate citing an ordinary file is admitted with a warning naming it (#24)."""
    import sys
    from hook_regression_helpers import begin
    from test_hook_batch6 import _manifest,_source
    begin(cli,tmp_path,repo)
    argv=[sys.executable,'-c','print("ok")']
    file=tmp_path/'foreign.json'
    file.write_text(json.dumps(_manifest(state(cli),source=_source(repo,'svc/app.py'),active='loc',argv=argv)))
    _,_,err=invoke(cli,'gates','plan','--file',str(file))
    assert ('warning: gate loc cites svc/app.py, which is not a repository rule file; '
            'review admission keeps it only while that file is unchanged') in err
    assert err.count('warning: gate ')==1
    (repo/'Makefile').write_text('loc:\n\ttrue\n')
    file.write_text(json.dumps(_manifest(state(cli),rule=_source(repo,'Makefile'),active='loc',argv=argv)))
    _,_,err=invoke(cli,'gates','plan','--file',str(file))
    assert 'warning: gate ' not in err


def test_recognised_rule_files():
    import al_gate_currency as C
    for path in ('AGENTS.md','svc/CLAUDE.md','pyproject.toml','svc/go.mod','Makefile','.gitlab-ci.yml',
                 '.github/workflows/quality.yml','.pre-commit-config.yaml','tox.ini','Jenkinsfile'):
        assert C.recognised_rule_file(path),path
    for path in ('svc/app.py','README.md','docs/guide.md'):
        assert not C.recognised_rule_file(path),path
    manifest={'gates':[{'id':'a','source':{'path':'svc/app.py','sha256':'1'*64}},
                       {'id':'b','source':{'path':'Makefile','sha256':'1'*64}},
                       {'id':'c','source':{'path':'.gitlab-ci.yml','sha256':'0'*64}},{'id':'d','source':None}]}
    assert C.foreign_gate_sources(manifest)==[('a','svc/app.py')]
