"""Nested dependency operations and argv boundaries preserve safety and literals."""
import os
import pytest
import al_shell
import al_store
import al_writers as W
from hook_regression_helpers import invoke
from batch13_helpers import start
from batch16_helpers import setup, state

DENIED=['uv pip '+op+' requests'+suffix for op in ['install','sync','uninstall'] for suffix in ['', ' --dry-run',' --check']]+[
 'uv pip','uv pip unknown','uv pip list --unknown','uv pip list --python','uv pip show',
 'uv pip freeze install','uv pip check --dry-run','uv run uv pip install requests',
 'env X=value uv --directory workspace pip install requests',
]
SAFE=['uv pip list','uv pip freeze','uv pip check','uv pip show requests install sync',
 'uv pip list --format=json','uv pip list --exclude install','uv pip show --python python3 requests',
 'uv run -- rg "uv pip install > cp --fix" docs/query.txt',
 'uv --directory install run pytest -q','rg "uv pip uninstall --dry-run" .']


@pytest.mark.parametrize('command',DENIED)
def test_uv_pip_mutations_and_unknown_forms_refuse_both_policies_and_ownership(cli,repo,tmp_path,command):
    start(cli,repo)
    for policy in ['readonly','shared']:assert al_shell.check(command,policy)
    code,_,_=invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/peer','--engineer-commands',command,ok=False)
    assert code!=0


@pytest.mark.parametrize('command',SAFE)
def test_observations_and_quoted_command_data_remain_safe(command):
    for policy in ['readonly','shared']:assert al_shell.check(command,policy)==[]


def overlap_state(cli,repo,tmp):
    setup(cli,repo,tmp,['svc/owned.py'])
    st=state(cli)
    st['evidence']['entries'].append({'id':'active','status':'RUNNING','owner_pid':os.getpid(),'argv':['rg','git reset > cp --fix','docs/query.txt'],'scope':['docs/query.txt']})
    return st


def test_writer_and_check_admission_preserve_quoted_argv_in_both_directions(cli,repo,tmp_path,monkeypatch):
    from argparse import Namespace
    st=overlap_state(cli,repo,tmp_path)
    monkeypatch.setattr(al_store,'load',lambda sid:st)
    saved=[];monkeypatch.setattr(al_store,'save',lambda sid,data:saved.append(data))
    W.cmd_writer(Namespace(action='start',id='writer',role='al-builder',files='svc/owned.py',commands_file=None),'test')
    assert saved and st['writers']['writer']['state']=='ACTIVE'
    base_entries=[entry for entry in st['evidence']['entries'] if entry['id']!='active']
    W.admit_check(st,{'argv':['rg','git reset > cp --fix','docs/query.txt'],'scope':['docs/query.txt'],'cwd':'.','env':[]})
    for argv in [['sh','-c','echo value > output'],['uv','pip','install','requests']]:
        with pytest.raises(SystemExit):W.admit_check(st,{'argv':argv,'scope':['docs/query.txt'],'cwd':'.','env':[]})
    st['writers']={};st['evidence']['entries']=base_entries+[{'id':'mutation','status':'RUNNING','argv':['sh','-c','echo value > output'],'scope':['docs/query.txt']}]
    with pytest.raises(SystemExit):W.cmd_writer(Namespace(action='start',id='blocked',role='al-builder',files='svc/owned.py',commands_file=None),'test')
