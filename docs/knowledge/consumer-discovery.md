# Consumer discovery (D1, criterion K8)

Read-only discovery. Records facts; changes no product behavior.

## Method and caveats

- Every command was run through the Harness `run_command` tool under task `D1-consumer-discovery`, cwd `/Users/alirezaghiastabari/.local/share/harness/workspaces/knowledge-restart-20261116`.
- The tool returns stdout and stderr merged in a single `output` field, so they cannot be told apart. The output of each command is recorded verbatim in that merged form.
- Several `git` invocations printed two `xcodebuild[...]` noise lines (macOS developer-tools shim, written to stderr). They are kept verbatim below. The `git status --porcelain` run also printed `.claude/` permission warnings, caused by git itself; this task did not read, list or touch `.claude/`.
- No output was truncated. Each poll returned `"more": false`.
- First attempt: the first batch of commands was issued in parallel. Commands 1, 2 and 3 were refused by the harness with the errors `task is not currently running` (commands 1 and 2) and `task requires this native principal's active writer` (command 3). They produced no output. They were re-run one at a time and succeeded; the outputs below come from those re-runs. Commands 4, 5 and 6 succeeded on the first attempt.
- `git grep` without `--untracked` searches tracked files only. Untracked files (`conftest.py`, `tests/test_sqlite_capabilities.py`, `docs/specs/t-a38f0351/`, the new `docs/decisions/DEC-00{34..47}-*.md`) were therefore not searched.
- The source of the `’` in the `knowledge.js:17` line: the tool returned JSON-escaped text. The file contains the character U+2019 (right single quote); it is written below as that character.

## 1. Knowledge package literal call sites

argv:

```text
git grep -n -E "harness\.knowledge|from harness\.knowledge|import harness\.knowledge|knowledge_query|knowledge_explain|ContextBundle|ContextRequest|derive_scope|operator_scope|publish_snapshot|investigate_with_context" -- harness al/claude al/codex tests
```

Raw output (exit 0):

```text
2026-11-16 19:50:03.800 xcodebuild[13503:350639311]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-11-16 19:50:03.877 xcodebuild[13503:350639310] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
harness/cli_commands.py:147:        from harness.knowledge.integration import investigate_with_context
harness/cli_commands.py:149:        result = investigate_with_context(controller, task.id, root, decisions=decisions)
harness/cli_knowledge.py:6:from harness.knowledge.models import MAX_SOURCE_BYTES, ContextRequest, RefreshRequest, digest
harness/cli_knowledge.py:7:from harness.knowledge.policy import operator_scope
harness/cli_knowledge.py:8:from harness.knowledge.query import explain, query
harness/cli_knowledge.py:9:from harness.knowledge.refresh import refresh
harness/cli_knowledge.py:20:    scope = operator_scope(repository)
harness/cli_knowledge.py:87:        refresh(controller.conn, operator_scope(repository, task_id=task_id), RefreshRequest())
harness/cli_knowledge.py:94:        operator_scope(repository),
harness/cli_knowledge.py:95:        ContextRequest(text, tuple(entity_refs), snapshot_id),
harness/cli_knowledge.py:100:    return explain(controller.conn, operator_scope(repository), bundle_hash, assertion_id)
harness/cli_knowledge.py:105:    from harness.knowledge.sources import read_source
harness/cli_knowledge.py:107:    scope = operator_scope(repository)
harness/execution_mcp.py:24:    "knowledge_query": _schema(
harness/execution_mcp.py:31:    "knowledge_explain": _schema(
harness/execution_operations.py:397:                                    if name in {"knowledge_query", "knowledge_explain"}
harness/execution_operations.py:415:                    "knowledge_query",
harness/execution_operations.py:416:                    "knowledge_explain",
harness/execution_operations.py:437:                        if name in {"knowledge_query", "knowledge_explain"}
harness/execution_operations.py:447:        if name in {"knowledge_query", "knowledge_explain"}:
harness/execution_operations.py:448:            from harness.knowledge.models import ContextRequest
harness/execution_operations.py:449:            from harness.knowledge.policy import derive_scope
harness/execution_operations.py:450:            from harness.knowledge.query import explain, query
harness/execution_operations.py:453:            scope = derive_scope(controller, execution, principal, state, Path(root))
harness/execution_operations.py:454:            if name == "knowledge_query":
harness/execution_operations.py:460:                    ContextRequest(a.get("query", ""), tuple(a.get("entity_refs", ()))),
harness/knowledge/__init__.py:3:from harness.knowledge.models import ContextRequest, KnowledgeScope, RefreshRequest
harness/knowledge/__init__.py:4:from harness.knowledge.query import explain, query
harness/knowledge/__init__.py:5:from harness.knowledge.refresh import refresh
harness/knowledge/__init__.py:7:__all__ = ["ContextRequest", "KnowledgeScope", "RefreshRequest", "explain", "query", "refresh"]
harness/knowledge/extract.py:7:from harness.knowledge.models import Assertion, Extraction, SourceVersion
harness/knowledge/integration.py:5:from harness.knowledge.models import ContextRequest, RefreshRequest
harness/knowledge/integration.py:6:from harness.knowledge.policy import operator_scope
harness/knowledge/integration.py:7:from harness.knowledge.query import query
harness/knowledge/integration.py:8:from harness.knowledge.refresh import refresh
harness/knowledge/integration.py:11:def investigate_with_context(controller, task_id, repo_root, request=None, decisions=None):
harness/knowledge/integration.py:15:    scope = operator_scope(repo_root, task_id=task_id, purpose="investigation")
harness/knowledge/integration.py:22:            bundle = query(controller.conn, scope, request or ContextRequest(task.request[:4096]))
harness/knowledge/models.py:122:class ContextRequest:
harness/knowledge/models.py:130:class ContextBundle:
harness/knowledge/policy.py:5:from harness.knowledge.models import KnowledgeScope, canonical, digest, identity
harness/knowledge/policy.py:10:def operator_scope(repository, *, task_id=None, purpose="operator"):
harness/knowledge/policy.py:50:def derive_scope(controller, execution, principal, state, repository):
harness/knowledge/query.py:8:from harness.knowledge.models import (
harness/knowledge/query.py:10:    ContextBundle,
harness/knowledge/query.py:11:    ContextRequest,
harness/knowledge/query.py:15:from harness.knowledge.policy import policy_digest, validate_scope
harness/knowledge/query.py:16:from harness.knowledge.sources import read_source
harness/knowledge/query.py:18:_DEFAULT_CONTEXT_REQUEST = ContextRequest()
harness/knowledge/query.py:98:def query(conn, scope, request: ContextRequest = _DEFAULT_CONTEXT_REQUEST) -> ContextBundle:
harness/knowledge/query.py:312:    return ContextBundle(bundle_hash, payload)
harness/knowledge/refresh.py:4:from harness.knowledge.extract import extract_source
harness/knowledge/refresh.py:5:from harness.knowledge.models import (
harness/knowledge/refresh.py:12:from harness.knowledge.policy import validate_scope
harness/knowledge/refresh.py:13:from harness.knowledge.sources import enumerate_sources, inventory_names, read_source
harness/knowledge/refresh.py:14:from harness.knowledge.store import ensure_project, publish_snapshot, record_refresh_attempt
harness/knowledge/refresh.py:67:        snapshot = publish_snapshot(conn, prepared, expected_active=expected)
harness/knowledge/sources.py:10:from harness.knowledge.models import (
harness/knowledge/store.py:6:from harness.knowledge.models import (
harness/knowledge/store.py:52:def publish_snapshot(conn, prepared: PreparedSnapshot, *, expected_active: str | None) -> str:
harness/native_tool_policy.py:15:    "knowledge_query",
harness/native_tool_policy.py:16:    "knowledge_explain",
tests/test_execution_snapshot.py:22:from harness.knowledge.extract import extract_source
tests/test_execution_snapshot.py:23:from harness.knowledge.models import (
tests/test_execution_snapshot.py:24:    EXTRACTOR_VERSION, ContextRequest, PreparedSnapshot, QueryLimits,
tests/test_execution_snapshot.py:27:from harness.knowledge.policy import operator_scope
tests/test_execution_snapshot.py:28:from harness.knowledge.query import explain, query
tests/test_execution_snapshot.py:29:from harness.knowledge.store import publish_snapshot
tests/test_execution_snapshot.py:32:scope = operator_scope(root)
tests/test_execution_snapshot.py:39:    publish_snapshot(conn, PreparedSnapshot(
tests/test_execution_snapshot.py:42:    bundle = query(conn, scope, ContextRequest('calculate', limits=QueryLimits(bytes=4096)))
tests/test_execution_snapshot.py:48:    module = sys.modules['harness.knowledge.query']
tests/test_execution_snapshot.py:57:def test_snapshot_runs_bounded_sourced_knowledge_query(tmp_path, versioned):
tests/test_knowledge_cli.py:11:from harness.knowledge.models import RefreshRequest
tests/test_knowledge_cli.py:12:from harness.knowledge.refresh import refresh
tests/test_knowledge_cli.py:13:from harness.knowledge.store import ensure_project
tests/test_knowledge_extraction.py:5:from harness.knowledge.extract import extract_source
tests/test_knowledge_extraction.py:6:from harness.knowledge.models import SourceVersion
tests/test_knowledge_extraction.py:7:from harness.knowledge.sources import eligible, read_source
tests/test_knowledge_integration.py:5:from harness.knowledge.integration import investigate_with_context
tests/test_knowledge_integration.py:6:from harness.knowledge.models import ContextRequest
tests/test_knowledge_integration.py:7:from harness.knowledge.policy import operator_scope
tests/test_knowledge_integration.py:8:from harness.knowledge.query import query
tests/test_knowledge_integration.py:21:    result = investigate_with_context(controller, task.id, root)
tests/test_knowledge_integration.py:28:    later = query(conn, operator_scope(root), ContextRequest("calculate"))
tests/test_knowledge_policy.py:6:from harness.knowledge.models import ContextRequest
tests/test_knowledge_policy.py:7:from harness.knowledge.query import explain, query
tests/test_knowledge_policy.py:8:from harness.knowledge.refresh import refresh
tests/test_knowledge_policy.py:19:    bundle = query(conn, reviewer, ContextRequest('consumer'))
tests/test_knowledge_policy.py:22:    general = query(conn, scope, ContextRequest('consumer'))
tests/test_knowledge_policy.py:26:        query(conn, replace(scope, purpose='fresh-review'), ContextRequest('service'))
tests/test_knowledge_policy.py:32:    bundle = query(conn, scope, ContextRequest('service'))
tests/test_knowledge_query.py:8:from harness.knowledge.models import ContextRequest, QueryLimits, canonical, digest
tests/test_knowledge_query.py:9:from harness.knowledge.query import explain, query
tests/test_knowledge_query.py:10:from harness.knowledge.refresh import refresh
tests/test_knowledge_query.py:17:    bundle = query(conn, scope, ContextRequest(entity_refs=("module:service",)))
tests/test_knowledge_query.py:26:    updated = query(conn, scope, ContextRequest(entity_refs=("module:service",)))
tests/test_knowledge_query.py:34:    bundle = query(conn, scope, ContextRequest("service", limits=QueryLimits(bytes=2048)))
tests/test_knowledge_query.py:51:    bundle = query(conn, scope, ContextRequest("large-contract"))
tests/test_knowledge_query.py:68:    current = query(conn, scope, ContextRequest("large-contract"))
tests/test_knowledge_snapshots.py:11:from harness.knowledge.extract import extract_source
tests/test_knowledge_snapshots.py:12:from harness.knowledge.models import (
tests/test_knowledge_snapshots.py:18:from harness.knowledge.policy import operator_scope
tests/test_knowledge_snapshots.py:19:from harness.knowledge.refresh import refresh
tests/test_knowledge_snapshots.py:20:from harness.knowledge.sources import enumerate_sources
tests/test_knowledge_snapshots.py:21:from harness.knowledge.store import SnapshotConflict, publish_snapshot
tests/test_knowledge_snapshots.py:33:    scope = operator_scope(root)
tests/test_knowledge_snapshots.py:62:    second = publish_snapshot(conn, prepared, expected_active=first)
tests/test_knowledge_snapshots.py:64:        publish_snapshot(conn, prepared, expected_active=first)
tests/test_knowledge_snapshots.py:73:        publish_snapshot(conn, invalid, expected_active=second)
tests/test_knowledge_snapshots.py:153:    import harness.knowledge.sources as sources
tests/test_knowledge_snapshots.py:176:        import_module("harness.knowledge.refresh"), "enumerate_sources", lambda *_: inventory
tests/test_knowledge_snapshots.py:185:    from harness.knowledge.sources import read_source
tests/test_knowledge_snapshots.py:195:    monkeypatch.setattr(import_module("harness.knowledge.refresh"), "read_source", unstable_read)
```

## 2. AL scripts: knowledge / bundle_hash / context_bundle

argv:

```text
git grep -n -E "knowledge|bundle_hash|context_bundle" -- al/claude/skills/al/scripts al/codex/skills/al/scripts
```

Raw output (exit 0):

```text
2026-11-16 19:50:08.010 xcodebuild[13515:350639411]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-11-16 19:50:08.085 xcodebuild[13515:350639410] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
al/claude/skills/al/scripts/al_events.py:62:        offset = 1 if pending and pending[0]['kind'] == 'delivery.correction.acknowledged' else 0
al/claude/skills/al/scripts/al_execution_contract.py:24:    "delivery.correction.acknowledged",
al/claude/skills/al/scripts/al_execution_contract.py:40:    "decision.acknowledged",
al/claude/skills/al/scripts/al_execution_contract.py:229:        "delivery.correction.acknowledged": {"request_id", "request_digest", "generation", "prior_completion_digest"},
al/claude/skills/al/scripts/al_execution_contract.py:258:        "decision.acknowledged": {"delivery_id", "blocker_id", "state", "decision_hash"},
al/claude/skills/al/scripts/al_execution_contract.py:326:    if kind == "decision.acknowledged":
al/claude/skills/al/scripts/al_execution_corrections.py:47:    E.queue(st, 'delivery.correction.acknowledged', ack)
al/claude/skills/al/scripts/al_execution_decisions.py:1:"""Acknowledged developer decisions and narrowly invalidated amendment chains."""
al/claude/skills/al/scripts/al_execution_decisions.py:87:    E.queue(st, 'decision.acknowledged', ack)
al/claude/skills/al/scripts/al_planning.py:75:        refuse("planning provenance requires current planning or acknowledged scoped revalidation")
al/claude/skills/al/scripts/al_recovery_subjects.py:176:                    "An acknowledged scoped developer decision is required.",
al/claude/skills/al/scripts/al_recovery_subjects.py:178:                        "acknowledged-developer-decision",
al/claude/skills/al/scripts/al_recovery_subjects.py:179:                        "Use the existing Harness decision delivery and acknowledgement path for this exact blocker.",
al/claude/skills/al/scripts/al_task.py:57:    if 'integration' in st: refuse('harness-bound amendments require an acknowledged scoped developer decision')
al/codex/skills/al/scripts/al_events.py:62:        offset = 1 if pending and pending[0]['kind'] == 'delivery.correction.acknowledged' else 0
al/codex/skills/al/scripts/al_execution_contract.py:24:    "delivery.correction.acknowledged",
al/codex/skills/al/scripts/al_execution_contract.py:40:    "decision.acknowledged",
al/codex/skills/al/scripts/al_execution_contract.py:229:        "delivery.correction.acknowledged": {"request_id", "request_digest", "generation", "prior_completion_digest"},
al/codex/skills/al/scripts/al_execution_contract.py:258:        "decision.acknowledged": {"delivery_id", "blocker_id", "state", "decision_hash"},
al/codex/skills/al/scripts/al_execution_contract.py:326:    if kind == "decision.acknowledged":
al/codex/skills/al/scripts/al_execution_corrections.py:47:    E.queue(st, 'delivery.correction.acknowledged', ack)
al/codex/skills/al/scripts/al_execution_decisions.py:1:"""Acknowledged developer decisions and narrowly invalidated amendment chains."""
al/codex/skills/al/scripts/al_execution_decisions.py:87:    E.queue(st, 'decision.acknowledged', ack)
al/codex/skills/al/scripts/al_planning.py:75:        refuse("planning provenance requires current planning or acknowledged scoped revalidation")
al/codex/skills/al/scripts/al_recovery_subjects.py:176:                    "An acknowledged scoped developer decision is required.",
al/codex/skills/al/scripts/al_recovery_subjects.py:178:                        "acknowledged-developer-decision",
al/codex/skills/al/scripts/al_recovery_subjects.py:179:                        "Use the existing Harness decision delivery and acknowledgement path for this exact blocker.",
al/codex/skills/al/scripts/al_task.py:57:    if 'integration' in st: refuse('harness-bound amendments require an acknowledged scoped developer decision')
```

## 3. AL packet and handoff builders

argv:

```text
git grep -n -E "def cmd_packet|review.*packet|def .*handoff" -- al/claude/skills/al/scripts al/codex/skills/al/scripts
```

Raw output (exit 0):

```text
2026-11-16 19:50:12.250 xcodebuild[13527:350639545]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-11-16 19:50:12.333 xcodebuild[13527:350639544] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
al/claude/skills/al/scripts/al_behavior_observations.py:251:def cmd_packet(a, sid):
al/claude/skills/al/scripts/al_events.py:9:def initialise(st, envelope, handoff_path):
al/claude/skills/al/scripts/al_planning.py:179:def cmd_packet(a, sid):
al/claude/skills/al/scripts/al_recovery_guidance.py:44:    "review.packet": ("review-packet", None, {"stage", "replace_unavailable_attempt", "output"}),
al/claude/skills/al/scripts/al_recovery_subjects.py:187:        if stage not in review["packets"]:
al/claude/skills/al/scripts/al_recovery_subjects.py:189:                "review.packet",
al/claude/skills/al/scripts/al_review.py:63:    st['review']={'id':'R'+str(st['review_iteration']),'stage':'fresh','candidate':candidate,'task_hash':al_task.task_hash(st),'packets':{},'correction_consumed':False}
al/claude/skills/al/scripts/al_review.py:68:def cmd_packet(a,sid):
al/claude/skills/al/scripts/al_review.py:89:    raw=json.dumps(data,sort_keys=True).encode(); ref=A.snapshot_bytes(sid,raw,'review-packet-'+a.stage+('-v2' if Behavior.enabled(st) else ''),'runtime')
al/claude/skills/al/scripts/al_review.py:95:    r=st['review']; packet=r['packets'].get(stage)
al/claude/skills/al/scripts/al_review.py:96:    if not packet: refuse('review packet must be captured before report')
al/claude/skills/al/scripts/al_review.py:143:    Behavior.validate_review_lineage(st,r,A.read_json(r['packets']['fresh']))
al/claude/skills/al/scripts/al_review.py:177:    p=sub.add_parser('review-packet'); p.add_argument('--stage',choices=['fresh','compare'],required=True); p.add_argument('--output'); p.add_argument('--replace-unavailable-attempt',action='store_true'); p.add_argument('--recovery-file',dest='operation_recovery_file'); p.set_defaults(fn=cmd_packet)
al/claude/skills/al/scripts/al_review_behavior.py:33:def validate_review_lineage(st, review, packet=None):
al/claude/skills/al/scripts/al_review_behavior.py:57:            or packet.get("kind") != "review-packet-v2"
al/claude/skills/al/scripts/al_review_behavior.py:63:            refuse("review packet authority/candidate/document differs")
al/claude/skills/al/scripts/al_review_behavior.py:122:        kind="review-packet-v2",
al/claude/skills/al/scripts/al_review_behavior.py:196:    validate_review_lineage(st, st["review"], packet_value)
al/claude/skills/al/scripts/al_review_behavior.py:215:    packet_ref = st["review"]["packets"][stage]
al/claude/skills/al/scripts/al_review_behavior.py:216:    bridge = validate_review_lineage(st, st["review"], A.read_json(packet_ref))
al/claude/skills/al/scripts/al_rework_recovery.py:48:def cmd_packet(a,sid):
al/claude/skills/al/scripts/al_writer_handoff.py:40:def cmd_handoff(a, sid):
al/codex/skills/al/scripts/al_behavior_observations.py:251:def cmd_packet(a, sid):
al/codex/skills/al/scripts/al_events.py:9:def initialise(st, envelope, handoff_path):
al/codex/skills/al/scripts/al_planning.py:179:def cmd_packet(a, sid):
al/codex/skills/al/scripts/al_recovery_guidance.py:44:    "review.packet": ("review-packet", None, {"stage", "replace_unavailable_attempt", "output"}),
al/codex/skills/al/scripts/al_recovery_subjects.py:187:        if stage not in review["packets"]:
al/codex/skills/al/scripts/al_recovery_subjects.py:189:                "review.packet",
al/codex/skills/al/scripts/al_review.py:63:    st['review']={'id':'R'+str(st['review_iteration']),'stage':'fresh','candidate':candidate,'task_hash':al_task.task_hash(st),'packets':{},'correction_consumed':False}
al/codex/skills/al/scripts/al_review.py:68:def cmd_packet(a,sid):
al/codex/skills/al/scripts/al_review.py:89:    raw=json.dumps(data,sort_keys=True).encode(); ref=A.snapshot_bytes(sid,raw,'review-packet-'+a.stage+('-v2' if Behavior.enabled(st) else ''),'runtime')
al/codex/skills/al/scripts/al_review.py:95:    r=st['review']; packet=r['packets'].get(stage)
al/codex/skills/al/scripts/al_review.py:96:    if not packet: refuse('review packet must be captured before report')
al/codex/skills/al/scripts/al_review.py:143:    Behavior.validate_review_lineage(st,r,A.read_json(r['packets']['fresh']))
al/codex/skills/al/scripts/al_review.py:177:    p=sub.add_parser('review-packet'); p.add_argument('--stage',choices=['fresh','compare'],required=True); p.add_argument('--output'); p.add_argument('--replace-unavailable-attempt',action='store_true'); p.add_argument('--recovery-file',dest='operation_recovery_file'); p.set_defaults(fn=cmd_packet)
al/codex/skills/al/scripts/al_review_behavior.py:33:def validate_review_lineage(st, review, packet=None):
al/codex/skills/al/scripts/al_review_behavior.py:57:            or packet.get("kind") != "review-packet-v2"
al/codex/skills/al/scripts/al_review_behavior.py:63:            refuse("review packet authority/candidate/document differs")
al/codex/skills/al/scripts/al_review_behavior.py:122:        kind="review-packet-v2",
al/codex/skills/al/scripts/al_review_behavior.py:196:    validate_review_lineage(st, st["review"], packet_value)
al/codex/skills/al/scripts/al_review_behavior.py:215:    packet_ref = st["review"]["packets"][stage]
al/codex/skills/al/scripts/al_review_behavior.py:216:    bridge = validate_review_lineage(st, st["review"], A.read_json(packet_ref))
al/codex/skills/al/scripts/al_rework_recovery.py:48:def cmd_packet(a,sid):
al/codex/skills/al/scripts/al_writer_handoff.py:40:def cmd_handoff(a, sid):
```

## 4. knowledge in CLI and dashboard

argv:

```text
git grep -n -E "knowledge" -- harness/cli.py harness/cli_commands.py harness/cli_knowledge.py harness/dashboard
```

Raw output (exit 0):

```text
2026-11-16 19:49:46.931 xcodebuild[13433:350638697]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-11-16 19:49:47.024 xcodebuild[13433:350638695] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
harness/cli.py:18:from harness.cli_knowledge import add_knowledge_parser
harness/cli.py:93:    add_knowledge_parser(subparsers)
harness/cli_commands.py:147:        from harness.knowledge.integration import investigate_with_context
harness/cli_knowledge.py:6:from harness.knowledge.models import MAX_SOURCE_BYTES, ContextRequest, RefreshRequest, digest
harness/cli_knowledge.py:7:from harness.knowledge.policy import operator_scope
harness/cli_knowledge.py:8:from harness.knowledge.query import explain, query
harness/cli_knowledge.py:9:from harness.knowledge.refresh import refresh
harness/cli_knowledge.py:19:def inspect_knowledge(controller, repository):
harness/cli_knowledge.py:22:        "SELECT * FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
harness/cli_knowledge.py:36:        "SELECT status,coverage_json,created_at FROM knowledge_refresh_attempts "
harness/cli_knowledge.py:53:            "SELECT COUNT(*) FROM knowledge_snapshot_sources WHERE snapshot_id=? "
harness/cli_knowledge.py:59:            "SELECT a.subject_id AS entity_id FROM knowledge_assertions a "
harness/cli_knowledge.py:60:            "JOIN knowledge_snapshot_assertions m ON m.assertion_id=a.assertion_id "
harness/cli_knowledge.py:62:            "SELECT a.object_id AS entity_id FROM knowledge_assertions a "
harness/cli_knowledge.py:63:            "JOIN knowledge_snapshot_assertions m ON m.assertion_id=a.assertion_id "
harness/cli_knowledge.py:68:            "SELECT COUNT(*) FROM knowledge_snapshot_assertions WHERE snapshot_id=?", (snapshot,)
harness/cli_knowledge.py:72:                "SELECT coverage_json FROM knowledge_snapshots WHERE snapshot_id=?",
harness/cli_knowledge.py:85:def refresh_knowledge(controller, repository, *, task_id=None):
harness/cli_knowledge.py:91:def query_knowledge(controller, repository, text="", *, entity_refs=(), snapshot_id=None):
harness/cli_knowledge.py:99:def explain_knowledge(controller, repository, bundle_hash, assertion_id):
harness/cli_knowledge.py:103:def evidence_knowledge(controller, repository, bundle_hash, assertion_id):
harness/cli_knowledge.py:105:    from harness.knowledge.sources import read_source
harness/cli_knowledge.py:128:def add_knowledge_parser(subparsers):
harness/cli_knowledge.py:129:    root = subparsers.add_parser("knowledge", help="inspect and query the sourced project graph")
harness/cli_knowledge.py:130:    actions = root.add_subparsers(dest="knowledge_action", required=True)
harness/cli_knowledge.py:137:            action.add_argument("text", nargs="*", help="search the current knowledge snapshot")
harness/cli_knowledge.py:141:        action.set_defaults(handler=cmd_knowledge)
harness/cli_knowledge.py:144:def cmd_knowledge(args: argparse.Namespace) -> int:
harness/cli_knowledge.py:149:        if args.knowledge_action == "inspect":
harness/cli_knowledge.py:150:            result = inspect_knowledge(controller, repository)
harness/cli_knowledge.py:151:        elif args.knowledge_action == "refresh":
harness/cli_knowledge.py:152:            result = refresh_knowledge(controller, repository, task_id=args.task_id)
harness/cli_knowledge.py:153:        elif args.knowledge_action == "query":
harness/cli_knowledge.py:154:            result = query_knowledge(controller, repository, " ".join(args.text))
harness/cli_knowledge.py:156:            result = explain_knowledge(controller, repository, args.bundle_hash, args.assertion_id)
harness/dashboard/knowledge_view.py:1:"""Task-scoped knowledge graph and exact-preview planning recovery routes."""
harness/dashboard/knowledge_view.py:7:from harness.cli_knowledge import (
harness/dashboard/knowledge_view.py:8:    evidence_knowledge,
harness/dashboard/knowledge_view.py:9:    explain_knowledge,
harness/dashboard/knowledge_view.py:10:    inspect_knowledge,
harness/dashboard/knowledge_view.py:11:    query_knowledge,
harness/dashboard/knowledge_view.py:12:    refresh_knowledge,
harness/dashboard/knowledge_view.py:20:def read_knowledge(db_path, task_id):
harness/dashboard/knowledge_view.py:22:        raise LookupError("knowledge database not found")
harness/dashboard/knowledge_view.py:25:        return inspect_knowledge(controller, repository)
harness/dashboard/knowledge_view.py:28:def read_knowledge_evidence(db_path, task_id, bundle_hash, assertion_id):
harness/dashboard/knowledge_view.py:30:        raise LookupError("knowledge database not found")
harness/dashboard/knowledge_view.py:33:        return evidence_knowledge(controller, repository, bundle_hash, assertion_id)
harness/dashboard/knowledge_view.py:36:def knowledge_action(db_path, body, action):
harness/dashboard/knowledge_view.py:49:            raise ValueError("unexpected knowledge action fields")
harness/dashboard/knowledge_view.py:55:                result = refresh_knowledge(controller, repository, task_id=data["task"])
harness/dashboard/knowledge_view.py:57:                result = query_knowledge(controller, repository, data["query"])
harness/dashboard/knowledge_view.py:59:                result = explain_knowledge(
harness/dashboard/server.py:55:    "/api/knowledge/refresh": "knowledge-refresh",
harness/dashboard/server.py:56:    "/api/knowledge/query": "knowledge-query",
harness/dashboard/server.py:57:    "/api/knowledge/explain": "knowledge-explain",
harness/dashboard/server.py:81:    "/static/knowledge.js": ("knowledge.js", "text/javascript; charset=utf-8"),
harness/dashboard/server.py:216:            "/api/knowledge",
harness/dashboard/server.py:217:            "/api/knowledge/evidence",
harness/dashboard/server.py:225:                    if url.path == "/api/knowledge"
harness/dashboard/server.py:228:                        if url.path == "/api/knowledge/evidence"
harness/dashboard/server.py:234:                if url.path == "/api/knowledge":
harness/dashboard/server.py:235:                    from harness.dashboard.knowledge_view import read_knowledge
harness/dashboard/server.py:237:                    data = read_knowledge(self.server.db_path, query["task"][0])
harness/dashboard/server.py:238:                elif url.path == "/api/knowledge/evidence":
harness/dashboard/server.py:239:                    from harness.dashboard.knowledge_view import read_knowledge_evidence
harness/dashboard/server.py:241:                    data = read_knowledge_evidence(
harness/dashboard/server.py:248:                    from harness.dashboard.knowledge_view import spec_restoration_preview
harness/dashboard/server.py:261:                    from harness.dashboard.knowledge_view import planning_preview
harness/dashboard/server.py:325:        elif path.startswith("/api/knowledge/"):
harness/dashboard/server.py:326:            from harness.dashboard.knowledge_view import knowledge_action
harness/dashboard/server.py:328:            self._json(*knowledge_action(db_path, body, action.removeprefix("knowledge-")))
harness/dashboard/server.py:330:            from harness.dashboard.knowledge_view import planning_action
harness/dashboard/server.py:334:            from harness.dashboard.knowledge_view import spec_restoration_action
harness/dashboard/static/app.css:1151:.knowledge-panel {
harness/dashboard/static/app.css:1159:.knowledge-panel p,
harness/dashboard/static/app.css:1160:.knowledge-panel h4,
harness/dashboard/static/app.css:1161:.knowledge-panel summary {
harness/dashboard/static/app.css:1165:.knowledge-query-form {
harness/dashboard/static/app.css:1172:.knowledge-query-form input {
harness/dashboard/static/app.css:1179:.knowledge-panel button {
harness/dashboard/static/app.css:1184:.knowledge-panel .knowledge-refresh-incomplete {
harness/dashboard/static/app.css:1188:.knowledge-coverage {
harness/dashboard/static/app.css:1193:.knowledge-coverage summary {
harness/dashboard/static/app.css:1197:.knowledge-coverage p {
harness/dashboard/static/app.css:1202:.knowledge-assertion,
harness/dashboard/static/app.css:1203:.knowledge-graph {
harness/dashboard/static/app.css:1213:.knowledge-graph-svg {
harness/dashboard/static/app.css:1220:.knowledge-graph-scroll {
harness/dashboard/static/app.css:1228:.knowledge-arrow {
harness/dashboard/static/app.css:1234:.knowledge-arrowhead {
harness/dashboard/static/app.css:1238:.knowledge-node {
harness/dashboard/static/app.css:1243:.knowledge-node rect {
harness/dashboard/static/app.css:1249:.knowledge-node:focus rect,
harness/dashboard/static/app.css:1250:.knowledge-node:hover rect {
harness/dashboard/static/app.css:1255:.knowledge-node text {
harness/dashboard/static/app.css:1260:.knowledge-node .knowledge-node-state {
harness/dashboard/static/app.css:1265:.knowledge-graph-selection {
harness/dashboard/static/app.css:1269:.knowledge-assertion h4,
harness/dashboard/static/app.css:1270:.knowledge-graph h4 {
harness/dashboard/static/app.css:1274:.knowledge-source-text {
harness/dashboard/static/app.js:289:  const knowledge = el("details");
harness/dashboard/static/app.js:290:  knowledge.dataset.viewDisclosure = "knowledge";
harness/dashboard/static/app.js:291:  knowledge.append(el("summary", "Project knowledge"), renderKnowledgePanel(task.id));
harness/dashboard/static/app.js:292:  section.append(knowledge);
harness/dashboard/static/execution.js:922:      acknowledged: "Acknowledged by AL",
harness/dashboard/static/index.html:15:    <script src="/static/knowledge.js" defer></script>
harness/dashboard/static/knowledge.js:3:let knowledgeGraphSequence = 0;
harness/dashboard/static/knowledge.js:13:  section.id = "knowledge";
harness/dashboard/static/knowledge.js:14:  section.className = "knowledge-panel";
harness/dashboard/static/knowledge.js:16:  section.append(kEl("h3", "Project knowledge graph"));
harness/dashboard/static/knowledge.js:17:  section.append(kEl("p", "Sourced context for this task’s investigated repository. Relationships are knowledge observations, not approved architecture or execution authority."));
harness/dashboard/static/knowledge.js:19:  status.className = "knowledge-status";
harness/dashboard/static/knowledge.js:23:  const refresh = kEl("button", "Refresh project knowledge");
harness/dashboard/static/knowledge.js:25:  refresh.className = "knowledge-refresh";
harness/dashboard/static/knowledge.js:31:  form.className = "knowledge-query-form";
harness/dashboard/static/knowledge.js:35:  input.setAttribute("aria-label", "Search project knowledge");
harness/dashboard/static/knowledge.js:41:  results.className = "knowledge-results";
harness/dashboard/static/knowledge.js:46:    const result = await postJson("/api/knowledge/query", { task: taskId, query: input.value });
harness/dashboard/static/knowledge.js:59:  const status = section.querySelector(".knowledge-status");
harness/dashboard/static/knowledge.js:60:  const refresh = section.querySelector(".knowledge-refresh");
harness/dashboard/static/knowledge.js:63:    const response = await fetch(`/api/knowledge?${params}`, { cache: "no-store" });
harness/dashboard/static/knowledge.js:79:  details.className = "knowledge-coverage";
harness/dashboard/static/knowledge.js:101:  if (!hasSnapshot && failed) summary = "No knowledge snapshot yet. Refresh incomplete.";
harness/dashboard/static/knowledge.js:102:  else if (!hasSnapshot) summary = "No knowledge snapshot yet. Refresh to build one from eligible repository sources.";
harness/dashboard/static/knowledge.js:105:  else summary = "A complete knowledge snapshot is available; latest refresh status is unknown.";
harness/dashboard/static/knowledge.js:119:  const status = section.querySelector(".knowledge-status");
harness/dashboard/static/knowledge.js:120:  const refresh = section.querySelector(".knowledge-refresh");
harness/dashboard/static/knowledge.js:125:    const result = await postJson("/api/knowledge/refresh", { task: taskId });
harness/dashboard/static/knowledge.js:132:      message.className = "knowledge-refresh-incomplete";
harness/dashboard/static/knowledge.js:143:      retained.className = "knowledge-refresh-incomplete";
harness/dashboard/static/knowledge.js:160:  notice.className = "knowledge-notice";
harness/dashboard/static/knowledge.js:177:  selection.className = "knowledge-graph-selection";
harness/dashboard/static/knowledge.js:182:    article.className = "knowledge-assertion";
harness/dashboard/static/knowledge.js:183:    article.id = `knowledge-evidence-${assertion.assertion_id}`;
harness/dashboard/static/knowledge.js:194:      const result = await postJson("/api/knowledge/explain", {
harness/dashboard/static/knowledge.js:210:        source.className = "knowledge-source-text";
harness/dashboard/static/knowledge.js:217:            const response = await fetch(`/api/knowledge/evidence?${params}`, { cache: "no-store" });
harness/dashboard/static/knowledge.js:233:  graph.querySelectorAll("[data-knowledge-node]").forEach(node => {
harness/dashboard/static/knowledge.js:235:      const label = node.dataset.knowledgeNode;
harness/dashboard/static/knowledge.js:238:      const article = related.length && document.getElementById(`knowledge-evidence-${related[0].assertion_id}`);
harness/dashboard/static/knowledge.js:256:  section.className = "knowledge-graph";
harness/dashboard/static/knowledge.js:257:  section.setAttribute("aria-label", "Selected knowledge relationship graph");
harness/dashboard/static/knowledge.js:259:  section.append(kEl("p", "Sourced knowledge observations only; this is separate from approved architecture and AL execution diagrams."));
harness/dashboard/static/knowledge.js:279:  svg.classList.add("knowledge-graph-svg");
harness/dashboard/static/knowledge.js:286:  title.textContent = "Bounded sourced knowledge graph";
harness/dashboard/static/knowledge.js:290:  marker.setAttribute("id", `knowledge-arrow-${++knowledgeGraphSequence}`);
harness/dashboard/static/knowledge.js:298:  arrowHead.setAttribute("class", "knowledge-arrowhead");
harness/dashboard/static/knowledge.js:314:    line.setAttribute("class", "knowledge-arrow");
harness/dashboard/static/knowledge.js:322:    group.classList.add("knowledge-node");
harness/dashboard/static/knowledge.js:326:    group.dataset.knowledgeNode = label;
harness/dashboard/static/knowledge.js:349:    state.classList.add("knowledge-node-state");
harness/dashboard/static/knowledge.js:355:  scroll.className = "knowledge-graph-scroll";
```

## 5. SHA-256 digests

argv:

```text
shasum -a 256 harness/knowledge/__init__.py harness/knowledge/models.py harness/knowledge/policy.py harness/knowledge/query.py harness/knowledge/refresh.py harness/knowledge/sources.py harness/knowledge/store.py harness/knowledge/extract.py harness/knowledge/integration.py harness/migrations/0015_knowledge.sql harness/execution_operations.py harness/execution_bootstrap.py harness/execution_mcp.py harness/execution_authority.py harness/native_tool_policy.py harness/cli.py harness/cli_knowledge.py harness/cli_commands.py harness/db.py harness/dashboard/knowledge_view.py harness/dashboard/static/knowledge.js al/claude/skills/al/scripts/al_review.py al/codex/skills/al/scripts/al_review.py
```

Raw output (exit 0):

```text
433f073bfacacc80fc2af7bf23df379b50e5167120f5118af11fbcbf83af51c2  harness/knowledge/__init__.py
98dbf82f98e9b7173a53f28a453655c951950c98567398f92da7bbd537ae5179  harness/knowledge/models.py
74e3f7ec5a13afdf000e59cf7738b3b81464fdf48fb7c79c583770adc04fd273  harness/knowledge/policy.py
78dc37f23ae0b3fd0b29c3755d4f304182e6c485b6a0e70c4c78d078258bfdcb  harness/knowledge/query.py
bc05498c1fc66cafc5d0a6acf05262e40230d1645f3a4e0bdf4af882319b5a41  harness/knowledge/refresh.py
04c7e3b7b2b9fd7e7348648e0d3818c91f6250ea70379c477dc7de352b96eaa1  harness/knowledge/sources.py
52ef595ec84e857d3ec10aa80c563f5a0c64ec4da421c72ecf5b2b429d306249  harness/knowledge/store.py
dd9fea260137ccd9d2ed24e926fcaedd6e4efab226eb73dc7587f54ca753077e  harness/knowledge/extract.py
0ed408c8bf24498099d3ca3195a3143b54048cee626e6d6f6c326929154b800b  harness/knowledge/integration.py
17e4e5d087ca54350f902362dbb889725d021d1a587bed16133507c165cefa82  harness/migrations/0015_knowledge.sql
51cd0195877d63ae1b07fed69a594a00cd88f01a54e2aa3c89c428aa9fd9a456  harness/execution_operations.py
0f23ff8c3806f6ab57b18a6698f5355b315868e6569467c180611ae71cec260f  harness/execution_bootstrap.py
ddcd751844eb258a684f634703c473964cc3a9474bd8b05eb605db8fa1b3234c  harness/execution_mcp.py
7a7b318fa881235000a62b8e921e8ab62b0ef06278b3bba3117af6397a099a91  harness/execution_authority.py
1f8404b45e8dfa6643d345594685a961c8718981b47787ac9b8edd43320540a8  harness/native_tool_policy.py
4c1bbdc1a5eaac8bb5c347dbd9164f0da529325c1928f03058d44695f60501c2  harness/cli.py
19db852ec150878a839ae617779bb8e6453262e872f9c062d9e8df09760d4d79  harness/cli_knowledge.py
ea2837df60ea89fa51964d1599bf0994c499a0aa1c89097f19d84c2ea9776cd6  harness/cli_commands.py
09e41bfdddc2f7e9733160dcd71af0c22e5a696d0636661d7469bc153adec224  harness/db.py
4bd4c9ed4420d12e67739afe7b37d00eed5d0d54677bc3b902433bf26863f45e  harness/dashboard/knowledge_view.py
7903225c27a789debd65d1526d3b80fc43c9e274a05a247fcbb9a9f0af7eb5f3  harness/dashboard/static/knowledge.js
3ba43d6745808fe38920313077047c52847854739cbbcd7bcbc89a5b46d2326d  al/claude/skills/al/scripts/al_review.py
3ba43d6745808fe38920313077047c52847854739cbbcd7bcbc89a5b46d2326d  al/codex/skills/al/scripts/al_review.py
```

## 6. Repository state

argv: `git rev-parse HEAD`

```text
2026-11-16 19:49:48.468 xcodebuild[13444:350638808]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-11-16 19:49:48.551 xcodebuild[13444:350638807] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
71db5c321ca997df08c94f877b0768675edb9c0d
```

argv: `git status --porcelain`

```text
2026-11-16 19:49:50.938 xcodebuild[13470:350638988]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-11-16 19:49:51.014 xcodebuild[13470:350638986] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
.claude/commands/harness.md: Operation not permitted
warning: could not open directory '.claude/': Operation not permitted
?? conftest.py
?? docs/decisions/DEC-0034-imported-old32.md
?? docs/decisions/DEC-0035-imported-old34.md
?? docs/decisions/DEC-0036-imported-old36.md
?? docs/decisions/DEC-0037-imported-old38.md
?? docs/decisions/DEC-0038-imported-old39.md
?? docs/decisions/DEC-0039-imported-old41.md
?? docs/decisions/DEC-0040-imported-old43.md
?? docs/decisions/DEC-0041-imported-old45.md
?? docs/decisions/DEC-0042-imported-old48.md
?? docs/decisions/DEC-0043-imported-old49.md
?? docs/decisions/DEC-0044-imported-old52.md
?? docs/decisions/DEC-0045-imported-old53.md
?? docs/decisions/DEC-0046-imported-old56.md
?? docs/decisions/DEC-0047-imported-old58.md
?? docs/specs/t-a38f0351/
?? tests/test_sqlite_capabilities.py
```

The status was taken before `docs/knowledge/consumer-discovery.md` was created, so that file is not listed.

## Consumer table

Facts come straight from the outputs above. Anything marked "Interpretation" is inference, not something the grep showed.

### Knowledge package (producers)

| Location | Role | Kind | Contract (as shown by the grep line) |
|---|---|---|---|
| harness/knowledge/models.py:122 | producer | code | `class ContextRequest` |
| harness/knowledge/models.py:130 | producer | code | `class ContextBundle` |
| harness/knowledge/policy.py:10 | producer | code | `operator_scope(repository, *, task_id=None, purpose="operator")` |
| harness/knowledge/policy.py:50 | producer | code | `derive_scope(controller, execution, principal, state, repository)` |
| harness/knowledge/query.py:98 | producer | code | `query(conn, scope, request: ContextRequest = _DEFAULT_CONTEXT_REQUEST) -> ContextBundle` |
| harness/knowledge/query.py:312 | producer | code | `return ContextBundle(bundle_hash, payload)` |
| harness/knowledge/store.py:52 | producer | code | `publish_snapshot(conn, prepared: PreparedSnapshot, *, expected_active: str \| None) -> str` |
| harness/knowledge/integration.py:11 | producer | code | `investigate_with_context(controller, task_id, repo_root, request=None, decisions=None)` |
| harness/knowledge/__init__.py:3-7 | producer | export | re-exports `ContextRequest`, `KnowledgeScope`, `RefreshRequest`, `explain`, `query`, `refresh` via `__all__` |
| harness/execution_mcp.py:24 | producer | message | `"knowledge_query": _schema(` (tool schema entry) |
| harness/execution_mcp.py:31 | producer | message | `"knowledge_explain": _schema(` (tool schema entry) |
| harness/native_tool_policy.py:15-16 | producer | other | `"knowledge_query"`, `"knowledge_explain"` literal entries (tool-name list) |
| harness/cli_knowledge.py:128 | producer | code | `add_knowledge_parser(subparsers)`; registers the `knowledge` subcommand |
| harness/cli_knowledge.py:19, 85, 91, 99, 103, 144 | producer | code | `inspect_knowledge`, `refresh_knowledge`, `query_knowledge`, `explain_knowledge`, `evidence_knowledge`, `cmd_knowledge` |
| harness/dashboard/knowledge_view.py:20, 28, 36 | producer | code | `read_knowledge`, `read_knowledge_evidence`, `knowledge_action` |
| harness/dashboard/server.py:55-57 | producer | message | route map `/api/knowledge/refresh`, `/api/knowledge/query`, `/api/knowledge/explain` to `knowledge-refresh`, `knowledge-query`, `knowledge-explain` |
| harness/dashboard/server.py:81 | producer | link | `/static/knowledge.js` served as `text/javascript; charset=utf-8` |
| harness/dashboard/server.py:216-217, 234, 238 | producer | message | GET routes `/api/knowledge` and `/api/knowledge/evidence` |
| harness/dashboard/server.py:325-328 | producer | message | POST prefix `/api/knowledge/`; action is `action.removeprefix("knowledge-")` |
| harness/dashboard/static/index.html:15 | producer | link | `<script src="/static/knowledge.js" defer>` |
| harness/dashboard/static/knowledge.js:46, 125, 194 | producer | message | POSTs to `/api/knowledge/query` with `{task, query}`, `/api/knowledge/refresh` with `{task}`, `/api/knowledge/explain` |
| harness/dashboard/static/knowledge.js:63, 217 | producer | message | GETs `/api/knowledge?${params}` and `/api/knowledge/evidence?${params}` |

### Consumers of the knowledge package (`harness.knowledge.*` imports and calls)

| Location | Role | Kind | Contract |
|---|---|---|---|
| harness/cli_commands.py:147, 149 | consumer | code | lazy import and call `investigate_with_context(controller, task.id, root, decisions=decisions)` |
| harness/cli_knowledge.py:6-9, 20, 87, 94-95, 100, 105, 107 | consumer | code | imports `MAX_SOURCE_BYTES`, `ContextRequest`, `RefreshRequest`, `digest`, `operator_scope`, `explain`, `query`, `refresh`, `read_source`. Calls `refresh(conn, operator_scope(repository, task_id=...), RefreshRequest())`, `ContextRequest(text, tuple(entity_refs), snapshot_id)` and `explain(conn, scope, bundle_hash, assertion_id)` |
| harness/execution_operations.py:397, 415-416, 437, 447-460 | consumer | code | handles `knowledge_query` and `knowledge_explain`. Lazy-imports `ContextRequest`, `derive_scope`, `explain`, `query`. Builds `derive_scope(controller, execution, principal, state, Path(root))` and `ContextRequest(a.get("query", ""), tuple(a.get("entity_refs", ())))` |
| harness/cli.py:18, 93 | consumer | link | imports and calls `add_knowledge_parser(subparsers)` |
| harness/dashboard/knowledge_view.py:7-12, 55-59 | consumer | code | imports `evidence_knowledge`, `explain_knowledge`, `inspect_knowledge`, `query_knowledge`, `refresh_knowledge` from `harness.cli_knowledge` (not from `harness.knowledge` directly) |
| harness/dashboard/server.py:235, 239, 326 | consumer | code | lazy imports `read_knowledge`, `read_knowledge_evidence`, `knowledge_action` |
| harness/dashboard/static/app.js:289-292 | consumer | code | calls `renderKnowledgePanel(task.id)` and appends it as a "Project knowledge" disclosure |
| harness/knowledge/* (intra-package) | consumer | code | extract.py:7, integration.py:5-8, policy.py:5, query.py:8-16, refresh.py:4-14, sources.py:10, store.py:6 import from sibling modules |
| tests/test_execution_snapshot.py:22-29, 32, 39, 42, 48 | consumer | code | imports `extract_source`, models, `operator_scope`, `explain`, `query`, `publish_snapshot`; patches `sys.modules['harness.knowledge.query']` |
| tests/test_knowledge_cli.py:11-13 | consumer | code | imports `RefreshRequest`, `refresh`, `ensure_project` |
| tests/test_knowledge_extraction.py:5-7 | consumer | code | imports `extract_source`, `SourceVersion`, `eligible`, `read_source` |
| tests/test_knowledge_integration.py:5-8, 21, 28 | consumer | code | imports and calls `investigate_with_context`, `query`, `operator_scope`, `ContextRequest` |
| tests/test_knowledge_policy.py:6-8, 19-32 | consumer | code | uses `ContextRequest`, `query`, `explain`, `refresh` |
| tests/test_knowledge_query.py:8-10, 17-68 | consumer | code | uses `ContextRequest`, `QueryLimits`, `canonical`, `digest`, `query`, `explain`, `refresh` |
| tests/test_knowledge_snapshots.py:11-21, 33, 62-73, 153-195 | consumer | code | uses `extract_source`, `operator_scope`, `refresh`, `enumerate_sources`, `SnapshotConflict`, `publish_snapshot`; monkeypatches `harness.knowledge.refresh` attributes `enumerate_sources` and `read_source` |

### AL packet and handoff builders (al/claude and al/codex)

Grep 1 returned no line under `al/claude` or `al/codex`. Grep 2 returned only lines containing the substring "knowledge" inside the word "acknowledged" (for example `delivery.correction.acknowledged`, `decision.acknowledged`). It returned no `bundle_hash` or `context_bundle` match.

| Location (same line numbers in both `al/claude/skills/al/scripts/` and `al/codex/skills/al/scripts/`) | Role | Kind | Contract |
|---|---|---|---|
| al_review.py:68 | producer | code | `cmd_packet(a,sid)` for `review-packet --stage {fresh,compare}`; snapshot kind is `review-packet-<stage>` plus `-v2` when `Behavior.enabled(st)` (line 89); parser registration at line 177 |
| al_review_behavior.py:122 | producer | code | `kind="review-packet-v2"` |
| al_review_behavior.py:33, 57, 63, 196, 215-216 | consumer | code | `validate_review_lineage(st, review, packet=None)` checks `packet.get("kind") != "review-packet-v2"` |
| al_review.py:95-96, 143 | consumer | code | reads `r['packets']`; refuses with `review packet must be captured before report` |
| al_planning.py:179 | producer | code | `cmd_packet(a,sid)` (planning packet; see interpretation below) |
| al_behavior_observations.py:251 | producer | code | `cmd_packet(a, sid)` |
| al_rework_recovery.py:48 | producer | code | `cmd_packet(a,sid)` |
| al_writer_handoff.py:40 | producer | code | `cmd_handoff(a, sid)` |
| al_events.py:9 | consumer | code | `initialise(st, envelope, handoff_path)` |
| al_recovery_guidance.py:44 | other | other | `"review.packet": ("review-packet", None, {"stage", "replace_unavailable_attempt", "output"})` |
| al_recovery_subjects.py:187-189 | consumer | code | checks `stage not in review["packets"]`; emits `"review.packet"` |

### Interpretation (not shown directly by the commands)

- The `al_review.py` files in `al/claude` and `al/codex` have identical SHA-256 (`3ba43d67...326d`). Every grep 3 line differs only by the directory prefix, so the two platform copies appear to be mirrors. Digests of the other AL scripts were not requested, so this is only shown for `al_review.py`.
- The AL packet and handoff builders show no literal reference to the knowledge package, `ContextBundle`, `bundle_hash` or `context_bundle` in these greps. They are therefore not direct importers or call sites of `harness.knowledge`. This does not exclude indirect coupling, for example generic packet contents or handoff text that may carry knowledge data. The greps would not reveal that.
- Which `cmd_packet` serves which CLI subcommand, other than `review-packet` in `al_review.py:177`, was not shown. The role labels for `al_planning.py`, `al_behavior_observations.py` and `al_rework_recovery.py` are inferred from the function name only.
- `harness/execution_bootstrap.py`, `harness/execution_authority.py` and `harness/db.py` produced no match in greps 1 and 4. Grep 4 did not cover them, and grep 1 covered them via the `harness` pathspec. `harness/migrations/0015_knowledge.sql` was hashed but not searched. Migration and SQL consumers (for example the `knowledge_*` tables read in `cli_knowledge.py:22-72`) were not enumerated beyond grep 4.
- `harness/execution_operations.py` and `harness/native_tool_policy.py` show the `knowledge_query` and `knowledge_explain` tool names twice each (`execution_operations.py` lines 397, 415-416, 437, 447; `native_tool_policy.py` lines 15-16) alongside the schema in `execution_mcp.py`. Whether any other tool-name allowlist exists elsewhere (for example in `.claude/` or generated policy) is not covered by these commands.
- Untracked files were not searched, as noted at the top.
