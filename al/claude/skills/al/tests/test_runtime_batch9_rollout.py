"""Selected revision deletions and complete five-dimension public review barriers."""
import copy
import json
import pytest
import al_assessments as R
from batch9_helpers import PATH,DIMENSIONS,review_candidate
from hook_regression_helpers import invoke


@pytest.mark.parametrize('path',[
 PATH,'./services/bid-service/alembic/versions/003_next.py',
 'services/bid-service/alembic/./versions/003_next.py',
])
def test_selected_alembic_paths_trigger_even_without_surviving_file_rows(path):
    # A deleted or previously committed revision can be absent from surviving rows.
    assert R.triggers({'files':[],'changed_paths':[path],'diff':''})==(True,False)


@pytest.mark.parametrize('path',[
 'services/bid-service/src/versions/data.py',
 'services/bid-service/alembic/versions_notes/readme.py',
 'services/bid-service/alembic/versions/../readme.py',
 'docs/services/bid-service/alembic/versions/example.py',
 'services/bid-service/src/handler.py',
])
def test_unrelated_versions_names_do_not_force_rollout(path):
    assert R.triggers({'files':[{'path':path}],'changed_paths':[path],'diff':''})==(False,False)


@pytest.mark.parametrize('deleted',[False,True])
def test_public_review_rejects_na_or_missing_dimensions_then_accepts_complete_report(cli,repo,tmp_path,state_file,deleted):
    complete=review_candidate(cli,repo,tmp_path,deleted)
    candidate=json.loads(cli('show')[1])['review']['candidate'];assert PATH in candidate['changed_paths']
    file=tmp_path/'rollout-comparison.json'
    omitted=copy.deepcopy(complete);omitted['assessments']['rollout']={'applicable':False,'reason':'Incorrectly omitted migration review'}
    reports=[omitted]
    for name in DIMENSIONS:
        report=copy.deepcopy(complete);del report['assessments']['rollout'][name];reports.append(report)
    for report in reports:
        file.write_text(json.dumps(report));before=state_file().read_bytes()
        invoke(cli,'review-result','--file',str(file),ok=False);assert state_file().read_bytes()==before
    file.write_text(json.dumps(complete));invoke(cli,'review-result','--file',str(file))
    invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved')
