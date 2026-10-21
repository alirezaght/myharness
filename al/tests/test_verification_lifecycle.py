"""Public refusal and real descendants pin verification lifecycle ownership."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from al import verification_process as P, verification_runner as R
from al.verification_plan import build_plan
from al.tests.verification_helpers import source, declarations, collected


@pytest.mark.parametrize('relative', ['execution/journal.json', 'collect-codex/process-journal.json', 'unexpected/journal.json'])
@pytest.mark.parametrize('status', ['RUNNING', 'PENDING', 'UNKNOWN', 'corrupt', 'FINISHED', 'missing'])
def test_public_full_refuses_actual_layout_before_new_collection(tmp_path, relative, status):
    report = tmp_path / 'verification.json'
    artifacts = tmp_path / 'verification-runs'
    journal = artifacts / 'run-prior' / relative
    journal.parent.mkdir(parents=True)
    if status != 'missing':
        journal.write_text('{' if status == 'corrupt' else json.dumps({'schema_version': 1, 'status': status}))
    before = sorted(str(p.relative_to(artifacts)) for p in artifacts.rglob('*'))
    command = Path(__file__).resolve().parents[1] / 'release_verify.py'
    env = {k: v for k, v in os.environ.items() if not k.startswith('GITHUB_')}
    result = subprocess.run([sys.executable, str(command), '--full', '--plan-only', '--report', str(report)],
                            cwd=command.parent.parent, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, (result.returncode, result.stdout[:1500], result.stderr[:1500])
    failure = json.loads(report.read_text())
    assert 'owned verification' in failure['error']
    assert sorted(str(p.relative_to(artifacts)) for p in artifacts.rglob('*')) == before


def descendant_plan(tmp_path):
    marker = tmp_path / 'late'
    descendant = 'import time;from pathlib import Path;time.sleep(1);Path(' + repr(str(marker)) + ').write_text("leaked")'
    body = 'import subprocess,sys\ndef test_a():\n subprocess.Popen([sys.executable,"-c",' + repr(descendant) + '])\n'
    root = source(tmp_path, {s: body for s in ('installer', 'codex', 'claude')})
    collections, binding = collected(root, tmp_path)
    return build_plan(collections, declarations(root), tier='full', changed_paths=[], binding=binding), marker


def observe_owners(monkeypatch):
    owners = []
    real = P.OwnedProcess.spawn
    def spawn(owner, *args, **kwargs):
        process = real(owner, *args, **kwargs)
        owners.append(owner)
        return process
    monkeypatch.setattr(P.OwnedProcess, 'spawn', spawn)
    return owners


def test_post_exit_output_close_failure_keeps_actual_descendant_owned(tmp_path, monkeypatch):
    plan, marker = descendant_plan(tmp_path)
    owners = observe_owners(monkeypatch)
    original = Path.open
    failed = []
    class FallibleOutput:
        def __init__(self, stream):self.stream = stream
        def __getattr__(self, name):return getattr(self.stream, name)
        def close(self):
            self.stream.close()
            if not failed:
                failed.append(True)
                raise OSError('post-exit stream close failed')
    def opened(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        return FallibleOutput(stream) if path.name == 'output.log' and args == ('wb',) else stream
    monkeypatch.setattr(Path, 'open', opened)
    try:
        result = R.run(plan, workers=1, artifact_root=tmp_path / 'execution', cache_mode='cold')
        assert failed and result['cause']['kind'] == 'OSError'
        assert result['cleanup']['complete'] and not result['reconciliation']['complete']
        time.sleep(1.1)
        assert not marker.exists()
        assert all(not owner.members() for owner in owners)
    finally:
        for owner in owners:owner.cleanup()


def test_denied_descendant_cleanup_and_secondary_signal_remain_pending(tmp_path, monkeypatch):
    plan, marker = descendant_plan(tmp_path)
    owners = observe_owners(monkeypatch)
    real = P.OwnedProcess.cleanup
    failed = []
    def cleanup(owner):
        if failed:return real(owner)
        failed.append(True)
        def denied(*_):
            os.kill(os.getpid(), signal.SIGINT)
            raise PermissionError('descendant signal denied')
        with monkeypatch.context() as patch:
            patch.setattr(P.os, 'killpg', denied)
            return real(owner)
    monkeypatch.setattr(P.OwnedProcess, 'cleanup', cleanup)
    try:
        result = R.run(plan, workers=1, artifact_root=tmp_path / 'execution', cache_mode='cold')
        journal = json.loads((tmp_path / 'execution/journal.json').read_text())
        assert result['cause']['kind'] == 'KeyboardInterrupt'
        assert not result['cleanup']['complete'] and result['cleanup']['errors'] and result['cleanup']['unresolved']
        assert result['cleanup']['secondary_signals']
        assert journal['status'] == 'PENDING' and not result['reconciliation']['complete']
        time.sleep(1.1)
        assert not marker.exists()
        assert all(not owner.members() for owner in owners)
    finally:
        for owner in owners:real(owner)


def test_real_completed_collection_and_execution_receipts_allow_next_run(tmp_path):
    from al.verification_lifecycle import require_finished_runs
    from al.verification_plan import collect_suites
    from al.verification_profile import capture_source_binding
    root = source(tmp_path)
    artifacts = tmp_path / 'runs'
    directory = artifacts / 'run-prior'
    directory.mkdir(parents=True)
    collections = collect_suites(root, {'artifact_root': str(directory)})
    require_finished_runs(artifacts)
    plan = build_plan(collections, declarations(root), tier='full', changed_paths=[], binding=capture_source_binding(root))
    result = R.run(plan, workers=2, artifact_root=directory / 'execution', cache_mode='cold')
    assert result['reconciliation']['complete'] and result['cleanup']['complete']
    require_finished_runs(artifacts)


def test_post_exit_cleanup_exception_keeps_actual_descendant_owned(tmp_path, monkeypatch):
    plan, marker = descendant_plan(tmp_path)
    owners = observe_owners(monkeypatch)
    real = P.OwnedProcess.cleanup
    failed = []
    def cleanup(owner):
        if not failed:
            failed.append(True)
            raise OSError('post-exit cleanup observation failed')
        return real(owner)
    monkeypatch.setattr(P.OwnedProcess, 'cleanup', cleanup)
    try:
        result = R.run(plan, workers=1, artifact_root=tmp_path / 'execution', cache_mode='cold')
        assert result['cause']['kind'] == 'OSError' and result['cleanup']['complete']
        assert not result['reconciliation']['complete']
        time.sleep(1.1)
        assert not marker.exists()
        assert all(not owner.members() for owner in owners)
    finally:
        for owner in owners:real(owner)
