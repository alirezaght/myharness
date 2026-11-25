"""Prebuilt run states (#36): every test gets a private, byte-identical copy; nothing leaks between tests."""
import json
import os
from pathlib import Path

import pytest

import run_templates


def _template(name):
    return run_templates._TEMPLATES[name]


def _meta(name):
    return json.loads((_template(name) / "meta.json").read_text())


@pytest.mark.parametrize("name", ["planned", "implemented", "finalizing", "verified"])
def test_each_state_is_the_admitted_run_its_helpers_build(cli, run_state, name):
    shown = run_state(name)
    state = cli.state()
    assert state["classification"] == ("SMALL" if name == "verified" else "STANDARD")
    assert state["spec_status"] == "SPEC_READY" and state["proof_admission"]["status"] != "pending"
    assert state["phase"] == {"planned": "PLANNING", "implemented": "IMPLEMENTATION",
                              "finalizing": "FINALIZATION", "verified": "VERIFICATION"}[name]
    entries = state["evidence"]["entries"]
    assert [e["area"] for e in entries if e["status"] == "FAIL"] == (["repro"] if name == "finalizing" else [])
    if name == "verified":
        assert shown == json.loads(cli("show")[1])
        assert [e["area"] for e in entries if e["status"] == "PASS" and e["area"] == "unit"] == ["unit"]
    else:
        assert shown is None


def test_a_mutated_copy_never_reaches_the_template_or_the_next_copy(cli, run_state, repo, state_file):
    run_state("implemented")
    template, meta = _template("implemented"), _meta("implemented")
    parts = {part: Path(path) for part, path in meta["paths"].items()}
    parts["index"] = Path(os.environ["AL_ANALYSIS_INDEX_DIR"])
    # Private copies: nothing of the template is inside the test's paths, nothing is hard-linked.
    assert not template.is_relative_to(repo) and not repo.is_relative_to(template)
    for path in (repo / "svc/app.py", state_file(), parts["transaction"]):
        assert os.stat(path).st_nlink == 1
    assert run_states_digest(parts) == meta["digest"]

    # Mutate every part the run owns: source, Git, runtime state, transaction, initialization, index.
    (repo / "svc/app.py").write_text("x = 'mutated'\n")
    assert cli("writer", "start", "--id", "mutator", "--role", "al-engineer", "--files", "svc/app.py")[0] == 0
    (repo / ".git" / "mutated").write_text("x")
    parts["transaction"].write_bytes(parts["transaction"].read_bytes() + b" ")
    if "initialization" in parts:
        parts["initialization"].unlink()
    parts["index"].mkdir(parents=True, exist_ok=True)
    (parts["index"] / "mutated").write_text("x")
    assert run_states_digest(parts) != meta["digest"]
    # The template is untouched by everything above.
    assert RunStateDigest.template(template, meta) == meta["digest"]

    # The memo tables a command filled are emptied before the next copy is handed out.
    assert any(_filled(module, name) for module, name in run_templates._memo_tables())
    copy = run_templates.RunState(cli=cli, repo=repo, tmp_path=repo.parent, launcher=run_state.launcher,
                                  seed_digest=run_state.seed_digest, store=run_state.store)
    copy._restore("implemented")
    assert not any(_filled(module, name) for module, name in run_templates._memo_tables())
    assert copy.restored == meta["digest"] == run_states_digest(parts)
    assert (repo / "svc/app.py").read_text() == "x = 1\n" and not (repo / ".git" / "mutated").exists()
    state = json.loads(state_file().read_text())
    assert state["phase"] == "IMPLEMENTATION" and not state.get("writers")


def test_run_state_must_start_from_the_untouched_repository(cli, run_state, repo):
    (repo / "svc/app.py").write_text("x = 2\n")
    with pytest.raises(AssertionError, match="first action"):
        run_state("implemented")


def run_states_digest(parts):
    return run_templates.RunState._digest(parts)


class RunStateDigest:
    @staticmethod
    def template(template, meta):
        return run_templates.RunState._digest({part: template / part for part in meta["paths"]})


def _filled(module, name):
    value = getattr(module, name)
    if hasattr(value, "cache_info"):
        return value.cache_info().currsize > 0
    if hasattr(value, "entries"):
        return bool(value.entries)
    if hasattr(value, "__len__"):
        return len(value) > 0
    return False
