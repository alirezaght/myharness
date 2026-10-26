"""Explicit delivery effects are CLI operations; dashboard reads never dispatch."""

import json
from pathlib import Path

from harness.cli_commands import _controller
from harness.delivery import publish_delivery, reconcile_delivery, watch_delivery
from harness.delivery_monitor import poll_delivery
from harness.delivery_store import configure_delivery, delivery_view
from harness.execution_store import get_execution


def cmd_delivery(args):
    with _controller(args) as controller:
        if get_execution(controller.conn, args.execution_id)["task_id"] != args.task_id:
            raise ValueError("execution belongs to another task")
        if args.action == "configure":
            value = configure_delivery(
                controller, args.execution_id, json.loads(Path(args.file).read_bytes())
            )
        elif args.action == "status":
            value = delivery_view(controller.conn, args.execution_id)
        else:
            operations = {
                "publish": publish_delivery,
                "reconcile": reconcile_delivery,
                "poll": poll_delivery,
                "watch": watch_delivery,
            }
            value = operations[args.action](controller, args.execution_id)
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


def add_delivery_parser(subparsers):
    actions = subparsers.add_parser("delivery").add_subparsers(dest="action", required=True)
    for name in ("configure", "status", "publish", "poll", "watch", "reconcile"):
        parser = actions.add_parser(name)
        parser.add_argument("task_id")
        parser.add_argument("execution_id")
        if name == "configure":
            parser.add_argument("--file", required=True)
        parser.set_defaults(handler=cmd_delivery)
