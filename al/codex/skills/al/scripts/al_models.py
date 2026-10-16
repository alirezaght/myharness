#!/usr/bin/env python3
"""AL model routing policy — the single source of truth for the model and
effort every AL role runs on, and what it is allowed to fall back to.

In Codex every AL role is an `[agents.<name>]` entry in `.codex/config.toml`
whose role TOML (`.codex/agents/<name>.toml`) pins `model`,
`model_reasoning_effort` and `sandbox_mode`. `spawn_agent` also takes explicit
`model` / `reasoning_effort` overrides, but setting `model` alone resets the
effort to the model default. So each role with an approved fallback owns a
SECOND agent type pinned to the fallback model/effort pair, and AL switches
`agent_type` — passing that slot's model AND effort together, never the model
alone — when the primary model is genuinely unavailable.

A role whose `fallback` is None must never be downgraded: the run stops.

Each slot's `model` is the latch key (astra | sol | luna) used by
`model-unavailable`; `slug` is the Codex model name passed to `spawn_agent`.

AUX_POLICY holds non-decisional helper roles (read-only parallel workers). They
are deliberately NOT in ROLES: they carry no authority, they are optional
accelerators, and they must never appear in or alter the four-role allocation
above. AL-Scout reuses Luna — a model already required by AL-Builder — so the
fan-out introduces no new model entitlement surface. It has no fallback: if
Luna is unavailable the fan-out is skipped (and AL-Builder blocks the run
anyway, under the normal model policy).
"""

import time

ROLES = ["al-architect", "al-designer", "al-engineer", "al-builder"]

RO, RW = "read-only", "workspace-write"
ASTRA, SOL, LUNA = "gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"


def _slot(agent_type, model, slug, effort, sandbox):
    return {"agent_type": agent_type, "model": model, "slug": slug, "effort": effort, "sandbox": sandbox}


POLICY = {
    "al-architect": {
        "display": "AL-Architect",
        "primary": _slot("al-architect", "astra", ASTRA, "medium", RO),
        "fallback": _slot("al-architect-fallback", "sol", SOL, "high", RO),
    },
    "al-designer": {
        "display": "AL-Designer",
        "primary": _slot("al-designer", "astra", ASTRA, "low", RO),
        "fallback": _slot("al-designer-fallback", "sol", SOL, "medium", RO),
    },
    "al-engineer": {
        "display": "AL-Engineer",
        "primary": _slot("al-engineer", "sol", SOL, "medium", RW),
        "fallback": None,
    },
    "al-builder": {
        "display": "AL-Builder",
        "primary": _slot("al-builder", "luna", LUNA, "high", RW),
        "fallback": None,
    },
}

AUX_POLICY = {
    "al-scout": {
        "display": "AL-Scout",
        "primary": _slot("al-scout", "luna", LUNA, "medium", RO),
        "fallback": None,
    },
}

ALL_POLICY = {**POLICY, **AUX_POLICY}


def initial_state():
    return {
        "model_tier": {r: "primary" for r in ROLES},
        "unavailable_models": [],
        "model_fallbacks": [],
        "roles_run": [],
        "aux_runs": [],
    }


def tier(st, role):
    return (st.get("model_tier") or {}).get(role, "primary")


def resolve(st, role):
    """The slot (agent_type + model + effort + sandbox) this role currently runs on."""
    entry = ALL_POLICY[role]
    if tier(st, role) == "fallback" and entry["fallback"]:
        return entry["fallback"]
    return entry["primary"]


def display(role):
    return ALL_POLICY[role]["display"] if role in ALL_POLICY else role


def label(slot):
    return f"{slot['model'].capitalize()}/{slot['effort'].upper()}"


def spawn_args(slot):
    """The exact `spawn_agent` arguments for a slot — model and effort always together."""
    return {"agent_type": slot["agent_type"], "model": slot["slug"],
            "reasoning_effort": slot["effort"], "fork_turns": "none"}


def mark_unavailable(st, model, reason):
    """Latch `model` as unavailable for the rest of the run.

    Returns (banner_lines, blocked_roles, switched_roles). `blocked_roles` are
    roles left with no approved model — the caller must STOP, never downgrade.
    """
    model = model.strip().lower()
    tiers = st.setdefault("model_tier", {r: "primary" for r in ROLES})
    seen = st.setdefault("unavailable_models", [])
    if model not in seen:
        seen.append(model)
    lines, switched, blocked = [], [], []
    for role in ROLES:
        slot = resolve(st, role)
        if slot["model"] != model:
            continue
        fb = POLICY[role]["fallback"]
        if not fb or tiers.get(role) == "fallback" or fb["model"] in seen:
            blocked.append(role)
            continue
        tiers[role] = "fallback"
        switched.append(role)
        st.setdefault("model_fallbacks", []).append({
            "role": role,
            "from": label(slot),
            "to": label(fb),
            "reason": reason,
            "at": time.time(),
        })
        lines += [f"{label(slot)} unavailable", f"→ {POLICY[role]['display']} · {label(fb)}"]
    if lines:
        lines.insert(0, "AL · Model fallback")
    return lines, blocked, switched


def blocked_reason(st, blocked):
    parts = []
    for role in blocked:
        entry = POLICY[role]
        approved = [label(entry["primary"])]
        if entry["fallback"]:
            approved.append(label(entry["fallback"]))
        parts.append(f"{entry['display']} ({' → '.join(approved)})")
    return ("no approved model left for " + ", ".join(parts)
            + f"; unavailable: {', '.join(st.get('unavailable_models') or [])}")


def render_models(st):
    out = ["Models used:", ""]
    for role in ROLES:
        entry = POLICY[role]
        slot = resolve(st, role)
        notes = []
        if tier(st, role) == "fallback":
            notes.append(f"{entry['primary']['model'].capitalize()} unavailable")
        if slot["model"] in (st.get("unavailable_models") or []):
            notes.append("no approved model left — run stopped")
        if role not in (st.get("roles_run") or []):
            phase = {"al-designer": "PSEUDOCODE", "al-builder": "FINALIZATION"}.get(role)
            deliberate = phase and phase in (st.get("skipped_phases") or [])
            notes.append("skipped" if deliberate else "not used")
        tail = f" ({', '.join(notes)})" if notes else ""
        out.append(f"* {entry['display']} — {slot['model'].capitalize()} / {slot['effort'].upper()}{tail}")
    for name, entry in AUX_POLICY.items():
        runs = [r for r in (st.get("aux_runs") or []) if r.get("role") == name]
        if not runs:
            continue
        slot = entry["primary"]
        n = sum(int(r.get("count") or 0) for r in runs)
        out.append(f"* {entry['display']} — {slot['model'].capitalize()} / {slot['effort'].upper()}"
                   f" (read-only helper, {n} worker{'s' if n != 1 else ''} in {len(runs)} fan-out"
                   f"{'s' if len(runs) != 1 else ''}; no authority)")
    return "\n".join(out)
