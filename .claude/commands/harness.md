---
description: "Start the AL Engineering Harness for a request (only entry point)"
argument-hint: <request>
---

Run `uv run harness start "$ARGUMENTS"` from the repository root and relay its output verbatim, including the task id and the proposed complexity. Tell the developer that the proposal is only a recommendation and can be overridden with the `override:` command printed in the output. Then run `uv run harness investigate <task id>` with the task id from that output and relay the report verbatim (it is read-only: verified facts, assumptions and unresolved questions stay separate). Do nothing else; the harness is activated only by this command and never implicitly (SPEC §2, AC 1).
