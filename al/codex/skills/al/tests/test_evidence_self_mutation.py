"""#40: retiring a misregistered area for a current passing replacement, and a check that
rewrites a file in its own scope being named instead of a generic tear."""
import json

from evidence_helpers import PY, entries, ok_cmd

SPEC = "Verify the candidate task preserves repository state while recording evidence for its implementation."
FAILING = [PY, "-c", "import sys; print('1 failed'); sys.exit(1)"]
# Rewrites a tracked file inside its scope (as `uv run` re-locking uv.lock does), then passes.
MUTATING = [PY, "-c", "open('svc/app.py', 'a').write('y = 2\\n'); print('1 passed')"]


def test_retire_accepts_the_replacement_area_whose_latest_run_status_shows_valid(cli, repo, state_file):
    assert cli("start", "--spec", SPEC)[0] == 0
    assert cli("evidence", "plan", "--area", "uls-unit", "--scope", "svc", "--", *FAILING)[0] == 0
    assert cli("evidence", "plan", "--area", "uls-unit-frozen", "--scope", "svc", "--", *ok_cmd("1 passed"))[0] == 0
    code, out, err = cli("evidence", "retire", "--area", "uls-unit", "--reason", "Wrong flags.",
                         "--replaced-by", "uls-unit-frozen")
    assert code != 0 and "has no recorded run" in str(code) and "evidence run --area uls-unit-frozen" in str(code)
    assert cli("evidence", "run", "--area", "uls-unit-frozen")[0] == 0
    passing = entries(state_file)[-1]
    code, out, err = cli("evidence", "retire", "--area", "uls-unit", "--reason", "Wrong flags.", "--replaced-by", "uls-unit")
    assert code != 0 and "cannot replace itself" in str(code)
    code, out, err = cli("evidence", "retire", "--area", "uls-unit", "--reason", "Wrong flags.",
                         "--replaced-by", "uls-unit-frozen")
    assert code == 0, (code, out, err)
    ledger = json.loads(state_file().read_text())["evidence"]
    assert "uls-unit" not in ledger["plan"] and ledger["retired"][-1]["replaced_by"] == [passing["id"]]


def test_current_pass_is_the_status_verdict(cli, repo, state_file):
    import al_ledger as L
    assert cli("start", "--spec", SPEC)[0] == 0
    assert cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))[0] == 0
    st = cli.state()
    entry = L.ledger(st)["entries"][-1]
    assert L.current_pass(st, entry) and L.entry_verdict(st, entry)[0] == "VALID"
    (repo / "svc/app.py").write_text("x = 3\n")
    assert not L.current_pass(st, entry) and L.entry_verdict(st, entry)[0] == "STALE"
    assert not L.current_pass(st, None)


def test_supersede_refusal_names_the_retire_command():
    import al_evidence_supersession as Sup
    original = {"id": "E9", "area": "uls-unit"}
    text = Sup.changed_obligation_guidance(original, {"id": "E10", "area": "uls-unit-frozen"})
    assert "evidence retire --area uls-unit --replaced-by uls-unit-frozen" in text
    replanned = Sup.changed_obligation_guidance(original, {"id": "E10", "area": "uls-unit"})
    assert "PLAN_CHANGED" in replanned and "nothing needs superseding" in replanned


def test_a_check_rewriting_a_file_in_its_scope_is_named_and_its_sibling_is_affected(cli, repo, state_file):
    import al_intent
    import al_ledger as L
    assert cli("start", "--spec", SPEC)[0] == 0
    assert cli("evidence", "plan", "--area", "lint", "--scope", "svc", "--", *ok_cmd("1 passed"))[0] == 0
    assert cli("evidence", "plan", "--area", "uls-unit", "--scope", "svc", "--", *MUTATING)[0] == 0
    assert cli("evidence", "run", "--area", "lint")[0] == 0
    code, out, err = cli("evidence", "run", "--area", "uls-unit")
    assert code != 0
    torn = entries(state_file)[-1]
    assert torn["status"] == "TORN"
    assert torn["scope_changes"] == {"files": ["svc/app.py"], "concurrent": [], "attributed": True}
    assert "check uls-unit modified tracked file svc/app.py in its own scope" in torn["note"]
    assert "--frozen/--locked" in torn["note"] and "check uls-unit modified" in out
    st = cli.state()
    lint = next(e for e in L.ledger(st)["entries"] if e["area"] == "lint")
    verdict, why = L.entry_verdict(st, lint)  # the row `evidence status` prints
    assert verdict == "STALE" and why.startswith("affected-by-uls-unit") and "svc/app.py" in why
    mutating, affected = al_intent.self_mutation_effects(st, ["lint", "uls-unit"])
    assert mutating == {"uls-unit": ["svc/app.py"]} and affected == {"lint": "uls-unit"}
    assert al_intent._failed({"lint": "AFFECTED-BY-uls-unit"}) == ["lint"]
    # Restoring the file revalidates the sibling: its fingerprint describes the tree again.
    (repo / "svc/app.py").write_text("x = 1\n")
    assert L.current_pass(st, lint)


def test_concurrent_checks_are_named_instead_of_blaming_one():
    import al_evidence
    import al_ledger as L
    me = {"id": "E2", "run": "r", "area": "a", "started_at": 10}
    rows = [me,
            {"id": "E1", "run": "r", "area": "b", "started_at": 5, "ended_at": 12, "status": "PASS"},
            {"id": "E0", "run": "r", "area": "old", "started_at": 1, "ended_at": 2, "status": "PASS"},
            {"id": "E3", "run": "r", "area": "c", "started_at": 11, "ended_at": None, "status": "RUNNING"},
            {"id": "X1", "run": "other", "area": "d", "started_at": 11, "ended_at": None, "status": "RUNNING"}]
    concurrent = al_evidence.concurrent_checks(rows, me)
    assert concurrent == ["check b (E1)", "check c (E3)"]
    note = L.self_mutation_note("a", ["uv.lock"], concurrent)
    assert "while check a ran" in note and "check b (E1), check c (E3)" in note and "one of them" in note
    assert L.snapshot_changes({"x": (1, 1), "y": (1, 1)}, {"x": (1, 2), "z": (1, 1)}) == ["x", "y", "z"]
    assert L.snapshot_changes(None, {"x": (1, 1)}) == []
