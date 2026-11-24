"""The candidate capture shared by every AL command uses the snapshot tree exactly (#25)."""
import json

import pytest

import al_analysis_cache as C
import al_scope as S
from conftest import _git
from test_service_coverage import put
from test_snapshot_capture import _fixture as _capture_fixture


def _fixture(repo):
    # The candidate capture refuses an untracked nested repository by design.
    import shutil
    _capture_fixture(repo)
    shutil.rmtree(repo / 'services/nested')


def _rows(repo):
    base = S.git_bytes('rev-parse', 'HEAD', root=repo).decode().strip()
    return json.dumps(S._records(base, repo), sort_keys=True)


def _direct(repo, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        patch.setattr(S, '_DIGESTS', {})
        return _rows(repo)


@pytest.fixture
def counted(monkeypatch):
    """Vouch for files the test just wrote, and record which files are hashed from disk."""
    monkeypatch.setattr(S, '_DIGEST_RACY_NS', -10**12)
    monkeypatch.setattr(S, '_DIGESTS', {})
    hashed = []
    real = S._file_digest
    monkeypatch.setattr(S, '_file_digest', lambda p: hashed.append(p.as_posix()) or real(p))
    return hashed


def test_records_equal_direct_hashing_on_every_membership_case(repo, monkeypatch, counted):
    _fixture(repo)
    put(repo, 'services/b/src/app.py', 'import common\nEDIT = 1\n')       # uncommitted
    put(repo, 'services/b/src/untracked.py', 'UNTRACKED = 1\n')           # untracked
    put(repo, 'services/a/src/kept.log', 'tracked but ignored, edited\n')  # tracked-but-ignored
    _git(repo, 'update-index', '--assume-unchanged', 'services/c/src/app.py')
    _git(repo, 'update-index', '--skip-worktree', 'packages/shared/src/common.py')
    put(repo, 'services/c/src/app.py', 'import commoN\n')                 # hidden from git status
    put(repo, 'packages/shared/src/common.py', 'VALUE=9\n')
    vouched = _rows(repo)
    assert vouched == _direct(repo, monkeypatch)
    # Unchanged tracked files are vouched for by the snapshot tree; changed, untracked and
    # flagged files are hashed from the disk.
    counted.clear()
    _rows(repo)
    hashed = {p.split('/repo/', 1)[1] for p in counted}
    assert 'packages/shared/pyproject.toml' not in hashed and 'services/a/src/app.py' not in hashed
    assert {'services/b/src/app.py', 'services/b/src/untracked.py', 'services/c/src/app.py',
            'packages/shared/src/common.py'} <= hashed


def test_converted_and_recent_files_are_hashed_from_disk(repo, monkeypatch):
    _fixture(repo)
    put(repo, '.gitattributes', '*.py text eol=crlf\n')
    _git(repo, 'add', '.gitattributes')
    _git(repo, 'commit', '-qm', 'attributes')
    (repo / 'services/c/src/app.py').write_bytes(b'import common\r\n')
    monkeypatch.setattr(S, '_DIGESTS', {})
    hashed = []
    real = S._file_digest
    monkeypatch.setattr(S, '_file_digest', lambda p: hashed.append(p.name) or real(p))
    assert _rows(repo) == _direct(repo, monkeypatch)
    assert 'app.py' in hashed  # recent, and converted


def test_read_only_inspection_never_writes_git_objects(repo, monkeypatch, counted):
    _fixture(repo)
    import al_snapshot
    calls = []
    monkeypatch.setattr(al_snapshot, 'snapshot', lambda root=None: calls.append(root) or 'x')
    with C.ObservationContext({'inspection': 1}, readonly_git=True):
        _rows(repo)
    assert calls == []
