# Approved reliability architecture and prerequisite status

The developer replied **approved** to the concrete P1–P5 recommendations on 19 November 2026. Those verdicts were recorded through the existing Harness `InterviewEngine.decide` workflow in task `t-73243652`; [approved-decisions.json](approved-decisions.json) retains the user text, interpretation and generated registry records. The original [pending snapshot](pending-decisions.json) is historical and has not been rewritten.

| Proposal | Approved registry record | Behavior |
| --- | --- | --- |
| P1 / RP1 | [DEC-0048 v2](../decisions/DEC-0048-reliability-sequencing.md) | Implement the full mission now in controlled increments. Validate specific Knowledge interfaces when consumed; no blanket Knowledge acceptance gate. |
| P2 / RP2 | [DEC-0049](../decisions/DEC-0049-improvement-activation-authority.md) | Automatically collect incidents/draft proposals; require explicit developer approval for activation. |
| P3 / RP3 | [DEC-0050](../decisions/DEC-0050-initial-runtime-boundary.md) | Supported supervised operation boundary; native host/authentication trust remains explicit. |
| P4 / RP4 | [DEC-0051](../decisions/DEC-0051-observation-retention-and-export.md) | Local closed records; no exporter initially; derived diagnostic detail limited to 30 days. |
| P5 / RP5 | [DEC-0052](../decisions/DEC-0052-evaluation-release-standard.md) | Frozen paired evaluation and predetermined thresholds, no material acceptance regression, independent review and explicit activation approval. |

Architecture approval does not mark Knowledge acceptance, final implementation review, provider qualification or a future improvement activation complete. No sealed implementation specification, execution launch or production policy change was created by accepting these proposals.

## Completed prerequisite work in this worktree

A temporary, project-local test toolchain was provisioned without replacing the user's global Python, uv or native clients:

- Python 3.13.18 at `/private/tmp/al-reliability-python/cpython-3.13.18-macos-aarch64-none`.
- Locked development environment `/private/tmp/al-reliability-verified-venv`.
- Actual linked SQLite 3.53.1; existing capability suite: **6 passed**.
- Knowledge and native-diagnostic regression selection: **411 passed**, six browser tests deselected, 588.52 seconds.
- Knowledge Chrome selection: **5 passed, 1 failed** initially. The failure was Chrome not publishing its DevTools port within the fixture's ten-second startup deadline. The unchanged focused recheck passed in 1.27 seconds.
- Viewed desktop and 390-pixel screenshots for the graph, long observation disclosure and provenance UI. No alignment/clipping defect was observed. The narrow graph intentionally scrolls horizontally; literal hostile fixture text remained inert, paragraphs wrapped and evidence controls remained reachable.

The initial audit's older SQLite failure and both operation-sandbox attempts remain in [verification.md](verification.md). New passes are additional evidence, not edits to those historical results. Chrome startup timeout is retained as an unresolved intermittent infrastructure observation; a successful recheck does not erase it.

Runtime distribution provenance: [Python 3.13.18 release](https://www.python.org/downloads/release/python-31318/), [Astral Python standalone distributions](https://github.com/astral-sh/python-build-standalone/releases), and [SQLite release history](https://sqlite.org/changes.html). The local capability test result establishes the actual linked version used here. The repository's older `.venv` and `.python-version` were not changed; future checks must select the qualified environment explicitly or recreate it after temporary storage loss.

## Knowledge acceptance ownership

Read-only Codex app inspection located the existing chat **Implement phase 5**, ID `01a1799a-ee42-7641-8426-47180eacddf7`, which owns Knowledge acceptance, native observer reconstruction and the K7 Claude method. Its latest inspected turn was **interrupted**, with acceptance still incomplete. This worktree did not send messages to, resume, edit or import unfinished state from that chat.

Current fixture passes and UI checks do not establish K1–K9 through actual authorized agents. In particular, K5 cross-agent return/contribution proof, K7 independent measured-value evaluation and K8 clean-review/native provenance remain separate obligations of that work. Existing observer/usage/donor refusals are unchanged. The developer explicitly superseded DEC-0048 v1: those obligations do not gate this mission's implementation or independent review. Later reliability integration must validate the actual interfaces it uses.

A narrowly scoped Claude 2.1.328 compatibility plan was prepared before ownership was located. It is retained as **unimplemented** under AL session `reliability-r0-claude328-20261119`. No writer was admitted, no native profile was widened, and no provider qualification was claimed. Do not duplicate the Knowledge owner's work by executing that plan without reconciling ownership and accepted source first.

## Prepared first reliability increment

[The R1 specification](r1-implementation-spec.md) defines eligible current-epoch dispatch observations, closed outcome codes, two-table persistence, transactional fault behavior, idempotent evidence ingestion, scoped read/search/pagination and the existing dashboard section. Its fourteen acceptance criteria cover side effects, privacy canaries, epoch fencing, crash windows and Chrome behavior.

Review removed a proposed historical append path before implementation. Stale or inactive controllers cannot create/finalize records. A generation transition after pending intent leaves the original outcome unknown and never triggers tool replay or successor mutation. Early transport/epoch/assignment paths and asynchronous completion are explicit sensor gaps.

R1 collects no optional diagnostic detail. The approved 30-day limit applies if later detail collection is introduced; no extra table or material is created merely to exercise retention. No Knowledge adapter, provider observation, retry, resolution, proposal promotion or authority change is included in this first increment.

The R1 backend is authored and its focused checks passed; dashboard integration and regression verification are underway. Continue its AL Engineer → Builder → verification → independent review sequence, then the remaining roadmap increments. The full original mission remains authorized without another generic approval round. Only actual unresolved developer choices or explicit activation requirements need a decision. See [implementation-authorization.json](implementation-authorization.json) for the retained user correction and DEC-0048 v2 provenance.

## Evidence and storage

Public test XML and selected screenshots are retained under [r0-evidence](r0-evidence/), with exact source/argv/runtime/ownership metadata in [r0-status.json](r0-status.json). The evidence manifest hashes the retained artifacts. Full disposable fixture databases, copied runtimes and Chrome profiles are not delivery artifacts; after evidence capture and process completion, the three task-created fixture directories were removed. Current XML/screenshots and the reusable temporary toolchain remain. Other chats' storage is untouched.

The audit AL session preserves the original task and accepted amendment. The separate compatibility session is checkpointed with no writer or test execution. Neither is marked AL APPROVED.
