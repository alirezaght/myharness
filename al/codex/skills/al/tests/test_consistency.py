#!/usr/bin/env python3
"""Shipped Codex role TOMLs and SKILL.md agree with al_models, and the package
contains none of the forbidden opposite-platform tokens (I4/I6)."""
import sys
import tomllib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import al_models  # noqa: E402
from test_resources import package_root, role_dir

PACKAGE = package_root(__file__)
AGENTS_DIR = role_dir(__file__)
SKILL_MD = PACKAGE / "SKILL.md"

# Built by concatenation, not as literal substrings: this file lives under
# .codex/skills/al, which the I6 grep (VERIFICATION_COMMANDS) itself scans —
# spelling these out directly would make this very file a false positive.
FORBIDDEN_TOKENS = [
    "CLAUDE" + "_", "." + "claude/", "Send" + "Message", "Task" + "Stop",
    "subagent" + "_type", "isolation" + ":", "PreTool" + "Use",
    "al_readonly" + "_guard", "al_status" + "line",
]


def _all_slots():
    slots = []
    for entry in al_models.ALL_POLICY.values():
        slots.append(entry["primary"])
        if entry["fallback"]:
            slots.append(entry["fallback"])
    return slots


def test_every_policy_slot_has_a_matching_role_toml():
    for slot in _all_slots():
        path = AGENTS_DIR / f"{slot['agent_type']}.toml"
        assert path.is_file(), f"missing {path}"
        with path.open("rb") as f:
            data = tomllib.load(f)
        assert data["model"] == slot["slug"], path
        assert data["model_reasoning_effort"] == slot["effort"], path
        assert data["sandbox_mode"] == slot["sandbox"], path
        assert data.get("developer_instructions", "").strip(), f"{path} developer_instructions blank"


def test_skill_md_mentions_every_slug_and_label():
    text = SKILL_MD.read_text()
    for slot in _all_slots():
        assert slot["slug"] in text, f"missing slug {slot['slug']}"
        assert al_models.label(slot) in text, f"missing label {al_models.label(slot)}"


def _scan_for_forbidden_tokens(path):
    text = path.read_text()
    return [tok for tok in FORBIDDEN_TOKENS if tok in text]


def test_no_claude_harness_tokens_anywhere_in_the_codex_al_copy():
    targets = [p for p in PACKAGE.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts]
    targets += [p for p in AGENTS_DIR.glob("al-*.toml")]
    offenders = {}
    for path in targets:
        hits = _scan_for_forbidden_tokens(path)
        if hits:
            offenders[str(path.relative_to(PACKAGE.parent))] = hits
    assert not offenders, offenders
