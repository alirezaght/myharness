"""Launcher: bind the dashboard, print its URL, open the browser, serve until Ctrl-C/SIGTERM."""

import argparse
import signal
import sys
import webbrowser
from pathlib import Path
from types import FrameType

from harness.cli_commands import resolve_db_path
from harness.dashboard.server import DashboardServer
from harness.dashboard.view import NO_ACTIVE_TASK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness.dashboard",
        description="local web dashboard for a harness task (127.0.0.1 only, free port)",
    )
    parser.add_argument("task_id", nargs="?", help="the active task (omit to list the tasks)")
    parser.add_argument("--db", metavar="PATH", help="state DB (default: $HARNESS_DB or per-repo)")
    parser.add_argument("--specs", metavar="PATH", help="specs root (default: <repo>/docs/specs)")
    parser.add_argument("--no-browser", action="store_true", help="only print the URL")
    return parser


def _open(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except webbrowser.Error:
        return False


def start_dashboard(
    db_path: Path, task_id: str | None, specs: str | None, open_browser: bool
) -> DashboardServer:
    server = DashboardServer(db_path, task_id, specs)
    if task_id and Path(db_path).is_file():
        from harness.dashboard.action_readiness import probe_readiness
        from harness.dashboard.view import open_state
        from harness.execution_store import latest_execution

        with open_state(db_path) as controller:
            execution = latest_execution(controller.conn, task_id)
            if execution:
                readiness = probe_readiness(execution)
                if not readiness["actions"].get("decision") or not readiness["actions"].get(
                    "reconnect"
                ):
                    print(
                        "dashboard actions unavailable: " + readiness["message"],
                        file=sys.stderr,
                        flush=True,
                    )
    subject = f"task {task_id}" if task_id else NO_ACTIVE_TASK
    print(f"dashboard: {server.url} · {subject} · Ctrl-C stops it", flush=True)
    if open_browser and not _open(server.url):
        print(f"could not open a browser; open {server.url} yourself", flush=True)
    return server


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        try:
            server = start_dashboard(
                resolve_db_path(args.db), args.task_id, args.specs, not args.no_browser
            )
        except OSError as error:
            print(f"harness dashboard: {error}", file=sys.stderr)
            return 1
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        signal.signal(signal.SIGTERM, previous)
    print("dashboard stopped", flush=True)
    return 0
