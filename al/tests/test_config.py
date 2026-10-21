"""Focused configuration ownership and semantic preservation."""
import tomllib
import pytest
from al.installer.config import plan_config, rollback_desired


def present(value):
    return {'present': True, 'value': value}


def test_toml_comments_unrelated_and_shared_depth():
    raw = b'# preserve\nmodel = "custom"\n[agents]\nmax_depth = 1\n[agents.other]\nconfig_file = "other.toml"\n'
    desired = {'agents.max_depth': present(1), 'agents.al-engineer.config_file': present('/roles/engineer.toml')}
    one = plan_config(raw, {}, desired)
    assert 'agents.max_depth' not in one['next_owned_keys']
    assert b'# preserve' in one['postimage_bytes']
    updated = one['postimage_bytes'] + b'\n[custom]\nvalue = 42\n'
    desired['agents.al-engineer.config_file'] = present('/roles/v2.toml')
    two = plan_config(updated, one['next_owned_keys'], desired)
    assert tomllib.loads(two['postimage_bytes'].decode())['custom']['value'] == 42
    back = plan_config(two['postimage_bytes'], two['next_owned_keys'], rollback_desired(one['next_owned_keys']))
    assert b'/roles/engineer.toml' in back['postimage_bytes']
    assert b'value = 42' in back['postimage_bytes']


def test_conflict_drift_unsupported_and_duplicate_json():
    with pytest.raises(ValueError, match='conflict'):
        plan_config(b'[agents]\nmax_depth=5\n', {}, {'agents.max_depth': present(1)})
    one = plan_config(None, {}, {'agents.al-engineer.config_file': present('one')})
    with pytest.raises(ValueError, match='drift'):
        plan_config(one['postimage_bytes'].replace(b'one', b'edited'), one['next_owned_keys'], {})
    with pytest.raises(ValueError, match='unsupported'):
        plan_config(b'agents = { al = {config_file="one"} }\n', {}, {'agents.al.config_file': present('two')},
                    {'agents.al.config_file': {'reviewed': True, 'reason': 'explicit', 'before': present('one'), 'after': present('two')}})
    with pytest.raises(ValueError, match='duplicate'):
        plan_config(b'{"statusLine":null,"statusLine":{}}', {}, {}, format='json')


def test_retired_keys_and_reintroduction():
    one = plan_config(b'# user\n', {}, {'agents.al-a.config_file': present('one')})
    two = plan_config(one['postimage_bytes'], one['next_owned_keys'], {'agents.al-b.config_file': present('two')})
    assert not two['next_owned_keys']['agents.al-a.config_file']['active']
    back = plan_config(two['postimage_bytes'], two['next_owned_keys'], rollback_desired(one['next_owned_keys']))
    doc = tomllib.loads(back['postimage_bytes'].decode())
    assert doc['agents']['al-a']['config_file'] == 'one'
    assert 'config_file' not in doc['agents']['al-b']
    altered = two['postimage_bytes'].replace(b'[agents.al-a]', b'[agents.al-a]\nconfig_file="user"')
    with pytest.raises(ValueError, match='drift'):
        plan_config(altered, two['next_owned_keys'], rollback_desired(one['next_owned_keys']))


def test_json_null_is_not_absence_and_custom_statusline_preserved():
    with pytest.raises(ValueError, match='conflict'):
        plan_config(b'{"statusLine":null,"other":1}', {}, {'statusLine': present({'type': 'command', 'command': 'safe'})}, format='json')
    raw = b'{ "statusLine": {"command":"custom"}, "other": 1 }'
    assert plan_config(raw, {}, {}, format='json')['postimage_bytes'] == raw


def test_depth_type_and_released_key_edits():
    with pytest.raises(ValueError, match='conflict'):
        plan_config(b'[agents]\nmax_depth=true\n', {}, {'agents.max_depth': present(1)})
    one = plan_config(None, {}, {'agents.al-old.config_file': present('one')})
    retired = plan_config(one['postimage_bytes'], one['next_owned_keys'], {})
    edited = retired['postimage_bytes'] + b'\n[custom]\nvalue=7\n'
    edited = edited.replace(b'[agents.al-old]', b'[agents.al-old]\nconfig_file="now-user-owned"')
    no_op = plan_config(edited, retired['next_owned_keys'], {})
    assert no_op['postimage_bytes'] == edited
    with pytest.raises(ValueError, match='drift'):
        plan_config(edited, retired['next_owned_keys'], rollback_desired(one['next_owned_keys']))
