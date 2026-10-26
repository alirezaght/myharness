"""Owned process groups and cancellation through the gate's durable finalization."""
import os
import signal
import subprocess
import secrets
import time

SIGNALS={signal.SIGINT,signal.SIGTERM}


class Owner:
    def __init__(self,log):
        self.log=log;self.proc=None;self.reason=None;self.defer=False;self.stopped=False
        self.token=secrets.token_hex(32);self.spawned_at=None

    def __enter__(self):
        self.previous={number:signal.signal(number,self.signal) for number in SIGNALS}
        return self

    def signal(self,number,frame):
        if self.reason is None:self.reason=signal.Signals(number).name
        if not self.defer:
            self.defer=True;raise KeyboardInterrupt

    def spawn(self,*args,**kwargs):
        self.defer=True
        try:
            self.spawned_at=time.time()
            import al_harness_process
            self.proc=al_harness_process.popen(*args,**kwargs,start_new_session=True)
        finally:self.defer=False
        if self.reason:raise KeyboardInterrupt
        return self.proc

    def completion(self):
        """No reusable proof when any owned group member or inspection doubt remains."""
        if self.proc is not None and hasattr(self.proc, 'completion'):
            return dict(self.proc.completion(), ownership='private-Harness-owned-Popen', token=self.token,
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
        self.stopped=True
        if self.proc is not None:
            stop(self.proc)
            if self.proc.stdout is not None or self.proc.stderr is not None:
                try:stdout,stderr=self.proc.communicate(timeout=1)
                except subprocess.TimeoutExpired as exc:
                    stdout,stderr=exc.output,exc.stderr
                    for pipe in (self.proc.stdout,self.proc.stderr):
                        if pipe is not None:pipe.close()
                with self.log.open('ab') as out:out.write(stdout or b'');out.write(stderr or b'')
        with self.log.open('ab') as out:out.write(('\nGate cancelled: '+self.reason+'\n').encode())

    def pending(self):
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
