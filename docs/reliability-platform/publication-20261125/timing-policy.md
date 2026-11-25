PLAN_STATUS: UNCHANGED

RESOLUTION

**Keep the per-test/file budgets enforced.** The sources settle the apparent contradiction: total run wall time is informational; individual test and accumulated file durations are explicit gates for `--tier all` and release verification.

Evidence:

- `AGENTS.md:22` originated in `8b05f7c` on November 24.
- Later commit `724e964` explicitly introduced the 5-second/test and 60-second/file gates, stating: “The total run time is still reported, not judged.”
- `al/README.md:84–86` explicitly documents enforcement by `verify_changed.py --tier all` and `release_verify.py`.
- `al/verify_changed.py:292–297` enforces duration violations only with `--tier all`; fast-tier violations remain advisory.
- `al/release_verify.py:237–240` enforces the same per-test/file budget. `.github/workflows/al-release.yml:39–41` invokes that release runner.
- `al/verification_budget.py:34–49` sums non-exempt setup/call/teardown durations per file. This is accumulated test time, not suite wall time.
- [Issue #36’s owner instruction](https://github.com/alirezaght/myharness/issues/36#issuecomment-7159166585) explicitly requests the budget guard. [The latest relevant profiling update](https://github.com/alirezaght/myharness/issues/36#issuecomment-7167539072) says budgets/exemptions remain untouched and identifies repeated installer fixture setup as a concrete optimization target.

Minimal consistent action:

1. Preserve E22 as **FAIL: 110 functional cases passed; 26 duration-budget violations**. No overall PASS, waiver, exemption expansion or switch to fast tier to discharge this selected slow-flow obligation.
2. Add the exact E22 reproduction and artifact references to existing issue #36. Its current evidence already identifies default development splitting of installer service tests as wasteful: repeated setup across pytest processes.
3. Inspect E22’s assignments and setup durations against that diagnosis. If the same fixture is rebuilt across split processes, use the authorized engineering workflow to preserve complete node selection while retaining whole-file installer grouping. Otherwise profile the actual slow cases before prescribing changes.
4. A documentation clarification is reasonable: replace ambiguous “Elapsed time” with “Total run wall time,” and reference the separate per-test/file budget. This clarifies the existing contract; it does not change enforcement.
5. Keep runtime policy deadlines, retries, token/cost budgets, resource claims and unresolved-process accounting entirely unchanged.

Required verification after any runner/grouping fix:

- Prove every originally selected node still runs exactly as required; preserve host-specific coverage and isolation.
- Exercise runner grouping/failure-reporting tests through `al/verify_changed.py`, plus `al/tests/test_verification_budget.py`.
- Rerun E22’s exact four-file `--tier all` selection on stable source, preserving its declared worker configuration. Record fresh evidence; retain E22.
- Release verification remains a separate obligation when applicable. A diagnostic serial/profile run can establish cause, but cannot substitute for the declared acceptance run.

No new user decision is needed. This is a documented performance obligation with an ambiguous sentence to clarify, not authorization to remove timing gates.