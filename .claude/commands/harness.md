---
description: "Start the AL Engineering Harness for a request (only entry point)"
argument-hint: <request>
---

Run `uv run harness start "$ARGUMENTS"` from the repository root and relay its output verbatim, including the task id and the proposed complexity. Tell the developer that the proposal is only a recommendation and can be overridden with the `override:` command printed in the output. Then run `uv run harness investigate <task id>` with the task id from that output and relay the report verbatim (it is read-only: verified facts, assumptions and unresolved questions stay separate).

Then run the requirements interview. You only relay and record; the CLI owns the question state and the checklist, and you never answer a question for the developer.

1. Ask the developer to confirm or override the proposed complexity (`uv run harness classify <id> <level>`), then run `uv run harness phase <id> next` to enter the interview phase.
2. Loop: run `uv run harness interview next <id>` (add `--group` when the developer prefers one topic at a time) and relay each question verbatim.
   - Plain question: ask it, then record exactly what the developer typed with `uv run harness interview answer <id> <qid> <text>`.
   - `(decision)` question with `recommendation needed`: write a recommendation and its rationale from the investigation facts, register it with `uv run harness interview propose <id> <qid> --title "<title>" --recommendation "<text>" --rationale "<why>"` (all three flags are required), present it, and record the developer's verdict with `uv run harness interview decide <id> <pid> accept|reject|modify` (`modify` needs `--response "<developer's change>"`). A recommendation is never a requirement until the developer accepts or modifies it.
   - "I don't know", unsure or deferred: run `uv run harness interview unknown <id> <qid>`. Never fill the gap with an assumption. The question stays open and blocking, and `next` moves on to the next one.
   - Only when the investigation report or an approved decision already answers a question, run `uv run harness interview skip <id> <qid> --source decision --ref DEC-xxxx` or `--source codebase --ref "<file or fact>"`, and tell the developer what was skipped and why. Use `--source not_relevant --ref "<reason>"` only when the developer says it does not apply.
   - A follow-up you need: `uv run harness interview ask <id> <topic> <text> [--decision]`.
3. After each round run `uv run harness interview checklist <id>` and relay it verbatim. Entries with a `catalogue:<key>` ref are due questions not asked yet.
4. When `next` prints `no questions left to ask; N open question(s) stay unresolved`, the remaining questions were deferred: show the checklist and ask the developer whether to revisit them. `no open questions` means nothing is open. Stop when the developer says so or nothing blocks; show `uv run harness interview summary <id>` at the end.

Do nothing beyond this; the harness is activated only by this command and never implicitly (SPEC §2, AC 1).
