"""Actual terminal/JSON dispatch preserves failures and current phase occurrence."""
import json
import pytest
import al_render as Render
import al_ledger as L
from evidence_helpers import start_implementation


def test_current_reentered_phase_is_active_and_uses_current_duration(cli,tmp_path,state_file,monkeypatch):
    start_implementation(cli,tmp_path)
    st=json.loads(state_file().read_bytes())
    st['completed_phases'].append('IMPLEMENTATION')
    st['phase_timings']['IMPLEMENTATION']={'started':1,'ended':4}
    st['phase_occurrences'].append({'phase':'IMPLEMENTATION','started':90,'ended':None,'completed':False})
    monkeypatch.setattr(Render.time,'time',lambda:100)
    panel=Render.render_panel(st)
    assert any(line.startswith('● Implementation') for line in panel.splitlines())
    assert Render.phase_elapsed(st,'IMPLEMENTATION')==10
    st.update(status='STOPPED_WITH_BLOCKERS',stop_reason='Provider authorization unavailable')
    assert 'Provider authorization unavailable' in Render.render_line(st)


@pytest.mark.parametrize('mode',[(),('--line',),('--panel',),('--spec',),('--models',),('--spawn','al-architect')])
def test_every_show_mode_dispatches_after_failure(cli,state_file,mode):
    assert cli('start','--spec','Render every presentation mode after a failed or blocked workflow.')[0]==0
    assert cli('stopped','--reason','Required provider is unavailable')[0]==0
    result=cli('show',*mode)
    if mode and mode[0]=='--spawn':
        assert result[0]!=0  # Claude routes native role definitions instead of Codex spawn args.
        return
    assert result[0]==0,result
    if not mode:assert json.loads(result[1])['status']=='STOPPED_WITH_BLOCKERS'
    elif mode[0] in {'--line','--panel'}:assert 'STOPPED_WITH_BLOCKERS' in result[1]
    before=state_file().read_bytes()
    assert cli('acceptance','status')[0]==0
    assert state_file().read_bytes()==before


@pytest.mark.parametrize('exit_code,counts,expected',[(0,{'failed':1},'FAIL'),(1,{'passed':2},'FAIL'),(127,{},'INFRA'),(0,{'passed':0},'EMPTY')])
def test_failed_result_classification_cannot_be_masked(exit_code,counts,expected):
    assert L.classify('test',exit_code,counts,False,'same','same')[0]==expected
