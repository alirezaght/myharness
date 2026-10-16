"""Isolation shares effective-parent overlay while preserving ordinary staging."""
import json
import pytest
import al_scope as S
from evidence_helpers import git
from gitlink_helpers import gitlink,commit_child
from hook_regression_helpers import invoke


def test_isolation_uses_effective_pointer_without_copying_child_and_preserves_ordinary_index(cli,repo,tmp_path,gitlink):
    path,_,_=gitlink
    invoke(cli,'start','--spec','Isolate effective parent entries without recreating child contents or ordinary staging.')
    b=commit_child(path,'B');git(repo,'add','svc/vendor');c=commit_child(path,'C')
    (repo/'svc/app.py').write_text('ordinary unstaged')
    destination=tmp_path/'isolation'
    invoke(cli,'isolate','--authorized','--path',str(destination))
    assert S.git_bytes('ls-files','--stage','svc/vendor',root=destination).decode().startswith('160000 '+c+' ')
    assert not list((destination/'svc/vendor').iterdir())
    assert (destination/'svc/app.py').read_text()=='ordinary unstaged'
    assert S.git_bytes('show',':svc/app.py',root=destination)==b'x = 1\n'
    assert S.git_bytes('ls-files','--stage','svc/vendor').decode().startswith('160000 '+b+' ')


@pytest.mark.parametrize('kind',['dirty','inspection-failed'])
def test_uncaptured_child_isolation_refuses_before_destination_creation(cli,repo,tmp_path,gitlink,kind):
    path,_,_=gitlink
    invoke(cli,'start','--spec','Reject isolation before creation when child work cannot be captured.')
    if kind=='dirty':(path/'file').write_text('dirty')
    else:(path/'.git').write_text('gitdir: missing\n')
    destination=tmp_path/'never'
    result=cli('isolate','--authorized','--path',str(destination))
    assert result[0]!=0 and 'uncaptured child' in str(result) and not destination.exists()
