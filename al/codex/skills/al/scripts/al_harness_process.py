"""Private inherited dispatch for Harness checks; standalone AL retains native Popen."""
import array
import json
import os
import selectors
import socket
import struct
import subprocess
import time


def exact(stream, size):
    data = bytearray()
    while len(data) < size:
        raw = stream.recv(size - len(data))
        if not raw: raise OSError('owned check supervisor disconnected')
        data.extend(raw)
    return bytes(data)


class OwnedCheck:
    def __init__(self, argv, kwargs):
        self.argv = argv;self.returncode = None;self.stdin = None
        self.stream = socket.socket(fileno=os.dup(int(os.environ['HARNESS_CHECK_FD'])))
        self.stream.settimeout(30)
        descriptors, close = [], []
        self.stdout = self.stderr = None
        for name in ('stdout', 'stderr'):
            target = kwargs.get(name)
            if name == 'stderr' and target == subprocess.STDOUT:
                descriptors.append(descriptors[0]);continue
            if target == subprocess.PIPE:
                reader, writer = os.pipe();setattr(self, name, os.fdopen(reader, 'rb'))
                descriptors.append(writer);close.append(writer)
            elif hasattr(target, 'fileno'): descriptors.append(target.fileno())
            elif isinstance(target, int) and target >= 0: descriptors.append(target)
            else: descriptors.append(1 if name == 'stdout' else 2)
        try:
            result = self.rpc({'action':'start','argv':list(argv), 'cwd':str(kwargs.get('cwd') or os.getcwd())}, descriptors)
            self.pid, self.handle = result['pid'], result['handle']
        finally:
            for fd in close: os.close(fd)

    def rpc(self, value, descriptors=()):
        raw = json.dumps(value).encode();frame = struct.pack('!I',len(raw)) + raw
        ancillary = [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))] if descriptors else []
        sent = self.stream.sendmsg([frame],ancillary)
        if sent < len(frame): self.stream.sendall(frame[sent:])
        size = struct.unpack('!I',exact(self.stream,4))[0]
        if not 0 < size <= 65536: raise OSError('invalid owned check response')
        result = json.loads(exact(self.stream,size))
        if 'error' in result: raise OSError(result['error'])
        return result

    def poll(self):
        if self.returncode is None:
            self.returncode = self.rpc({'action':'poll','handle':self.handle})['returncode']
        return self.returncode

    def wait(self, timeout=None):
        started = time.monotonic()
        while self.poll() is None:
            if timeout is not None and time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(self.argv, timeout)
            time.sleep(.05)
        return self.returncode

    def cancel(self):
        self.returncode = self.rpc({'action':'cancel','handle':self.handle})['returncode']
        return self.returncode

    def completion(self):
        return self.rpc({'action':'completion','handle':self.handle})

    def communicate(self, timeout=None):
        outputs = {'stdout':bytearray(),'stderr':bytearray()};started=time.monotonic()
        with selectors.DefaultSelector() as selector:
            for name in outputs:
                pipe=getattr(self,name)
                if pipe is not None:selector.register(pipe,selectors.EVENT_READ,name)
            while selector.get_map():
                if timeout is not None and time.monotonic()-started >= timeout:
                    raise subprocess.TimeoutExpired(self.argv, timeout)
                for key,_ in selector.select(.1):
                    raw=os.read(key.fd,65536)
                    if raw:
                        outputs[key.data].extend(raw)
                        if sum(map(len,outputs.values())) > 16*1024*1024:
                            self.cancel();raise OSError('owned check output exceeds 16 MiB')
                    else:selector.unregister(key.fileobj)
        self.wait(timeout=max(.01,timeout-(time.monotonic()-started)) if timeout else None)
        return bytes(outputs['stdout']),bytes(outputs['stderr'])


def popen(argv, **kwargs):
    if os.environ.get('HARNESS_CHECK_CONTEXT'):
        if not os.environ.get('HARNESS_CHECK_FD'):raise OSError('private Harness check dispatch channel unavailable')
        return OwnedCheck(argv, kwargs)
    return subprocess.Popen(argv, **kwargs)
