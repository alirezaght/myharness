PLAN_STATUS: REVISED

RESOLUTION

**Yes: a separately named functional-verification area is appropriate under the explicit timing amendment.** It must execute the real shared runner and validate its report; it must not relabel the runner’s result or import E38 as fresh evidence.

No narrower existing interface was found:

- `verify_changed.py:292–297` combines functional success, source stability and timing budgets into its exit status. There is no functional-only CLI mode.
- `evidence require` requires current VALID PASS evidence. Changed acceptance semantics require explicit replanning; `reference/evidence.md:89–91` forbids asserting equivalence through supersession.
- `baseline-exception` requires an actual failed **base gate** (`al_baseline_exceptions.py:25–54`). It cannot dispose of E38’s candidate timing failure without inventing baseline evidence.
- Gate areas cannot be retired through ordinary evidence retirement (`al_evidence.py:590`).

Minimal implementation/registration:

1. In the new correct-base run, preserve the exact user amendment and plan an area such as `al-selected-functional`. State explicitly that it proves selected functional assertions, **not timing compliance or release verification**.
2. Use a small reviewed adapter that invokes the unchanged shared runner with the complete intended selection and ordinary configuration. Include adapter source, runner/configuration, selected tests and dependencies, and amendment artifact in the evidence scope.
3. Accept:
   - exit `0` only with a consistent successful report; or
   - exit `1` only when the current invocation’s report proves timing-only failure.
4. For the timing-only branch, require all of:
   - recognized report schema/contract, raw `status == "FAIL"`, `release_claim == false`;
   - `source_stable == true`;
   - `reconciliation.selected_success == true`;
   - zero executed failures, errors, skips and missing obligations;
   - every planned selected obligation accounted for, with no unexpected duplicates or selection reduction;
   - nonempty `over_budget`, consistent with the recorded durations and unchanged budget configuration;
   - no launch failure, timeout, interruption, malformed/truncated report, source mismatch or unresolved process/storage obligation.
5. Bind the report to **this invocation** using a fresh owned output location, retained plan hash, source binding and actual child exit. Hash and retain the original report, logs, timings and selection. A preexisting JSON file or summary counts alone are insufficient.
6. Emit a separate result identifying `functional: PASS`, `raw_runner: FAIL`, `timing: accepted checkpoint limitation`, and the exact amendment reference. Never emit `timing: PASS` or alter the raw report.

Before using the adapter as evidence, test that functional failure, missing/skipped nodes, source drift, wrong/stale report, timeout and malformed output all remain failures; only the expressly authorized timing-only case succeeds.

Preserve E38 unchanged in its original run and carry its reference into the new review materials. Do not use `evidence supersede` across these different semantics. If replacing an ordinary combined area within the same run, use supported replanning/retirement with the amendment as reason; this cannot retire a discovered gate.

Packaging, Chrome, independent review, mandatory discovered gates and runtime resource/budget controls remain separate requirements. The amendment authorizes this checkpoint’s timing disposition, not a general timing exemption. No additional timing optimization, profiling or baseline rerun is needed.