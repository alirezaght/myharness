"""Discovery cost grows with the repository and the task, not with their product (#8)."""
import fcntl
import hashlib
import io
import json
import os
import random
import threading
import time

import pytest

import al_python_imports as imports
import al_repository_facts as F
import al_scope as S
import al_store
import al_writer_progress as progress
from al_repository_facts import PathScope, within
from conftest import _git


def _build(repo, n):
    for i in range(n):
        path = repo / f'svc/pkg{i % 7}/m{i}.py'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'import os\nVALUE_{i} = "{i}"\n')
        doc = repo / f'docs/section{i % 5}/page{i}.md'
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(f'# page {i}\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'scaled fixture')


def _content_reads(repo, n):
    """Source payload accesses of one discovery whose task changes every document."""
    _build(repo, n)
    reads = [0]
    original_get = F.CapturedContent.__getitem__
    original_items = F.CapturedContent.content_items

    def get(self, path):
        reads[0] += 1
        return original_get(self, path)

    def items(self, paths):
        for row in original_items(self, paths):
            reads[0] += 1
            yield row

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(F.CapturedContent, '__getitem__', get)
        patch.setattr(F.CapturedContent, 'content_items', items)
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        docs = sorted(p.relative_to(repo).as_posix() for p in (repo / 'docs').rglob('*.md'))
        base = S.git_bytes('rev-parse', 'HEAD').decode().strip()
        facts = F.collect(repo, base_oid=base, paths=docs, opaque=set())
    return reads[0], facts


def test_artifact_reference_discovery_reads_each_source_once(repo, tmp_path, monkeypatch, seed_repo):
    import shutil
    small, small_facts = _content_reads(repo, 10)
    large_repo = tmp_path / 'large'
    shutil.copytree(seed_repo, large_repo, symlinks=True)
    monkeypatch.chdir(large_repo)
    monkeypatch.setattr(al_store, 'REPO_ROOT', large_repo)
    large, large_facts = _content_reads(large_repo, 80)
    # 8x documents and 8x sources: per-document rescans made this ~64x; one shared pass is ~8x.
    assert large <= small * 12, (small, large)
    rows = {r['path']: r for r in large_facts['dependencies']['artifact_consumers']}
    assert rows['docs/section0/page0.md']['kind'] == 'documentation'
    assert len(small_facts['dependencies']['artifact_consumers']) == 11  # 10 pages + docs/readme.md


def test_path_scope_matches_pairwise_within():
    rng = random.Random(8)
    segments = ['a', 'b', 'ab', 'c.py', 'd']

    def path():
        value = '/'.join(rng.choice(segments) for _ in range(rng.randint(1, 4)))
        return value + ('/' if rng.random() < 0.2 else '')

    for _ in range(2000):
        scopes = [path() for _ in range(rng.randint(0, 5))] + (['.'] if rng.random() < 0.05 else [])
        scope = PathScope(scopes)
        for _ in range(10):
            candidate = '.' if rng.random() < 0.05 else path()
            assert scope.covers(candidate) == any(within(s, candidate) for s in scopes)
            assert scope.contained(candidate) == any(within(candidate, s) for s in scopes)
            assert scope.overlaps(candidate) == any(within(s, candidate) or within(candidate, s) for s in scopes)


def test_token_finder_matches_substring_search():
    rng = random.Random(9)
    alphabet = b'ab/."\''
    for _ in range(3000):
        tokens = {bytes(rng.choice(alphabet) for _ in range(rng.randint(1, 5))) for _ in range(rng.randint(0, 8))}
        data = bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert imports.TokenFinder(tokens).present(data) == {t for t in tokens if t in data}


def test_unchanged_worktree_files_are_hashed_once_per_process(repo, monkeypatch):
    monkeypatch.setattr(S, '_DIGEST_RACY_NS', -10**12)
    monkeypatch.setattr(S, '_DIGESTS', {})
    target = repo / 'svc/app.py'
    target.write_text('changed = 1\n')
    reads = []
    original = type(target).read_bytes

    def read_bytes(self):
        if '.git' not in self.parts:
            reads.append(self.name)
        return original(self)

    monkeypatch.setattr(type(target), 'read_bytes', read_bytes)
    base = S.git_bytes('rev-parse', 'HEAD').decode().strip()
    first = S._records(base, repo)
    hashed = len(reads)
    assert hashed and S._records(base, repo) == first and len(reads) == hashed
    target.write_text('changed = 22\n')
    second = {r['path']: r for r in S._records(base, repo)}
    assert second['svc/app.py']['worktree']['sha256'] != {r['path']: r for r in first}['svc/app.py']['worktree']['sha256']


def test_recent_files_are_never_reused():
    S._DIGESTS.clear()
    path = al_store.REPO_ROOT / 'svc/app.py'
    path.write_text('recent = 1\n')
    S._file_digest(path)
    assert not S._DIGESTS


def test_long_silent_phase_prints_heartbeat(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(progress.sys, 'stderr', stream)
    monkeypatch.setattr(progress, '_HEARTBEAT', 0.05)
    with progress.command_progress('gates', 'discover'):
        progress.report('inventory-analysis', count=7, total=9, force=True)
        time.sleep(0.4)
    lines = stream.getvalue().splitlines()
    assert '7/9 items processed' in lines[0]
    # The heartbeat shows progress out of the phase total, so slow is told apart from stuck (#23).
    assert any('analyzing repository relationships and checks · still working · 7/9 items processed so far' in line
               for line in lines[1:]), lines


def test_heartbeat_follows_counts_reported_between_beats(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(progress.sys, 'stderr', stream)
    monkeypatch.setattr(progress, '_HEARTBEAT', 0.05)
    with progress.command_progress('gates', 'discover'):
        progress.report('inventory-provenance', count=0, total=40, force=True)
        # Rate-limited reports print nothing themselves but move the heartbeat's count.
        progress.report('inventory-provenance', count=12, total=40)
        time.sleep(0.3)
        progress.report('inventory-membership', count=5, force=True)
        time.sleep(0.3)
    lines = stream.getvalue().splitlines()
    assert any('binding scanned inputs to units · still working · 12/40 items processed so far' in line for line in lines), lines
    assert any('enumerating repository membership · still working · 5 items processed so far' in line for line in lines), lines


def _bind_boundaries(repo, links):
    """Content reads of one collection whose repository tracks `links` symlink boundaries."""
    _build(repo, 40)
    for i in range(links):
        os.symlink('../m0.py', repo / f'svc/pkg{i % 7}/link{i}.py')
    (repo / 'svc/pkg0/consumer.py').write_text('PATH = "pkg1/link1.py"\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'symlink boundaries')
    reads = [0]
    original_items = F.CapturedContent.content_items

    def items(self, paths):
        for row in original_items(self, paths):
            reads[0] += 1
            yield row

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(F.CapturedContent, 'content_items', items)
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        base = S.git_bytes('rev-parse', 'HEAD').decode().strip()
        facts = F.collect(repo, base_oid=base, paths=['svc/pkg0/m0.py'], opaque=set())
    return reads[0], facts


def test_boundary_consumers_share_one_source_pass(repo, tmp_path, monkeypatch, seed_repo):
    import shutil
    few, few_facts = _bind_boundaries(repo, 2)
    many_repo = tmp_path / 'many'
    shutil.copytree(seed_repo, many_repo, symlinks=True)
    monkeypatch.chdir(many_repo)
    monkeypatch.setattr(al_store, 'REPO_ROOT', many_repo)
    many, many_facts = _bind_boundaries(many_repo, 24)
    consumers = many_facts['dependencies']['boundary_consumers']
    assert len(consumers) >= 24
    assert consumers['svc/pkg1/link1.py'] == ['svc/pkg0/consumer.py']
    assert few_facts['dependencies']['boundary_consumers']['svc/pkg1/link1.py'] == ['svc/pkg0/consumer.py']
    # 12x boundaries: a source pass per boundary made this ~12x; one shared pass keeps it flat.
    assert many <= few + 60, (few, many)


class _Files(dict):
    def __init__(self, snapshot, values, reads):
        super().__init__(values)
        self.snapshot, self.reads = snapshot, reads
        self.records = {p: {'sha256': hashlib.sha256(v).hexdigest()} for p, v in values.items()}

    def content_items(self, paths):
        for p in paths:
            self.reads.append(p)
            yield p, self[p]


def _scan(values, reads):
    return type('Scan', (), {'files': _Files('current', values, reads), 'base': _Files('base', {}, reads)})


def test_referencing_has_one_definition_and_excludes_the_artifact_itself(monkeypatch):
    import inspect
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    source = inspect.getsource(imports)
    assert source.count('def _referencing_many(') == 1
    reads = []
    scan = _scan({'a/x.py': b'x.py and a/', 'b/y.py': b'import x.py'}, reads)
    assert imports._referencing_many(scan, ['a/x.py']) == {'a/x.py': ['b/y.py']}


def test_a_later_command_reuses_references_for_identical_captured_sources():
    """`gates plan` after `gates discover` reuses the literal-reference pass; a changed source rebinds (#23)."""
    values = {'a/x.py': b'x.py and a/', 'b/y.py': b'import x.py', 'c/z.py': b'nothing'}
    first_reads, again_reads, changed_reads = [], [], []
    first = imports._referencing_many(_scan(values, first_reads), ['a/x.py', 'c/z.py'])
    again = imports._referencing_many(_scan(values, again_reads), ['a/x.py', 'c/z.py'])
    assert first == again == {'a/x.py': ['b/y.py'], 'c/z.py': []}
    assert len(first_reads) == 3 and again_reads == []
    changed = dict(values, **{'c/z.py': b'uses x.py'})
    assert imports._referencing_many(_scan(changed, changed_reads), ['a/x.py', 'c/z.py']) == {'a/x.py': ['b/y.py', 'c/z.py'], 'c/z.py': []}
    assert len(changed_reads) == 3
    # A corrupt record is a miss, never a result.
    for record in (al_store.RUNTIME_DIR / 'derived' / 'literal-references').glob('*.json'):
        record.write_text(record.read_text().replace('b/y.py', 'b/q.py'))
    corrupt_reads = []
    assert imports._referencing_many(_scan(values, corrupt_reads), ['a/x.py', 'c/z.py']) == first
    assert len(corrupt_reads) == 3


def test_scan_inputs_match_pairwise_unit_binding():
    rng = random.Random(23)
    segments = ['a', 'b', 'src', 'lib']

    def path(suffix=''):
        return '/'.join(rng.choice(segments) for _ in range(rng.randint(1, 4))) + suffix

    class Files:
        def __init__(self, records):
            self.records = records

    for _ in range(300):
        units = [{'path': p, 'source_roots': [p + '/src'] if rng.random() < 0.7 else [p]}
                 for p in {path() for _ in range(rng.randint(0, 4))}]
        if rng.random() < 0.05:
            units.append({'path': '.', 'source_roots': ['.']})
        files = {path(rng.choice(['.py', '.ts', '.md', '.go'])): {'sha256': 'x'} for _ in range(20)}
        scan = type('Scan', (), {'base': Files(dict(list(files.items())[:7])), 'files': Files(files)})
        expected = [{'path': p, 'snapshot': which, 'sha256': record['sha256']}
                    for which, group in [('base', scan.base), ('current', scan.files)]
                    for p, record in sorted(group.records.items())
                    if p.endswith(F.SOURCE_SUFFIXES) and any(
                        (p.endswith('.py') and any(within(r, p) for r in u['source_roots']))
                        or (not p.endswith('.py') and within(u['path'], p)) for u in units)]
        assert F.scan_inputs(scan, units) == expected


def test_waiting_command_names_the_lock_holder(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(progress.sys, 'stderr', stream)
    sid = 'lock-wait'
    al_store.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with progress.command_progress('gates', 'discover'):
            with al_store.locked(sid):
                acquired.set()
                release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(5)
    record = json.loads((al_store.RUNTIME_DIR / f'.{sid}.lock.holder').read_text())
    assert record['command'] == 'gates discover' and record['pid'] == os.getpid()
    timer = threading.Timer(0.5, release.set)
    timer.start()
    with progress.command_progress('task', 'amend'):
        with al_store.locked(sid):
            pass
    thread.join(5)
    waits = [line for line in stream.getvalue().splitlines() if 'waiting for the run admission lock' in line]
    assert waits and 'AL · task amend · ' in waits[0]
    assert f'held by `gates discover` (pid {os.getpid()}' in waits[0]
    assert not (al_store.RUNTIME_DIR / f'.{sid}.lock.holder').exists()


def test_lock_wait_renders_only_plain_holder_fields(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(progress.sys, 'stderr', stream)
    progress.lock_wait({'command': '/secret/path; rm', 'pid': 'x', 'acquired': 'never'}, 1.0)
    progress.lock_wait(None, 2.0)
    text = stream.getvalue()
    assert 'secret' not in text and 'held by `another command`' in text
    assert 'held by an unidentified command' in text


def test_uncontended_lock_is_silent(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(progress.sys, 'stderr', stream)
    with al_store.locked('quiet'):
        fd = os.open(al_store.RUNTIME_DIR / '.quiet.lock', os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                raise AssertionError('lock must be held')
            except BlockingIOError:
                pass
        finally:
            os.close(fd)
    assert 'waiting' not in stream.getvalue()
