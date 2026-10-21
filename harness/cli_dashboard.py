"""Installed dashboard entry point delegates to the existing launcher."""

import argparse


def add_dashboard_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("dashboard", help="serve the local task dashboard")
    parser.add_argument("task_id", nargs="?", help="the active task (omit to list tasks)")
    parser.add_argument("--db", dest="dashboard_db", metavar="PATH", help="state database")
    parser.add_argument("--specs", metavar="PATH", help="specification root")
    parser.add_argument("--no-browser", action="store_true", help="only print the URL")
    parser.set_defaults(handler=cmd_dashboard)


def cmd_dashboard(args: argparse.Namespace) -> int:
    from harness.dashboard.launch import main

    argv = [args.task_id] if args.task_id else []
    if path := args.dashboard_db or args.db:
        argv.extend(["--db", path])
    if args.specs:
        argv.extend(["--specs", args.specs])
    if args.no_browser:
        argv.append("--no-browser")
    return main(argv)
