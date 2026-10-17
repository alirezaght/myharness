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
    assert step.index("do not launch AL.") < step.index("open the dashboard")
