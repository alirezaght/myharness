"""Fixed private inherited channel; never a generic accounting or execution RPC."""
import json
import os
import socket
import struct
from al_schema import refuse


def claim(payload):
    try:
        descriptor = int(os.environ.pop('HARNESS_POLICY_RETRY_FD'))
        nonce = os.environ.pop('HARNESS_POLICY_RETRY_NONCE')
        os.set_inheritable(descriptor, False)
        with socket.socket(fileno=descriptor) as stream:
            stream.settimeout(15)
            raw = json.dumps({'version': 1, 'operation': 'claim', 'nonce': nonce, 'payload': payload}, sort_keys=True, separators=(',', ':')).encode()
            if len(raw) > 65536:
                raise ValueError()
            stream.sendall(struct.pack('!I', len(raw)) + raw)
            def exact(size):
                value = bytearray()
                while len(value) < size:
                    chunk = stream.recv(size-len(value))
                    if not chunk:
                        raise ValueError()
                    value.extend(chunk)
                return bytes(value)
            size = struct.unpack('!I', exact(4))[0]
            if not 0 < size <= 65536:
                raise ValueError()
            grant = json.loads(exact(size))
        if (set(grant) != {'version', 'attempt_id', 'grant_id', 'payload'} or grant['version'] != 1 or grant['payload'] != payload
                or any(not isinstance(grant[key], str) or len(grant[key]) != 32 or any(c not in '0123456789abcdef' for c in grant[key]) for key in ('attempt_id', 'grant_id'))):
            raise ValueError()
        return grant
    except (OSError, ValueError, KeyError, TypeError):
        refuse('policy retry grant unavailable; reconcile recorded attempt, never repeat the exchange')
