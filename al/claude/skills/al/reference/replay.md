# Replay fixtures and results

`al_replay.py` runs each fixture's public AL actions in a disposable repository and evaluates any declared command-policy expectations against the explicitly selected package. Replay never executes the command string from a fixture.

A fixture has `id`, simulation `provenance`, and one or more public `actions`. The optional `commands` mapping retains the existing shape:

```json
"commands": {
  "readonly": ["rg 'git reset > cp --fix' ."],
  "shared": ["find . -name '-delete'"]
}
```

Each listed command is expected to be allowed by its named policy. Replay invokes the selected package's `al_shell.check` in a fresh subprocess, verifies the parser's module origin, and records a separate `kind: "policy"` outcome with the returned violations and availability. The `harmless-searches` fixture remains unchanged and declares seven strings under each policy, so its result accounts for fourteen policy checks plus its two actions.

`schemas/replay-fixture.json` describes the input, and `schemas/replay-result.json` describes the machine-readable output. Each case reports action and policy totals separately, each divided into matched, failed, and unavailable results. A policy mismatch or candidate error makes the candidate case unmatched, increments `safety_regressions`, and makes the CLI exit nonzero. Malformed declarations or unavailable candidate interfaces never count as passes.

The optional baseline is evaluated independently with its own selected parser. If that parser is missing, the baseline comparison is `unavailable`; the candidate parser is never substituted. `expected_baseline_failure` remains descriptive fixture metadata and does not alter observed outcomes. Existing action-only fixtures remain supported.

The command remains:

```sh
python3 "$AL_PACKAGE/scripts/al_replay.py" \
  --fixtures "$FIXTURES" --candidate "$CANDIDATE" \
  --baseline "$BASELINE" --output "$REPORT"
```

See `examples/replay-result.json` for a compact successful result with one action and one policy check. It illustrates the shape only; measured counts come from the executed fixture.
