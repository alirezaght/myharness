"""Private inherited dispatch for Harness checks; standalone AL retains native Popen."""
import array
import json
import os
import selectors
import socket
import struct
import subprocess
import time
import threading
import weakref


def exact(stream, size):
    data = bytearray()
    while len(data) < size:
        raw = stream.recv(size - len(data))
        if not raw: raise OSError('owned check supervisor disconnected')
        data.extend(raw)
    return bytes(data)


_TRANSPORTS = weakref.WeakValueDictionary()


class _Transport:
    def __init__(self, descriptor):
        self.descriptor = os.dup(descriptor)
        observed = os.fstat(self.descriptor)
        self.identity = (observed.st_dev, observed.st_ino)

    def __del__(self):
        descriptor = getattr(self, 'descriptor', None)
        if descriptor is not None:
            try: os.close(descriptor)
            except OSError: pass


def _transport():
    descriptor = int(os.environ['HARNESS_CHECK_FD'])
    key = (descriptor, os.environ.get('HARNESS_CHECK_CONTEXT'))
    current = _TRANSPORTS.get(key)
    try:
        observed = os.fstat(descriptor)
        identity = (observed.st_dev, observed.st_ino)
    except OSError:
        if current is None:raise
        # Retain the live capability already owned by a prior check object.
        return current
    if current is None or current.identity != identity:
        current = _Transport(descriptor)
        _TRANSPORTS[key] = current
    return current


class OwnedCheck:
    def __init__(self, argv, kwargs):
        self.argv = argv;self.returncode = None;self.stdin = None
        self.transport = _transport()
        descriptors, close = [], []
        self.relays = []
        self.output_bytes = 0
        self.output_lock = threading.Lock()
        self.stdout = self.stderr = None
        policy = kwargs.get('execution_policy')
        if 'execution_policy' in kwargs:
            from al_verification_policy import execution_policy
            if (type(policy) is not dict or policy != execution_policy()
                    or type(policy.get('version')) is not int or 'stdin' in kwargs):
                raise OSError('unsupported managed verification input policy')
            capabilities = self.rpc({'action':'capabilities', 'schema_version':1})
            expected = {'schema_version':1, 'execution_policy':execution_policy()}
            if (capabilities != expected or type(capabilities.get('schema_version')) is not int
                    or type(capabilities['execution_policy'].get('version')) is not int):
                raise OSError('Harness managed verification input policy unavailable')
        try:
            for name in ('stdout', 'stderr'):
                target = kwargs.get(name)
                if name == 'stderr' and target == subprocess.STDOUT:
                    descriptors.append(descriptors[0]);continue
                destination = None
                if target != subprocess.PIPE:
                    destination = (target.fileno() if hasattr(target, 'fileno') else target
                                   if isinstance(target, int) and target >= 0 else 1 if name == 'stdout' else 2)
                reader, writer = os.pipe()
                close.append(writer)
                if target == subprocess.PIPE:
                    try: stream = os.fdopen(reader, 'rb')
                    except BaseException:
                        os.close(reader);raise
                    setattr(self, name, stream)
                else:
                    relay = {'reader': reader, 'target': destination, 'bytes': 0, 'errors': [],
                             'complete': False, 'stop': threading.Event(), 'thread': None}
                    self.relays.append(relay)
                descriptors.append(writer)
            request = {'action':'start','argv':list(argv), 'cwd':str(kwargs.get('cwd') or os.getcwd())}
            if policy is not None:request['execution_policy'] = policy
            if kwargs.get('process_observation') is not None:request['process_observation'] = kwargs['process_observation']
            if kwargs.get('managed_storage') is not None:request['storage'] = kwargs['managed_storage']
            result = self.rpc(request, descriptors)
            handle = result.get('handle')
            if not isinstance(handle, str) or not handle:
                raise OSError('invalid owned check start handle')
            self.handle = handle
            self.pid = result.get('pid')
            if type(self.pid) is not int or self.pid <= 0:
                raise OSError('invalid owned check start pid')
            self.storage = result.get('storage')
            acknowledged = result.get('execution_policy')
            if policy is not None and (type(acknowledged) is not dict or acknowledged != policy
                                       or type(acknowledged.get('version')) is not int):
                raise OSError('Harness managed verification input policy acknowledgement mismatch')
            if kwargs.get('managed_storage') is not None and not self.storage:
                raise OSError('managed check storage response unavailable')
            for relay in self.relays:
                relay['thread'] = threading.Thread(target=self._relay, args=(relay,), daemon=False)
                relay['thread'].start()
        except BaseException as exc:
            cleanup_error = None
            if hasattr(self, 'handle'):
                try:self.cancel()
                except BaseException as cleanup:cleanup_error = cleanup
            for relay in self.relays:
                thread = relay['thread']
                if thread is None or thread.ident is None:os.close(relay['reader'])
                else:relay['stop'].set()
            self._settle_relays()
            for stream in (self.stdout, self.stderr):
                if stream is not None:stream.close()
            if cleanup_error is not None:
                exc.add_note('Owned check launch cancellation failed; its handle is retained.')
                raise exc from cleanup_error
            raise
        finally:
            for fd in close: os.close(fd)

    def _relay(self, relay):
        descriptor = relay['reader']
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(descriptor, selectors.EVENT_READ)
                while not relay['stop'].is_set():
                    if not selector.select(.1):continue
                    raw = os.read(descriptor, 65536)
                    if not raw:
                        relay['complete'] = True
                        return
                    with self.output_lock:
                        self.output_bytes += len(raw)
                        if self.output_bytes > 16 * 1024 * 1024:
                            relay['errors'].append('owned_check_output_limit')
                            return
                    remaining = memoryview(raw)
                    while remaining:
                        written = os.write(relay['target'], remaining)
                        if written <= 0:raise OSError('output write did not progress')
                        remaining = remaining[written:]
                    relay['bytes'] += len(raw)
                relay['errors'].append('owned_check_output_stream_unsettled')
        except (OSError, ValueError):
            relay['errors'].append('owned_check_output_delivery_failed')
        finally:
            os.close(descriptor)

    def _settle_relays(self):
        for relay in self.relays:
            thread = relay['thread']
            if thread is None or thread.ident is None:continue
            thread.join(timeout=2)
            if thread.is_alive():
                relay['stop'].set()
                thread.join(timeout=2)
            if thread.is_alive() and 'owned_check_output_relay_unsettled' not in relay['errors']:
                relay['errors'].append('owned_check_output_relay_unsettled')

    def rpc(self, value, descriptors=()):
        raw = json.dumps(value).encode();frame = struct.pack('!I',len(raw)) + raw
        ancillary = [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))] if descriptors else []
        # Sequential recipe children retain handles, not one open socket per partition.
        with socket.socket(fileno=os.dup(self.transport.descriptor)) as stream:
            stream.settimeout(30)
            sent = stream.sendmsg([frame],ancillary)
            if sent < len(frame): stream.sendall(frame[sent:])
            size = struct.unpack('!I',exact(stream,4))[0]
            if not 0 < size <= 65536: raise OSError('invalid owned check response')
            result = json.loads(exact(stream,size))
        if 'error' in result: raise OSError(result['error'])
        return result

    def poll(self):
        if self.returncode is None:
            self.returncode = self.rpc({'action':'poll','handle':self.handle})['returncode']
            if self.returncode is not None:self._settle_relays()
        return self.returncode

    def wait(self, timeout=None):
        started = time.monotonic()
        while self.poll() is None:
            if any(relay['errors'] for relay in self.relays):self.cancel()
            if timeout is not None and time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(self.argv, timeout)
            time.sleep(.05)
        return self.returncode

    def cancel(self):
        self.returncode = self.rpc({'action':'cancel','handle':self.handle})['returncode']
        self._settle_relays()
        return self.returncode

    def completion(self):
        result = self.rpc({'action':'completion','handle':self.handle})
        self._settle_relays()
        errors = [code for relay in self.relays for code in relay['errors']]
        unsettled = any(relay['thread'] is not None and relay['thread'].is_alive() for relay in self.relays)
        if any(not relay['complete'] for relay in self.relays) and not errors:
            errors.append('owned_check_output_incomplete')
        return result | {'complete': result['complete'] and not errors and not unsettled,
                         'errors': [*result['errors'], *errors], 'unresolved': bool(result['unresolved']) or unsettled,
                         'output': {'complete': not errors and not unsettled, 'bytes': self.output_bytes}}

    def acknowledge_publication(self, reference, *, reconciliation=None):
        request = {'action':'acknowledge_publication', 'schema_version':1,
                   'handle':self.handle, 'publication':reference}
        if reconciliation is not None:request['reconciliation'] = reconciliation
        return self.rpc(request)

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


def request(value):
    client = object.__new__(OwnedCheck)
    client.transport = _transport()
    return client.rpc(value)


def popen(argv, **kwargs):
    if os.environ.get('HARNESS_CHECK_CONTEXT'):
        if not os.environ.get('HARNESS_CHECK_FD'):raise OSError('private Harness check dispatch channel unavailable')
        return OwnedCheck(argv, kwargs)
    kwargs.pop('process_observation', None)
    return subprocess.Popen(argv, **kwargs)


def verification_popen(argv, **kwargs):
    """Managed verification dispatch; generic popen keeps its interactive default."""
    from al_verification_policy import execution_policy, verification_input
    if os.environ.get('HARNESS_CHECK_CONTEXT'):
        if 'stdin' in kwargs:
            raise OSError('explicit managed Harness input is unsupported')
        return popen(argv, **(kwargs | {'execution_policy':execution_policy()}))
    with verification_input(kwargs) as inputs:
        return popen(argv, **inputs)
