"""Gate launch retains private cleanup authority before returning a child handle."""
from contextlib import contextmanager
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest
import al_check_setup as Setup
import al_gate_process as Gate

PYTHON=str(Path(sys.executable).resolve())


@pytest.fixture
def lifecycle(monkeypatch):
    from launcher_context import AL_ROOT
    monkeypatch.syspath_prepend(str(AL_ROOT))
    from installer import owned_process
    created=[];original=owned_process.OwnedProcess.__init__
    def retain(owner,*args,**kwargs):
        original(owner,*args,**kwargs);created.append(owner)
    monkeypatch.setattr(owned_process.OwnedProcess,'__init__',retain)
    yield owned_process
    for owner in reversed(created):
        try:proof=owner.cleanup()
        finally:
            if owner.process is not None:
                for stream in (owner.process.stdin,owner.process.stdout,owner.process.stderr):
                    if stream is not None and not stream.closed:stream.close()
        assert proof['complete'],proof


@contextmanager
def signal_window(lifecycle,tmp_path,monkeypatch,secret=b''):
    """The red path also has an independent, receipt-bound teardown owner."""
    ready_read,ready_write=os.pipe();hold_read,hold_write=os.pipe()
    body=('import os;os.write(1,'+repr(secret)+');os.write(2,'+repr(secret)+');'
          'os.write('+str(ready_write)+',b"R");os.read('+str(hold_read)+',1)')
    argv=[PYTHON,'-I','-c',body];created=[];known=[];fired=[]
    original_init=lifecycle.OwnedProcess.__init__;popen=subprocess.Popen
    def registered(owner,*args,**kwargs):
        original_init(owner,*args,**kwargs);created.append(owner)
    def spawn(command,*args,**kwargs):
        logical=[os.fspath(value) for value in command]
        if logical==argv:
            # Original gate Popen has no receipt. This test-owned wrapper makes its
            # teardown safe even when the old Owner abandons the returned child.
            guard=lifecycle.OwnedProcess(tmp_path/'red-guardian','gate-launch-red-guardian')
            return guard.spawn(argv,cwd=kwargs.get('cwd'),env=kwargs.get('env') or dict(os.environ),
                               output=kwargs.get('stdout'),pass_fds=(ready_write,hold_read))
        if logical[-len(argv):]!=argv:return popen(command,*args,**kwargs)
        receipt=Path(logical[-len(argv)-2])
        guard=next(owner for owner in created if owner.receipt==receipt)
        kwargs['pass_fds']=tuple(set(kwargs.get('pass_fds',()))|{ready_write,hold_read})
        proc=popen(command,*args,**kwargs);known.append((guard,proc))
        assert select.select([ready_read],[],[],5)[0], 'owned child never became ready'
        assert os.read(ready_read,1)==b'R'
        fired.append(proc.pid);os.kill(os.getpid(),signal.SIGTERM)
        return proc
    try:
        with monkeypatch.context() as patch:
            patch.setattr(lifecycle.OwnedProcess,'__init__',registered)
            patch.setattr(subprocess,'Popen',spawn)
            try:yield argv,known,fired
            finally:
                for guard,proc in reversed(known):
                    # Retain the exact Popen created for this guard's receipt even
                    # if the test's readiness assertion prevented spawn returning.
                    if guard.process is None:guard.process=proc
                    proof=guard.cleanup();assert proof['complete'],proof
    finally:
        for descriptor in (ready_read,ready_write,hold_read,hold_write):os.close(descriptor)


@contextmanager
def held_child():
    ready_read,ready_write=os.pipe();hold_read,hold_write=os.pipe()
    argv=[PYTHON,'-I','-c','import os;os.write('+str(ready_write)+',b"R");os.read('+str(hold_read)+',1)']
    def ready():
        assert select.select([ready_read],[],[],5)[0], 'owned child never became ready'
        assert os.read(ready_read,1)==b'R'
    try:yield argv,(ready_write,hold_read),ready
    finally:
        for descriptor in (ready_read,ready_write,hold_read,hold_write):os.close(descriptor)


def test_real_setup_pre_return_signal_settles_child_and_cannot_record_pass(tmp_path,monkeypatch,lifecycle):
    root=tmp_path/'checkout';root.mkdir()
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    tool=Setup.executable(PYTHON);rows=[];log=tmp_path/'setup.log'
    with signal_window(lifecycle,tmp_path,monkeypatch) as (argv,known,fired):
        recipe={'schema_version':1,'kind':'check-setup-v1','manager':'controlled',
                'argv':argv,'cwd':'.','inputs':[]}
        monkeypatch.setattr(Setup,'plan',lambda *_args,**_kwargs:recipe)
        monkeypatch.setattr(Setup,'executable',lambda _name:tool)
        with Gate.Owner(log) as owner:
            with pytest.raises(KeyboardInterrupt):Setup.execute(root,'.',owner,log,10,rows)
            assert fired and known
            assert all(proc.poll() is not None and not guard.members() for guard,proc in known)
            assert owner.reason=='SIGTERM' and owner.stopped
            assert rows and all(row['status']!='PASS' for row in rows)


def test_private_pre_return_cancel_never_writes_package_pipes(tmp_path,monkeypatch,lifecycle):
    from al_package_output import PrivateOwner
    secret=('private-package-'+os.urandom(16).hex()).encode();log=tmp_path/'private.log'
    with signal_window(lifecycle,tmp_path,monkeypatch,secret) as (argv,known,fired):
        with PrivateOwner(log) as owner:
            with pytest.raises(KeyboardInterrupt):
                owner.spawn(argv,cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            assert fired and all(proc.poll() is not None and not guard.members() for guard,proc in known)
            assert owner.reason=='SIGTERM' and owner.stopped
        assert b'AL PACKAGE CANCELLED' in log.read_bytes()
        assert all(secret not in path.read_bytes() for path in tmp_path.rglob('*') if path.is_file())


def test_streams_exit_environment_and_next_child_are_preserved(tmp_path,lifecycle):
    import al_gate_owned_process  # noqa: F401 — required new capability before starting a child
    log=tmp_path/'gate.log'
    with Gate.Owner(log) as owner:
        argv=[PYTHON,'-I','-c','import os;assert os.getcwd()==os.environ["EXPECTED_CWD"];os.write(1,b"out\\x00");os.write(2,b"err\\xff");raise SystemExit(7)']
        proc=owner.spawn(argv,cwd=tmp_path,env=dict(os.environ,EXPECTED_CWD=str(tmp_path)),
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert list(proc.args)==argv
        assert proc.communicate(timeout=5)==(b'out\0',b'err\xff')
        assert proc.wait(timeout=5)==proc.poll()==proc.returncode==7
        Gate.stop(proc)
        assert owner.completion()['complete'] and owner.completion()['group_absent']
        with log.open('ab') as output:
            next_argv=[PYTHON,'-I','-c','import os;os.write(1,b"file-out");os.write(2,b"file-err");raise SystemExit(3)']
            next_proc=owner.spawn(next_argv,cwd=tmp_path,stdout=output,stderr=subprocess.STDOUT)
            assert list(next_proc.args)==next_argv and list(proc.args)==argv
            try:assert next_proc.wait(timeout=5)==3
            finally:Gate.stop(next_proc)
        assert next_proc is not proc
        assert owner.completion()['complete'] and log.read_bytes()==b'file-outfile-err'


def test_active_child_handle_cannot_be_replaced(tmp_path,lifecycle):
    import al_gate_owned_process  # noqa: F401 — required new capability before starting a child
    with Gate.Owner(tmp_path/'gate.log') as owner:
        try:
            proc=owner.spawn([PYTHON,'-I','-c','import sys;sys.stdin.buffer.read(1)'],stdin=subprocess.PIPE)
            with pytest.raises(OSError,match='previous gate child'):
                owner.spawn([PYTHON,'-I','-c','pass'])
            assert owner.proc is proc and proc.poll() is None
        finally:
            if owner.proc is not None:Gate.stop(owner.proc)
            if owner.proc is not None and owner.proc.stdin is not None:owner.proc.stdin.close()


@pytest.mark.parametrize('boundary',['receipt','observation'])
def test_post_spawn_failure_preserves_exception_and_settles_child(tmp_path,monkeypatch,lifecycle,boundary):
    import al_gate_owned_process  # noqa: F401 — required new capability before starting a child
    failure=OSError('controlled post-spawn failure');seen=[]
    with Gate.Owner(tmp_path/'gate.log') as owner:
        owner.entry={}
        if boundary=='receipt':
            original=lifecycle.OwnedProcess.capture_identity
            def fail(process,*args,**kwargs):
                value=original(process,*args,**kwargs)
                if not seen:seen.append(process);raise failure
                return value
            monkeypatch.setattr(lifecycle.OwnedProcess,'capture_identity',fail)
        else:
            import al_process_observation
            def fail(*_args):seen.append(owner.proc);raise failure
            monkeypatch.setattr(al_process_observation,'spawned',fail)
        try:
            with pytest.raises(OSError) as caught:
                owner.spawn([PYTHON,'-I','-c','import sys;sys.stdin.buffer.read(1)'],stdin=subprocess.PIPE)
            assert caught.value is failure and seen
            assert owner.proc.poll() is not None and not owner.proc.owner.members()
            assert owner.stopped and not owner.completion()['complete']
        finally:
            if owner.proc is not None:
                assert owner.proc.owner.cleanup()['complete']
                if owner.proc.stdin is not None:owner.proc.stdin.close()


def test_missing_receipt_cleanup_stays_unknown_and_preserves_original_failure(tmp_path,monkeypatch,lifecycle):
    import al_gate_owned_process  # noqa: F401 — required new capability before starting a child
    failure=OSError('controlled receipt unavailable')
    with held_child() as (argv,pass_fds,ready):
        with Gate.Owner(tmp_path/'gate.log') as owner:
            try:
                original=lifecycle.OwnedProcess.capture_identity
                def unavailable(process,*args,**kwargs):
                    original(process,*args,**kwargs)
                    process.launch_identity=None
                    raise failure
                with monkeypatch.context() as patch:
                    patch.setattr(lifecycle.OwnedProcess,'capture_identity',unavailable)
                    with pytest.raises(OSError) as caught:
                        owner.spawn(argv,stdin=subprocess.PIPE,pass_fds=pass_fds)
                    ready()
                    assert caught.value is failure and isinstance(caught.value.__cause__,OSError)
                    assert owner.proc is not None and owner.proc.poll() is None
                    assert not owner.completion()['complete'] and owner.completion()['unresolved']
            finally:
                if owner.proc is not None:
                    assert owner.proc.owner.cleanup()['complete']
                    if owner.proc.stdin is not None:owner.proc.stdin.close()


def test_foreign_identity_is_not_signal_authority(tmp_path,monkeypatch,lifecycle):
    import al_gate_owned_process  # noqa: F401 — required new capability before starting a child
    with held_child() as (argv,pass_fds,ready):
        with Gate.Owner(tmp_path/'gate.log') as owner:
            try:
                proc=owner.spawn(argv,stdin=subprocess.PIPE,pass_fds=pass_fds);ready()
                real=lifecycle.identity;signals=[]
                def reused(pid):
                    row=real(pid)
                    if row is not None and pid==proc.pid:row={**row,'start':[part+1 for part in row['start']]}
                    return row
                with monkeypatch.context() as patch:
                    patch.setattr(lifecycle,'identity',reused)
                    patch.setattr(lifecycle.os,'killpg',lambda *args:signals.append(args))
                    with pytest.raises(OSError,match='unresolved'):Gate.stop(proc)
                    assert not signals and proc.poll() is None
                    proof=owner.completion();assert not proof['complete'] and proof['unresolved'] and proof['errors']
            finally:
                if owner.proc is not None:
                    assert owner.proc.owner.cleanup()['complete']
                    if owner.proc.stdin is not None:owner.proc.stdin.close()


def test_signal_during_ordinary_cleanup_cannot_become_a_later_pass(tmp_path,monkeypatch,lifecycle):
    import al_gate_owned_process  # noqa: F401 — required new capability before starting a child
    with Gate.Owner(tmp_path/'gate.log') as owner:
        proc=owner.spawn([PYTHON,'-I','-c','pass'])
        assert proc.wait(timeout=5)==0
        original=proc.owner.members;fired=[]
        def interrupted():
            if not fired:fired.append(True);os.kill(os.getpid(),signal.SIGTERM)
            return original()
        with monkeypatch.context() as patch:
            patch.setattr(proc.owner,'members',interrupted)
            Gate.stop(proc)
        persisted=[]
        with pytest.raises(KeyboardInterrupt):owner.finish(lambda reason:persisted.append(reason))
        assert fired and persisted==['SIGTERM'] and owner.reason=='SIGTERM'
        assert not owner.completion()['complete']


def test_ordinary_cancel_preserves_legacy_pipe_output(tmp_path,monkeypatch,lifecycle):
    secret=('ordinary-output-'+os.urandom(16).hex()).encode();log=tmp_path/'ordinary.log'
    with signal_window(lifecycle,tmp_path,monkeypatch,secret) as (argv,known,fired):
        with Gate.Owner(log) as owner:
            with pytest.raises(KeyboardInterrupt):
                owner.spawn(argv,cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            assert fired and all(proc.poll() is not None and not guard.members() for guard,proc in known)
        assert log.read_bytes().count(secret)==2
        assert b'Gate cancelled: SIGTERM' in log.read_bytes()


def test_actual_github_pre_return_cancel_keeps_provider_output_private(repo,tmp_path,monkeypatch,lifecycle):
    import al_remote_github as Remote
    from test_remote_github import gate_for
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    secret=('private-provider-'+os.urandom(16).hex()).encode();log=tmp_path/'remote.log'
    with signal_window(lifecycle,tmp_path,monkeypatch,secret) as (local_argv,known,fired):
        calls=[]
        class LocalOwner(Gate.Owner):
            def spawn(self,argv,**kwargs):
                assert argv[:6]==['gh','api','--hostname','github.com','--method','GET']
                calls.append(argv)
                return super().spawn(local_argv,**kwargs)
        with LocalOwner(log) as owner:
            entry={};owner.entry=entry
            with pytest.raises(KeyboardInterrupt):
                Remote.execute({'identity':{'merge_base':sha}},gate,'candidate',log,10,'local-fixture',entry,owner)
            assert calls and fired and known
            assert all(proc.poll() is not None and not guard.members() for guard,proc in known)
            assert owner.reason=='SIGTERM' and owner.stopped
            assert owner.proc.stdout is None
        assert b'provider output redacted' in log.read_bytes()
        assert b'Gate cancelled: SIGTERM' in log.read_bytes()
        assert all(secret not in path.read_bytes() for path in tmp_path.rglob('*') if path.is_file())


def test_non_draining_cancel_closes_pipes_even_when_stop_fails(tmp_path,monkeypatch,lifecycle):
    import al_gate_owned_process  # noqa: F401 — required new capability before starting a child
    failure=OSError('controlled stop unavailable');log=tmp_path/'gate.log'
    with held_child() as (argv,pass_fds,ready):
        with Gate.Owner(log) as owner:
            owner.drain_cancelled_output=False
            try:
                proc=owner.spawn(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=pass_fds);ready()
                with monkeypatch.context() as patch:
                    patch.setattr(Gate,'stop',lambda _proc:(_ for _ in ()).throw(failure))
                    with pytest.raises(OSError) as caught:owner.cancel()
                    assert caught.value is failure and proc.poll() is None
                    assert proc.stdout.closed and proc.stderr.closed
                    assert log.read_bytes()==b'\nGate cancelled: KeyboardInterrupt\n'
            finally:
                if owner.proc is not None:assert owner.proc.owner.cleanup()['complete']


def test_harness_context_keeps_existing_dispatch(tmp_path,monkeypatch,lifecycle):
    import al_harness_process
    calls=[]
    class HarnessChild:
        pid=123;returncode=0;stdout=stderr=None
        def completion(self):return {'complete':True,'group_absent':True,'errors':[],'unresolved':False}
        def cancel(self):return self.returncode
    child=HarnessChild()
    def dispatch(*args,**kwargs):calls.append((args,kwargs));return child
    monkeypatch.setenv('HARNESS_CHECK_CONTEXT','private-test-context')
    monkeypatch.setattr(al_harness_process,'popen',dispatch)
    with Gate.Owner(tmp_path/'gate.log') as owner:
        owner.entry={'process_observation':{'mode':'harness','binding':'fixture'}}
        monkeypatch.setattr(__import__('al_process_observation'),'spawned',lambda *_args:None)
        assert owner.spawn(['fixed-check'],stdout=subprocess.PIPE) is child
        assert calls==[((['fixed-check'],),{'stdout':subprocess.PIPE,'execution_policy':{'version':2,'default_stdin':'closed-pipe'},'process_observation':owner.entry['process_observation'],'start_new_session':True})]
        assert owner.completion()['ownership']=='private-Harness-owned-Popen'
