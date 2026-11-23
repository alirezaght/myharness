"""Storage operations do not open a project database or adopt legacy files."""

import json
from pathlib import Path

from harness.storage import load_core, manager_for_operator


def add_storage_parser(subparsers):
    parser = subparsers.add_parser(
        "storage",
        help="inspect and reclaim managed disposable storage",
        description=(
            "expire removes scratch unchanged for HARNESS_STORAGE_RETENTION_SECONDS "
            "(default 3 days); it also runs automatically before each new allocation."
        ),
    )
    actions = parser.add_subparsers(dest="storage_action", required=True)
    for name in ("inspect", "preview", "apply", "expire"):
        action = actions.add_parser(name)
        action.add_argument(
            "--root", help="existing managed-storage namespace (default: Harness home)"
        )
        action.add_argument("--max-entries", type=int, default=10000)
        action.add_argument("--max-records", type=int, default=1000)
        action.add_argument("--depth", type=int, default=64)
        action.add_argument("--seconds", type=float, default=1.0)
        action.add_argument(
            "--json", action="store_true", help="print the complete structured result"
        )
        if name == "inspect":
            action.add_argument(
                "--include-legacy",
                action="store_true",
                help="bounded metadata scan; never deletion authority",
            )
        elif name == "preview":
            action.add_argument("--output", help="save an immutable cleanup plan for apply")
        elif name == "apply":
            action.add_argument("--plan", required=True, help="exact plan saved by storage preview")
        action.set_defaults(handler=cmd_storage)


def cmd_storage(args):
    core = load_core()
    bound = {
        "entries": args.max_entries,
        "records": args.max_records,
        "depth": args.depth,
        "seconds": args.seconds,
    }
    core["storage_filesystem"].limits(bound)
    manager = manager_for_operator(root=args.root, create=False)
    if args.storage_action == "inspect":
        result = core["storage_inventory"].inspect(
            manager, limits=bound, include_legacy=args.include_legacy
        )
    elif args.storage_action == "preview":
        result = manager.preview(limits=bound)
        if args.output:
            reference = core["storage_filesystem"].publish_json(
                Path(args.output).absolute(), result
            )
            if not args.json:
                print("Cleanup plan saved: " + reference["reference"])
    elif args.storage_action == "expire":
        result = manager.expire(limits=bound)
    else:
        plan = core["storage_filesystem"].read_json(Path(args.plan).absolute())
        result = manager.apply(plan, limits=bound)
    if args.json or args.storage_action == "preview" and not args.output:
        print(json.dumps(result, sort_keys=True))
    elif args.storage_action == "inspect":
        print(
            f"Managed allocations: {len(result['allocations'])}; "
            f"observation {'complete' if result['complete'] else 'partial'}"
        )
        print(
            f"Observed logical bytes: {result['logical_bytes']}; "
            f"allocated estimate: {result['allocated_bytes_estimate']}"
        )
        pressure = result["pressure"]
        print(
            f"Filesystem free bytes: {pressure['free_bytes']}; "
            f"reserve met: {pressure['reserve_met']}"
        )
        print(
            f"Soft warning threshold: {pressure['warning_bytes']}; warning: {pressure['warning']}"
        )
        for reason, count in sorted(result["retained_by_reason"].items()):
            print(f"Retained: {count} · {reason}")
        for row in result["legacy"]:
            print("Unregistered, retained: " + row["name"])
        print("Only registered, settled and successfully published scratch is reclaimable.")
    elif args.storage_action in {"apply", "expire"}:
        for row in result["results"]:
            print(f"{row['allocation_id']}: {row['state']} · {row.get('reason', '')}")
        if not result.get("complete", True):
            print(
                "Cleanup is partial; run expire again to continue."
                if args.storage_action == "expire"
                else "Cleanup is partial; refresh preview and apply to continue bounded work."
            )
    return 0
