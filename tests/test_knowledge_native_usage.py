"""Synthetic own metadata qualification; installed-host availability remains separate."""

import json

import pytest

from scripts.knowledge_evaluation.method import profile_for_role
from scripts.knowledge_evaluation.usage import observe_usage
from tests.test_knowledge_native_eval import metadata_fixture


def usage_fixture(tmp_path, role="al-architect"):
    native, root, child, files = metadata_fixture(tmp_path, role=role)
    path = files / ("rollout-" + child + ".jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    terminal = rows.pop()
    profile = profile_for_role(role)
    rows[2]["payload"].update(model=profile["model"], effort=profile["effort"])
    counts = {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 30,
        "reasoning_output_tokens": 5,
        "total_tokens": 130,
    }
    event = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": counts, "last_token_usage": dict(counts)},
        },
    }
    rows.extend([event, event, terminal])
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return native, root, child, path, rows


def test_bound_usage_deduplicates_cumulative_and_never_copies_prose(tmp_path):
    native, root, child, _, _ = usage_fixture(tmp_path)
    result = observe_usage(
        native, root, child, "al-architect", expected_profile=profile_for_role("al-architect")
    )
    assert result["turn_generation_delta"]["total_tokens"] == 130
    assert len(result["observations"]) == 2
    assert result["observations"][-1]["duplicate"]
    assert result["answer_tokens"] is None and result["answer_token_compliance"] == "UNKNOWN"
    assert "MUST_NOT" not in json.dumps(result)


@pytest.mark.parametrize(
    "fault",
    [
        "negative",
        "bool",
        "reset",
        "baseline",
        "missing-terminal",
        "partial",
        "foreign-turn",
        "multiple",
        "alias",
        "role",
    ],
)
def test_invalid_usage_is_unavailable_not_zero(tmp_path, fault):
    native, root, child, path, rows = usage_fixture(tmp_path)
    if fault in {"negative", "bool", "reset", "baseline"}:
        info = rows[-2]["payload"]["info"]
        if fault == "reset":
            info = json.loads(json.dumps(info))
            rows[-2]["payload"]["info"] = info
        key = "input_tokens"
        info["total_token_usage"][key] = {
            "negative": -1,
            "bool": True,
            "reset": 1,
            "baseline": 101,
        }[fault]
    elif fault == "missing-terminal":
        rows.pop()
    elif fault == "foreign-turn":
        rows[-2]["payload"]["turn_id"] = "other-turn"
    elif fault == "multiple":
        rows.append(rows[1])
    elif fault == "alias":
        path.rename(path.with_suffix(".original"))
        path.symlink_to(path.with_suffix(".original"))
    if fault != "alias":
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
            + ('{"partial":' if fault == "partial" else "")
        )
    with pytest.raises(ValueError):
        observe_usage(
            native,
            root,
            child,
            "al-engineer" if fault == "role" else "al-architect",
            expected_profile=profile_for_role("al-architect"),
        )


@pytest.mark.parametrize("fault", ["foreign-profile", "prose-object"])
def test_review_repro_untrusted_context_is_not_persisted(tmp_path, fault):
    from types import SimpleNamespace

    from scripts.knowledge_evaluation.method import contract
    from scripts.knowledge_evaluation.usage import usage_receipt

    native, root, child, path, rows = usage_fixture(tmp_path)
    context = rows[2]["payload"]
    if fault == "foreign-profile":
        context.update(model="gpt-6.1-sol", effort="high")
    else:
        context.update(model={"prose": "PUBLIC_SENTINEL_DO_NOT_PERSIST"}, effort=None)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    adapter = SimpleNamespace(
        operation=SimpleNamespace(registry=SimpleNamespace(root=root, native_root=native)),
        principals={child: {"role": "al-architect"}},
        package={
            "schema_version": 2,
            "method": contract(),
            "host": "codex",
            "role": "al-architect",
            "model": "gpt-6-astra",
            "effort": "medium",
        },
    )
    result = usage_receipt(adapter)
    assert result["children"][0]["status"] == "unavailable"
    assert "PUBLIC_SENTINEL_DO_NOT_PERSIST" not in json.dumps(result)


@pytest.mark.parametrize("role", ["al-architect", "al-engineer"])
def test_matching_usage_and_native_return_bind_actual_child_profile(tmp_path, role):
    from scripts.knowledge_evaluation.observer import observe_return

    native, root, child, _, _ = usage_fixture(tmp_path, role)
    expected = profile_for_role(role)
    usage = observe_usage(native, root, child, role, expected_profile=expected)
    returned = observe_return(native, root, child, role, expected_profile=expected)
    assert usage["host_context"] == returned["terminal"]["host_context"]
    assert usage["host_context"]["model"] == expected["model"]
    assert usage["turn_generation_delta"]["output_tokens"] == 30
    assert "MUST_NOT" not in json.dumps([usage, returned])
    with pytest.raises(ValueError, match="exact declared"):
        observe_usage(native, root, child, role)


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", {"prose": "PUBLIC_SENTINEL_DO_NOT_PERSIST"}),
        ("model", ["PUBLIC_SENTINEL_DO_NOT_PERSIST"]),
        ("model", None),
        ("model", True),
        ("model", ""),
        ("model", "x" * 129),
        ("model", "PUBLIC_SENTINEL_DO_NOT_PERSIST"),
        ("model", "gpt-6.1-sol"),
        ("effort", None),
        ("effort", {"prose": "PUBLIC_SENTINEL_DO_NOT_PERSIST"}),
        ("effort", True),
        ("effort", ""),
        ("effort", "medium prose"),
        ("effort", "high"),
        ("turn_id", []),
        ("turn_id", ""),
        ("turn_id", "x" * 129),
        ("turn_id", "PUBLIC_SENTINEL_DO_NOT_PERSIST with prose"),
    ],
)
def test_usage_and_m2_native_return_reject_malformed_or_mismatched_context(tmp_path, field, value):
    from scripts.knowledge_evaluation.observer import observe_return

    native, root, child, path, rows = usage_fixture(tmp_path)
    rows[2]["payload"][field] = value
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    expected = profile_for_role("al-architect")
    with pytest.raises(ValueError) as usage_error:
        observe_usage(native, root, child, "al-architect", expected_profile=expected)
    with pytest.raises(ValueError) as return_error:
        observe_return(native, root, child, "al-architect", expected_profile=expected)
    assert "PUBLIC_SENTINEL" not in str(usage_error.value) + str(return_error.value)


def test_root_context_is_separate_from_declared_child_profile(tmp_path):
    import uuid
    from types import SimpleNamespace

    from scripts.knowledge_evaluation.method import contract
    from scripts.knowledge_evaluation.usage import usage_receipt

    native, root, child, path, rows = usage_fixture(tmp_path)
    root_rows = json.loads(json.dumps(rows))
    root_rows[0]["payload"].update(id=root, session_id=root, source="exec")
    turn = str(uuid.uuid4())
    for row in root_rows:
        payload = row["payload"]
        if "turn_id" in payload:
            payload["turn_id"] = turn
    root_rows[2]["payload"].update(model="gpt-6.1-sol", effort="high")
    root_path = path.with_name("rollout-" + root + ".jsonl")
    root_path.write_text("".join(json.dumps(row) + "\n" for row in root_rows))
    adapter = SimpleNamespace(
        operation=SimpleNamespace(registry=SimpleNamespace(root=root, native_root=native)),
        principals={child: {"role": "al-architect"}},
        package={
            "schema_version": 2,
            "method": contract(),
            "host": "codex",
            "role": "al-architect",
            "model": "gpt-6-astra",
            "effort": "medium",
        },
    )
    receipt = usage_receipt(adapter)
    assert receipt["root"]["status"] == "observed"
    assert receipt["root"]["host_context"]["model"] == "gpt-6.1-sol"
    assert receipt["root"]["host_context"]["effort"] == "high"
    assert receipt["children"][0]["host_context"]["model"] == "gpt-6-astra"
    assert receipt["children"][0]["host_context"]["effort"] == "medium"
    assert "MUST_NOT" not in json.dumps(receipt)
