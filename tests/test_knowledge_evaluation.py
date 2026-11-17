import json

from scripts.knowledge_eval import main


def test_cli_writes_reproducible_accounting_report_without_acceptance_claim(tmp_path):
    events = tmp_path / "public-events.jsonl"
    rows = []
    for arm, text, service in (("B", "baseline context", 40), ("C", "candidate", 30)):
        rows.append(
            {
                "pair_id": "public-pair-1",
                "arm": arm,
                "phase": "warm-query",
                "participant": "single",
                "visible_text": text,
                "service_read_bytes": service,
                "query_id": "q1",
                "query_number": 1,
                "integrity_violations": 0,
            }
        )
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    output = tmp_path / "report.json"

    assert (
        main(
            [
                str(events),
                "--adapter-version",
                "fixture-adapter-v1",
                "--adapter-sha256",
                "a" * 64,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["adapter"] == {"version": "fixture-adapter-v1", "sha256": "a" * 64}
    assert report["arms"]["B"]["service_read_bytes"] == 40
    assert report["arms"]["C"]["agent_visible_bytes"] == len(b"candidate")
    assert report["median_agent_visible_reduction_fraction"] > 0.2
    assert (
        report["acceptance"]
        == "not_assessed_without_independent_quality_labels_and_native_workflow_evidence"
    )
