"""The membership listing observes each path once per listing with unchanged results (#25)."""
import os
import subprocess

import pytest

from al_repository_capture import CaptureDrift
from al_repository_facts import Snapshot


def _git(repo, *args):
    subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=repo, check=True,
                   capture_output=True)


def test_a_path_listed_by_several_sources_keeps_every_exclusion_record(repo, monkeypatch):
    (repo / 'dist').mkdir()
    (repo / 'dist/out.py').write_text('x = 1\n')
    _git(repo, 'add', '-f', 'dist/out.py')
    _git(repo, 'commit', '-qm', 'tracked generated-looking file')
    base = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True).stdout.strip()
    stats = []
    real = os.stat
    monkeypatch.setattr(os, 'stat', lambda p, *a, **k: stats.append(str(p)) or real(p, *a, **k))
    scan = Snapshot(repo, base, ['dist/out.py'], [], listing_only=True)
    records = [e for e in scan.listing_observation['exclusions'] if e['path'] == 'dist/out.py']
    # Tracked, base and explicit scope each list it: one record per listing source, as before.
    assert len(records) == 3 and all(r == records[0] for r in records)
    assert 'dist/out.py' in scan.members
    # One stat per path and protection, however many sources list it.
    assert stats.count(str(repo / 'dist/out.py')) <= 2


def test_revalidation_still_refuses_membership_change(repo):
    scan = Snapshot(repo, None, ['svc/app.py'], [])
    scan.observe_matches('svc/*.py', purpose='test')
    scan.revalidate()
    (repo / 'svc/late.py').write_text('late = 1\n')
    with pytest.raises(CaptureDrift):
        scan.revalidate()
