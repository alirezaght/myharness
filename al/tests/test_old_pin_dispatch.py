"""The current run child still drives runs pinned before later installer modules existed (#168)."""

import shutil
from pathlib import Path

import pytest

from al.installer import runtime_pin
from al.installer.runtime_pin import prepare_pin, verify_pin
from al.tests.runtime_run_helpers import _environment, invoke
from al.tests.test_runtime_pin_cache import _distribution

RETRY = 'installer/runtime_retry_control.py'
SUCCESSOR = ('installer/runtime_successor_store.py', 'installer/runtime_successor_channel.py')


def _old_pin(tmp_path, monkeypatch, missing):
    from al.installer import runtime_pin_transaction as transactions
    from al.installer.engine import MODULES

    distribution = _distribution(tmp_path / 'distribution')
    installer = Path(runtime_pin.__file__).parent
    for source in installer.glob('*.py'):
        shutil.copyfile(source, distribution / 'installer' / source.name)
    scripts = distribution / 'codex/skills/al/scripts'
    for name in (*MODULES, 'al_runtime_resources'):
        shutil.copyfile(installer.parent / f'codex/skills/al/scripts/{name}.py', scripts / f'{name}.py')
    (scripts / 'al_state.py').write_text(
        "import sys\ndef main():\n    print('fixture-application-dispatched', sys.argv[-2:], flush=True)\n")
    for name in missing:  # the pin predates these modules, as the 724 pins before 2026-11-26 do
        (distribution / name).unlink()
    base = tmp_path / 'al-run-pins'
    base.mkdir(mode=0o700)
    binding = prepare_pin(distribution, 'codex', base / 'pins')
    verified = verify_pin(base / 'pins', binding)
    assert not set(missing) & set(verified['bytes'])
    repository = tmp_path / 'repository'
    repository.mkdir()
    home = tmp_path / 'home'
    home.mkdir()
    env = _environment(home)
    for name in ('HARNESS_RETRY_CONTROL_FD', 'HARNESS_RETRY_CONTROL_NONCE'):
        env.pop(name, None)
    for args in (('init', '-q'), ('commit', '--allow-empty', '-qm', 'initial')):
        result = invoke(['git', *args], repository, env)
        assert result.returncode == 0, result.stderr
    monkeypatch.chdir(repository)
    monkeypatch.setattr(transactions, 'storage', lambda **kwargs: base)
    transaction = transactions.reserve(transactions.subject(repository, 'codex', 'old-pin-run'), binding)
    return verified, transaction, base, repository, env


def _child(verified, transaction, base, argv):
    from al.installer.runtime_pin_boot import program

    request = {'transaction': transaction, 'storage': str(base), 'argv': argv}
    return [verified['manifest']['interpreter']['executable'], '-I', '-S', '-B', '-c', program(request)]


@pytest.mark.parametrize('missing', [(RETRY,), (RETRY, *SUCCESSOR)], ids=['no-retry-control', 'no-successor-runs'])
def test_run_child_drives_a_pin_without_later_modules(tmp_path, monkeypatch, missing):
    verified, transaction, base, repository, env = _old_pin(tmp_path, monkeypatch, missing)
    result = invoke(_child(verified, transaction, base, ['start']), repository, env)
    assert result.returncode == 0, result.stderr
    assert 'unbound validator module' not in result.stderr
    # The pinned application ran this run's command (argv ends `--session old-pin-run start`).
    assert result.stdout.splitlines()[-1] == "fixture-application-dispatched ['old-pin-run', 'start']"


def test_old_pin_refuses_an_offered_sealed_control_exchange(tmp_path, monkeypatch):
    verified, transaction, base, repository, env = _old_pin(tmp_path, monkeypatch, (RETRY,))
    env = dict(env, HARNESS_RETRY_CONTROL_FD='9', HARNESS_RETRY_CONTROL_NONCE='n' * 32)
    result = invoke(_child(verified, transaction, base, ['start']), repository, env)
    assert result.returncode != 0
    assert 'sealed control requires a pinned runtime that carries it' in result.stderr
    assert 'fixture-application-dispatched' not in result.stdout


@pytest.mark.parametrize('missing', [(), SUCCESSOR], ids=['current-pin', 'no-successor-runs'])
def test_child_ordinary_authority_check_follows_the_pin(tmp_path, monkeypatch, missing):
    from al.installer.runtime_pin_transaction import key

    verified, transaction, base, repository, env = _old_pin(tmp_path, monkeypatch, missing)
    subject = {k: transaction[k] for k in ('repository_identity', 'platform', 'session_id')}
    fences = base / 'successor-fences'
    fences.mkdir(mode=0o700)
    (fences / (key(subject) + '.json')).write_text('{}')  # a prepared successor owns this session
    result = invoke(_child(verified, transaction, base, ['start']), repository, env)
    if not missing:
        # A pin that carries successor runs still refuses in the child, not only in the launcher.
        assert result.returncode != 0
        assert 'prepared successor has no product authority' in result.stderr
    else:
        # A pin that predates successor runs relies on the launcher's check (runtime_run.run).
        assert result.returncode == 0, result.stderr
