"""Only the external Architect response is supplied by this protocol fixture."""

import json

from al.tests.planning_reports import digest, report_for, section  # noqa: F401
from al.tests.runtime_run_helpers import public_run


def prepare(
    launched, platform="codex", spec="Establish independently authored observable admission proof."
):
    for args in [
        ("start", "--spec", spec),
        ("spec-gate", "SPEC_READY"),
        ("classify", "SMALL", "--designer", "no", "--reason", "Bounded protocol task."),
    ]:
        result = public_run(launched, platform, *args)
        assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    result = public_run(launched, platform, "planning", "packet")
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    packet = json.loads(result.stdout)
    return packet, report_for(packet)


def save_report(launched, platform, report):
    path = launched[2].parent / "model-response.json"
    path.write_text(json.dumps(report))
    return public_run(launched, platform, "planning", "report", "--file", str(path))
