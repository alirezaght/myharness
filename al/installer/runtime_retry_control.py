"""Authenticated inherited sealed-al-retry-v1 transport; no environment-only activation."""
import base64
import json
import os
from pathlib import Path
import socket
import struct
import sys
import types

PROTOCOL = 'sealed-al-control-v1'
LIMIT = 12 * 1024 * 1024


def _exact(stream, size):
    out = bytearray()
    while len(out) < size:
        raw, ancillary, flags, _ = stream.recvmsg(size-len(out), socket.CMSG_SPACE(256))
        if ancillary or flags & socket.MSG_CTRUNC or not raw:
            raise ValueError('sealed control framing refused')
        out.extend(raw)
    return bytes(out)


class Client:
    def __init__(self, descriptor, nonce):
        self.stream = socket.socket(fileno=descriptor)
        self.stream.settimeout(20)
        self.nonce, self.sequence = nonce, 0
        self.exec_permit = None

    def request(self, method, operands):
        self.sequence += 1
        raw = json.dumps({'protocol':PROTOCOL,'nonce':self.nonce,'sequence':self.sequence,
                          'method':method,'operands':operands}, separators=(',',':')).encode()
        if len(raw)>LIMIT:raise ValueError('sealed control request limit')
        self.stream.sendall(struct.pack('!I',len(raw))+raw)
        size=struct.unpack('!I',_exact(self.stream,4))[0]
        if not 0<size<=LIMIT:raise ValueError('sealed control response limit')
        value=json.loads(_exact(self.stream,size))
        if set(value)!={'sequence','result'} or value['sequence']!=self.sequence:
            raise ValueError('sealed control response refused')
        return value['result']

    def git(self, form, operands=None, root=None):
        if root is not None and str(Path(root).resolve()) != self.binding['repository']:
            raise ValueError('sealed control repository not bound')
        value=self.request(form,operands or {})
        if set(value)!={'bytes','returncode'} or value['returncode']:
            raise ValueError('sealed Git operation refused')
        return base64.b64decode(value['bytes'],validate=True)

    def blobs(self, root, oids, max_bytes, file_bytes):
        if str(Path(root).resolve())!=self.binding['repository']:raise ValueError('unbound blob repository')
        value=self.request('read_blobs',{'oids':list(oids),'max_bytes':max_bytes,'file_bytes':file_bytes})
        return {key:base64.b64decode(raw,validate=True) for key,raw in value.items()}

    def allow_exec(self, executable, argv):
        if executable!=self.binding['interpreter']:raise ValueError('unbound control interpreter')
        self.exec_permit=(os.fsencode(executable),tuple(argv))
        os.set_inheritable(self.stream.fileno(),True)

    def audit(self, event, args):
        if event in {'subprocess.Popen','os.system','os.posix_spawn','os.spawn','pty.spawn','os.fork','os.forkpty'}:
            raise ValueError('unwrapped spawn in sealed control')
        if event=='os.exec':
            executable,argv,_=args
            expected=self.exec_permit
            self.exec_permit=None
            if expected is None or (os.fsencode(executable),tuple(argv))!=expected:
                raise ValueError('unwrapped exec in sealed control')


def current():
    return getattr(sys.modules.get('_al_retry_control_context'),'client',None)


def activate(stage, *, repository, platform, session, argv, pin=None):
    existing=current()
    if existing:return existing
    descriptor=os.environ.get('HARNESS_RETRY_CONTROL_FD')
    if descriptor is None:return None
    if list(argv[:1])==['retry'] and len(argv)==4 and argv[2]=='--reason':
        profile='sealed-al-retry-v1'
    elif len(argv)==6 and list(argv[:3])==['writer','finish','--id'] and argv[4]=='--outcome' and argv[5] in {'success','failed','abandoned'}:
        profile='sealed-al-writer-finish-v1'
    else:raise ValueError('sealed control exact method refused')
    client=Client(int(descriptor),os.environ['HARNESS_RETRY_CONTROL_NONCE'])
    result=client.request('activate',{'stage':stage,'profile':profile,'repository':str(Path(repository).resolve()),
                          'platform':platform,'session':session,'argv':list(argv),'pin':pin})
    if set(result)!={'protocol','profile','repository','interpreter','pin_identity'} or result['protocol']!=PROTOCOL or result['profile']!=profile:
        raise ValueError('sealed control activation refused')
    client.binding=result
    os.set_inheritable(client.stream.fileno(),False)
    context=types.ModuleType('_al_retry_control_context');context.client=client
    sys.modules['_al_retry_control_context']=context
    sys.addaudithook(client.audit)
    return client


def complete(exit_code=0):
    client=current()
    if client:
        client.request('complete',{'exit_code':exit_code})
        client.stream.close()
