"""Foreground cooperative bridge CLI. No native continuation, model or daemon invocation."""

import json
import os
import sqlite3
from importlib.resources import files
from pathlib import Path

from harness.cli_commands import resolve_db_path
from harness.controller import HarnessController
from harness.db import connect, repo_root
from harness.planning_session import PlanningSessionService
from harness.planning_session_contract import SessionError
from harness.planning_session_identity import (
    prepare_capability_artifact,
    read_capability,
    resolve_caller,
)


def add_session_parser(subparsers):
    parser = subparsers.add_parser("session", help="cooperate with this invoking planning agent")
    actions = parser.add_subparsers(dest="session_action", required=True)
    for name in (
        "start",
        "attach",
        "wait",
        "publish",
        "ack",
        "status",
        "detach",
        "prepare-interview",
        "observe-repository",
    ):
        action = actions.add_parser(name)
        action.add_argument("--repo", type=Path, default=None)
        action.add_argument("--task")
        if name == "status":
            action.add_argument(
                "--context",
                action="store_true",
                help="include the bounded canonical saved task input",
            )
        if name != "status":
            action.add_argument(
                "--file", required=True, type=Path, help="bounded JSON task request; no credentials"
            )
            action.add_argument("--provider", required=True, choices=("codex", "claude"))
            action.add_argument("--claude-session-id")
            action.add_argument("--capability", type=Path)
            action.add_argument("--capability-dir", type=Path)
        if name == "attach":
            action.add_argument("--takeover", action="store_true")
        if name == "wait":
            action.add_argument("--timeout", type=float, default=25)
        if name == "detach":
            action.add_argument("--destination", required=True, choices=("manual", "standalone"))
        action.set_defaults(handler=cmd_session)
    project = actions.add_parser(
        "project", help="print the canonical project identity without DB access"
    )
    project.add_argument("--repo", type=Path, default=Path.cwd())
    project.set_defaults(handler=cmd_session)
    skill = subparsers.add_parser("skill", help="read bundled local cooperative instructions")
    show = skill.add_subparsers(dest="skill_action", required=True).add_parser("show")
    show.add_argument("--provider", required=True, choices=("codex", "claude"))
    show.set_defaults(handler=cmd_skill_show)


def cmd_skill_show(args):
    root = files("harness") / "skills" / "harness"
    shared, entry = root / "SKILL.md", root / (args.provider + ".md")
    if not shared.is_file() or not entry.is_file():
        raise ValueError("Bundled cooperative skill resources are unavailable in this package.")
    protocol = root / "protocol.md"
    if not protocol.is_file():
        raise ValueError("Bundled cooperative protocol reference is unavailable in this package.")
    print(shared.read_text() + "\n" + protocol.read_text() + "\n" + entry.read_text())
    return 0


def cmd_session(args):
    if args.session_action == "project":
        from harness.authoring import project_identity

        root = args.repo.resolve()
        print(json.dumps({"project": project_identity(root), "repository": str(root)}))
        return 0
    path = resolve_db_path(args.db)
    root = args.repo or repo_root()
    if args.session_action == "status":
        if not args.task or not path.is_file():
            raise ValueError("session status requires an existing task/database")
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, autocommit=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            service = PlanningSessionService(HarnessController(conn), root, args.task)
            result = {"task": args.task, "base": service.base(), "session": service.status()}
            if args.context:
                from harness.authoring import AuthoringService
                from harness.authoring_contract import validate_context
                from harness.authoring_jobs import immutable_input

                authoring = AuthoringService(service.controller, root, args.task)
                if not authoring.store.binding():
                    raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
                context = immutable_input(authoring)
                validate_context(context)
                result["context"] = context
        finally:
            conn.close()
    else:
        from harness.authoring_contract import decode

        if args.file.stat().st_size > 1048576:
            raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
        request = decode(args.file.read_bytes(), limit=1048576)
        caller = resolve_caller(args.provider, os.environ, args.claude_session_id)
        if args.task and args.task != request.get("task"):
            raise SessionError("SESSION_OWNER_LOST")
        if args.session_action in {"start", "attach"}:
            if args.capability:
                capability = read_capability(args.capability, caller)
            else:
                directory = (
                    args.capability_dir
                    or Path(
                        os.environ.get("HARNESS_HOME", str(Path.home() / ".local/share/harness"))
                    )
                    / "session-capabilities"
                )
                capability = prepare_capability_artifact(
                    {"project": request["project"], "request_id": request["request_id"]},
                    caller,
                    directory,
                )
        elif args.capability:
            capability = read_capability(args.capability, caller)
        else:
            raise SessionError("SESSION_CAPABILITY_INVALID")
        conn = connect(path)
        try:
            service = PlanningSessionService(HarnessController(conn), root, request.get("task"))
            kwargs = {}
            if args.session_action == "attach":
                kwargs["takeover"] = args.takeover
            elif args.session_action == "wait":
                kwargs["timeout_seconds"] = args.timeout
            elif args.session_action == "detach":
                kwargs["destination"] = args.destination
            method = (
                "ack_received"
                if args.session_action == "ack"
                else args.session_action.replace("-", "_")
            )
            result = getattr(service, method)(request, caller, capability, **kwargs)
        finally:
            conn.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
