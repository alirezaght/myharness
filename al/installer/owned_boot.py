"""Fixed isolated child bootstrap using only parent-bound lifecycle resources."""

import base64
import json
from pathlib import Path
import sys

from .validator_origin import capture

NAMES = ("activity_process", "owned_artifacts", "owned_child")
BOOT = r"""
import base64,json,sys,types
value=json.loads(sys.argv[1])
if set(value)!={'activity_process','owned_artifacts','owned_child'}:raise ValueError('unknown owned child resource')
pkg=types.ModuleType('_al_owned');pkg.__path__=[];sys.modules[pkg.__name__]=pkg
for name in ('activity_process','owned_artifacts','owned_child'):
 row=value[name]
 if set(row)!={'filename','bytes'}:raise ValueError('invalid owned child resource')
 module=types.ModuleType('_al_owned.'+name);module.__file__=row['filename'];module.__package__='_al_owned'
 sys.modules[module.__name__]=module
 exec(compile(base64.b64decode(row['bytes'],validate=True),row['filename'],'exec'),module.__dict__)
sys.argv=[value['owned_child']['filename'],*sys.argv[2:]]
sys.modules['_al_owned.owned_child'].main()
"""


def command_for(receipt, argv):
    root = Path(__file__).resolve().parent
    context = sys.modules.get("_al_validator_context")
    resources = {}
    for name in NAMES:
        path = root / (name + ".py")
        raw, state = capture(path)
        if context is not None:
            expected = context.captured["bytes"].get("installer/" + name + ".py")
            expected_state = context.captured["origin"]["closure"]["installer/" + name + ".py"]
            if (
                root != Path(context.captured["snapshot"]) / "installer"
                or raw != expected
                or state != {k: expected_state[k] for k in ("sha256", "mode")}
            ):
                raise ValueError("owned bootstrap differs from protected CLI resource")
        resources[name] = {"filename": str(path), "bytes": base64.b64encode(raw).decode()}
    return [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        BOOT,
        json.dumps(resources, sort_keys=True),
        str(receipt),
        "--",
        *argv,
    ]
