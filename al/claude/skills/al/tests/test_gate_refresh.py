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
    invoke(cli,'writer','finish','--id','first','--outcome','success')
    invoke(cli,'writer','start','--id','second','--role','al-engineer','--files','svc/app.py')
    s=state(cli);new=s['gate_manifests'][-1]['sha256']
    assert new!=old and 'gate_recovery' not in s and s['writers']['second']['state']=='ACTIVE'
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
    before=state_file().read_bytes()
    out=invoke(cli,'writer','start','--id','second','--role','al-engineer','--files','svc/app.py',ok=False)
    assert 'new repository rule source: AGENTS.md' in ''.join(out)
    assert state_file().read_bytes()==before and 'gate_refreshes' not in state(cli)
