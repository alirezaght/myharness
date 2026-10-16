#!/usr/bin/env python3
"""al_models.py is the single source of truth (I4): every role TOML, every
`[agents.<name>]` entry in config.toml, and SKILL.md must agree with it, and
none of the Codex AL files may carry a Claude-harness token (I6)."""
import sys
import tomllib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import al_models  # noqa: E402

CODEX = SCRIPTS.parents[2]  # <codex home or repo>/.codex/skills/al/scripts -> .codex
AGENTS_DIR = CODEX / "agents"
SKILL_MD = CODEX / "skills" / "al" / "SKILL.md"
CONFIG_TOML = CODEX / "config.toml"

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


def test_codex_dir_resolves_where_expected():
    assert (CODEX / "config.toml").is_file()
    assert (CODEX / "agents").is_dir()


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


def test_config_toml_registers_every_agent_type_and_nothing_else():
    with CONFIG_TOML.open("rb") as f:
        config = tomllib.load(f)
    assert config["features"]["multi_agent"] is True
    assert config["agents"]["max_depth"] == 1
    assert config["agents"]["max_threads"] >= 5
    agent_types = {slot["agent_type"] for slot in _all_slots()}
    registered = {k: v for k, v in config["agents"].items() if k not in ("max_depth", "max_threads")}
    assert set(registered) == agent_types, (set(registered), agent_types)
    for name, entry in registered.items():
        assert entry.get("description", "").strip(), name
        resolved = (CODEX / entry["config_file"]).resolve()
        assert resolved.is_file(), f"{name}: config_file does not resolve to {resolved}"


def test_skill_md_mentions_every_slug_and_label():
    text = SKILL_MD.read_text()
    for slot in _all_slots():
        assert slot["slug"] in text, f"missing slug {slot['slug']}"
        assert al_models.label(slot) in text, f"missing label {al_models.label(slot)}"


def _scan_for_forbidden_tokens(path):
    text = path.read_text()
    return [tok for tok in FORBIDDEN_TOKENS if tok in text]


def test_no_claude_harness_tokens_anywhere_in_the_codex_al_copy():
    targets = [p for p in (CODEX / "skills" / "al").rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts]
    targets += [p for p in AGENTS_DIR.glob("al-*.toml")]
    targets += [CONFIG_TOML]
    offenders = {}
    for path in targets:
        hits = _scan_for_forbidden_tokens(path)
        if hits:
            offenders[str(path.relative_to(CODEX.parent))] = hits
    assert not offenders, offenders
