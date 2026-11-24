"""Membership listings served from the resident service's change set (#35).

A fake change set stands in for the service's watcher: the test names every path it touches.
A listing is served only when every change since its token is a content edit of a listed member
file and the Git index, configuration and exclude file are unchanged; every served inventory is
compared byte for byte with a cold, cache-disabled one."""
import json
import os

import pytest

import al_gate_inventory as V
import al_repository_facts as F
import al_snapshot
from conftest import _git
from test_discovery_reuse import _fixture
from test_service_coverage import put, start

PATHS = ['services/a/src/app.py', 'packages/shared/src/common.py']


class FakeService:
    def __init__(self, repo):
        self.root = os.path.realpath(str(repo))
        self.events, self.answer = [], True

    def touch(self, *paths):
        self.events.extend(os.path.join(self.root, p) for p in paths)

    def __call__(self, root, since):
        if not self.answer or os.path.realpath(root) != self.root:
            return None
        now = 'e:' + str(len(self.events))
        if not since:
            return {'token': now, 'paths': []}
        number = int(since.split(':')[1])
        return {'token': now, 'paths': sorted(set(self.events[number:]))}


@pytest.fixture
def service(repo, monkeypatch):
    fake = FakeService(repo)
    monkeypatch.setattr(al_snapshot, '_SERVICE_CHANGES', fake)
    listings = []
    original = F.Snapshot._observe_listing

    def counted(self):
        listings.append(1)
        return original(self)

    monkeypatch.setattr(F.Snapshot, '_observe_listing', counted)
    fake.listings = listings
    return fake


def _same(a, b):
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _cold(monkeypatch, st):
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        patch.setattr(al_snapshot, '_SERVICE_CHANGES', None)
        return V.inventory(st, PATHS)


def test_a_content_edit_is_inventoried_without_listing_the_repository(cli, repo, monkeypatch, service):
    _fixture(repo)
    st = start(cli)
    V.inventory(st, PATHS)
    # The first capture lists once; its end-of-capture listing is served.
    assert len(service.listings) == 1
    service.listings.clear()
    put(repo, 'services/a/src/app.py', 'import common\nVALUE = 2\n')
    service.touch('services/a/src/app.py')
    warm = V.inventory(st, PATHS)
    assert service.listings == [], 'verification, capture and revalidation are all served'
    _same(warm, _cold(monkeypatch, st))
    # Unchanged since: the recorded answer, still without a listing.
    service.listings.clear()
    assert V.inventory(st, PATHS) == warm and service.listings == []


@pytest.mark.parametrize('change', ['added', 'ignore', 'declaration', 'index', 'unknown', 'outside'])
def test_anything_but_a_member_edit_lists_again(cli, repo, monkeypatch, service, change):
    _fixture(repo)
    put(repo, '.gitignore', '*.log\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'ignore rules')
    st = start(cli)
    V.inventory(st, PATHS)
    service.listings.clear()
    if change == 'added':
        put(repo, 'services/a/src/new.py', 'VALUE = 3\n')
        service.touch('services/a/src/new.py')
    elif change == 'ignore':
        put(repo, '.gitignore', '*.log\n*.tmp\n')
        service.touch('.gitignore')
    elif change == 'declaration':
        put(repo, 'services/b/pyproject.toml', '[project]\nname="b"\ndependencies=["shared"]\n')
        service.touch('services/b/pyproject.toml')
    elif change == 'index':
        # Staging changes what `ls-files --cached` reads, without any working-copy event.
        put(repo, 'services/a/src/new.py', 'VALUE = 3\n')
        _git(repo, 'add', 'services/a/src/new.py')
        service.events.clear()
    elif change == 'unknown':
        service.answer = False
    else:
        put(repo, 'docs/scratch.log', 'ignored output\n')
        service.touch('docs/scratch.log')
    warm = V.inventory(st, PATHS)
    assert service.listings, change
    _same(warm, _cold(monkeypatch, st))


def test_a_listing_reading_generated_directories_is_never_served(repo, service):
    put(repo, 'dist/bundle.js', 'x\n')
    _git(repo, 'add', '-f', 'dist/bundle.js')
    scan = F.Snapshot(repo, None, ['dist'], set(), listing_only=True)
    # A tracked member under a generated directory: the watcher does not report changes there.
    assert any(e['reason'] == F.PROTECTED_OVERRIDE for e in scan.listing_observation['exclusions'])
    assert F.token_of(scan.listing_observation) is None
