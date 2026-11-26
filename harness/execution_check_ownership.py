"""Current-host process references; serialized references are selectors, never proof."""

import os
import secrets
import threading


def _authority(context):
    if context is None:
        return None
    result = tuple(
        getattr(context, name, None)
        for name in ("execution_id", "epoch_id", "authority_generation", "principal_id")
    )
    if (
        any(type(result[i]) is not str or not result[i] for i in (0, 1, 3))
        or type(result[2]) is not int
        or result[2] < 0
    ):
        return None
    return result


def _process_state(process):
    """Observe the retained Popen without cleanup, signals or caller-supplied PIDs."""
    if process is None:
        return "unknown"
    try:
        if process.poll() is None:
            return "live"
        # A completed wrapper does not prove that its owned group has settled.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return "settled"
        return "unknown"
    except (OSError, ValueError, ChildProcessError):
        return "unknown"


class CheckOwnership:
    LIMIT = 4096

    def __init__(self):
        self.instance = secrets.token_hex(24)
        self.owners = {}
        self.allocations = {}
        self.lock = threading.RLock()

    def bind_owner(self, process, context, observer):
        with self.lock:
            if len(self.owners) >= self.LIMIT:
                raise ValueError("retained check-owner capacity exceeded")
            reference = secrets.token_hex(24)
            self.owners[reference] = {
                "process": process,
                "context": context,
                "observer": observer,
            }
            return reference

    def register(self, owner, binding, *, coordinator):
        if (
            not isinstance(binding, dict)
            or set(binding) != {"run", "id", "reservation"}
            or any(type(v) is not str or not v or len(v) > 256 for v in binding.values())
            or type(coordinator) is not bool
        ):
            raise ValueError("invalid check allocation binding")
        with self.lock:
            if owner not in self.owners or len(self.allocations) >= self.LIMIT:
                raise ValueError("check owner unavailable")
            key = tuple(binding[k] for k in ("run", "id", "reservation"))
            if key in self.allocations:
                raise ValueError("check allocation already registered")
            reference = dict(
                schema_version=1,
                mode="harness",
                host_instance=self.instance,
                owner_ref=owner,
                **binding,
            )
            self.allocations[key] = {
                "reference": reference,
                "coordinator": coordinator,
                "checks": {},
            }
            return dict(reference)

    def _resolve(self, reference, context):
        if (
            not isinstance(reference, dict)
            or set(reference)
            != {"schema_version", "mode", "host_instance", "owner_ref", "run", "id", "reservation"}
            or type(reference.get("schema_version")) is not int
            or reference["schema_version"] != 1
        ):
            return None
        key = tuple(reference.get(k) for k in ("run", "id", "reservation"))
        if any(type(v) is not str for v in key):
            return None
        allocation = self.allocations.get(key)
        if allocation is None or reference != allocation["reference"]:
            return None
        owner = self.owners.get(reference["owner_ref"])
        if (
            owner is None
            or _authority(context) is None
            or _authority(context) != _authority(owner["context"])
        ):
            return None
        return owner, allocation

    def attach(self, reference, owner_ref, context, handle, record):
        with self.lock:
            resolved = self._resolve(reference, context)
            if resolved is None or reference["owner_ref"] != owner_ref:
                raise ValueError("check allocation owner changed")
            _, allocation = resolved
            if (
                allocation["coordinator"]
                or handle in allocation["checks"]
                or len(allocation["checks"]) >= self.LIMIT
            ):
                raise ValueError("invalid check allocation attachment")
            allocation["checks"][handle] = record

    def observe(self, reference, context):
        unknown = {"owner": "unknown", "checks": "unknown"}
        with self.lock:
            resolved = self._resolve(reference, context)
            if resolved is None:
                return unknown
            owner, allocation = resolved
            states = []
            for record in allocation["checks"].values():
                state = _process_state(record.get("process"))
                completion = record.get("completion")
                if state == "settled" and not (
                    isinstance(completion, dict)
                    and completion.get("complete") is True
                    and completion.get("leader_reaped") is True
                    and completion.get("owned_group_settled") is True
                ):
                    state = "unknown"
                states.append(state)
            checks = (
                "live"
                if "live" in states
                else "unknown"
                if "unknown" in states or not states and not allocation["coordinator"]
                else "settled"
            )
            return {"owner": _process_state(owner["process"]), "checks": checks}
