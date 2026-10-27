#!/usr/bin/env python3
"""al_models: primary/fallback resolution, the model latch and its banners."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import al_models  # noqa: E402


def test_resolve_returns_spec_table_slots_for_every_role_and_aux():
    st = al_models.initial_state()
    cases = {
        "al-architect": ("astra", "gpt-6-astra", "medium", "read-only"),
        "al-designer": ("astra", "gpt-6-astra", "low", "read-only"),
        "al-engineer": ("sol", "gpt-6.1-sol", "medium", "workspace-write"),
        "al-builder": ("luna", "gpt-6-luna", "high", "workspace-write"),
        "al-scout": ("luna", "gpt-6-luna", "medium", "read-only"),
    }
    for role, (model, slug, effort, sandbox) in cases.items():
        slot = al_models.resolve(st, role)
        assert slot["model"] == model
        assert slot["slug"] == slug
        assert slot["effort"] == effort
        assert slot["sandbox"] == sandbox


def test_mark_unavailable_astra_switches_architect_and_designer_together():
    st = al_models.initial_state()
    lines, blocked, switched = al_models.mark_unavailable(st, "astra", "not entitled")
    assert blocked == []
    assert switched == ["al-architect", "al-designer"]
    assert lines == [
        "AL · Model fallback",
        "Astra/MEDIUM unavailable",
        "→ AL-Architect · Sol/HIGH",
        "Astra/LOW unavailable",
        "→ AL-Designer · Sol/MEDIUM",
    ]
    assert al_models.tier(st, "al-architect") == "fallback"
    assert al_models.tier(st, "al-designer") == "fallback"
    assert al_models.resolve(st, "al-architect")["slug"] == "gpt-6.1-sol"
    # untouched: al-engineer was already primary/sol, never part of the switch
    assert al_models.tier(st, "al-engineer") == "primary"


def test_mark_unavailable_sol_after_astra_blocks_architect_designer_engineer():
    st = al_models.initial_state()
    al_models.mark_unavailable(st, "astra", "r1")
    lines, blocked, switched = al_models.mark_unavailable(st, "sol", "r2")
    assert set(blocked) == {"al-architect", "al-designer", "al-engineer"}
    assert switched == []


def test_mark_unavailable_luna_on_fresh_state_blocks_only_builder():
    st = al_models.initial_state()
    lines, blocked, switched = al_models.mark_unavailable(st, "luna", "r")
    assert blocked == ["al-builder"]
    assert switched == []
    assert lines == []


def test_mark_unavailable_unknown_model_switches_and_blocks_nothing():
    st = al_models.initial_state()
    lines, blocked, switched = al_models.mark_unavailable(st, "not-a-real-model", "r")
    assert lines == []
    assert blocked == []
    assert switched == []
    for role in al_models.ROLES:
        assert al_models.tier(st, role) == "primary"


def test_latched_model_is_never_re_resolved_or_re_switched():
    st = al_models.initial_state()
    al_models.mark_unavailable(st, "astra", "first report")
    lines, blocked, switched = al_models.mark_unavailable(st, "astra", "second report")
    assert lines == []
    assert blocked == []
    assert switched == []
    assert al_models.tier(st, "al-architect") == "fallback"


def test_render_models_lists_every_role_label():
    st = al_models.initial_state()
    out = al_models.render_models(st)
    assert "Astra / MEDIUM" in out
    assert "Sol / MEDIUM" in out
    assert "Luna / HIGH" in out


def test_spawn_args_pairs_model_and_effort_for_every_policy_slot():
    for entry in al_models.ALL_POLICY.values():
        for slot in (entry["primary"], entry["fallback"]):
            if slot is None:
                continue
            args = al_models.spawn_args(slot)
            assert args == {
                "agent_type": slot["agent_type"],
                "model": slot["slug"],
                "reasoning_effort": slot["effort"],
                "fork_turns": "none",
            }


def test_all_codex_role_instruction_files_parse_as_toml():
    import tomllib
    from test_resources import role_dir
    roles=list(role_dir(__file__).glob('al-*.toml'))
    assert len(roles)==7
    for role in roles:
        value=tomllib.loads(role.read_text())
        assert value['developer_instructions'].strip()
        assert value['model'] and value['model_reasoning_effort']
