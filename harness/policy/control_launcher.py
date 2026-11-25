"""Reconstruct the one audited managed launcher without executing snapshot modules."""

import ast
import hashlib
import shlex

from harness.policy.contracts import require


def _literals(snapshot, name, expected):
    raw = (snapshot / name).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected[name], "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    return {
        target.id: ast.literal_eval(node.value)
        for node in ast.parse(raw).body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def expected_launcher(request, snapshot, expected):
    boot = _literals(snapshot, "installer/validator_boot.py", expected)["BOOT"]
    client = _literals(snapshot, "installer/runtime_service_client.py", expected)
    require(
        request.get("mode") == "snapshot"
        and isinstance(boot, str)
        and client["PROTOCOL"] == 1
        and isinstance(client["CLIENT"], str),
        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
    )
    service_root = str(snapshot.parents[3] / "al-services")
    prelude = (
        "PROTOCOL=1\n"
        + client["CLIENT"]
        + "\n"
        + (
            "if len(sys.argv)>2 and sys.argv[1]=='run' "
            "and 'HARNESS_RETRY_CONTROL_FD' not in os.environ:\n"
        )
        + "    _al_status=_al_service_run("
        + repr(service_root)
        + ","
        + repr(request["expected"]["snapshot_identity"])
        + ",sys.argv[2:],os.environ)\n"
        + "    if _al_status is not None:os._exit(_al_status)\n"
    )
    return (
        "#!/bin/sh\n"
        + "'''exec' "
        + shlex.quote(request["interpreter"])
        + ' -I -S -B "$0" "$@"\n'
        + "' '''\n"
        + boot
        + "\n"
        + prelude
        + "captured=_boot("
        + repr(request)
        + ")\n"
        + "utility=_utility(captured,'validator_loader.py')\n"
        + "utility.install_closed_loader(captured,'codex',installers=True)\n"
        + "filename=str(Path(captured['snapshot'])/'alctl.py')\n"
        + (
            "exec(compile(captured['bytes']['alctl.py'],filename,'exec'),"
            "{'__name__':'__main__','__file__':filename})\n"
        )
    ).encode()
