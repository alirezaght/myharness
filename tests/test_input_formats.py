"""The installed package prints the closed context-import and risk-assessment formats."""

import copy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from harness.cli import build_parser, main
from harness.context_import import import_context
from harness.task_risk import assess

COMMAND = Path(__file__).resolve().parents[1] / ".claude" / "commands" / "harness.md"


def _printed(capsys, argv):
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 0
    return json.loads(capsys.readouterr().out)


def test_context_import_schema_needs_no_task_and_its_example_imports(capsys, controller):
    printed = _printed(capsys, ["context", "import", "--schema"])
    jsonschema.Draft202012Validator.check_schema(printed["json_schema"])
    jsonschema.validate(printed["example"], printed["json_schema"])
    assert printed["notes"]
    task = controller.create_task("Implement the reviewed ticket", "trivial")
    payload = import_context(controller, task.id, copy.deepcopy(printed["example"]))
    assert payload["status"] == "PROPOSED"


def test_assessment_schema_needs_no_task_and_its_example_assesses(capsys, tmp_path):
    printed = _printed(capsys, ["classify", "--assessment-schema"])
    jsonschema.Draft202012Validator.check_schema(printed["json_schema"])
    example = printed["example"]
    jsonschema.validate(example, printed["json_schema"])
    assert printed["notes"]
    for row in example["evidence"]:
        path = tmp_path / row["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def retry_limit(): return 2\n")
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    # The example is a local change following an existing pattern with no new design: the
    # printed routing rule makes it SMALL.
    assert assess(example, tmp_path) == "SMALL"
    # The same evidence routes differently when the assessed facts differ, so a constant
    # answer cannot satisfy the example.
    assert assess(example | {"scope": "feature"}, tmp_path) == "STANDARD"
    assert assess(example | {"existing_pattern": False}, tmp_path) == "STANDARD"
    hard = {
        "kind": "concurrency",
        "new_design": True,
        "description": "Writers must agree on one column order while both hold the lock.",
    }
    designed = example | {
        "hard_parts": [hard],
        "pseudocode_benefit": "Pseudocode pins down the lock order before any code is written.",
    }
    assert assess(designed, tmp_path) == "COMPLEX"
    # Evidence that changed after inspection, or that does not name a repository file, is
    # refused rather than routed.
    (tmp_path / example["evidence"][0]["path"]).write_text("def retry_limit(): return 3\n")
    with pytest.raises(ValueError, match="routing evidence changed"):
        assess(example, tmp_path)
    for source in ("../outside.py", "/etc/passwd", "src/export/missing.py"):
        moved = copy.deepcopy(example)
        moved["evidence"][0]["path"] = source
        with pytest.raises(ValueError, match="repository-relative|unsafe or unavailable"):
            assess(moved, tmp_path)


def test_schemas_reject_documents_the_validators_reject(capsys):
    context = _printed(capsys, ["context", "import", "--schema"])
    broken = copy.deepcopy(context["example"])
    broken["sources"][0]["kind"] = "rumour"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, context["json_schema"])
    risk = _printed(capsys, ["classify", "--assessment-schema"])
    broken = copy.deepcopy(risk["example"])
    broken["unexpected"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, risk["json_schema"])


def test_help_names_the_schema_commands():
    parser = build_parser()
    subparsers = next(a for a in parser._actions if a.dest == "command")
    classify_help = subparsers.choices["classify"].format_help()
    assert "--assessment-schema" in classify_help
    context = subparsers.choices["context"]
    actions = next(a for a in context._actions if a.dest == "action")
    assert "--schema" in actions.choices["import"].format_help()


def test_host_command_points_at_installed_formats_not_a_repository_path():
    text = COMMAND.read_text(encoding="utf-8")
    assert "docs/harness-al-maintenance.md" not in text
    assert "`uv run harness context import --schema`" in text
    assert "`uv run harness classify --assessment-schema`" in text
