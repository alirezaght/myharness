"""Named bounded read adapters, no arbitrary command or URL fallback."""

import uuid
from pathlib import Path

from harness.command_runner import run_command
from harness.db import utcnow
from harness.diagnostics_db import query_sqlite
from harness.execution_contract import canonical
from harness.execution_logs import redact_value


def run_diagnostic(controller, request):
    audit = uuid.uuid4().hex
    controller.conn.execute(
        "INSERT INTO operation_audit VALUES (?,NULL,?,?,NULL,?)",
        (audit, canonical(redact_value(request)), "read_only_diagnostic", utcnow()),
    )
    kind = request.get("kind")
    if kind == "sqlite":
        result = query_sqlite(request["target"], request["query"])
    elif kind in {"log", "config", "source"}:
        root = Path(request["root"]).resolve(strict=True)
        path = (root / request["path"]).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("diagnostic target escapes configured root")
        with path.open("rb") as stream:
            raw = stream.read(65537)
        result = {"text": raw[:65536].decode(errors="replace"), "truncated": len(raw) > 65536}
    elif kind == "git":
        result = run_command(
            controller, {"argv": ["git", *request["arguments"]], "cwd": request["root"]}
        )
    elif kind == "container_status":
        result = run_command(
            controller, {"argv": ["docker", "ps", "--format", "{{json .}}"], "cwd": request["root"]}
        )
    elif kind == "service_status":
        result = run_command(
            controller,
            {
                "argv": ["launchctl", "print", "system/" + request["service"]],
                "cwd": request["root"],
            },
        )
    elif kind == "ci":
        from harness.delivery_store import delivery_view

        result = delivery_view(controller.conn, request["execution_id"])
    else:
        raise ValueError("unsupported diagnostic capability: " + str(kind))
    result = redact_value(result)
    controller.conn.execute(
        "UPDATE operation_audit SET outcome=? WHERE id=?", (canonical(result), audit)
    )
    return result
