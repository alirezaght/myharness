#!/usr/bin/env python3
"""AL parallelism mechanics — write-ownership checks, tree fingerprints and the
banners AL prints when it fans work out.

Everything here is *mechanical*. AL's own judgement decides whether two tasks
are independent; these functions exist so that a wrong judgement about FILES
cannot silently become two agents writing the same path. They only ever refuse
parallel execution — they never authorise it.

Codex (checked against codex-cli 0.156.1) gives sibling `spawn_agent` writers in
one working tree no mutual exclusion at all: there is no cross-agent file lock,
so one agent's patch can silently overwrite a sibling's concurrent edit.
Ownership must therefore be settled before the agents are spawned.

Disjoint *files* are not sufficient. A command can mutate state the whole tree
shares — a lockfile, generated sources, a migrated database, a snapshot, the git
index — while touching nothing either agent declared. `shared_state_hits`
recognises those commands so `ownership` can refuse parallelism for them too.
"""

import os

def shared_state_hits(commands):
    import al_shell
    return [(raw, '; '.join(al_shell.check(raw,'shared'))) for raw in commands if al_shell.check(raw,'shared')]


def parse_commands(raw):
    from al_shell import command_list
    return command_list(raw)


def norm(path):
    return os.path.normpath(path.strip().strip('"').strip("'").rstrip("/")) if path.strip() else ""


def parse_paths(raw):
    """Split a comma/newline separated ownership list into normalised paths."""
    if not raw:
        return []
    out = []
    for chunk in raw.replace("\n", ",").split(","):
        p = norm(chunk)
        if p and p not in out:
            out.append(p)
    return out


def _contains(parent, child):
    return child == parent or child.startswith(parent + "/")


def overlaps(a_paths, b_paths):
    """Every conflicting (a, b) pair. A directory containing the other side's
    file counts as a conflict: two agents inside one directory tree are exactly
    the case that must not run concurrently."""
    hits = []
    for a in a_paths:
        for b in b_paths:
            if _contains(a, b) or _contains(b, a):
                hits.append((a, b))
    return hits


def check_ownership(sets, preexisting=()):
    """sets: {agent: [paths]}. Returns (conflicts, warnings).

    conflicts is a list of (agent_a, path_a, agent_b, path_b) — any entry means
    the phase MUST run sequentially. warnings never block.
    """
    conflicts, warnings = [], []
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for pa, pb in overlaps(sets[a], sets[b]):
                conflicts.append((a, pa, b, pb))
    pre = {norm(l[3:]) for l in preexisting if len(l) > 3}
    for agent, paths in sets.items():
        for p in paths:
            if p in pre:
                warnings.append(f"{agent} owns {p}, which already carried the user's "
                                f"uncommitted changes before AL started")
    empty = [a for a, paths in sets.items() if not paths]
    for a in empty:
        warnings.append(f"{a} declared no owned files")
    return conflicts, warnings


def tree_fingerprint(git):
    import al_scope
    return al_scope.tree_fingerprint()


def fanout_banner(title, entries, footer=None):
    """Tree banner:  AL · <title> / ├─ ... / └─ ... / <footer>"""
    lines = [f"AL · {title}"]
    for i, e in enumerate(entries):
        lines.append(("└─ " if i == len(entries) - 1 else "├─ ") + e)
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def sequential_banner(title, entries, reason=None, footer=None, skipped=None):
    """`AL · <title>` / `→ <agent>` … / optional `AL · <skipped>` + `Reason: …`.

    `skipped` states, once and explicitly, that AL chose sequential execution
    over an available parallel split — the reason is not optional there."""
    lines = [f"AL · {title}"]
    lines += [f"→ {e}" for e in entries]
    if skipped:
        lines.append(f"AL · {skipped}")
    if reason:
        lines.append(f"Reason: {reason}")
    elif skipped:
        lines.append("Reason: not stated — AL must state why parallel execution was skipped")
    if footer:
        lines.append(footer)
    return "\n".join(lines)
