# AL orchestrator

This directory keeps the AL multi-agent development orchestrator in version control, for both
Claude Code and Codex. The harness in this repository hands implementation to AL (see
`docs/SPEC.md` §9), so AL is maintained alongside it.

| Path | Installed to | Contents |
|---|---|---|
| `claude/skills/al/` | `~/.claude/skills/al/` | Skill (`SKILL.md`), references, runtime scripts, tests |
| `claude/agents/` | `~/.claude/agents/al/` | Role agents (`al-architect`, `al-designer`, `al-engineer`, `al-builder`, `al-scout`, fallbacks) |
| `codex/skills/al/` | `~/.codex/skills/al/` | Codex variant of the skill |
| `codex/agents/` | `~/.codex/agents/` | Codex role agents (`al-*.toml`) |

The two variants differ on purpose (for example, the Claude package ships the read-only guard
and status line scripts). Change both when a fix applies to both.

## Syncing

Install or update the global copies with AL's own distribution tool, which plans, backs up,
applies and verifies the change (see `claude/skills/al/reference/distribution.md`):

```sh
python3 al/claude/skills/al/scripts/al_distribution.py plan --source al/claude/skills/al \
  --inventory "$INVENTORY" --reviews "$REVIEWS" --platform claude \
  --registry "$EXTERNAL_REGISTRY" --output "$PLAN"
```

Use `--source al/codex/skills/al --platform codex` for Codex. To refresh this directory from the
global installs instead, copy them back without caches (`__pycache__`, `.pytest_cache`,
`.DS_Store`).

## Tests and lint

AL keeps its own style, so the harness's ruff config excludes `al/` and the harness test suite
does not collect it. Run AL's tests from its package directory:

```sh
cd al/claude/skills/al && python3 -m pytest -q
```
