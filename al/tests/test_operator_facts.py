"""Native private-fixture regressions for standalone operator capture boundaries."""
import hashlib
import importlib
import json
import os
import shlex
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from al.installer import operator_facts_source as Source
from al.installer.owned_process import OwnedProcess


LOGICAL_BUDGET = 4096
AL_SCRIPTS = Path(__file__).resolve().parents[1] / 'codex/skills/al/scripts'


@contextmanager
def _real_snapshot_module(repository):
    """Load shared snapshot code only after the fixture repo and Git context exist."""
    previous_cwd = Path.cwd()
    previous_path = list(sys.path)
    saved = {name: module for name, module in sys.modules.items() if name.startswith('al_')}
    for name in saved:
        sys.modules.pop(name, None)
    try:
        os.chdir(repository)
        sys.path.insert(0, str(AL_SCRIPTS))
        module = importlib.import_module('al_snapshot')
        yield module
    finally:
        sys.path[:] = previous_path
        os.chdir(previous_cwd)
        for name in [name for name in sys.modules if name.startswith('al_')]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _git(repository, artifacts, environment, owners, *arguments, data=b''):
    """Every fixture child has its own receipt, bounded wait and retained binary output."""
    directory = artifacts / str(len(owners))
    directory.mkdir(mode=0o700, parents=True)
    owner = OwnedProcess(directory / 'owner', 'operator-facts-pack-fixture')
    owners.append(owner)
    assert len(data) <= 65536, 'fixture input must remain modest'
    (directory / 'input').write_bytes(data)
    failure = None
    with (directory / 'input').open('rb') as input_, (directory / 'stdout').open('wb') as output, \
            (directory / 'stderr').open('wb') as error:
        try:
            process = owner.spawn_streams(
                ['/usr/bin/git', '--no-optional-locks', '-c', 'core.hooksPath=/dev/null',
                 '-c', 'core.fsmonitor=false', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
                 *arguments], cwd=repository, env=environment, stdin=input_, stdout=output, stderr=error)
            process.wait(timeout=10)
        except BaseException as exc:
            failure = exc
        finally:
            try:
                cleanup = owner.cleanup()
                if not cleanup['complete']:
                    raise OSError('fixture child cleanup uncertain: ' + str(cleanup))
                if cleanup['secondary_signals'] and failure is None:
                    failure = KeyboardInterrupt(cleanup['secondary_signals'][0])
            except BaseException as exc:
                if failure is not None:
                    failure.add_note('fixture cleanup: ' + str(exc))
                else:
                    failure = exc
    if failure is not None:
        raise failure
    assert process.returncode == 0, (directory / 'stderr').read_text(errors='replace')[-2000:]
    assert (directory / 'stdout').stat().st_size <= 65536, 'fixture output must remain modest'
    return (directory / 'stdout').read_bytes()


def _repository_state(repository):
    result = {}
    for path in sorted(repository.rglob('*')):
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            state = ('symlink', mode, os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            state = ('directory', mode)
        else:
            state = ('file', mode, hashlib.sha256(path.read_bytes()).hexdigest())
        result[path.relative_to(repository).as_posix()] = state
    return result


def test_pack_logical_admission_refuses_unrelated_members_before_pair_verification(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path.resolve()
    home, temporary, repository = root / 'home', root / 'tmp', root / 'repository'
    for path in (home, temporary, repository):
        path.mkdir(mode=0o700)
    environment = dict(PATH=os.defpath, HOME=str(home), HARNESS_HOME=str(home / 'harness'),
        TMPDIR=str(temporary), AL_DISTRIBUTION_STATE=str(home / 'registry'), AL_SERVICE='0',
        PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0',
        GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    fixture_owners = []
    def git(*args, data=b''):
        return _git(repository, root / 'fixture-commands', environment, fixture_owners, *args, data=data)

    git('init', '-q', '--initial-branch=main')
    (repository / 'app.py').write_text('import sample\n')
    (repository / 'sample.py').write_text('VALUE = 1\n')
    git('add', '--', 'app.py', 'sample.py')
    git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--no-gpg-sign', '-qm', 'small selected tree')
    demanded = [git('rev-parse', selector).decode().strip()
                for selector in ('HEAD', 'HEAD^{tree}', 'HEAD:app.py', 'HEAD:sample.py')]
    unrelated_bytes = b'unrelated\n' * 1024  # Ten KiB; never a decompression-bomb fixture.
    unrelated = git('hash-object', '-w', '--no-filters', '--stdin', data=unrelated_bytes).decode().strip()
    members = [*demanded, unrelated]
    assert len(set(members)) == 5 and all(len(oid) == 40 for oid in members)
    pack_name = git('pack-objects', '--window=0', str(repository / '.git/objects/pack/pack'),
                    data=('\n'.join(members) + '\n').encode()).decode().strip()
    index = repository / '.git/objects/pack' / ('pack-' + pack_name + '.idx')
    decoded = git('show-index', '--object-format=sha1', data=index.read_bytes())
    assert {line.split()[1].decode() for line in decoded.splitlines()} == set(members)
    # Force actual packed access: remove only the five loose objects this fixture created.
    for oid in members:
        (repository / '.git/objects' / oid[:2] / oid[2:]).unlink()
    metadata = git('cat-file', '--batch-check', data=('\n'.join(members) + '\n').encode())
    rows = [line.split() for line in metadata.splitlines()]
    assert len(rows) == 5 and [row[0].decode() for row in rows] == members
    assert [row[1] for row in rows] == [b'commit', b'tree', b'blob', b'blob', b'blob']
    sizes = [int(row[2]) for row in rows]
    assert sizes[-1] == len(unrelated_bytes)
    assert sum(sizes[:-1]) < LOGICAL_BUDGET < sum(sizes)
    before = _repository_state(repository)

    # The old source ignores this new budget and reaches real pair verification. The seam
    # changes no production bytes or public configuration, and cannot fake a refusal.
    monkeypatch.setattr(Source, 'PACK_LOGICAL_BYTES', LOGICAL_BUDGET, raising=False)
    capture_owners, verification_calls = [], []
    def observed_owner(*args, **kwargs):
        owner = OwnedProcess(*args, **kwargs)
        capture_owners.append(owner)
        return owner
    original_command = Source.OperatorSource.command
    def observed_command(self, arguments, **kwargs):
        if arguments[:1] == ['verify-pack']:
            verification_calls.append(tuple(arguments))
        return original_command(self, arguments, **kwargs)
    monkeypatch.setattr(Source, 'OwnedProcess', observed_owner)
    monkeypatch.setattr(Source.OperatorSource, 'command', observed_command)
    captured, refusal = None, None
    try:
        with Source.capture(repository, demanded[0]) as captured:
            captured.ensure_object(demanded[0], 'commit')
    except ValueError as error:
        refusal = error

    assert captured is not None, 'capture initialization failed before the admission boundary'
    assert capture_owners, 'the capture must exercise actual owned Git children'
    for owner in [*fixture_owners, *capture_owners]:
        assert owner.last_cleanup['complete'], owner.last_cleanup
        assert owner.process is not None and owner.process.poll() is not None
        assert owner.members() == []
    assert not captured.directory.exists(), 'settled invocation scratch must be removed'
    assert _repository_state(repository) == before
    output = capsys.readouterr()
    assert output.out == '', 'capture refusal must not publish success JSON'
    assert refusal is not None and 'selected_pack_logical_bytes' in str(refusal), (
        'missing selected-pack logical admission', str(refusal), verification_calls)
    assert verification_calls == [], 'over-budget pack reached verify-pack'
    diagnostic = [json.loads(line.removeprefix('AL operator facts capture '))
                  for line in output.err.splitlines() if line.startswith('AL operator facts capture ')]
    assert len(diagnostic) == 1 and diagnostic[0]['outcome'] == 'failure'
    assert diagnostic[0]['cleanup'] == 'known'


def test_duplicate_objects_in_distinct_packs_keep_pair_admission_isolated(
    tmp_path, monkeypatch
):
    root = tmp_path.resolve()
    home, temporary, repository = root / 'home', root / 'tmp', root / 'repository'
    for path in (home, temporary, repository):
        path.mkdir(mode=0o700)
    environment = dict(PATH=os.defpath, HOME=str(home), HARNESS_HOME=str(home / 'harness'),
        TMPDIR=str(temporary), AL_DISTRIBUTION_STATE=str(home / 'registry'), AL_SERVICE='0',
        PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0',
        GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    owners = []
    artifacts = root / 'fixture-commands'

    def git(*arguments, data=b''):
        return _git(repository, artifacts, environment, owners, *arguments, data=data)

    git('init', '-q', '--initial-branch=main')
    for name, text in {'app.py': 'import dependency\n', 'dependency.py': 'VALUE = 1\n'}.items():
        (repository / name).write_text(text)
    git('add', '--', 'app.py', 'dependency.py')
    git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--no-gpg-sign', '-qm', 'duplicate pack source A')
    commit_a = git('rev-parse', 'HEAD').decode().strip()
    tree = git('rev-parse', 'HEAD^{tree}').decode().strip()
    app = git('rev-parse', 'HEAD:app.py').decode().strip()
    dependency = git('rev-parse', 'HEAD:dependency.py').decode().strip()
    git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--no-gpg-sign', '--allow-empty', '-qm', 'duplicate pack source B')
    commit_b = git('rev-parse', 'HEAD').decode().strip()
    unrelated = git('hash-object', '-w', '--no-filters', '--stdin',
                    data=b'unrelated object in the duplicate pair\n').decode().strip()
    first = [commit_a, tree, app, dependency]
    second = [commit_b, tree, app, dependency, unrelated]
    pack_names = []
    for members in (first, second):
        name = git('pack-objects', '--window=0', str(repository / '.git/objects/pack/pack'),
                   data=('\n'.join(members) + '\n').encode()).decode().strip()
        assert name
        pack_names.append('pack-' + name + '.idx')
    all_members = sorted(set(first + second))
    size_rows = git('cat-file', '--batch-check',
                    data=('\n'.join(all_members) + '\n').encode()).splitlines()
    sizes = {row.split()[0].decode(): int(row.split()[2]) for row in size_rows}
    for oid in set(first + second):
        (repository / '.git/objects' / oid[:2] / oid[2:]).unlink()
    before = _repository_state(repository)
    capture_owners = []
    original = Source.OperatorSource.command
    observed = []
    captured_ref = []

    def observe_pair(self, arguments, **kwargs):
        if arguments[:2] == ['cat-file', '--batch-check']:
            captured = captured_ref[0]
            directory = kwargs['object_directory']
            selected = directory.name.removeprefix('admission-') + '.idx'
            staged = directory / 'pack'
            assert {path.name for path in staged.iterdir()} == {
                selected[:-4] + '.idx', selected[:-4] + '.pack'
            }
            duplicate_pairs = [name for name, members in captured.pack_members.items()
                               if set(first) & set(second) & set(members)]
            assert len(duplicate_pairs) == 2
            assert selected in duplicate_pairs
            observed.append(selected)
        return original(self, arguments, **kwargs)

    def observed_owner(*args, **kwargs):
        owner = OwnedProcess(*args, **kwargs)
        capture_owners.append(owner)
        return owner

    monkeypatch.setattr(Source.OperatorSource, 'command', observe_pair)
    monkeypatch.setattr(Source, 'OwnedProcess', observed_owner)
    with Source.capture(repository, commit_a) as captured:
        captured_ref.append(captured)
        assert captured.ensure_object(commit_a, 'commit')
        assert captured.ensure_object(commit_b, 'commit')
        assert len(captured.packs) == 2
        assert set(observed) == captured.packs
        paired_logical_bytes = sum(
            sizes[oid] for name in captured.packs for oid in captured.pack_members[name]
        )
        unique_logical_bytes = sum(sizes[oid] for oid in set().union(
            *(set(captured.pack_members[name]) for name in captured.packs)
        ))
        duplicated_oids = set(captured.pack_members[pack_names[0]]) & set(
            captured.pack_members[pack_names[1]]
        )
        assert duplicated_oids
        assert paired_logical_bytes == unique_logical_bytes + sum(sizes[oid] for oid in duplicated_oids)
        assert captured.counts['selected_pack_logical_bytes'] == paired_logical_bytes
    assert _repository_state(repository) == before
    assert all(owner.last_cleanup['complete'] and owner.process.poll() is not None
               and owner.members() == [] for owner in [*owners, *capture_owners])


@pytest.mark.parametrize(('frame', 'message'), [
    (b'not-an-object-header\n', 'invalid private object frame'),
    (b'0' * 40 + b' blob 7\nshort\n', 'truncated private object body'),
    (b'0' * 40 + b' tree 7\ncontent\n', 'private object hash mismatch'),
    (b'0' * 40 + b' blob 7\nchanged\n', 'private object hash mismatch'),
    (b'0' * 40 + b' blob 7\ncontent\ntrailing', 'trailing private object bytes'),
])
def test_synthetic_private_object_transport_frames_are_validated(monkeypatch, frame, message):
    """Exercise read_objects framing with controlled private Git output, not a native run."""
    content = b'content'
    oid = hashlib.sha1(b'blob 7\0' + content).hexdigest()
    if message == 'truncated private object body':
        frame = oid.encode() + b' blob 7\nshort\n'
    elif message == 'private object hash mismatch':
        kind = b'tree' if b' tree ' in frame else b'blob'
        frame = oid.encode() + b' ' + kind + b' 7\nchanged\n'
    elif message == 'trailing private object bytes':
        frame = oid.encode() + b' blob 7\ncontent\ntrailing'
    capture = Source.OperatorSource.__new__(Source.OperatorSource)
    capture.width = 40
    capture.objects = {}
    capture.copied = set()
    capture.algorithm = 'sha1'
    capture.counts = {'requested_payload_bytes': 0}
    monkeypatch.setattr(capture, 'check', lambda: None)
    monkeypatch.setattr(capture, 'materialize', lambda _oid: None)
    monkeypatch.setattr(capture, 'command', lambda *_args, **_kwargs: frame)
    with pytest.raises(ValueError, match=message):
        capture.read_objects([oid])


def test_synthetic_private_object_transport_accepts_valid_frame(monkeypatch):
    """The controlled runner returns one correctly framed, correctly hashed blob."""
    content = b'content'
    oid = hashlib.sha1(b'blob 7\0' + content).hexdigest()
    capture = Source.OperatorSource.__new__(Source.OperatorSource)
    capture.width = 40
    capture.objects = {}
    capture.copied = set()
    capture.algorithm = 'sha1'
    capture.counts = {'requested_payload_bytes': 0}
    monkeypatch.setattr(capture, 'check', lambda: None)
    monkeypatch.setattr(capture, 'materialize', lambda _oid: None)
    monkeypatch.setattr(
        capture, 'command', lambda *_args, **_kwargs: oid.encode() + b' blob 7\ncontent\n'
    )
    assert capture.read_objects([oid]) == {oid: ('blob', content)}


def _working_copy_fixture(root, monkeypatch):
    home, temporary, repository = root / 'home', root / 'tmp', root / 'repository'
    for path in (home, temporary, repository):
        path.mkdir(mode=0o700)
    environment = dict(PATH=os.defpath, HOME=str(home), HARNESS_HOME=str(home / 'harness'),
        TMPDIR=str(temporary), AL_DISTRIBUTION_STATE=str(home / 'registry'), AL_SERVICE='0',
        PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0',
        GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    owners = []
    artifacts = root / 'fixture-commands'

    def git(*arguments, data=b''):
        return _git(repository, artifacts, environment, owners, *arguments, data=data)

    git('init', '-q', '--initial-branch=main')
    (repository / '.gitignore').write_text('ignored.bin\n')
    (repository / 'service.py').write_text('import dependency\n')
    (repository / 'dependency.py').write_text('VALUE = 1\n')
    git('add', '--', '.gitignore', 'service.py', 'dependency.py')
    git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--no-gpg-sign', '-qm', 'working-copy source')
    git('repack', '-adf', '--window=0')
    return repository, git, owners


def test_working_copy_capture_ignores_untracked_ignored_payload_and_history(
    tmp_path, monkeypatch
):
    root = tmp_path.resolve()
    repository, _git_fixture, fixture_owners = _working_copy_fixture(root, monkeypatch)
    ignored = repository / 'ignored.bin'
    ignored.write_bytes(b'ignored payload' * 512)
    before = _repository_state(repository)
    # The payload is intentionally only 7.5 KiB. A lowered cap catches an accidental read.
    monkeypatch.setattr(Source, 'CURRENT_BYTES', 1024)
    captured = None
    with _real_snapshot_module(repository):
        with Source.capture(repository) as captured:
            tree = captured.snapshot()
            entries = captured.entries(tree)
            captured.fence()
            assert set(entries) == {'.gitignore', 'dependency.py', 'service.py'}
            assert captured.counts['current_bytes'] < 1024
            assert captured.counts['selected_pack_bytes'] == 0
            assert captured.counts['loose_object_bytes'] == 0
            assert captured.counts['requested_payload_bytes'] == 0
    assert captured is not None and not captured.directory.exists()
    assert _repository_state(repository) == before
    assert all(owner.last_cleanup['complete'] and owner.process.poll() is not None
               and owner.members() == [] for owner in fixture_owners)


def test_source_move_between_capture_and_publication_fence_is_refused(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path.resolve()
    repository, _git_fixture, fixture_owners = _working_copy_fixture(root, monkeypatch)
    source = repository / 'service.py'
    original = source.read_bytes()
    captured = None
    with _real_snapshot_module(repository):
        with pytest.raises(ValueError, match='source input changed'):
            with Source.capture(repository) as captured:
                captured.snapshot()
                source.write_bytes(original + b'# moved after capture\n')
                captured.fence()
    source.write_bytes(original)
    assert captured is not None and not captured.directory.exists()
    assert all(owner.last_cleanup['complete'] and owner.process.poll() is not None
               and owner.members() == [] for owner in fixture_owners)
    diagnostic = [json.loads(line.removeprefix('AL operator facts capture '))
                  for line in capsys.readouterr().err.splitlines()
                  if line.startswith('AL operator facts capture ')]
    assert len(diagnostic) == 1 and diagnostic[0]['outcome'] == 'failure'
    assert diagnostic[0]['cleanup'] == 'known'




@pytest.mark.parametrize('setting', ['core.fsmonitor', 'core.hooksPath', 'filter.fixture.clean'])
def test_target_execution_canary_configuration_is_refused_without_running(
    tmp_path, monkeypatch, capsys, setting
):
    import sys

    root = tmp_path.resolve()
    repository, git, fixture_owners = _working_copy_fixture(root, monkeypatch)
    marker = root / 'canary-ran'
    canary = root / 'target-canary.py'
    canary.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ran")\n')
    canary.chmod(0o700)
    if setting == 'core.hooksPath':
        hook_dir = root / 'target-hooks'
        hook_dir.mkdir(mode=0o700)
        hook = hook_dir / 'pre-commit'
        hook.write_text('#!/bin/sh\n' + shlex.quote(sys.executable) + ' '
                        + shlex.quote(str(canary)) + '\n')
        hook.chmod(0o700)
        value = str(hook_dir)
    else:
        value = shlex.quote(sys.executable) + ' ' + shlex.quote(str(canary))
        if setting == 'filter.fixture.clean':
            (repository / '.gitattributes').write_text('*.py filter=fixture\n')
    git('config', '--local', setting, value)
    before = _repository_state(repository)

    with pytest.raises(ValueError, match='unsupported operator Git configuration'):
        with Source.capture(repository):
            pass

    assert not marker.exists(), 'target-configured executable ran during read-only capture'
    assert _repository_state(repository) == before
    assert all(owner.last_cleanup['complete'] and owner.process.poll() is not None
               and owner.members() == [] for owner in fixture_owners)
    output = capsys.readouterr()
    assert output.out == ''
    diagnostic = [json.loads(line.removeprefix('AL operator facts capture '))
                  for line in output.err.splitlines()
                  if line.startswith('AL operator facts capture ')]
    assert len(diagnostic) == 1 and diagnostic[0]['outcome'] == 'failure'
    assert diagnostic[0]['cleanup'] == 'known'


@pytest.mark.parametrize('selector', ['', '../outside', '.git/config', 'nested/../file.py'])
def test_unsafe_operator_selectors_are_rejected(selector):
    with pytest.raises(ValueError, match='unsafe operator facts path'):
        Source.relative(selector)


@pytest.mark.parametrize('selector', ['../outside', '/etc/passwd', 'refs/../main'])
def test_unsafe_operator_revisions_are_rejected(selector):
    with pytest.raises(ValueError, match='unsupported operator facts revision'):
        Source.revision(selector)


def test_symlinked_working_copy_source_is_refused_without_reading_target(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path.resolve()
    repository, _git_fixture, fixture_owners = _working_copy_fixture(root, monkeypatch)
    outside = root / 'outside.py'
    outside.write_text('SECRET = 1\n')
    (repository / 'service-link.py').symlink_to(outside)
    before = _repository_state(repository)
    with _real_snapshot_module(repository):
        with pytest.raises(ValueError, match='unsupported selected working-copy source type'):
            with Source.capture(repository) as captured:
                captured.snapshot()
    assert _repository_state(repository) == before
    assert outside.read_text() == 'SECRET = 1\n'
    assert all(owner.last_cleanup['complete'] and owner.process.poll() is not None
               and owner.members() == [] for owner in fixture_owners)
    output = capsys.readouterr()
    assert output.out == ''
    diagnostic = [json.loads(line.removeprefix('AL operator facts capture '))
                  for line in output.err.splitlines()
                  if line.startswith('AL operator facts capture ')]
    assert len(diagnostic) == 1 and diagnostic[0]['outcome'] == 'failure'
    assert diagnostic[0]['cleanup'] == 'known'


def test_many_file_working_copy_cost_does_not_depend_on_discarded_git_history(
    tmp_path, monkeypatch
):
    root = tmp_path.resolve()
    repository, git, owners = _working_copy_fixture(root, monkeypatch)
    for number in range(256):
        (repository / f'module_{number:02}.py').write_text(f'VALUE = {number}\n')
    git('add', '--', '*.py')
    git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--no-gpg-sign', '-qm', 'many ordinary source files')
    measured = ('files', 'visited_files', 'current_bytes', 'selected_pack_bytes',
                'selected_pack_logical_bytes', 'loose_object_bytes', 'requested_payload_bytes',
                'demanded_objects', 'git_commands', 'git_stdout_bytes')
    with _real_snapshot_module(repository):
        with Source.capture(repository) as captured:
            tree = captured.snapshot()
            assert len(captured.entries(tree)) == 259
            baseline = {key: captured.counts[key] for key in measured}
            captured.fence()
    (repository / 'transient-history.txt').write_text('h' * (64 * 1024))
    git('add', '--', 'transient-history.txt')
    git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--no-gpg-sign', '-qm', 'unrelated history')
    git('reset', '--hard', '-q', 'HEAD~1')
    before = _repository_state(repository)
    with _real_snapshot_module(repository):
        with Source.capture(repository) as captured:
            tree = captured.snapshot()
            entries = captured.entries(tree)
            captured.fence()
            after_history = {key: captured.counts[key] for key in measured}
            assert len(entries) == 259
            assert captured.counts['files'] == 259
            assert captured.counts['selected_pack_bytes'] == 0
            assert captured.counts['loose_object_bytes'] == 0
            assert captured.counts['requested_payload_bytes'] == 0
    assert after_history == baseline, 'unrelated discarded Git history must not increase capture work'
    assert _repository_state(repository) == before
    assert all(owner.last_cleanup['complete'] and owner.process.poll() is not None
               and owner.members() == [] for owner in owners)
