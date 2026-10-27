"""Protected planning export is explicit, fresh, read-only and non-authorizing."""

import json

import pytest

from al.tests.editorial_receipt_helpers import prepare_readers, state_path, succeed
from al.tests.runtime_run_helpers import invoke, launched


@pytest.mark.parametrize('platform',['codex','claude'])
def test_planning_source_proof_preserves_state_and_refuses_source_drift(launched, platform):
    st=prepare_readers(launched,platform,duplicate=False)
    path=state_path(launched,platform)
    before=path.read_bytes()
    def query(run=st['run_id']):
        return invoke([launched[3],'planning-proof','--repository',launched[2],
                       '--platform',platform,'--session',st['session_id'],'--run',run],
                      launched[2],launched[4])
    result=json.loads(succeed(query()).stdout)
    assert result['kind']=='planning-workspace-source-proof' and result['schema_version']==2
    assert result['planning']['report']['content']['kind']=='architect-planning-report-v1'
    assert result['authority']=='REFERENCE_ONLY_CURRENT_HARNESS_ARCHITECT_REVALIDATION_REQUIRED'
    assert result['validator']['loaded_modules'] and result['validator']['read_set']
    assert result['task_package']['sha256']==st['task_package']['sha256']
    assert path.read_bytes()==before
    assert query('00000000').returncode!=0 and path.read_bytes()==before
    (launched[2]/'rows.json').write_text('{"rows":[3]}\n')
    assert query().returncode!=0 and path.read_bytes()==before
