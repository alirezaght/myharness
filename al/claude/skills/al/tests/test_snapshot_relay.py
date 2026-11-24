"""#34: inside the Linux operation sandbox AL asks the service through an inherited relay
descriptor (`AL_SERVICE_FD=<fd>:<inode>`), never by connecting to a socket path."""
import json
import os
import socket
import struct
import tempfile
import threading

import al_snapshot


def _frame(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return struct.pack('>I', len(data)) + data


def _relay(outer, seen, tree):
    def serve():
        with outer:
            while True:
                head = outer.recv(4)
                if not head:
                    return
                seen.append(json.loads(outer.recv(struct.unpack('>I', head)[0])))
                outer.sendall(_frame({'event': 'snapshot', 'tree': tree}))
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def test_relay_requests_are_sequential_snapshot_frames_without_a_token(repo):
    outer, inner = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    seen = []
    _relay(outer, seen, 'b' * 40)
    value = f'{inner.fileno()}:{os.fstat(inner.fileno()).st_ino}'
    with inner:
        for _ in range(2):  # one descriptor serves every snapshot of the process
            assert al_snapshot._relay_answer(repo, value) == {'event': 'snapshot', 'tree': 'b' * 40}
    assert seen == [{'op': 'snapshot', 'repository': str(repo)}] * 2


def test_relay_answer_is_checked_like_a_direct_one(repo, monkeypatch):
    outer, inner = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    _relay(outer, [], 'c' * 40)  # not a tree of this repository
    with inner:
        monkeypatch.setenv('AL_SERVICE_FD', f'{inner.fileno()}:{os.fstat(inner.fileno()).st_ino}')
        assert al_snapshot._service_tree(repo) is None


def test_descriptor_that_is_not_the_relay_is_ignored(repo):
    with tempfile.TemporaryFile() as handle:
        fd = handle.fileno()
        assert al_snapshot._relay_answer(repo, f'{fd}:{os.fstat(fd).st_ino}') is None
        assert al_snapshot._relay_answer(repo, f'{fd}:1') is None
        assert handle.read() == b''
    assert al_snapshot._relay_answer(repo, 'not-a-descriptor') is None
