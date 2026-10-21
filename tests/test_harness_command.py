import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMAND = REPO_ROOT / ".claude" / "commands" / "harness.md"
START_SENTENCE = (
    'Run `uv run harness start "$ARGUMENTS"` from the repository root and relay its output '
    "verbatim, including the task id and the proposed complexity."
)
LAUNCH = "uv run python -m harness.dashboard <task id>"
STEP = re.compile(r"^(\d+)\. ", re.M)


def _text() -> str:
    return COMMAND.read_text(encoding="utf-8")


def _launch_paragraph() -> str:
    paragraphs = [p for p in _text().split("\n\n") if LAUNCH in p]
    assert len(paragraphs) == 1, "exactly one paragraph launches the dashboard"
    return paragraphs[0]


def test_command_launches_the_dashboard_in_the_background_on_loopback():
    paragraph = _launch_paragraph()
    assert "background" in paragraph
    assert "127.0.0.1" in paragraph
    assert "dashboard: http://127.0.0.1:<port>/ · task <id> · Ctrl-C stops it" in paragraph
    assert "could not open a browser; open <url> yourself" in paragraph
    assert "dashboard stopped" in paragraph


def test_launch_paragraph_follows_the_start_paragraph_and_precedes_the_interview():
    text = _text()
    assert text.index(START_SENTENCE) < text.index(LAUNCH) < text.index("requirements interview")
    assert text.index(LAUNCH) < text.index("\n1. Ask the developer")


def test_original_start_sentence_and_fifteen_steps_are_intact():
    text = _text()
    assert START_SENTENCE in text
    assert [int(n) for n in STEP.findall(text)] == list(range(1, 16))
    assert text.rstrip().endswith(
        "Do nothing beyond this; the harness is activated only by this command "
        "and never implicitly (SPEC §2, AC 1)."
    )


def test_resume_step_opens_the_dashboard_for_the_chosen_task():
    step = _text().split("\n15. ", 1)[1].split("\n\nDo nothing", 1)[0]
    assert "Once the developer picks the task to continue, open the dashboard for it" in step
    assert (
        "A current approval with no run can be started with the explicit execution command" in step
    )
    assert step.index("A current approval with no run") < step.index("open the dashboard")


def _redesign_paragraphs() -> list[str]:
    return [p for p in _text().split("\n\n") if p.startswith("Redesign loop:")]


def test_one_redesign_loop_paragraph_sits_between_steps_eight_and_nine():
    text = _text()
    paragraphs = _redesign_paragraphs()
    assert len(paragraphs) == 1, "exactly one paragraph describes the redesign loop"
    assert not paragraphs[0][0].isdigit(), "it is not a numbered step"
    assert text.index("\n8. ") < text.index(paragraphs[0]) < text.index("\n9. ")
    assert text.count("Redesign loop:") == 1


def test_redesign_loop_paragraph_names_both_commands_and_keeps_the_developer_in_charge():
    paragraph = _redesign_paragraphs()[0]
    assert "`uv run python -m harness.redesign <id>`" in paragraph
    assert "`uv run python -m harness.redesign address <id> <comment id>...`" in paragraph
    assert "never address" in paragraph.lower()
    assert "resolve or re-open" in paragraph
    assert "developer's say-so" in paragraph
    assert "uv run harness design propose" in paragraph
    assert "uv run harness design withdraw" in paragraph
    assert "the developer decides, never you" in paragraph
    assert paragraph.index("design propose") < paragraph.index("design withdraw")
    assert paragraph.index("design withdraw") < paragraph.index("harness.redesign address")


def test_redesign_loop_does_not_disturb_the_numbered_steps_or_the_launch_paragraph():
    text = _text()
    assert [int(n) for n in STEP.findall(text)] == list(range(1, 16))
    assert len([p for p in text.split("\n\n") if LAUNCH in p]) == 1


def test_launch_paragraph_names_the_approval_snapshot_written_from_the_approval_center():
    paragraph = _launch_paragraph()
    assert "the approval snapshot written when the developer presses Approve & Implement" in (
        paragraph
    )
    assert "Approval Center" in paragraph
    assert paragraph.index("diagram versions") < paragraph.index("Approval Center")


def _step_fourteen() -> str:
    return _text().split("\n14. ", 1)[1].split("\n\n15. ", 1)[0]


def test_step_fourteen_lets_the_developer_press_the_dashboard_button_but_never_claude():
    step = _step_fourteen()
    assert "press Approve & Implement in the dashboard's Approval Center" in step
    assert "records the same approval through the same gate" in step
    assert (
        "stays disabled while a diagram comment is not resolved, a blocking question remains "
        "or the final review fails" in step
    )
    assert "you never press it or treat it as permission to implement" in step
    assert step.index("Approval Center") < step.index("Never run it on your own initiative")


def test_step_fourteen_keeps_its_cli_rules_and_the_steps_stay_fifteen():
    step = _step_fourteen()
    assert "Approve & Implement is `uv run harness approve <id>`. You only relay." in step
    assert "never treat `spec approve` as an approval" in step
    assert "leaves launch as the separate" in step
    assert "Never run it on your own initiative" in step
    assert [int(n) for n in STEP.findall(_text())] == list(range(1, 16))
