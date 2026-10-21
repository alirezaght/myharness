"""Trusted isolated source entrypoint; actual global operations require terminal approval."""

import sys

if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    raise SystemExit("Use the trusted explicit Python launcher with -I -S -B.")

from pathlib import Path

# The explicit trusted script path selects this checkout; cwd/PYTHONPATH never select code.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    import argparse
    import json

    from al.installer.registry import digest
    from al.installer.validator_origin import capture, decode
    from al.installer.workspace import apply_workspace, plan_workspace, verify_workspace

    parser = argparse.ArgumentParser(
        description="Prepare and install a terminal-approved workspace globally."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "apply", "verify"):
        p = sub.add_parser(command)
        p.add_argument("--state-root", required=True)
        p.add_argument(
            "--file",
            required=True,
            help="exact request (plan) or registered manifest (apply/verify)",
        )
        p.add_argument("--sha256", required=True)
        if command == "plan":
            p.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    try:
        raw, _ = capture(args.file)
        if digest(raw) != args.sha256:
            raise ValueError("workspace input artifact hash differs")
        value = decode(raw)
        if args.command == "plan":
            result = plan_workspace(args.source, value, args.state_root)
        else:
            function = apply_workspace if args.command == "apply" else verify_workspace
            result = function(value, args.state_root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
