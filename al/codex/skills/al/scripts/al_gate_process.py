"""Owned process groups and cancellation through the gate's durable finalization."""
import os
import signal
import subprocess
import secrets
import time

SIGNALS={signal.SIGINT,signal.SIGTERM}

# Ambient settings that switch off or redirect dependency installation. A disposable gate checkout
# installs what its recipe declares, so an exported `UV_NO_SYNC=1` or `PIP_NO_INDEX=1` in the
# supervisor's shell must not turn a check into a spawn failure. A gate that declares one of these
# names in env_names keeps it (declared names are part of its environment fingerprint).
INSTALL_ENV=('UV_NO_SYNC','UV_OFFLINE','UV_FROZEN','UV_LOCKED','UV_NO_INSTALL_PROJECT','UV_NO_INSTALL_WORKSPACE',
             'UV_NO_INSTALL_LOCAL','UV_NO_INSTALL_PACKAGE','UV_NO_BUILD','UV_NO_BUILD_PACKAGE','UV_NO_INDEX',
             'VIRTUAL_ENV','PIP_NO_INDEX','PIP_NO_DEPS','PIP_REQUIRE_VIRTUALENV','npm_config_offline',
             'NPM_CONFIG_OFFLINE','YARN_ENABLE_OFFLINE_MODE','COREPACK_ENABLE_NETWORK')


def gate_environment(declared=()):
    """The ambient environment without undeclared installation switches, and the removed names."""
    env=dict(os.environ)
    removed=sorted(name for name in INSTALL_ENV if name in env and name not in set(declared))
    for name in removed:env.pop(name)
    return env,removed


class Owner:
    def __init__(self,log):
        self.log=log;self.proc=None;self.reason=None;self.defer=False;self.stopped=False
        self.token=secrets.token_hex(32);self.spawned_at=None
        self.entry=None;self.drain_cancelled_output=True

    def __enter__(self):
        self.previous={number:signal.signal(number,self.signal) for number in SIGNALS}
        return self

    def signal(self,number,frame):
        if self.reason is None:self.reason=signal.Signals(number).name
        if not self.defer:
            self.defer=True;raise KeyboardInterrupt

    def spawn(self,*args,**kwargs):
        from al_verification_policy import verification_kwargs
        kwargs=verification_kwargs(kwargs)
        if self.reason:raise KeyboardInterrupt('gate cancelled: '+self.reason)
        if self.proc is not None and not self.completion().get('complete'):
            if self.reason:raise KeyboardInterrupt('gate cancelled: '+self.reason)
            raise OSError('previous gate child cleanup is unresolved')
        self.defer=True;self.stopped=False;self.proc=None
        try:
            self.spawned_at=time.time()
            if os.environ.get('HARNESS_CHECK_CONTEXT'):
                import al_harness_process
                if self.entry is not None:kwargs['process_observation']=self.entry.get('process_observation')
                self.proc=al_harness_process.popen(*args,**kwargs,start_new_session=True)
            else:
                from al_gate_owned_process import OwnedGateProcess
                kwargs.pop('process_observation',None)
                self.proc=OwnedGateProcess(self.log,self.token+'-'+secrets.token_hex(8))
                self.proc.spawn(*args,**kwargs,start_new_session=True)
            if self.entry is not None:
                import al_process_observation
                al_process_observation.spawned(self.entry,self.proc)
            if self.reason:raise KeyboardInterrupt('gate cancelled: '+self.reason)
            return self.proc
        except BaseException as exc:
            try:self.cancel()
            except BaseException as cleanup:
                exc.add_note('Gate launch cleanup failed; its private ownership record is retained.')
                raise exc from cleanup
            raise
        finally:self.defer=False

    def completion(self):
        """No reusable proof when any owned group member or inspection doubt remains."""
        if self.proc is not None and hasattr(self.proc, 'completion'):
            proof=self.proc.completion()
            if proof.get('secondary_signals') and self.reason is None:self.reason=proof['secondary_signals'][0]
            ownership='live-Popen-start_new_session' if hasattr(self.proc,'owner') else 'private-Harness-owned-Popen'
            return dict(proof, ownership=ownership, token=self.token, complete=proof.get('complete') is True and not self.reason,
                        pid=self.proc.pid, pgid=self.proc.pid, owner_pid=os.getpid(),
                        spawned_at=self.spawned_at, observed_at=time.time())
        errors=[];absent=False
        if self.proc is not None and self.proc.poll() is not None:
            try:os.killpg(self.proc.pid,0)
            except ProcessLookupError:absent=True
            except OSError as exc:errors.append(type(exc).__name__)
        return {'ownership':'live-Popen-start_new_session','token':self.token,
                'pid':self.proc.pid if self.proc else None,'pgid':self.proc.pid if self.proc else None,
                'owner_pid':os.getpid(),'spawned_at':self.spawned_at,'observed_at':time.time(),
                'exit':self.proc.returncode if self.proc else None,'group_absent':absent,
                'errors':errors,'unresolved':not absent,
                'complete':absent and not errors and not self.reason}

    def cancel(self):
        self.reason=self.reason or 'KeyboardInterrupt';self.defer=True
        if self.stopped:return
        self.stopped=True;failure=None
        try:
            if self.proc is not None:
                stop(self.proc)
                if self.drain_cancelled_output and (self.proc.stdout is not None or self.proc.stderr is not None):
                    try:stdout,stderr=self.proc.communicate(timeout=1)
                    except subprocess.TimeoutExpired as exc:
                        stdout,stderr=exc.output,exc.stderr
                        for pipe in (self.proc.stdout,self.proc.stderr):
                            if pipe is not None:pipe.close()
                    with self.log.open('ab') as out:out.write(stdout or b'');out.write(stderr or b'')
        except BaseException as exc:failure=exc
        finally:
            if not self.drain_cancelled_output and self.proc is not None:
                for pipe in (self.proc.stdout,self.proc.stderr):
                    try:
                        if pipe is not None and not pipe.closed:pipe.close()
                    except BaseException as exc:
                        if failure is None:failure=exc
                        else:failure.add_note('Gate output pipe closure also failed.')
            try:
                with self.log.open('ab') as out:out.write(('\nGate cancelled: '+self.reason+'\n').encode())
            except BaseException as exc:
                if failure is None:failure=exc
                else:failure.add_note('Gate cancellation metadata could not be written.')
        if failure is not None:raise failure

    def pending(self):
        if self.reason is None and getattr(self.proc,'secondary_signals',()):
            self.reason=self.proc.secondary_signals[0]
        for number in SIGNALS & signal.sigpending():
            signal.sigwait({number})
            if self.reason is None:self.reason=signal.Signals(number).name

    def finish(self,persist):
        self.defer=True
        mask=signal.pthread_sigmask(signal.SIG_BLOCK,SIGNALS)
        try:
            while True:
                self.pending()
                cancelled=bool(self.reason)
                if cancelled:self.cancel()
                try:persist(self.reason)
                except KeyboardInterrupt:
                    self.cancel();continue
                self.pending()
                if self.reason and not cancelled:continue
                break
        finally:
            for number,handler in self.previous.items():signal.signal(number,handler)
            signal.pthread_sigmask(signal.SIG_SETMASK,mask)
        if self.reason:raise KeyboardInterrupt('gate cancelled: '+self.reason)

    def __exit__(self,kind,error,traceback):
        for number,handler in self.previous.items():signal.signal(number,handler)


def stop(proc):
    if hasattr(proc, 'cancel'):return proc.cancel()
    try:os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError:pass
    try:proc.wait(timeout=1)
    except subprocess.TimeoutExpired:pass
    try:os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    return proc.wait()
