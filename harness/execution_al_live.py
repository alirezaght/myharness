"""Step 8 live view of the AL run (#31): progress, snapshot trees, cache hits, agent progress
and open questions, derived from AL's own run state.

The supervisor writes the view into the execution directory whenever it replays the run:
on a service event (`execution_al_service.Subscription`) or on its polling tick. Both
paths call the same `live_view` on the same file, so the view differs only in `transport`.
The dashboard reads the file and never the run state itself. Answers to agents' questions
travel back the other way: the dashboard queues one (`queue_answer`), and the supervisor
records it with AL's own `ask --answer` (`answer_pending`); AL's `next` then tells the
orchestrator to send it to the same warm agent that asked."""

import json
import os
import re
import secrets
import time
from pathlib import Path


def durable_file(path, raw):
    """Replace a private mutable file atomically (fsynced temp file, then rename)."""
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

LIVE = "al-live.json"
ANSWERS = "question-answers"
MAX_ANSWER = 2000
QUESTION_ID = re.compile(r"^Q[1-9][0-9]{0,5}$")


def _progress_row(row):
    out = {"note": row.get("note"), "at": row.get("at")}
    for key in ("role", "agent", "phase", "done", "total"):
        if row.get(key) is not None:
            out[key] = row[key]
    return out


def live_view(state):
    """The live facts of one AL run state (pure: same state, same view)."""
    notes = state.get("progress_notes") or []
    entries = (state.get("evidence") or {}).get("entries") or []
    plan = (state.get("evidence") or {}).get("plan") or {}
    latest = {}
    for entry in entries:
        latest[entry.get("area")] = entry
    finished = [a for a in plan if a in latest and latest[a].get("status") != "RUNNING"]
    agents = {}
    for row in notes:
        who = row.get("agent") or row.get("role")
        if who:
            agents[who] = _progress_row(row)
    hits, trees = [], []
    for entry in entries:
        cached = entry.get("action_cache") or {}
        if cached.get("tree"):
            trees.append({"entry": entry.get("id"), "area": entry.get("area"), "tree": cached["tree"]})
        if cached:
            origin = cached.get("origin") or {}
            hits.append(
                {
                    "entry": entry.get("id"),
                    "area": entry.get("area"),
                    "status": entry.get("status"),
                    "reused_from": {"run": origin.get("run"), "entry": origin.get("entry")},
                    "tree": cached.get("tree"),
                }
            )
    questions = []
    for q in state.get("agent_questions") or []:
        if q.get("state") == "open" or (q.get("state") == "answered" and not q.get("forwarded")):
            questions.append(
                {k: q.get(k) for k in ("id", "kind", "state", "question", "context", "role", "agent", "at")}
                | ({"answer": q.get("answer")} if q.get("state") == "answered" else {})
            )
    return {
        "schema_version": 1,
        "run": state.get("run_id"),
        "status": state.get("status"),
        "phase": state.get("phase"),
        "progress": _progress_row(notes[-1]) if notes else None,
        "checks": {"done": len(finished), "total": len(plan)},
        "agents": [dict(row, id=who) for who, row in sorted(agents.items())],
        "snapshots": trees[-20:],
        "cache_hits": hits[-50:],
        "questions": questions,
    }


def live_path(execution):
    return Path(execution["handoff_path"]).parent / LIVE


def publish(execution, state, transport):
    """Write the view when it changed; returns the view written (or None)."""
    view = live_view(state)
    path = live_path(execution)
    try:
        previous = json.loads(path.read_bytes())
    except (OSError, ValueError):
        previous = None
    if previous is not None and {k: v for k, v in previous.items() if k not in {"transport", "updated_at"}} == view:
        if previous.get("transport") == transport:
            return None
    record = view | {"transport": transport, "updated_at": time.time()}
    durable_file(path, json.dumps(record, sort_keys=True).encode())
    return record


def read(execution):
    try:
        return json.loads(live_path(execution).read_bytes())
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ answers


def queue_answer(execution, question_id, text, *, source="dashboard"):
    """Queue the developer's answer to an agent's open question for the supervisor."""
    if not isinstance(question_id, str) or not QUESTION_ID.match(question_id):
        raise ValueError("question id must look like Q<n>")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_ANSWER:
        raise ValueError(f"answer must be nonempty text of at most {MAX_ANSWER} characters")
    view = read(execution) or {}
    asked = {q["id"]: q for q in view.get("questions") or [] if q.get("state") == "open"}
    if question_id not in asked:
        raise ValueError("question " + question_id + " is not open")
    directory = Path(execution["handoff_path"]).parent / ANSWERS
    directory.mkdir(mode=0o700, exist_ok=True)
    request = {
        "id": "ans-" + secrets.token_hex(8),
        "question": question_id,
        "text": text.strip(),
        "source": source,
        "state": "Queued",
        "queued_at": time.time(),
    }
    durable_file(directory / (request["id"] + ".json"), json.dumps(request, sort_keys=True).encode())
    return request


def pending_answers(execution):
    directory = Path(execution["handoff_path"]).parent / ANSWERS
    rows = []
    for path in sorted(directory.glob("ans-*.json")) if directory.is_dir() else ():
        try:
            row = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        rows.append((path, row))
    return rows


def answer_pending(execution, run_al):
    """Deliver queued answers through AL (`run_al(argv) -> (returncode, stdout, stderr)`)."""
    delivered = []
    for path, row in pending_answers(execution):
        if row.get("state") != "Queued":
            continue
        code, stdout, stderr = run_al(["ask", "--answer", row["question"], "--text", row["text"]])
        row.update(
            state="Delivered" if code == 0 else "Refused",
            delivered_at=time.time(),
            result=(stdout if code == 0 else stderr)[-2000:],
        )
        durable_file(path, json.dumps(row, sort_keys=True).encode())
        delivered.append(row)
    return delivered


def answers(execution):
    return [row for _, row in pending_answers(execution)]


def environment_flag(name, default="1"):
    return os.environ.get(name, default) != "0"
