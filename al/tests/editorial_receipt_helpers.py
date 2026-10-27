"""Real managed gate executions; only the external planning response is supplied."""

import copy
import json
import sys

from al.tests.planning_helpers import prepare, report_for, save_report
from al.tests.runtime_run_helpers import invoke, public_run


def succeed(result):
    assert result.returncode == 0, (result.stdout[-2500:], result.stderr[-2500:])
    return result


def state_path(launched, platform):
    return launched[2] / ("." + platform) / "al-runtime/pin-flow.json"


def state(launched, platform):
    return json.loads(state_path(launched, platform).read_bytes())


def prepare_readers(launched, platform, *, duplicate=True, extra_paths=()):
    _, _, repo, _, env = launched
    (repo / "rows.json").write_text('{"rows": [1, 2]}\n')
    (repo / "Makefile").write_text("all:\n\ttrue\n")
    succeed(invoke(["git", "add", "rows.json", "Makefile", *extra_paths], repo, env))
    succeed(invoke(["git", "commit", "-qm", "literal reader inputs"], repo, env))
    prepare(launched, platform)
    plan_fixture_gates(
        launched,
        platform,
        ["Makefile", "rows.json", *extra_paths],
        readers=True,
        duplicate=duplicate,
    )
    packet = json.loads(succeed(public_run(launched, platform, "planning", "packet")).stdout)
    succeed(save_report(launched, platform, report_for(packet)))
    succeed(public_run(launched, platform, "planning", "admit"))
    return state(launched, platform)


def plan_fixture_gates(launched, platform, selected_paths, *, readers=False, duplicate=True):
    repo = launched[2]
    paths = repo.parent / "paths.json"
    paths.write_text(json.dumps(selected_paths))
    discovered = succeed(
        public_run(launched, platform, "gates", "discover", "--paths-file", str(paths))
    )
    manifest = json.loads(discovered.stdout)
    rule = next(row for row in manifest["rules"] if row["path"] == "Makefile")
    # These absence facts apply only to this physically source-free fixture.
    for gate in manifest["gates"]:
        if gate["category"] in {"loc", "pre-commit", "contracts", "ci"}:
            gate.update(
                applicability="NOT_APPLICABLE",
                required=False,
                reason="Fixture has no declaration for this check category.",
                source=rule,
            )
    reader = {
        "id": "reader",
        "category": "contracts",
        "applicability": "APPLICABLE",
        "required": True,
        "reason": "Real literal JSON reader boundary",
        "units": manifest["units"],
        "argv": [sys.executable, "-I", "-m", "json.tool", "rows.json"],
        "scope": ["Makefile", "rows.json"],
        "source": rule,
        "execution_kind": "local",
        "cwd": ".",
        "env_names": [],
        "sources": [],
    }
    if readers:
        manifest["gates"].append(reader)
        if duplicate:
            manifest["gates"].append(dict(copy.deepcopy(reader), id="duplicate"))
    path = repo.parent / "manifest.json"
    path.write_text(json.dumps(manifest))
    succeed(public_run(launched, platform, "gates", "plan", "--file", str(path)))
    return state(launched, platform)


def run_reader(launched, platform, gate="reader"):
    return public_run(launched, platform, "gates", "run", "--stage", "candidate", "--id", gate)


def latest(state, gate):
    row = next(row for row in reversed(state["gate_runs"]) if row["gate"] == gate)
    return next(entry for entry in state["evidence"]["entries"] if entry["id"] == row["evidence"])
