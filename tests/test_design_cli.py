import json
import re

import pytest

from harness.cli import main
from harness.design_models import DesignSection

TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")
ENTRY = re.compile(r"^E(\d+) ", re.M)


@pytest.fixture
def run(db_path, capsys):
    def _run(*argv: str) -> tuple[int, str, str]:
        code = main(["--db", str(db_path), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def task_id(run):
    code, out, _ = run("start", "--complexity", "trivial", "add", "csv", "export")
    assert code == 0
    return TASK_ID.search(out).group(1)


def _ok(run, *argv):
    code, out, err = run(*argv)
    assert (code, err) == (0, ""), err
    return out


def _entry(out):
    return ENTRY.search(out).group(1)


def test_guard_hint_and_read_only_views_in_other_phases(run, task_id):
    code, _, err = run("design", "add", task_id, "components", "an", "Exporter")
    assert code == 1 and f"run: harness phase {task_id} next" in err
    coverage = json.loads(_ok(run, "design", "coverage", task_id, "--json"))
    assert coverage["blocking"] is True and len(coverage["unresolved"]) == 8
    assert run("design", "coverage", "t-00000000")[0] == 1


def test_scripted_design_run(run, task_id):
    _ok(run, "phase", task_id, "next")
    _ok(run, "phase", task_id, "next")
    out = _ok(run, "design", "add", task_id, "components", "an", "Exporter", "--delegated")
    assert re.fullmatch(r"E\d+ stated \[components\] \(delegated\)", out.strip())
    out = _ok(
        run, "design", "propose", task_id, "data_flow", "rows", "to", "csv", "--rationale", "w"
    )
    proposal = _entry(out)
    assert out.startswith(f"E{proposal} pending [data_flow]")
    assert f"decide: harness design decide {task_id} {proposal} accept|reject|modify" in out
    code, _, err = run("design", "decide", task_id, proposal, "modify")
    assert code == 1 and "must not be empty" in err
    out = _ok(run, "design", "decide", task_id, proposal, "modify", "--response", "rows to RFC csv")
    assert out.strip() == f"E{proposal} modified"
    code, _, err = run("design", "decide", task_id, proposal, "accept")
    assert code == 1 and "already modified" in err
    out = _ok(run, "design", "none", task_id, "configuration", "--reason", "no settings")
    assert re.fullmatch(r"E\d+ \[configuration\] not applicable", out.strip())
    code, _, err = run("design", "add", task_id, "configuration", "EXPORT_DIR")
    assert code == 1 and "withdraw" in err
    assert run("design", "decide", task_id, "999", "accept")[0] == 1
    assert run("design", "withdraw", task_id, "999")[0] == 1
    remaining = [s for s in DesignSection if s not in ("components", "data_flow", "configuration")]
    for section in remaining:
        _ok(run, "design", "add", task_id, str(section), f"{section}", "stated")

    coverage = json.loads(_ok(run, "design", "coverage", task_id, "--json"))
    assert coverage["blocking"] is False and coverage["unresolved"] == []
    assert coverage["counts"]["delegated"] == 1 and coverage["counts"]["decided"] == 1
    assert _ok(run, "design", "coverage", task_id).strip().endswith("blocking: no")
    shown = _ok(run, "design", "show", task_id)
    assert shown.startswith("## Architecture and Scaffolding Design")
    assert shown.count("\n### ") == 10 and "### Delegated to AL" in shown
    assert "- [Components] an Exporter (developer)" in shown
    assert "- rows to RFC csv (fixed; modified_recommendation)" in shown
    data = json.loads(_ok(run, "design", "show", task_id, "--json"))
    assert set(data["sections"]) == {str(s) for s in DesignSection}
    assert data["sections"]["data_flow"][0]["provenance"] == "modified_recommendation"


def test_missing_database_is_not_created(tmp_path, capsys):
    db = tmp_path / "absent" / "state.db"
    assert main(["--db", str(db), "design", "show", "t-00000000"]) == 1
    assert not db.exists() and not db.parent.exists()


def test_withdraw_uncovers_the_section_and_help_lists_subcommands(run, task_id, capsys):
    _ok(run, "phase", task_id, "next")
    _ok(run, "phase", task_id, "next")
    entry = _entry(_ok(run, "design", "add", task_id, "interfaces", "POST", "/export"))
    assert _ok(run, "design", "withdraw", task_id, entry).strip() == f"E{entry} withdrawn"
    coverage = json.loads(_ok(run, "design", "coverage", task_id, "--json"))
    assert "section:interfaces" in [item["ref"] for item in coverage["unresolved"]]
    code, _, err = run("design", "withdraw", task_id, entry)
    assert code == 1 and err

    with pytest.raises(SystemExit):
        main(["design", "--help"])
    help_text = capsys.readouterr().out
    for name in ("add", "none", "propose", "decide", "withdraw", "show", "coverage"):
        assert f"\n    {name} " in help_text
