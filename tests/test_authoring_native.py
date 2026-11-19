import os
from pathlib import Path

import pytest

from harness.authoring_native import NativeAuthor, validate_output
from harness.authoring_policy import build_profile
from tests.test_authoring_jobs import output


def inputs():
    return {
        "valid_refs": ["request"],
        "graph": None,
        "body": "## Draft\n",
        "request": "A scheduler",
    }


def test_output_rejects_fabricated_authority_and_graph_omission():
    valid = output()
    assert validate_output(valid, inputs()) == valid
    with pytest.raises(ValueError):
        validate_output(valid | {"graph": None}, inputs())
    valid["graph"]["concrete"]["nodes"][0]["status"] = "accepted"
    with pytest.raises(ValueError, match="cannot grant"):
        validate_output(valid, inputs())
    with pytest.raises(ValueError):
        validate_output('{"assistant":"a","assistant":"b"}', inputs())


def test_authoring_profile_has_no_mcp_agents_or_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "harness.authoring_policy.authoring_executable_identity",
        lambda binary, host: {
            "path": str(tmp_path / "fake"),
            "version": "0.165.0",
            "sha256": "fixture",
        },
    )
    monkeypatch.setattr(
        "harness.authoring_policy.subprocess.run",
        lambda *a, **kw: __import__("subprocess").CompletedProcess(
            [], 0, b"--ignore-user-config --ignore-rules --strict-config --output-last-message", b""
        ),
    )
    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    profile = build_profile("codex", tmp_path, executable="fixture")
    options = " ".join(profile["options"])
    assert "mcp_servers={}" in options and "agents.enabled=false" in options
    assert profile["allowed_tools"] == []
    for feature in (
        "goals",
        "sleep_tool",
        "workspace_dependencies",
        "in_app_chat",
        "in_app_local_automation",
        "default_mode_request_user_input",
    ):
        assert "features." + feature + "=false" in profile["options"]
    with pytest.raises(ValueError, match="Claude"):
        build_profile("claude", tmp_path, executable="fixture")


@pytest.mark.native
def test_real_supported_native_authoring_requires_explicit_executable_and_auth_opt_in(
    controller, tmp_path
):
    path = os.environ.get("HARNESS_AUTHORING_NATIVE_EXECUTABLE")
    if os.environ.get("HARNESS_AUTHORING_NATIVE_SMOKE") != "1" or not path:
        pytest.skip(
            "Set HARNESS_AUTHORING_NATIVE_SMOKE=1 and HARNESS_AUTHORING_NATIVE_EXECUTABLE to "
            "the real supported binary; fixture PATH is not native proof"
        )
    binary = Path(path).resolve(strict=True)
    if not binary.is_absolute() or "pytest" in str(binary):
        pytest.fail("actual installed native executable required")
    diagnostics = []
    reminder = {
        "request": (
            "Generate the complete initial proposed specification and concrete logical "
            "system graph "
            "now for this fully specified local reminder application. The user creates reminders "
            "with a nonempty title and due UTC timestamp. One reminder service validates input, "
            "stores reminders in SQLite, and lists due reminders. One local user, one service, "
            "one SQLite database; no network or external calls. Acceptance: reject empty titles; "
            "saved reminders survive restart; list only reminders whose due time has passed. "
            "No unanswered design choices: propose this exact small design without follow-ups."
        ),
        "body": (
            "## Local reminder application\n"
            "Actor: one local user. Components: reminder service and SQLite database.\n"
            "User submits title and due UTC timestamp to service; service validates and persists.\n"
            "Service queries SQLite and returns due reminders to the user.\n"
            "Reject empty title; persistence survives restart; only due reminders are listed.\n"
            "No external calls. Produce the complete proposed specification and concrete graph.\n"
        ),
    }
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import immutable_input
    from harness.db import transaction

    task = controller.create_task(reminder["request"], "trivial")
    service = AuthoringService(controller, tmp_path, task.id)
    service.save_spec(
        expected=service.store.base(), request_id="native-smoke-save-1234", body=reminder["body"]
    )
    with transaction(controller.conn):
        native_inputs = immutable_input(service)
    assert native_inputs["request"] == reminder["request"]
    assert native_inputs["body"] == reminder["body"]
    assert "revision_targets" in native_inputs and "source" in native_inputs
    profile_directory = tmp_path / "native-profile"
    profile_directory.mkdir()
    profile = build_profile("codex", profile_directory, executable=str(binary))
    native_inputs["native_profile"] = {"available": True, "profile_identity": profile["identity"]}
    author = NativeAuthor("codex", executable=str(binary), diagnostic=diagnostics.append)
    try:
        result = author.run(native_inputs)
    except ValueError as error:
        from harness.authoring_contract import canonical

        raise ValueError(str(error) + "; bounded_diagnostics=" + canonical(diagnostics)) from None
    assert result["assistant"] and result["body"] and result["graph"]
    assert "concrete" in result["graph"]
    import hashlib
    import json

    (tmp_path / "native-authoring-result.json").write_text(json.dumps(result, sort_keys=True))
    (tmp_path / "native-authoring-proof.json").write_text(
        json.dumps(
            {
                "version": profile["executable"]["version"],
                "executable_sha256": profile["executable"]["sha256"],
                "profile_identity": profile["identity"],
                "catalog_hash": profile["catalog_hash"],
                "body_sha256": hashlib.sha256(result["body"].encode()).hexdigest(),
                "concrete_graph_nodes": len(result["graph"]["concrete"]["nodes"]),
                "diagnostics": author.diagnostics,
            },
            sort_keys=True,
        )
    )


def test_fixture_executable_crosses_no_tool_adapter_boundary(tmp_path, monkeypatch):
    import json

    script = tmp_path / "native-fixture"
    payload = json.dumps(output())
    script.write_text(
        """#!/usr/bin/env python3
import json,sys
from pathlib import Path
args=sys.argv[1:]
if '--version' in args:
 print('codex-cli 0.165.0');raise SystemExit()
if '--help' in args:
 print('--ignore-user-config --ignore-rules --strict-config --output-last-message')
 raise SystemExit()
assert '--ignore-user-config' in args and '--ignore-rules' in args and '--strict-config' in args
assert 'mcp_servers={}' in args and 'agents.enabled=false' in args
assert not any('http_headers' in a or 'Bearer' in a for a in args)
context=sys.stdin.read()
assert 'NO tools' in context
Path(args[args.index('--output-last-message')+1]).write_text("""
        + repr(payload)
        + """)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'fixture'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}}))
"""
    )
    script.chmod(0o700)
    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    result = NativeAuthor("codex", executable=str(script)).run(inputs())
    assert result["body"] == output()["body"]


@pytest.mark.parametrize(
    "mode,fragment",
    [
        ("tool", "unauthorized tool"),
        ("malformed", "Expecting"),
        ("oversized", "output limit"),
        ("failure", "invocation failed"),
        ("timeout", "timeout"),
        ("cancel", "cancelled"),
    ],
)
def test_owned_fixture_failures_are_bounded_and_sanitized(tmp_path, monkeypatch, mode, fragment):
    import json

    script = tmp_path / "fixture-provider"
    script.write_text(
        """#!/usr/bin/env python3
import json,sys,time,os
from pathlib import Path
args=sys.argv[1:]
if '--version' in args:
 print('codex-cli 0.165.0');raise SystemExit()
if '--help' in args:
 print('--ignore-user-config --ignore-rules --strict-config --output-last-message')
 raise SystemExit()
assert os.environ['AUTHORING_FIXTURE_AUTH_LOCATION']=='native-location-unchanged'
mode="""
        + repr(mode)
        + """
if mode in ('timeout','cancel'): time.sleep(30)
if mode=='failure':
 print('PRIVATE_PROVIDER_TOKEN=do-not-expose',file=sys.stderr);raise SystemExit(1)
output=Path(args[args.index('--output-last-message')+1])
output.write_text('not json' if mode=='malformed' else 'x'*262145 if mode=='oversized' else """
        + repr(json.dumps(output()))
        + """)
if mode=='tool':
 print(json.dumps({'type':'item.completed',
  'item':{'type':'command_execution','command':'forbidden'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}}))
"""
    )
    script.chmod(0o700)
    monkeypatch.setenv("AUTHORING_FIXTURE_AUTH_LOCATION", "native-location-unchanged")
    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    with pytest.raises(ValueError) as caught:
        NativeAuthor("codex", executable=str(script), timeout=0.1 if mode == "timeout" else 2).run(
            inputs(), cancelled=lambda: mode == "cancel"
        )
    assert fragment.lower() in str(caught.value).lower()
    assert "PRIVATE_PROVIDER_TOKEN" not in str(caught.value) and "do-not-expose" not in str(
        caught.value
    )


def test_invalid_nested_enum_and_excessive_json_depth_are_validation_errors():
    candidate = output()
    candidate["graph"]["concrete"]["nodes"][0]["kind"] = []
    with pytest.raises(ValueError, match="shape"):
        validate_output(candidate, inputs())
    with pytest.raises(ValueError):
        validate_output("[" * 2000 + "0" + "]" * 2000, inputs())


def test_native_error_item_is_classified_without_exposing_diagnostics():
    from harness.authoring_native import native_error_category

    assert (
        native_error_category({"message": "model metadata missing; PRIVATE_SECRET=secret"})
        == "model_metadata"
    )
    assert (
        native_error_category({"message": "authentication failed for PRIVATE_SECRET=secret"})
        == "authentication"
    )
    assert (
        native_error_category({"message": "PRIVATE_SECRET=secret"}) == "unclassified_provider_error"
    )


def test_private_native_diagnostic_shape_is_fixed_vocabulary(tmp_path, monkeypatch):
    from harness.authoring_native import native_error_shape

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native"))
    message = (
        f"Failed writing config cache file {tmp_path}/native/config.json: "
        "permission denied (os error 13); PRIVATE_TOKEN=secret-value"
    )
    result = native_error_shape({"message": message}, tmp_path / "temporary")
    assert result["errno"] == ["13"] and result["root_classes"] == ["native_home"]
    assert (
        "writing" in result["fixed_vocabulary_sequence"]
        and "cache" in result["fixed_vocabulary_sequence"]
    )
    assert (
        "PRIVATE_TOKEN" not in str(result)
        and "secret-value" not in str(result)
        and str(tmp_path) not in str(result)
    )


def _warning_executable(tmp_path, warning, *, mode="valid", version="0.165.0"):
    import json

    script = tmp_path / "warning-provider"
    payload = output()
    if mode == "invalid_graph":
        payload["graph"] = None
    if mode == "invalid_body":
        payload["body"] = ["invalid body"]
    events = [
        {"type": "item.completed", "item": {"id": "item-0", "type": "error", "message": warning}}
    ]
    if mode == "malformed":
        events[0]["item"]["message"] = [warning]
    if mode == "bad_id":
        events[0]["item"]["id"] = []
    if mode == "extra_key":
        events[0]["item"]["detail"] = "unexpected"
    if mode == "development_warning":
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "id": "item-1",
                    "type": "error",
                    "message": "Under-development features enabled: fixture",
                },
            }
        )
    if mode == "auth":
        events.append({"type": "error", "message": "authentication failed"})
    if mode == "failed_turn":
        events.append({"type": "turn.failed", "error": {"message": "provider failed"}})
    if mode == "tool":
        events.append(
            {
                "type": "item.completed",
                "item": {"id": "item-1", "type": "command_execution", "command": "forbidden"},
            }
        )
    if mode != "missing_terminal":
        events.append(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
            }
        )
    script.write_text(
        "#!/usr/bin/env python3\nimport sys,json\nfrom pathlib import Path\nargs=sys.argv[1:]\n"
        + "if '--version' in args:\n print('codex-cli "
        + version
        + "');raise SystemExit()\n"
        + "if '--help' in args:\n print('--ignore-user-config --ignore-rules --strict-config -"
        "-output-last-message');raise SystemExit()\n"
        + "if 'features' in args:\n print("
        + repr(
            "\n".join(
                key + " stable " + str(value).lower() for key, value in NATIVE170_FEATURES.items()
            )
        )
        + ");raise SystemExit()\n"
        + "Path(args[args.index('--output-last-message')+1]).write_text("
        + repr(json.dumps(payload))
        + ")\n"
        + "for event in "
        + repr(events)
        + ":print(json.dumps(event))\n"
        + ("raise SystemExit(1)\n" if mode == "nonzero" else "")
    )
    script.chmod(0o700)
    return script


@pytest.mark.parametrize("version", ["0.165.0", "0.170.0"])
def test_native170_exact_ignored_role_warning_requires_valid_completed_authoring(
    tmp_path, monkeypatch, version
):
    home = tmp_path / "native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    warning = (
        "Ignoring malformed agent role definition: agent role file at "
        f"{home}/agents/fixture.toml must define a non-empty `name`"
    )
    binary = _warning_executable(tmp_path, warning, version=version)
    diagnostics = []
    result = NativeAuthor("codex", executable=str(binary), diagnostic=diagnostics.append).run(
        inputs()
    )
    assert result["body"] and result["graph"]
    assert diagnostics == [{"classification": "ignored_malformed_native_role_definition"}]
    assert str(home) not in str(diagnostics)


@pytest.mark.parametrize(
    "case",
    [
        "near",
        "appended",
        "foreign",
        "traversal",
        "control",
        "unsupported",
        "malformed",
        "bad_id",
        "extra_key",
        "invalid_body",
        "development_warning",
        "future_version",
        "auth",
        "failed_turn",
        "tool",
        "missing_terminal",
        "nonzero",
        "invalid_graph",
    ],
)
@pytest.mark.parametrize("version", ["0.165.0", "0.170.0"])
def test_native170_warning_never_masks_unqualified_diagnostic_or_failed_invocation(
    tmp_path, monkeypatch, case, version
):
    home = tmp_path / "native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    path = f"{home}/agents/fixture.toml"
    if case == "foreign":
        path = str(tmp_path / "foreign.toml")
    if case == "traversal":
        path = f"{home}/agents/../fixture.toml"
    warning = (
        "Ignoring malformed agent role definition: agent role file at "
        f"{path} must define a non-empty `name`"
    )
    if case == "near":
        warning = warning.replace("Ignoring", "Skipping")
    if case == "appended":
        warning += "; authentication failure"
    if case == "control":
        warning += "\n"
    binary = _warning_executable(
        tmp_path,
        warning,
        mode=case,
        version="0.164.0"
        if case == "unsupported"
        else "0.171.0"
        if case == "future_version"
        else version,
    )
    with pytest.raises(ValueError):
        NativeAuthor("codex", executable=str(binary)).run(inputs())


@pytest.mark.parametrize(
    "missing",
    [
        "agents.enabled",
        "features.multi_agent",
        "features.multi_agent_v2",
        "mcp_servers",
        "features.shell_tool",
        "features.goals",
        "features.sleep_tool",
        "features.workspace_dependencies",
        "features.in_app_chat",
        "features.in_app_local_automation",
        "features.default_mode_request_user_input",
        "web_search",
        "approval_policy",
        "sandbox_mode",
        "flag",
    ],
)
def test_warning_exception_rejects_missing_restriction(tmp_path, monkeypatch, missing):
    from harness.authoring_policy import build_profile as original
    from harness.authoring_policy import profile_identity

    home = tmp_path / "native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    warning = (
        "Ignoring malformed agent role definition: agent role file at "
        f"{home}/agents/fixture.toml must define a non-empty `name`"
    )
    binary = _warning_executable(tmp_path, warning)
    if missing == "flag":
        monkeypatch.setattr(
            "harness.authoring_native.AUTHORING_FLAGS", ("--ignore-user-config", "--strict-config")
        )
    else:

        def altered(*args, **kwargs):
            profile = original(*args, **kwargs)
            options = profile["options"]
            at = next(i for i in range(1, len(options), 2) if options[i].startswith(missing + "="))
            del options[at - 1 : at + 1]
            profile["identity"] = profile_identity(profile)
            return profile

        monkeypatch.setattr("harness.authoring_native.build_profile", altered)
    with pytest.raises(ValueError, match="restrictions changed"):
        NativeAuthor("codex", executable=str(binary)).run(inputs())


def test_profile_identity_binds_restrictions_but_not_temporary_catalog_path(tmp_path, monkeypatch):
    from harness.authoring_policy import profile_identity

    home = tmp_path / "native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    binary = _warning_executable(tmp_path, "unused")
    first = tmp_path / "first"
    first.mkdir()
    second = tmp_path / "second"
    second.mkdir()
    a = build_profile("codex", first, executable=str(binary))
    b = build_profile("codex", second, executable=str(binary))
    assert a["identity"] == b["identity"]
    b["options"] += ["-c", "features.shell_tool=true"]
    assert profile_identity(b) != a["identity"]


def test_165_profile_uses_canonical_flags_without_deprecated_aliases(tmp_path, monkeypatch):
    from harness.authoring_policy import profile_identity, verify_profile

    home = tmp_path / "native"
    monkeypatch.setenv("CODEX_HOME", str(home))
    binary = _warning_executable(tmp_path, "unused")
    profile = build_profile("codex", tmp_path, executable=str(binary))
    for alias in (
        "codex_hooks",
        "connectors",
        "imagegenext",
        "memory_tool",
        "web_search_cached",
        "web_search_request",
    ):
        assert "features." + alias + "=false" not in profile["options"]
    for canonical in ("hooks", "apps", "image_generation", "memories"):
        assert "features." + canonical + "=false" in profile["options"]
    assert 'web_search="disabled"' in profile["options"]
    profile["options"] += ["-c", "features.codex_hooks=false"]
    profile["identity"] = profile_identity(profile)
    with pytest.raises(ValueError, match="restrictions changed"):
        verify_profile(profile)


NATIVE170_FEATURES = dict.fromkeys(
    (
        "agent_message_board agent_message_board_mcp runtime_agent_instances "
        "wake_on_agent_messages multi_agent multi_agent_v2 multi_agent_v2_dynamic_tools "
        "subagent_thread_environments use_agent_identity send_message_to_user_async "
        "shell_tool shell_snapshot shell_snapshot_v2 unified_exec unified_exec_tty "
        "code_mode code_mode_only code_mode_host code_mode_host_grpc "
        "code_mode_host_output_truncation code_mode_interrupt code_mode_only_strict_3p_tools "
        "code_mode_prewarm code_mode_tool_search "
        "apps apps_early_resource_discovery codex_apps_mcp_2026_07_28 enable_mcp_apps "
        "mcp_2026_07_28 mcp_connection_sharing mcp_lazy_stdio_startup mcp_tool_list_changed "
        "multi_environment_mcp non_prefixed_mcp_tool_names auth_elicitation "
        "tool_call_mcp_elicitation  "
        "incremental_exec_tools incremental_tools incremental_tools_legacy "
        "incremental_tools_legacy_gcp  "
        "deferred_tool_world_state executor_capability_discovery executor_capability_discovery_v2 "
        "plugins remote_plugin recommended_plugins plugin_sharing hooks tool_suggest "
        "browser_use browser_use_external browser_use_full_cdp_access browser_annotation_api "
        "computer_use in_app_browser in_app_chat in_app_local_automation in_app_dictation "
        "in_app_voice  "
        "image_generation image_generation_animation view_image artifact multi_env_copy_file "
        "memories external_agent_memory_import skill_search skill_mcp_dependency_install "
        "skill_catalog_deltas standalone_web_search hosted_search_policy request_permissions_tool "
        "daemon_auto_start guardian_approval guardianv2 guardianv3 goals sleep_tool "
        "workspace_dependencies default_mode_request_user_input"
    ).split(),
    False,
) | {"skip_host_skill_discovery": True, "unified_exec": True}


def native170_executable(tmp_path, *, version="0.170.0", mode="valid"):
    path = tmp_path / "native170-fixture"
    source = """#!/usr/bin/env python3
import json,sys,tomllib
from pathlib import Path
args=sys.argv[1:]
if '--version' in args:
 print('codex-cli '+VERSION);raise SystemExit()
if '--help' in args:
 print('--ignore-user-config --ignore-rules --strict-config --output-last-message')
 raise SystemExit(1 if MODE=='help_failure' else 0)
values={}
for i,arg in enumerate(args):
 if arg=='-c':
  key,raw=args[i+1].split('=',1)
  assert key not in values
  values[key]=tomllib.loads('value='+raw)['value']
if 'features' in args:
 assert '--strict-config' not in args and '--ignore-user-config' not in args
 assert values['agents.enabled'] is False and values['mcp_servers']=={}
 assert values['web_search']=='disabled' and values['approval_policy']=='never'
 assert values['sandbox_mode']=='read-only'
 catalog=json.loads(Path(values['model_catalog_json']).read_text())
 for model in catalog['models']:
  assert model['shell_type']=='disabled' and model['apply_patch_tool_type'] is None
  assert model['tool_mode']=='direct' and model['experimental_supported_tools']==[]
  assert model['node_repl_disabled'] is True and model['supports_search_tool'] is False
 assert {k[9:]:v for k,v in values.items() if k.startswith('features.')}==FEATURES
 if MODE=='probe_failure': raise SystemExit(1)
 for key,value in FEATURES.items():
  if MODE=='missing_feature' and key=='runtime_agent_instances':continue
  if MODE=='ignored_override' and key=='incremental_tools':value=True
  print(key+' stable '+str(value).lower())
 raise SystemExit()
assert '--ignore-user-config' in args and '--ignore-rules' in args and '--strict-config' in args
assert 'mcp_servers={}' in args and 'agents.enabled=false' in args
Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(OUTPUT))
if MODE=='tool':
 print(json.dumps({'type':'item.completed','item':{'type':'command_execution'}}))
if MODE=='error':print(json.dumps({'type':'error','message':'arbitrary provider error'}))
if MODE=='warning':
 import os
 home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
 message=('Ignoring malformed agent role definition: agent role file at '
  +str(home/'agents/fixture.toml')+' must define a non-empty `name`')
 print(json.dumps({'type':'item.completed','item':{'id':'item-0','type':'error','message':message}}))
if MODE!='missing_terminal':
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}}))
if MODE=='nonzero':raise SystemExit(1)
"""
    prefix = "\n".join(
        key + "=" + repr(value)
        for key, value in {
            "VERSION": version,
            "MODE": mode,
            "FEATURES": NATIVE170_FEATURES,
            "OUTPUT": output(),
        }.items()
    )
    path.write_text(source.replace("import json,sys,tomllib", prefix + "\nimport json,sys,tomllib"))
    path.chmod(0o700)
    return path


def test_native170_profile_has_exact_restrictions_and_stable_identity(tmp_path, monkeypatch):
    import tomllib

    from harness.authoring_policy import profile_identity, verify_profile

    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = native170_executable(tmp_path)
    directories = [tmp_path / "one", tmp_path / "two"]
    for directory in directories:
        directory.mkdir()
    profiles = [build_profile("codex", directory, executable=binary) for directory in directories]
    profile = profiles[0]
    values = {
        raw.split("=", 1)[0]: tomllib.loads("value=" + raw.split("=", 1)[1])["value"]
        for raw in profile["options"][1::2]
    }
    assert profile["executable"]["version"] == "0.170.0" and profile["allowed_tools"] == []
    assert {k[9:]: v for k, v in values.items() if k.startswith("features.")} == NATIVE170_FEATURES
    assert values["model_catalog_json"] == profile["catalog_path"]
    assert values["suppress_unstable_features_warning"] is True
    assert profiles[0]["identity"] == profiles[1]["identity"]
    old_options = list(profile["options"])
    index = profile["options"].index("features.unified_exec=true")
    profile["options"][index] = "features.unified_exec=false"
    assert profile_identity(profile) != profiles[1]["identity"]
    profile["identity"] = profile_identity(profile)
    with pytest.raises(ValueError, match="restrictions changed"):
        verify_profile(profile)
    profile["options"] = old_options
    profile["identity"] = profile_identity(profile)
    assert NativeAuthor("codex", executable=binary).run(inputs())["body"] == output()["body"]


@pytest.mark.parametrize(
    "mode", ["help_failure", "probe_failure", "missing_feature", "ignored_override"]
)
def test_native170_rejects_failed_or_incomplete_profile_probes(tmp_path, monkeypatch, mode):
    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = native170_executable(tmp_path, mode=mode)
    with pytest.raises(ValueError):
        build_profile("codex", tmp_path, executable=binary)


@pytest.mark.parametrize("version", ["0.165.0", "0.170.0"])
@pytest.mark.parametrize(
    "alter",
    ["catalog_option", "catalog_bytes", "missing_restriction", "extra_option", "feature_value"],
)
def test_native170_verifier_binds_actual_options_and_catalogue(
    tmp_path, monkeypatch, version, alter
):
    from harness.authoring_policy import profile_identity, verify_profile

    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = (
        native170_executable(tmp_path)
        if version == "0.170.0"
        else _warning_executable(tmp_path, "unused")
    )
    profile = build_profile("codex", tmp_path, executable=binary)
    if alter == "catalog_option":
        index = next(
            i
            for i, value in enumerate(profile["options"])
            if value.startswith("model_catalog_json=")
        )
        profile["options"][index] = 'model_catalog_json="/foreign/catalog.json"'
    elif alter == "catalog_bytes":
        Path(profile["catalog_path"]).write_text("{}")
    elif alter == "missing_restriction":
        index = profile["options"].index("features.shell_tool=false")
        del profile["options"][index - 1 : index + 1]
    elif alter == "feature_value":
        option = (
            "features.skip_host_skill_discovery=true"
            if version == "0.170.0"
            else "features.goals=false"
        )
        index = profile["options"].index(option)
        key, value = option.split("=")
        profile["options"][index] = key + ("=false" if value == "true" else "=true")
    else:
        profile["options"] += ["-c", 'model_provider="foreign"']
    profile["identity"] = profile_identity(profile)
    with pytest.raises(ValueError):
        verify_profile(profile)


@pytest.mark.parametrize("mode", ["tool", "error", "missing_terminal", "nonzero"])
def test_native170_does_not_expand_warning_or_parser_acceptance(tmp_path, monkeypatch, mode):
    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = native170_executable(tmp_path, mode=mode)
    with pytest.raises(ValueError):
        NativeAuthor("codex", executable=binary).run(inputs())


def test_native170_revalidates_executable_immediately_before_invocation(tmp_path, monkeypatch):
    from harness.authoring_policy import build_profile as original

    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = native170_executable(tmp_path)

    def replaced(*args, **kwargs):
        profile = original(*args, **kwargs)
        binary.write_text(binary.read_text() + "\n# executable changed\n")
        return profile

    monkeypatch.setattr("harness.authoring_native.build_profile", replaced)
    with pytest.raises(ValueError, match="executable changed"):
        NativeAuthor("codex", executable=binary).run(inputs())


@pytest.mark.parametrize("model_index", [0, -1])
def test_native170_independent_catalogue_gate_rejects_any_shell_enabled_model(
    tmp_path, monkeypatch, model_index
):
    from harness.authoring_contract import canonical, digest
    from harness.authoring_policy import profile_identity, restricted_catalog, verify_profile

    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = _warning_executable(tmp_path, "unused")
    profile = build_profile("codex", tmp_path, executable=binary)
    catalogue = restricted_catalog()
    catalogue["models"][model_index]["shell_type"] = "unified_exec"
    # Even a newly bound catalogue cannot remove the independent shell-exposure gate.
    monkeypatch.setattr("harness.authoring_policy.restricted_catalog", lambda: catalogue)
    Path(profile["catalog_path"]).write_text(canonical(catalogue))
    profile["catalog_hash"] = digest(canonical(catalogue))
    profile["identity"] = profile_identity(profile)
    with pytest.raises(ValueError, match="catalogue"):
        verify_profile(profile)


def test_native170_normalized_backend_never_allows_shell_feature(tmp_path, monkeypatch):
    from harness.authoring_policy import profile_identity, verify_profile

    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = native170_executable(tmp_path)
    profile = build_profile("codex", tmp_path, executable=binary)
    index = profile["options"].index("features.shell_tool=false")
    profile["options"][index] = "features.shell_tool=true"
    profile["identity"] = profile_identity(profile)
    with pytest.raises(ValueError, match="restrictions changed"):
        verify_profile(profile)


@pytest.mark.parametrize("alter", ["missing", "false", "duplicate", "string", "identity"])
def test_native170_unstable_warning_suppression_is_exact_and_bound(tmp_path, monkeypatch, alter):
    from harness.authoring_policy import profile_identity, verify_profile

    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = native170_executable(tmp_path)
    profile = build_profile("codex", tmp_path, executable=binary)
    option = "suppress_unstable_features_warning=true"
    assert profile["options"].count(option) == 1
    index = profile["options"].index(option)
    if alter == "missing":
        del profile["options"][index - 1 : index + 1]
    elif alter == "duplicate":
        profile["options"] += ["-c", option]
    else:
        profile["options"][index] = (
            'suppress_unstable_features_warning="true"'
            if alter == "string"
            else "suppress_unstable_features_warning=false"
        )
    assert profile_identity(profile) != profile["identity"]
    if alter != "identity":
        profile["identity"] = profile_identity(profile)
    with pytest.raises(ValueError):
        verify_profile(profile)


@pytest.mark.parametrize("version", ["0.164.0", "0.165.0"])
def test_native170_suppression_is_not_admitted_in_legacy_profile(tmp_path, monkeypatch, version):
    from harness.authoring_policy import profile_identity, verify_profile

    monkeypatch.setattr("harness.authoring_policy.auth_selection", lambda: {})
    binary = _warning_executable(tmp_path, "unused", version=version)
    profile = build_profile("codex", tmp_path, executable=binary)
    option = "suppress_unstable_features_warning=true"
    assert option not in profile["options"]
    profile["options"] += ["-c", option]
    profile["identity"] = profile_identity(profile)
    with pytest.raises(ValueError, match="restrictions changed"):
        verify_profile(profile)
