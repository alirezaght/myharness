"""Canonical replay containment must accept aliases and reject real escapes."""
import sys
from pathlib import Path
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import al_replay


@pytest.mark.parametrize('parent_alias',[False,True])
def test_fixture_write_accepts_root_alias_without_changing_containment(tmp_path,parent_alias):
    real=tmp_path/'real';real.mkdir();(real/'repo').mkdir()
    alias=tmp_path/'alias';alias.symlink_to(real/'repo' if not parent_alias else real,target_is_directory=True)
    root=alias/'repo' if parent_alias else alias
    al_replay.write_fixture(root,'wait.json',{'reason':'Resume only after verification'})
    assert (real/'repo/wait.json').read_text()=='{"reason": "Resume only after verification"}'


@pytest.mark.parametrize('absolute',[False,True])
def test_fixture_write_refuses_paths_outside_canonical_root(tmp_path,absolute):
    root=tmp_path/'replay-root';root.mkdir();escaped=tmp_path/'escaped.json'
    name=str(escaped) if absolute else '../escaped.json'
    with pytest.raises(ValueError,match='unsafe replay fixture path'):
        al_replay.write_fixture(root,name,'escape')
    assert not escaped.exists()


def test_fixture_write_refuses_symlink_directory_escape(tmp_path):
    root=tmp_path/'replay-root';root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (root/'link').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='unsafe replay fixture path'):
        al_replay.write_fixture(root,'link/escaped.json','escape')
    assert not (outside/'escaped.json').exists()


def test_fixture_write_does_not_overwrite_external_symlink_target(tmp_path):
    root=tmp_path/'replay-root';root.mkdir();outside=tmp_path/'external.json';outside.write_text('preserve external content')
    (root/'link.json').symlink_to(outside)
    with pytest.raises(ValueError,match='unsafe replay fixture path'):
        al_replay.write_fixture(root,'link.json','overwrite')
    assert outside.read_text()=='preserve external content'
