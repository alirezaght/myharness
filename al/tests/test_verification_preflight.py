"""The preflight exercises actual owned processes and fails before costly work."""
import pytest
from al import verification_preflight as P


def test_real_owned_preflight_leaves_disposable_root_empty(tmp_path):
    result = P.check(temporary_root=tmp_path)
    assert result['status'] == 'PASS' and result['cleanup_complete']
    assert 'kernel-start-identity' in result['checks'] and 'owned-group-cancellation' in result['checks']
    assert 1 <= result['max_workers'] <= 8 and not list(tmp_path.iterdir())


def test_missing_tool_refuses_before_disposable_processes(tmp_path, monkeypatch):
    real = P.shutil.which
    monkeypatch.setattr(P.shutil, 'which', lambda name: None if name == 'uv' else real(name))
    with pytest.raises(ValueError, match='executables required'):
        P.check(temporary_root=tmp_path)
    assert not list(tmp_path.iterdir())


def test_disk_shortfall_refuses_without_launch(tmp_path):
    with pytest.raises(ValueError, match='disk space'):
        P.check(temporary_root=tmp_path, minimum_free_bytes=2 ** 63)
    assert not list(tmp_path.iterdir())


def test_uncertain_cleanup_retains_owned_identity_journal(tmp_path, monkeypatch):
    import json
    actual = P.OwnedProcess.cleanup;owners = []
    def uncertain(owner):
        if owner.assignment == 'preflight-cancel':
            owners.append(owner)
            return {'complete': False, 'errors': ['denied'], 'unresolved': [owner.record()], 'secondary_signals': []}
        return actual(owner)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(P.OwnedProcess, 'cleanup', uncertain)
            with pytest.raises(ValueError, match='retained='):
                P.check(temporary_root=tmp_path)
        journal = next(tmp_path.glob('*/cancel/journal.json'))
        value = json.loads(journal.read_bytes())
        assert value['status'] == 'PENDING' and value['owner']['identity'] and value['owner']['token']
        assert owners[0].process.poll() is None
    finally:
        for owner in owners:assert actual(owner)['complete']
