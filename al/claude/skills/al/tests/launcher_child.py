"""Child-process entry for overlap tests: admit one al_state command like the launcher child."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher_context import Launcher  # noqa: E402
import al_state  # noqa: E402

request = json.loads(sys.argv[1])
sys.argv = ["al_state.py", "--session", request["session"], *sys.argv[2:]]
try:
    Launcher(Path(request["storage"]), request["pin"], request["platform"]).run(
        request["repository"], request["session"], request["start"], al_state.main)
except ValueError as exc:
    sys.exit("al_run: " + str(exc))
