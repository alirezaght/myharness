"""Only explicitly user-reported escape classes seed later reviews."""
import json
from pathlib import Path

SEEDS = Path(__file__).resolve().parents[1] / "reference" / "escape-seeds"


def test_seed_records_are_limited_to_user_described_escape_classes():
    rows = [json.loads(path.read_text()) for path in SEEDS.glob("*.json")]
    assert {row["id"] for row in rows} == {
        "user-rollout-safety-omission", "user-earlier-mr-corrections-omitted"}
    assert all(row["provenance"] == "user-reported" for row in rows)
    assert all("User-reported improvement request" in row["source"] for row in rows)
    assert all(row["regression_evidence"] == [] for row in rows)


def test_recording_a_user_seed_persists_it_for_the_current_run(cli, state_file):
    cli("start", "--spec", "Investigate the user-reported rollout review escape class.")
    seed = SEEDS / "user-rollout-safety-omission.json"
    assert cli("escape", "record", "--file", str(seed))[0] == 0
    state = json.loads(state_file().read_text())
    assert [item["id"] for item in state["escapes"]] == ["user-rollout-safety-omission"]
    assert state["escapes"][0]["provenance"] == "user-reported"


def test_unverified_incident_cannot_be_seeded_as_reproduced(cli, state_file, tmp_path):
    cli("start", "--spec", "Reject unsupported escape claims without regression evidence.")
    record = tmp_path / "unverified.json"
    record.write_text(json.dumps({"id": "unverified-case", "observed_defect": "Invented incident.",
                                  "source": "no user source", "areas": ["review"],
                                  "provenance": "reproduced", "regression_evidence": []}))
    before = state_file().read_bytes()
    code, _, _ = cli("escape", "record", "--file", str(record))
    assert isinstance(code, str) and "reproduced escape needs recorded evidence" in code
    assert state_file().read_bytes() == before
