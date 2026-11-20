"One prebound retry claim; broker never acquires controller, AL or process locks."

import secrets
import socket
import threading

from harness.execution_initialization_channel import receive, send
from harness.policy.contracts import require


class RetryChannel:
    def __init__(self, attempt, expected):
        self.attempt, self.expected = attempt, expected
        self.nonce = secrets.token_hex(32)
        self.server, self.client = socket.socketpair()
        self.server.settimeout(300)
        self.used = False
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def descriptor(self):
        return self.client.fileno()

    def spawned(self):
        self.client.close()

    def claim(self, request):
        require(
            not self.used
            and isinstance(request, dict)
            and set(request) == {"version", "operation", "nonce", "payload"},
            "CAPABILITY_INVALID",
        )
        require(
            request["version"] == 1
            and request["operation"] == "claim"
            and request["nonce"] == self.nonce
            and request["payload"] == self.expected,
            "CAPABILITY_INVALID",
        )
        self.used = True
        grant = secrets.token_hex(16)
        self.attempt.start(retry_claim=grant)
        return {
            "version": 1,
            "attempt_id": self.attempt.intent.id,
            "grant_id": grant,
            "payload": self.expected,
        }

    def serve(self):
        try:
            send(self.server, self.claim(receive(self.server)))
        except (ValueError, OSError, EOFError):
            try:
                send(self.server, {"version": 1, "error": "RETRY_GRANT_REFUSED"})
            except OSError:
                pass
        finally:
            self.server.close()

    def close(self):
        self.client.close()
        try:
            self.server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.server.close()
        self.thread.join(timeout=2)
        if not self.thread.is_alive():
            self.attempt.retry_fenced = True
