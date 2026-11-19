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


def test_native_usage_refuses_previously_accepted_cumulative_metadata(tmp_path):
    native, root, child, _, _ = usage_fixture(tmp_path)
    with pytest.raises(ValueError, match="^qualified native observer unavailable$"):
        observe_usage(
            native, root, child, "al-architect", expected_profile=profile_for_role("al-architect")
        )


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
def test_declared_profile_cannot_qualify_native_usage_or_return(tmp_path, role):
    from scripts.knowledge_evaluation.observer import observe_return

    native, root, child, _, _ = usage_fixture(tmp_path, role)
    expected = profile_for_role(role)
    for observe in (observe_usage, observe_return):
        with pytest.raises(ValueError, match="^qualified native observer unavailable$"):
            observe(native, root, child, role, expected_profile=expected)


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
    assert receipt["kind"] == "native-generation-usage-v2"
    assert receipt["root"]["status"] == "unavailable"
    assert receipt["children"][0]["status"] == "unavailable"
    assert receipt["root"]["turn_generation_delta"] is None
    assert receipt["children"][0]["turn_generation_delta"] is None
    assert receipt["answer_tokens"] is None
    assert receipt["answer_token_compliance"] == "UNKNOWN"
    assert "MUST_NOT" not in json.dumps(receipt)


def test_observation_never_opens_native_records(monkeypatch):
    from scripts.knowledge_evaluation.observer import observe_return, records

    def forbidden(*args, **kwargs):
        pytest.fail("native host record access is prohibited")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("os.open", forbidden)
    monkeypatch.setattr("pathlib.Path.open", forbidden)
    monkeypatch.setattr("pathlib.Path.glob", forbidden)
    for observe, args in (
        (records, ("not-a-real-native-root", "synthetic-child")),
        (observe_return, ("not-a-real-native-root", "root", "child", "al-architect")),
        (observe_usage, ("not-a-real-native-root", "root", "child", "al-architect")),
    ):
        with pytest.raises(ValueError, match="^qualified native observer unavailable$"):
            observe(*args)


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), {}, [], "SECRET"])
@pytest.mark.parametrize("field", ["input_tokens", "duration_ms", "total_cost_usd"])
def test_public_telemetry_rejects_unsafe_scalars(field, value):
    from scripts.knowledge_evaluation.live import TELEMETRY_UNAVAILABLE, public_telemetry

    event = {"type": "result"}
    if field == "input_tokens":
        event["usage"] = {field: value}
    else:
        event[field] = value
    assert public_telemetry(event) == TELEMETRY_UNAVAILABLE


@pytest.mark.parametrize(
    "event",
    [
        {"type": "assistant", "message": {"usage": {"secret": "PROSE_CANARY"}}},
        {"type": "result", "modelUsage": {"gpt-6-astra": {"inputTokens": {"auth": "AUTH_CANARY"}}}},
        {"type": "result", "modelUsage": {"UNKNOWN_CANARY": {"inputTokens": 1}}},
        {"type": "turn_context", "payload": {"model": "MODEL_CANARY"}},
        {"type": "turn_context", "payload": {"effort": {"reasoning": "REASONING_CANARY"}}},
        {"type": "result", "session_id": "CONFIG_CANARY"},
        {"type": "assistant", "parent_tool_use_id": {"auth": "AUTH_CANARY"}},
        {"type": "result", "subtype": ["SECRET_CANARY"]},
        ["SECRET_CANARY"],
    ],
)
def test_public_telemetry_nested_canaries_have_fixed_diagnostics(event):
    from scripts.knowledge_evaluation.live import TELEMETRY_UNAVAILABLE, public_telemetry
    from scripts.knowledge_evaluation.package import canonical, sha

    result = public_telemetry(event)
    assert result == TELEMETRY_UNAVAILABLE
    assert "CANARY" not in json.dumps(result)
    assert sha(canonical(event).encode()) not in json.dumps(result)


def test_public_telemetry_projects_only_supported_scalars():
    from scripts.knowledge_evaluation.live import public_telemetry

    result = public_telemetry(
        {
            "type": "result",
            "subtype": "success",
            "duration_ms": 12.5,
            "duration_api_ms": 10,
            "total_cost_usd": 0.01,
            "session_id": "12345678-1234-1234-1234-123456789abc",
            "usage": {"input_tokens": 100, "output_tokens": 30},
            "modelUsage": {"gpt-6-astra": {"inputTokens": 100, "costUSD": 0.01}},
            "result": "RAW_PROSE_CANARY",
            "authentication": "AUTH_CANARY",
            "reasoning": {"private": "REASONING_CANARY"},
        }
    )
    assert result["status"] == "reported"
    assert result["usage"]["output_tokens"] == 30
    assert result["modelUsage"]["gpt-6-astra"]["inputTokens"] == 100
    assert "CANARY" not in json.dumps(result)
    context = public_telemetry(
        {
            "type": "turn_context",
            "payload": {
                "model": "gpt-6-astra",
                "effort": "medium",
                "reasoning": "SECRET_CANARY",
            },
        }
    )
    assert context["model"] == "gpt-6-astra" and context["effort"] == "medium"
    assert "CANARY" not in json.dumps(context)
