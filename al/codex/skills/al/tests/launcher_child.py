"""Child-process entry for overlap tests: admit one al_state command like the launcher child."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher_context import Launcher  # noqa: E402
import al_state  # noqa: E402

request = json.loads(sys.argv[1])
session = request["session"]
try:
    if request.get("implicit"):
        from installer.runtime_run import native_session
        session = native_session(request["repository"], request["platform"], sys.argv[2:])
        sys.argv = ["al_state.py", *sys.argv[2:]]
    else:
        sys.argv = ["al_state.py", "--session", session, *sys.argv[2:]]
    Launcher(Path(request["storage"]), request["pin"], request["platform"]).run(
        request["repository"], session, request["start"], al_state.main)
except ValueError as exc:
    sys.exit("al_run: " + str(exc))
