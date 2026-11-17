"""Bounded private child output. Raw package output never has a durable pathname."""
import hashlib
import os
import selectors
import time

from al_gate_process import stop

MAX_BYTES=2*1024*1024


class Capture:
    def __init__(self):self.last=None

    def run(self,proc,timeout):
        selector=selectors.DefaultSelector();raw=bytearray();hasher=hashlib.sha256();total=0;limited=False;timed_out=False
        deadline=time.monotonic()+timeout
        try:
            selector.register(proc.stdout,selectors.EVENT_READ)
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    timed_out=True;stop(proc);break
                if not selector.select(remaining):
                    timed_out=True;stop(proc);break
                chunk=os.read(proc.stdout.fileno(),65536)
                if not chunk:break
                total+=len(chunk);hasher.update(chunk)
                if len(raw)+len(chunk)>MAX_BYTES:
                    limited=True;stop(proc);break
                raw.extend(chunk)
            if proc.poll() is None:
                try:proc.wait(timeout=max(.01,deadline-time.monotonic()))
                except __import__('subprocess').TimeoutExpired:timed_out=True;stop(proc)
        finally:
            selector.close()
            if proc.stdout:proc.stdout.close()
            stop(proc)
        value={'schema_version':1,'sha256':hasher.hexdigest(),'bytes':total,'complete':not limited and not timed_out,'truncated':limited,'timed_out':timed_out}
        self.last=value
        return proc.returncode,bytes(raw),value


def write_metadata(log,kind,value):
    import json
    # Callers provide closed runtime-generated records, never child excerpts.
    with log.open('a') as stream:stream.write('AL PACKAGE '+kind+' '+json.dumps(value,sort_keys=True)+'\n')


from al_gate_process import Owner


class PrivateOwner(Owner):
    """Cancellation cannot fall back to Owner's legacy raw pipe logging."""
    def cancel(self):
        self.reason=self.reason or 'KeyboardInterrupt';self.defer=True
        if self.stopped:return
        self.stopped=True
        if self.proc is not None:
            stop(self.proc)
            for pipe in (self.proc.stdout,self.proc.stderr):
                if pipe is not None and not pipe.closed:pipe.close()
        write_metadata(self.log,'CANCELLED',{'schema_version':1,'status':'cancelled'})


class InspectionError(Exception):
    """Closed package infrastructure failure; arbitrary exception text is excluded."""
    def __init__(self,boundary,kind,cleanup):
        self.metadata={'schema_version':1,'status':'INFRA','reason':'package output/runtime inspection unavailable',
                       'boundary':boundary,'kind':kind,'cleanup':cleanup}
        super().__init__(self.metadata['reason'])


def _settle(owner):
    """Reap only the current owned process; never drain its raw pipes to a log."""
    proc=owner.proc;failed=False
    if proc is None:return {'complete':True,'group_absent':True,'unresolved':False}
    try:stop(proc)
    except (OSError,ValueError):failed=True
    for pipe in (proc.stdout,proc.stderr):
        if pipe is not None and not pipe.closed:
            try:pipe.close()
            except (OSError,ValueError):failed=True
    try:observed=owner.completion()
    except (OSError,ValueError):observed={};failed=True
    complete=not failed and observed.get('complete') is True and observed.get('group_absent') is True
    return {'complete':complete,'group_absent':observed.get('group_absent') is True,'unresolved':not complete}


def inspect_call(owner,log,boundary,operation,*args):
    """Only trusted package read/capture/parser call sites use this boundary."""
    if boundary not in {'capture','parse','probe','recheck','setup-version','setup-output','runtime-version'}:
        raise ValueError('unknown package inspection boundary')
    try:return operation(*args)
    except (OSError,ValueError) as exc:
        failure=InspectionError(boundary,'io' if isinstance(exc,OSError) else 'invalid-data',_settle(owner))
        write_metadata(log,'INSPECTION',failure.metadata)
        raise failure from None
