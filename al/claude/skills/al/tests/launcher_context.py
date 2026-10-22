"""In-process equivalent of the managed `alctl run` child for suite fixtures.

The launcher child (installer/runtime_pin_boot.py) installs `_al_run_pin_context`
from a verified runtime pin and a reserved run transaction before importing
al_state, then commits RESERVED -> ACTIVE once state is published. Fixtures that
drive `al_state.main()` in-process perform the same steps with the real pin and
transaction primitives; they never bypass admission or fake its status.
"""
import importlib.util
import json
import shutil
import tempfile
import sys
import types
from pathlib import Path

AL_ROOT = Path(__file__).resolve().parents[4]
if str(AL_ROOT) not in sys.path:
    sys.path.append(str(AL_ROOT))

from installer.runtime_pin import _directory, prepare_pin, verify_pin  # noqa: E402
from installer.runtime_pin_transaction import (  # noqa: E402
    commit_run_binding, coordinated, read, read_state, reserve, subject, validate)

NAME = "_al_run_pin_context"
_PINS = {}


def pinned_storage(root, platform, *, storage=None, distribution=None):
    """Prepare one immutable pin of this checkout per storage/platform (session scoped)."""
    root = Path(root)
    if (root, platform) not in _PINS:
        storage = _directory(Path(storage or root / "al-run-pins"))
        distribution = Path(distribution or root / "distribution" / "al")
        if not distribution.exists():
            shutil.copytree(AL_ROOT, distribution, ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", "*.pyc"))
        pin = prepare_pin(distribution, platform, _directory(storage / "pins"))
        _PINS[(root, platform)] = (storage, pin)
    return _PINS[(root, platform)]


class Launcher:
    def __init__(self, storage, pin, platform):
        self.storage, self.pin, self.platform = storage, pin, platform

    def _install(self, transaction):
        module = types.ModuleType(NAME)
        module.transaction, module.storage = transaction, str(self.storage)
        sys.modules[NAME] = module
        return module

    def run(self, repository, session, start, main):
        """Run `main()` exactly as the launcher child would for one public command."""
        sys.modules.pop(NAME, None)
        try:
            module = self._install(None)
            value = subject(repository, self.platform, session)
            with coordinated(value) as path:
                transaction = read(path) if path.exists() else None
                if transaction:
                    validate(transaction, value)
            if transaction is None:
                if not start:
                    raise ValueError("no protected run binding; use the original issuer for legacy runs")
                verify_pin(self.storage / "pins", self.pin)
                transaction = reserve(value, self.pin)
            module.transaction = transaction
            old = read_state(transaction)
            if transaction["status"] == "ACTIVE" and old is None:
                raise ValueError("active runtime state missing")
            if transaction["status"] == "RESERVED" and old is not None:
                commit_run_binding(transaction)
                module.transaction = dict(transaction, status="ACTIVE")
            elif not start and old is None:
                raise ValueError("runtime start transaction has not published state")
            # Like the launcher child, commit only after the command returned normally.
            main()
            transaction = module.transaction
            if read_state(transaction) is not None:
                commit_run_binding(transaction)
        finally:
            sys.modules.pop(NAME, None)
            self.enter(repository, session)

    def enter(self, repository, session):
        """Keep the session's current protected binding installed for direct library calls."""
        module = self._install(None)
        try:
            value = subject(repository, self.platform, session)
            with coordinated(value) as path:
                transaction = read(path) if path.exists() else None
        except (OSError, ValueError):
            transaction = None
        if transaction is None:
            sys.modules.pop(NAME, None)
        else:
            module.transaction = transaction

    @staticmethod
    def leave():
        sys.modules.pop(NAME, None)

    def request(self):
        return json.dumps({"storage": str(self.storage), "pin": self.pin, "platform": self.platform,
                           "tests": str(Path(__file__).resolve().parent)})

    @staticmethod
    def enter_from(request, repository, session):
        """For hand-written child processes: install an active run's binding like the launcher child."""
        value = json.loads(request)
        Launcher(Path(value["storage"]), value["pin"], value["platform"]).enter(repository, session)

    def alctl_double(self, directory):
        """An executable with the `alctl run` interface, admitting commands like the launcher child."""
        path = Path(directory) / "alctl"
        request = {"storage": str(self.storage), "pin": self.pin, "platform": self.platform}
        path.write_text(
            "#!" + sys.executable + "\n"
            "import json, sys\n"
            "sys.path[:0] = " + repr([str(Path(__file__).resolve().parent.parent / "scripts"), str(Path(__file__).resolve().parent)]) + "\n"
            "from launcher_context import Launcher\n"
            "import al_state\n"
            "request = json.loads(" + repr(json.dumps(request)) + ")\n"
            "argv = sys.argv[1:]\n"
            "split = argv.index('--')\n"
            "options = dict(zip(argv[1:split:2], argv[2:split:2]))\n"
            "rest = argv[split + 1:]\n"
            "if argv[0] != 'run' or options.get('--platform') != request['platform']:\n"
            "    sys.exit('al_run: unsupported launcher invocation')\n"
            "sys.argv = ['al_state.py', '--session', options['--session'], *rest]\n"
            "try:\n"
            "    Launcher(request['storage'], request['pin'], request['platform']).run(\n"
            "        options['--repository'], options['--session'], rest[:1] == ['start'], al_state.main)\n"
            "except ValueError as exc:\n"
            "    sys.exit('al_run: ' + str(exc))\n")
        path.chmod(0o755)
        return str(path)

    def child_argv(self, repository, session, start, args):
        """argv for a real child process that admits one command like the launcher child."""
        request = json.dumps({"storage": str(self.storage), "pin": self.pin, "platform": self.platform,
                              "repository": str(repository), "session": session, "start": start})
        return [sys.executable, str(Path(__file__).with_name("launcher_child.py")), request, *args]


def _reports():
    spec = importlib.util.spec_from_file_location("_al_planning_reports", AL_ROOT / "tests/planning_reports.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def admit_planning(cli, directory=None):
    """Admit planning through the public packet -> report -> admit protocol.

    Only the external Architect model response is fixture data; the packet,
    report validation, obligation materialization and admission are real.
    An already admitted run is left unchanged."""
    result, out, err = cli("show")
    if result == 0 and json.loads(out).get("proof_admission", {}).get("status") != "pending":
        return
    directory = directory or tempfile.mkdtemp(prefix="al-planning-")
    result, out, err = cli("planning", "packet")
    assert result == 0, f"planning packet failed: {result} {out}{err}"
    packet = json.loads(out)
    path = Path(directory) / "architect-planning-report.json"
    path.write_text(json.dumps(_reports().report_for(packet)))
    result, out, err = cli("planning", "report", "--file", str(path))
    assert result == 0, f"planning report failed: {result} {out}{err}"
    obligations = json.loads(out)["materialized_obligations"]["stored_path"]
    result, out, err = cli("planning", "admit", "--file", obligations)
    assert result == 0, f"planning admission failed: {result} {out}{err}"


def _tracked_source(root, scopes=(".",)):
    """A real tracked file the executed evidence covered (preferring the seed's svc/app.py)."""
    import subprocess

    def covered(name):
        return any(s in {".", ""} or name == s or name.startswith(s.rstrip("/") + "/") for s in scopes)
    if (root / "svc/app.py").is_file() and covered("svc/app.py"):
        return "svc/app.py"
    names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                           capture_output=True, text=True, check=True).stdout.split()
    return next((n for n in names if (root / n).is_file() and covered(n)), None)


def observe_behavior(cli, path=None, line=1, directory=None):
    """Publish one observation per admitted scenario through the public behavior protocol.

    Each observation links the scenario to the latest passing evidence and a real,
    hashed source location; the runtime validates bindings and coverage itself."""
    import hashlib
    result, out, err = cli("behavior", "packet")
    assert result == 0, f"behavior packet failed: {result} {out}{err}"
    packet = json.loads(out)
    state = json.loads(cli("show")[1])
    root = Path(state["identity"]["root"])
    # Cite the newest passing execution whose scope covers an existing source file.
    evidence = None
    for executed in reversed(state["evidence"]["entries"]):
        if executed["status"] != "PASS":
            continue
        found = path or _tracked_source(root, executed.get("scope") or (".",))
        if found:
            evidence, path = executed["id"], found
            break
    assert evidence, "no passing evidence covers an existing source file"
    source = hashlib.sha256((root / path).read_bytes()).hexdigest()
    artifact = packet["obligations"]
    directory = Path(directory or tempfile.mkdtemp(prefix="al-behavior-"))
    for group in artifact["content"]["obligations"]:
        for scenario in group["scenarios"]:
            def rows(key, identifier, text=False, executed=True):
                return [{identifier: name,
                         **({"observation": "The linked real execution exercised this item."} if text else {}),
                         **({"evidence_ids": [evidence]} if executed else {}),
                         "artifact_refs": []} for name in scenario[key]]
            required = sorted(set().union(*(set(scenario[k]) for k in (
                "initial_state_ids", "transition_ids", "outcome_ids", "failure_case_ids", "real_component_ids"))))
            value = {
                "schema_version": 1, "kind": "behavior-observation-v1",
                "obligation_id": group["id"],
                "obligation_revision": {"artifact": artifact["artifact"], "revision": artifact["content"]["revision"]},
                "scenario_id": scenario["id"], "evidence_ids": [evidence],
                "binding": {"repository_identity": state["identity"], "session_id": state["session_id"],
                            "run_id": state["run_id"],
                            "verification_authority_hash": packet["verification_authority"]["hash"],
                            "document_hash": packet["document_hash"],
                            "candidate_fingerprint": packet["candidate_fingerprint"],
                            "input_bindings": [packet["verification_authority"]["artifact"], artifact["artifact"]]},
                "starting_state": rows("initial_state_ids", "state_id", text=True, executed=False),
                "real_components": rows("real_component_ids", "component_id"),
                "simulated_dependencies": [
                    {"component_id": name, "mechanism": "The fixture supplies the external Architect model response.",
                     "limitations": "No live model is invoked; every runtime operation is real."}
                    for name in scenario["permitted_simulated_component_ids"]],
                "exercised_transitions": rows("transition_ids", "transition_id"),
                "observed_outcomes": rows("outcome_ids", "outcome_id", text=True),
                "operational_failures": rows("failure_case_ids", "failure_case_id", text=True),
                "assertion_locations": [{"scenario_item_id": name, "path": path, "line": line,
                                         "source_sha256": source} for name in required],
                "artifact_refs": [], "limitations": [],
            }
            file = directory / (group["id"] + "-" + scenario["id"] + ".json")
            file.write_text(json.dumps(value))
            result, out, err = cli("behavior", "observe", "--file", str(file))
            assert result == 0, f"behavior observation {scenario['id']} failed: {result} {out}{err}"


def bind_pinned_resources(storage, pin, platform):
    """Bind runtime resources to the verified pin payload, as the launcher child does.

    Call once, in a dedicated process, before any AL runtime module is imported;
    resource identities (e.g. architecture runner paths) then match what the
    installed read-only validator later derives from the same pin."""
    storage = Path(storage)
    verified = verify_pin(storage / "pins", pin)
    manifest, captured = verified["manifest"], verified["bytes"]
    payload = storage / "pins" / pin["pin_identity"] / "payload"
    resources, identities = {}, {}
    for logical, name in manifest["resource_map"].items():
        key = logical[len("scripts/"):] if logical.startswith("scripts/") else logical
        resources[key], identities[key] = captured[name], str(payload / name)
    import al_runtime_resources
    al_runtime_resources._bind_verified_context({
        "identities": identities, "bytes": resources, "platform": platform,
        "closure_digest": manifest["closure_digest"], "origin_digest": manifest["pin_identity"]})


V2_REQUIRED = {"schema_version", "kind", "review_id", "stage", "reviewer_role", "model", "invocation_id",
               "candidate_hash", "task_hash", "packet_hash", "inspected_artifacts", "criteria", "findings",
               "status", "verification_authority_hash", "document_hash", "attempt_id", "effort", "policy_digest"}
V2_OPTIONAL = {"blocking", "nonblocking", "core", "tests", "both", "assessments", "plan_defects"}


def upgrade_report(state, report):
    """Carry a legacy (v1) review report onto the closed v2 wire format of a pinned run.

    Only fields v1 cannot express are filled from the issued packet; the test's own
    criteria, findings, hashes and verdict are kept, so deliberately invalid reports
    remain invalid and the runtime still validates everything."""
    import copy
    stage = report.get("stage")
    issued = json.loads(Path(state["review"]["packets"][stage]["stored_path"]).read_bytes())
    value = issued.get("content", issued)
    attempt = value["attempt"]
    registry = [c["id"] for c in value["behavior"]["obligations"]["content"]["criteria_registry"]]
    result = {k: copy.deepcopy(v) for k, v in report.items()
              if k in V2_REQUIRED | V2_OPTIONAL | ({"prior_resolutions"} if stage == "compare" else set())}

    legacy = {c.get("id") for c in report.get("criteria", []) if isinstance(c, dict)} - set(registry)

    def criterion(identifier):
        # Only the report's own legacy criterion ids are carried over; any other
        # (possibly deliberately invalid) source stays exactly as the test wrote it.
        return registry[0] if identifier in legacy and registry else identifier
    if isinstance(result.get("criteria"), list) and result["criteria"] and isinstance(result["criteria"][0], dict):
        template = result["criteria"][0]
        known = {c.get("id"): c for c in result["criteria"] if isinstance(c, dict)}
        result["criteria"] = [dict(known.get(r, template), id=r) for r in registry] or result["criteria"]
    for key in ("findings", "prior_resolutions"):
        for item in result.get(key, []) if isinstance(result.get(key), list) else []:
            if isinstance(item, dict) and "source" in item:
                item["source"] = criterion(item["source"])
    result.update(schema_version=2, kind="architect-review-report-v2",
                  attempt_id=attempt["attempt_id"], effort=attempt["effort"], policy_digest=attempt["policy_digest"],
                  document_hash=value["document_hash"],
                  verification_authority_hash=value["verification_authority"]["hash"])
    if report.get("model") == state.get("model"):
        result["model"] = attempt["model"]
    if stage == "fresh":
        if not result.get("plan_defects"):
            result.pop("plan_defects", None)
        evidence = next((e["id"] for e in reversed(state["evidence"]["entries"]) if e["status"] == "PASS"), None)
        locations = [l for c in result.get("criteria", []) if isinstance(c, dict) for l in c.get("locations", [])][:4]
        result["counterexample_search"] = [{
            "obligation_ids": [g["id"] for g in value["behavior"]["obligations"]["content"]["obligations"]],
            "scenario": "A candidate that skipped the exercised workflow would contradict the declared outcome.",
            "inspected_locations": locations or ["svc/app.py:1"], "evidence": [evidence] if evidence else [],
            "result": "none_found", "rationale": "The linked verification exercised the cited source in the frozen candidate."}]
    return result


def _only_missing_observations(text):
    """True when review-start was refused solely because scenario observations are absent."""
    import ast
    import re
    found = re.search(r"review evidence incomplete: (\[.*?\])", text)
    if not found:
        return False
    try:
        gaps = ast.literal_eval(found.group(1))
    except (ValueError, SyntaxError):
        return False
    return bool(gaps) and all(str(g).startswith("missing current behavior scenario") for g in gaps)


def current_protocol(call):
    """Let suite cases written before the proof contract drive the current planning/review protocol.

    A successful classify of a SPEC_READY run admits planning (as AL does before gates);
    review-start refused only for missing scenario observations publishes them and retries;
    legacy review report files are upgraded to v2 on pinned runs. Nothing else changes."""
    def run(*args):
        prefix = args[:2] if args[:1] == ("--session",) else ()
        command = args[len(prefix):]
        def plain(*rest):
            return call(*prefix, *rest)
        if command[:1] == ("classify",):
            # AL admits Architect planning right after classification, before any gate runs.
            result = call(*args)
            if result[0] == 0:
                code, out, _ = plain("show")
                state = json.loads(out) if code == 0 else {}
                if (state.get("proof_admission", {}).get("status") == "pending"
                        and state.get("spec_status") == "SPEC_READY"):
                    admit_planning(plain)
            return result
        if command[:1] == ("review-start",):
            result = call(*args)
            if result[0] != 0 and _only_missing_observations("".join(map(str, result))):
                observe_behavior(plain)
                result = call(*args)
            return result
        if command[:1] in {("review-fresh",), ("review-result",)} and "--file" in command:
            path = Path(command[command.index("--file") + 1])
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict) and data.get("schema_version") == 1 and data.get("stage") in {"fresh", "compare"}:
                code, out, _ = plain("show")
                state = json.loads(out) if code == 0 else {}
                if "proof_admission" in state and state.get("review", {}).get("packets", {}).get(data["stage"]):
                    path.write_text(json.dumps(upgrade_report(state, data)))
        return call(*args)
    run.__dict__.update(getattr(call, "__dict__", {}))
    return run
