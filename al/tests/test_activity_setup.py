"""Installer activity cannot hide a dependency setup child behind a finished hook."""

import pytest

from al.installer.activity_operations import check_blockers, setup_checks


def setup(**changes):
    row={'schema_version':1,'kind':'check-setup-v1','manager':'uv','argv':['uv','sync','--locked'],
         'cwd':'.','inputs':[],'stage':'setup','status':'INFRA','started_at':10,'hook':'NOT_RUN',
         'pid':123,'ended_at':20,'exit':-9,
         'cleanup':{'complete':False,'errors':[]}}
    row.update(changes)
    return [{'id':'E1','status':'INFRA','setup':[row]}]


def test_finished_gate_does_not_hide_uncertain_setup_child():
    check=setup_checks(setup())[0]
    assert check['pid']==123 and 'cleanup' in ' '.join(check_blockers(check))
    assert setup_checks(setup(status='RUNNING'))[0]['status']=='RUNNING'


def test_completed_setup_remains_an_observable_child_without_cleanup_blocker():
    check=setup_checks(setup(status='PASS',exit=0,
                             cleanup={'complete':True,'errors':[],
                                      'unresolved':False,'group_absent':True}))[0]
    assert check['pid']==123 and check['ended_at']==20 and not check_blockers(check)


@pytest.mark.parametrize('changes',[{'background_lease':{'pid':456}}, {'pid':True},
                                   {'cleanup':{'complete':1,'errors':[]}}])
def test_unknown_or_ambiguous_setup_metadata_is_refused(changes):
    with pytest.raises(ValueError):setup_checks(setup(**changes))
