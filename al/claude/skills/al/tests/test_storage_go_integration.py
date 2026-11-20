"""Merged storage receipts retain failure truth and typed coordinator semantics."""
import copy

import pytest

import al_evidence_recipe as Recipes
import al_evidence_storage as Storage
import al_ledger as L


def entry(tmp_path, *, status="PASS", recipe=False):
    log = tmp_path / "receipt.log"
    log.write_text("recipe coordinator progress\n" if recipe else "$ check\n# cwd: .\n# started: fixture\n\n1 passed in 1s\n\n# termination: {}\n")
    value = {"status": status, "kind": "test", "argv": [] if recipe else ["check"],
             "cwd": ".", "exit": None if recipe else 0, "counts": {"passed": 1},
             "log": str(log), "log_sha256": L.file_sha(log), "scope": ["svc"],
             "fp_before": "same", "fp_after": "same", "note": "original result"}
    if recipe:
        value["recipe"] = {"fixture": "validation delegated to actual recipe validator"}
    return value


@pytest.mark.parametrize("status", ["FAIL", "INFRA", "TORN"])
def test_missing_managed_output_cannot_replace_the_original_failed_result(tmp_path, monkeypatch, status):
    value = entry(tmp_path, status=status)
    value["storage"] = {"fixture": "output unavailable"}
    before = copy.deepcopy(value)
    def missing(_entry):
        raise ValueError("output unavailable")
    monkeypatch.setattr(Storage, "validate_outputs", missing)
    assert L.assess(value) == (status, "original result")
    assert value == before


def test_validated_typed_recipe_does_not_run_process_output_reclassification(tmp_path, monkeypatch):
    value = entry(tmp_path, recipe=True)
    observed = []
    monkeypatch.setattr(Recipes, "assess_recipe", lambda item: observed.append(item) or {})
    def no_process_output(*_args, **_kwargs):
        raise AssertionError("typed coordinator has no subprocess output")
    monkeypatch.setattr(L.RunnerOutput, "captured_body", no_process_output)
    monkeypatch.setattr(L, "scope_fingerprint", lambda *_args, **_kwargs: "same")
    assert L.assess(value) == ("VALID", "")
    assert observed == [value]


@pytest.mark.parametrize("field,value", [("argv", ["go", "test", "./..."]), ("exit", 0), ("pid", 12345), ("process", {"pid": 12345})])
def test_process_receipt_cannot_claim_the_typed_recipe_coordinator_path(tmp_path, monkeypatch, field, value):
    receipt = entry(tmp_path, recipe=True)
    receipt[field] = value
    monkeypatch.setattr(Recipes, "assess_recipe", lambda _item: {})
    monkeypatch.setattr(L, "scope_fingerprint", lambda *_args, **_kwargs: "same")
    assert L.assess(receipt)[0] == "RECIPE_UNVERIFIED"


def test_recipe_validator_failure_still_refuses_coordinator(tmp_path, monkeypatch):
    receipt = entry(tmp_path, recipe=True)
    def reject(_item):
        raise ValueError("aggregate not authenticated")
    monkeypatch.setattr(Recipes, "assess_recipe", reject)
    assert L.assess(receipt)[0] == "RECIPE_UNVERIFIED"


def test_validated_recipe_still_requires_current_source(tmp_path, monkeypatch):
    receipt = entry(tmp_path, recipe=True)
    monkeypatch.setattr(Recipes, "assess_recipe", lambda _item: {})
    monkeypatch.setattr(L, "scope_fingerprint", lambda *_args, **_kwargs: "changed")
    assert L.assess(receipt)[0] == "STALE"
