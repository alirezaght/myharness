"""Operator update metadata invariants."""

import pytest

from al.tests.operator_update_helpers import (
    O,
    Path,
    _prefix_cache as _prefix_cache,
    artifact,
    enrolled as enrolled,
    inject,
    load_record,
    package_path,
    public,
    tag,
)


@pytest.mark.parametrize('name', ['record', 'source', 'ownership', 'sentinel', 'lock'])
@pytest.mark.parametrize('damage', ['hardlink', 'symlink', 'writable'])
def test_operator_installation_metadata_has_real_owned_no_alias_boundary(enrolled, name, damage):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    path = {'record': O.record_path(w['state'], target['target_id']),
            'source': next((w['state'] / 'installer/sources').glob('*.json')),
            'ownership': Path(row['current']['packages']['codex']['path']),
            'sentinel': Path(next(iter(row['current']['roles'].values()))['sentinel']['path']),
            'lock': w['state'] / 'installer/coordination.lock'}[name]
    if damage == 'hardlink': (w['root'] / 'metadata-alias').hardlink_to(path)
    elif damage == 'symlink':
        saved = w['root'] / 'metadata-saved'; path.rename(saved); path.symlink_to(saved)
    else: path.chmod(0o666)
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1, result.stdout + result.stderr
    assert any(word in result.data['error'] for word in ('metadata', 'symlink', 'ownership', 'alias'))
    assert not row['pending']
    # Reading unsafe metadata through normal APIs may itself refuse; resource payload stays old.
    assert (package_path(target, 'codex') / 'SKILL.md').read_bytes() == (
        Path(row['current']['cache']) / 'payload/codex/skills/al/SKILL.md').read_bytes()


def test_coordination_descriptor_and_legacy_held_callers_remain_compatible(tmp_path):
    from al.installer.registry import Held, coordination, namespace
    old = Held(namespace(tmp_path))
    assert old.fd is None
    with coordination(tmp_path) as held:
        assert type(held.fd) is int
        with coordination(tmp_path, held=held) as nested:
            assert nested is held
    assert held.active is False


def test_owned_metadata_rejects_a_foreign_owner_comparison(tmp_path, monkeypatch):
    from al.installer import operator_registry as G
    path = tmp_path / 'metadata'; path.write_text('fixture')
    uid = path.stat().st_uid
    monkeypatch.setattr(G.os, 'getuid', lambda: uid + 1)
    with pytest.raises(ValueError, match='ownership'):
        G.owned(path)


@pytest.mark.parametrize('point', ['before-lock', 'under-lock-replace', 'under-lock-alias'])
def test_real_lock_replacement_and_alias_races_refuse(enrolled, point):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    lock = str(w['state'] / 'installer/coordination.lock')
    if point == 'before-lock':
        inject(enrolled, [f"    if operator_registry is not None: Path({lock!r}).rename({lock + '.old'!r}); D.atomic(Path({lock!r}), b'', 0o644)"],
               '    with coordination(state_root) as held:\n', after=False)
    else:
        statement = (f"Path({lock!r}).rename({lock + '.old'!r}); D.atomic(Path({lock!r}), b'', 0o644)"
                     if point == 'under-lock-replace' else f"Path({lock + '.alias'!r}).hardlink_to(Path({lock!r}))")
        inject(enrolled, ["            if platform == 'codex': " + statement])
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1 and ('lock' in result.data['error'] or 'alias' in result.data['error'])
    current = load_record(w['state'], target['target_id'])
    assert current['current'] == row['current']
    assert bool(current['pending']) == (point != 'before-lock')
