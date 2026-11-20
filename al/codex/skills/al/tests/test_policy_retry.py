"""The fixed policy claim runs after every existing retry precondition, under the AL lock."""
import json
import os
import socket
import struct
import threading

import pytest
from hook_regression_helpers import invoke


def _receive(stream):
    def exact(size):
        value=b''
        while len(value)<size:
            raw=stream.recv(size-len(value))
            if not raw:raise EOFError()
            value+=raw
        return value
    return json.loads(exact(struct.unpack('!I',exact(4))[0]))


def _grant(monkeypatch,*,transform=None,disconnect=False):
    server,client=socket.socketpair();server.settimeout(3)
    descriptor=os.dup(client.fileno())
    monkeypatch.setenv('HARNESS_POLICY_RETRY_REQUIRED','1')
    monkeypatch.setenv('HARNESS_POLICY_RETRY_FD',str(descriptor))
    monkeypatch.setenv('HARNESS_POLICY_RETRY_NONCE','one-use-fixture')
    requests=[]
    def serve():
        try:
            request=_receive(server);requests.append(request)
            if disconnect:return
            value={'version':1,'attempt_id':'a'*32,'grant_id':'b'*32,'payload':request['payload']}
            if transform:value=transform(value)
            raw=json.dumps(value).encode();server.sendall(struct.pack('!I',len(raw))+raw)
        except (EOFError,OSError):pass
        finally:server.close()
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    return client,descriptor,thread,requests


def test_real_retry_state_lock_claim_records_exact_grant(cli,state_file,monkeypatch):
    invoke(cli,'start','--spec','Policy grants restrict the original bounded retry edge.')
    invoke(cli,'agent-failed','al-engineer','--reason','implementation runner failed','--retry-safe','yes')
    before=json.loads(state_file().read_text())
    client,descriptor,thread,requests=_grant(monkeypatch)
    try:
        invoke(cli,'retry','al-engineer','--reason','Inspected stable source and safe checkpoint.')
        thread.join(4);assert not thread.is_alive()
        assert requests[0]['operation']=='claim' and requests[0]['nonce']=='one-use-fixture'
        assert requests[0]['payload']['failure']==before['failure']
        after=json.loads(state_file().read_text())
        assert after['retries']['al-engineer']==1 and after['failure'] is None
        assert after['retry_history'][-1]['policy_attempt_id']=='a'*32
        assert after['retry_history'][-1]['policy_grant_id']=='b'*32
        with pytest.raises(OSError):os.fstat(descriptor)
    finally:client.close()


@pytest.mark.parametrize('mode',['wrong-binding','disconnect'])
def test_bad_or_lost_grant_never_applies_checkpoint_or_changes_retry_count(cli,state_file,monkeypatch,mode):
    invoke(cli,'start','--spec','An ambiguous grant is never retried or treated as authorization.')
    invoke(cli,'agent-failed','al-engineer','--reason','implementation runner failed','--retry-safe','yes')
    before=state_file().read_bytes()
    transform=lambda value:dict(value,payload={'foreign':'failure'})
    client,descriptor,thread,requests=_grant(monkeypatch,transform=transform,disconnect=mode=='disconnect')
    try:
        invoke(cli,'retry','al-engineer','--reason','Inspect checkpoint and reconcile.',ok=False)
        thread.join(4)
        assert len(requests)==1 and state_file().read_bytes()==before
    finally:client.close()


def test_existing_retry_refusal_precedes_channel_claim(cli,state_file,monkeypatch):
    invoke(cli,'start','--spec','Missing failure cannot claim a policy retry grant.')
    monkeypatch.setenv('HARNESS_POLICY_RETRY_REQUIRED','1')
    monkeypatch.setenv('HARNESS_POLICY_RETRY_FD','-1')
    before=state_file().read_bytes()
    code,out,err=invoke(cli,'retry','al-engineer','--reason','No matching failure.',ok=False)
    assert 'matching pending' in str(code)+out+err
    assert state_file().read_bytes()==before


def test_once_per_role_refusal_keeps_original_stopped_status_without_a_new_grant(cli,state_file,monkeypatch):
    invoke(cli,'start','--spec','A policy grant cannot invent another original retry allowance.')
    invoke(cli,'agent-failed','al-engineer','--reason','implementation runner failed','--retry-safe','yes')
    client,descriptor,thread,requests=_grant(monkeypatch)
    try:
        invoke(cli,'retry','al-engineer','--reason','Inspected safe checkpoint.')
        thread.join(4)
        code,out,err=invoke(cli,'retry','al-engineer','--reason','No further original allowance.',ok=False)
        assert 'already retried once' in str(code)+out+err
        state=json.loads(state_file().read_text())
        assert state['status']=='STOPPED_WITH_BLOCKERS' and state['retries']['al-engineer']==1
        assert len(requests)==1 and len(state['retry_history'])==1
    finally:client.close()


def test_checkpoint_bytes_are_reverified_after_grant_before_recovery(cli,state_file,monkeypatch):
    from pathlib import Path
    invoke(cli,'start','--spec','Grant accounting cannot skip the original immutable checkpoint read.')
    invoke(cli,'agent-failed','al-engineer','--reason','implementation runner failed','--retry-safe','yes')
    before=state_file().read_bytes();state=json.loads(before)
    path=Path(state['failure']['checkpoint']['stored_path']);original=path.read_bytes()
    def drift(value):
        path.write_bytes(original+b'\n')
        return value
    client,descriptor,thread,requests=_grant(monkeypatch,transform=drift)
    try:
        code,out,err=invoke(cli,'retry','al-engineer','--reason','Inspect source and checkpoint.',ok=False)
        thread.join(4)
        assert 'immutable artifact was altered' in str(code)+out+err
        assert len(requests)==1 and state_file().read_bytes()==before
    finally:
        path.write_bytes(original);client.close()
