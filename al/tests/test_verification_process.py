"""Kernel identity and private receipts constrain actual owned-group cleanup."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest
from al import verification_process as P
from al.verification_plan import build_plan
from al.verification_runner import run
from al.tests.verification_helpers import source, declarations, collected


def child(tmp_path, body='import time;time.sleep(60)'):
    owner=P.OwnedProcess(tmp_path/'owned','specific-assignment')
    output=(tmp_path/'output').open('wb')
    try:owner.spawn([sys.executable,'-c',body],cwd=tmp_path,env=os.environ,output=output)
    except BaseException:
        try:owner.cleanup()
        finally:output.close()
        raise
    return owner,output


def test_actual_private_receipt_binds_kernel_identity_and_assignment(tmp_path):
    owner,out=child(tmp_path)
    try:
        record=owner.record()
        assert record['identity']==P.identity(owner.process.pid)
        assert record['token'] and record['assignment']=='specific-assignment'
        assert Path(record['receipt']).parent.stat().st_mode & 0o777==0o700
        assert owner.members()
    finally:
        try:result=owner.cleanup()
        finally:out.close()
    assert result['complete'] and owner.process.poll() is not None


def test_reused_identity_is_never_signalled(tmp_path,monkeypatch):
    owner,out=child(tmp_path);real=P.identity;signals=[]
    def reused(pid):
        value=real(pid)
        if value and pid==owner.process.pid:value={**value,'start':[n+1 for n in value['start']]}
        return value
    try:
        with monkeypatch.context() as patch:
            patch.setattr(P,'identity',reused)
            patch.setattr(P.os,'killpg',lambda *args:signals.append(args))
            refused=owner.cleanup()
        assert not refused['complete'] and refused['unresolved'] and not signals
        assert owner.process.poll() is None
    finally:
        try:assert owner.cleanup()['complete']
        finally:out.close()


def test_denied_signal_remains_uncertain_and_does_not_claim_cleanup(tmp_path,monkeypatch):
    owner,out=child(tmp_path)
    def denied(*_args):raise PermissionError('owned signal denied')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(P.os,'killpg',denied)
            result=owner.cleanup()
        assert not result['complete'] and result['errors'] and result['unresolved']
        assert owner.process.poll() is None
    finally:
        try:assert owner.cleanup()['complete']
        finally:out.close()


def test_normal_exit_cannot_leave_descendant_late_write(tmp_path):
    marker=tmp_path/'late'
    body='import subprocess,sys;subprocess.Popen([sys.executable,"-c",'+repr('import time;from pathlib import Path;time.sleep(1);Path('+repr(str(marker))+').write_text("leaked")')+'])'
    owner,out=child(tmp_path,body)
    try:owner.process.wait(timeout=3)
    finally:
        try:result=owner.cleanup()
        finally:out.close()
    assert result['complete'] and not owner.members()
    time.sleep(1.1)
    assert not marker.exists()


def test_repeated_cancellation_cannot_interrupt_owned_cleanup(tmp_path):
    ready=tmp_path/'ready'
    owner,out=child(tmp_path,'import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('+repr(str(ready))+').write_text("ready");time.sleep(60)')
    timers=[]
    try:
        until=time.monotonic()+2
        while not ready.exists() and time.monotonic()<until:time.sleep(.01)
        assert ready.exists()
        timers=[threading.Timer(delay,lambda:os.kill(os.getpid(),signal.SIGINT)) for delay in (.1,.2)]
        for timer in timers:timer.start()
        result=owner.cleanup()
    finally:
        try:
            # Timers were started without masking; preserve the real repeated-SIGINT test.
            # A callback already firing during cancellation/join must stay contained too.
            with P.cleanup_barrier([]):
                for timer in timers:timer.cancel()
                for timer in timers:
                    if timer.ident is not None:timer.join()
        finally:
            try:owner.cleanup()
            finally:out.close()
    assert result['complete'] and result['secondary_signals']
    assert owner.process.poll() is not None


def test_journal_failure_after_real_spawn_preserves_cleanup_ownership(tmp_path,monkeypatch):
    import al.verification_runner as R
    root=source(tmp_path,{s:'import time\ndef test_a():\n time.sleep(60)\n' for s in ('installer','codex','claude')})
    collections,binding=collected(root,tmp_path)
    plan=build_plan(collections,declarations(root),tier='full',changed_paths=[],binding=binding)
    real=R.write_json;fired=[];owned=[];spawn=P.OwnedProcess.spawn
    def observed(owner,*args,**kwargs):
        proc=spawn(owner,*args,**kwargs);owned.append(owner);return proc
    def failed(path,value):
        if Path(path).name=='journal.json' and value.get('children') and not fired:
            fired.append(True);raise OSError('publication after Popen failed')
        return real(path,value)
    monkeypatch.setattr(P.OwnedProcess,'spawn',observed);monkeypatch.setattr(R,'write_json',failed)
    result=run(plan,workers=2,artifact_root=tmp_path/'execution',cache_mode='cold')
    assert fired and owned and result['cause']['kind']=='OSError'
    assert result['cleanup']['complete'] and not result['reconciliation']['complete']
    assert all(owner.process.poll() is not None and not owner.members() for owner in owned)


def test_spawn_failure_is_incomplete_without_invented_child_receipts(tmp_path,monkeypatch):
    root=source(tmp_path);collections,binding=collected(root,tmp_path)
    plan=build_plan(collections,declarations(root),tier='full',changed_paths=[],binding=binding)
    def failed(*_args,**_kwargs):raise OSError('cannot start child')
    monkeypatch.setattr(P.OwnedProcess,'spawn',failed)
    result=run(plan,workers=2,artifact_root=tmp_path/'execution',cache_mode='cold')
    assert result['cause']['kind']=='OSError' and result['cleanup']['complete']
    assert not result['shards'] and not result['reconciliation']['complete']


def test_owned_collection_timeout_retains_failure_and_reaps(tmp_path):
    with (tmp_path/'output').open('wb') as output:
        with pytest.raises(subprocess.TimeoutExpired):
            P.execute([sys.executable,'-c','import time;time.sleep(60)'],cwd=tmp_path,env=os.environ,
                      output=output,directory=tmp_path/'collection',assignment='collection:test',timeout=.1)
    import json
    journal=json.loads((tmp_path/'collection/process-journal.json').read_text())
    assert journal['status']=='FINISHED' and journal['cleanup']['complete']
    assert journal['cause']['kind']=='TimeoutExpired'
    assert P.identity(journal['ownership']['pid']) is None


def _capture_children(monkeypatch):
    actors = []
    original = child

    def captured(*args, **kwargs):
        owner, output = original(*args, **kwargs)
        actors.append((owner, output))
        return owner, output

    monkeypatch.setitem(globals(), 'child', captured)
    return actors


def _settle_captured(actors, timers=(), release=None):
    """Independent fallback: regressions must fail without leaving their real actors."""
    results = []
    try:
        with P.cleanup_barrier([]):
            if release is not None:
                release.set()
            for timer in timers:
                timer.cancel()
            for timer in timers:
                if timer.ident is not None:
                    timer.join(timeout=2)
    finally:
        for owner, output in actors:
            try:
                results.append(owner.cleanup())
            finally:
                output.close()
    assert all(result['complete'] for result in results)
    assert all(not timer.is_alive() for timer in timers)


def test_readiness_failure_settles_real_owned_child_and_preserves_failure(tmp_path, monkeypatch):
    actors = _capture_children(monkeypatch)
    ready = tmp_path / 'ready'
    exists = Path.exists
    failure = RuntimeError('injected readiness observation failure')

    def unreadable(path):
        # Wait for the real child to install SIGTERM-ignore before failing its readiness read.
        if path == ready and exists(path):
            raise failure
        return exists(path)

    monkeypatch.setattr(Path, 'exists', unreadable)
    try:
        with pytest.raises(RuntimeError) as raised:
            test_repeated_cancellation_cannot_interrupt_owned_cleanup(tmp_path)
        before_fallback = [(owner.process.poll(), owner.members(), output.closed)
                           for owner, output in actors]
    finally:
        _settle_captured(actors)
    assert raised.value is failure
    assert exists(ready), 'failure must follow actual child readiness'
    assert len(actors) == len(before_fallback) == 1
    assert before_fallback[0][0] is not None and not before_fallback[0][1]
    assert before_fallback[0][2], 'the code under test must close output before fallback'


def test_partial_timer_start_failure_joins_firing_timer_and_settles_child(tmp_path, monkeypatch):
    actors = _capture_children(monkeypatch)
    original_timer = threading.Timer
    timers = []
    callback_started, release, callback_sent = (threading.Event() for _ in range(3))
    failure = RuntimeError('injected second timer start failure')

    def timer_factory(delay, function):
        first = not timers

        def callback():
            callback_started.set()
            if release.wait(timeout=2):
                function()  # The real callback sends SIGINT to this pytest process.
                callback_sent.set()

        timer = original_timer(delay, callback if first else function)
        if first:
            cancel = timer.cancel

            def cancel_and_release():
                cancel()
                release.set()

            timer.cancel = cancel_and_release
        else:
            def failed_start():
                assert callback_started.wait(timeout=2), 'first real timer never entered callback'
                raise failure

            timer.start = failed_start
        timers.append(timer)
        return timer

    monkeypatch.setattr(threading, 'Timer', timer_factory)
    try:
        with pytest.raises(RuntimeError) as raised:
            test_repeated_cancellation_cannot_interrupt_owned_cleanup(tmp_path)
        before_fallback = [(owner.process.poll(), owner.members(), output.closed)
                           for owner, output in actors]
        timer_state = [(timer.ident, timer.is_alive()) for timer in timers]
        signal_sent_before_fallback = callback_sent.is_set()
    finally:
        _settle_captured(actors, timers, release)
    assert raised.value is failure
    assert len(actors) == len(before_fallback) == 1
    assert before_fallback[0][0] is not None and not before_fallback[0][1]
    assert before_fallback[0][2], 'the code under test must close output before fallback'
    assert len(timer_state) == 2 and timer_state[0][0] is not None and timer_state[1][0] is None
    assert not any(alive for _, alive in timer_state)
    assert signal_sent_before_fallback, 'a real SIGINT must fire during protected finalization'
