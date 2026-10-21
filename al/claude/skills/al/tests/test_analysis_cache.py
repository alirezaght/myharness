"""Derived-cache hits preserve fresh observation and admission semantics."""
import copy
import hashlib
import os

import al_analysis_cache as C
import al_repository_facts as F
import al_scope as S
import pytest


def facts(repo):
    return F.collect(repo, base_oid=S.git_bytes('rev-parse', 'HEAD').decode().strip(), paths=['svc/app.py'], opaque=set())


def test_bounded_defensive_cache_and_corrupt_entries():
    cache = C.AnalysisCache(max_entries=2, max_bytes=256)
    value = cache.get_or_compute('x', ['one'], lambda: {'items': [1]})
    value['items'].append(2)
    assert cache.get_or_compute('x', ['one'], lambda: None) == {'items': [1]}
    cache.entries[next(iter(cache.entries))]['value'] = {'forged': True}
    assert cache.get_or_compute('x', ['one'], lambda: {'fresh': True}) == {'fresh': True}
    for key in range(10):
        cache.put('x', key, {'value': key})
    assert len(cache.entries) <= 2 and cache.bytes <= 256
    cache.put('x', 'large', 'x' * 500)
    assert not cache.get('x', 'large')[0]


def test_disabled_and_warm_facts_equal_with_defensive_graph_results(repo, monkeypatch):
    C.CACHE.clear()
    cold = facts(repo)
    warm = facts(repo)
    assert cold == warm and C.CACHE.hits > 0
    warm['dependencies']['known_affected'].append('forged')
    assert facts(repo) == cold
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    assert facts(repo) == cold


def test_same_mtime_edit_new_and_deleted_sources_invalidate(repo):
    before = facts(repo)
    path = repo / 'svc/app.py';stat = path.stat()
    path.write_text('import importlib\nimportlib.import_module(target)\n')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    changed = facts(repo)
    assert changed['dependencies']['uncertainties'] and changed != before
    extra = repo / 'svc/new.py';extra.write_text('import app\n')
    assert facts(repo)['dependencies']['source_membership'] != changed['dependencies']['source_membership']
    extra.unlink()
    assert facts(repo) == changed


def test_scan_stability_is_required_even_on_cache_hits(repo, monkeypatch):
    facts(repo)
    monkeypatch.setattr(F.Snapshot, 'stable', lambda self: False)
    assert facts(repo)['scan']['stable'] is False


def test_graph_cache_replays_gap_and_consumed_policy_deltas(repo):
    from al_python_imports import analyze
    base = facts(repo)
    scan = F.Snapshot(repo, base['repository']['base_oid'], ['svc/new.js'], set())
    original = {k: copy.deepcopy(v) for k, v in base.items() if k not in {'dependencies', 'scan', 'rule_sources', 'scan_inputs'}}
    original['adapter'] = {'id': 'amber', 'version': 1}
    original['policy_selection'] = ['.']
    original['enumeration']['gaps'] = []
    scan.files['svc/new.js'] = b'const a = 1\n'
    first, second = copy.deepcopy(original), copy.deepcopy(original)
    assert analyze(first, scan) == analyze(second, scan)
    assert first['enumeration']['gaps'] == second['enumeration']['gaps']
    assert first['enumeration']['gaps'] and 'policy_selection' not in second


def test_verified_blob_cache_corruption_is_miss(repo):
    from al_git_cache import blobs, object_binding
    oid = S.git_bytes('rev-parse', 'HEAD:svc/app.py').decode().strip()
    before = blobs(repo, [oid])
    binding = object_binding(repo)
    C.CACHE.put('git-blob', [binding, oid, 'blob'], b'forged authoritative bytes')
    assert blobs(repo, [oid]) == before
    assert hashlib.sha256(before[oid]).hexdigest() == S._blob_hashes(repo, [oid])[oid]
