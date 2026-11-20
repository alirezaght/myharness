"""Observation-only pytest plugin for the fixed Harness recipe child."""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import sys
from pathlib import Path

import pytest

from . import verification_contracts as C
from .storage_filesystem import StorageError, artifact, canonical, digest, publish_json

STATE = None


def configure(assignment, expected_nodes, output, launch_token):
    global STATE
    STATE = {
        "assignment": assignment,
        "expected": expected_nodes,
        "output": output,
        "token": launch_token,
        "inventory": [],
        "selected": [],
        "deselected": [],
        "reports": {},
        "errors": [],
        "environment": None,
    }


def _file(path, root, *, builtin=False, module=None):
    path = Path(path).resolve(strict=True)
    observed = artifact(path, maximum=4 * 1024**2)
    if path.is_relative_to(root):
        name = path.relative_to(root).as_posix()
    elif builtin and module and module.startswith("_pytest."):
        name = "external:" + module.replace(".", "/") + ".py"
    else:
        raise StorageError("unresolved_external_fixture_source")
    return {"path": name, "sha256": observed["sha256"]}


def _record(item):
    root = Path(item.config.rootpath).resolve(strict=True)
    fixtures = []
    for name, definitions in sorted(item._fixtureinfo.name2fixturedefs.items()):
        for definition in definitions:
            fn = definition.func
            source = inspect.getsourcefile(fn)
            code = inspect.getsource(fn)
            module = getattr(fn, "__module__", "")
            builtin = module.startswith("_pytest.")
            fixtures.append(
                {
                    "name": name,
                    "scope": definition.scope,
                    "params": digest(
                        re.sub(r"0x[0-9a-fA-F]+", "<address>", repr(definition.params))
                    ),
                    "arguments": list(definition.argnames),
                    "source": _file(source, root, builtin=builtin, module=module),
                    "implementation": hashlib.sha256(code.encode()).hexdigest(),
                    "dynamic": "getfixturevalue" in code,
                    "builtin": builtin,
                }
            )
    return {
        "nodeid": item.nodeid,
        "source": _file(item.path, root),
        "fixtures": fixtures,
        "parameterization": getattr(getattr(item, "callspec", None), "id", None),
        "markers": sorted(set(marker.name for marker in item.iter_markers())),
        "initial_fixtures": sorted(set(item._fixtureinfo.initialnames)),
    }


def _environment(config):
    plugins = []
    for name, plugin in config.pluginmanager.list_name_plugin():
        if plugin is None:
            continue
        module = getattr(plugin, "__name__", plugin.__class__.__module__)
        is_ours = module == __name__
        allowed = (
            module.startswith("_pytest.")
            or module in {"_pytest", "pytest", "builtins"}
            or name
            in {
                "session",
                "funcmanage",
                "terminalreporter",
                "logging-plugin",
                "legacypath-tmpdir",
                "capturemanager",
            }
            or str(name).endswith("conftest.py")
            or is_ours
        )
        if not allowed:
            STATE["errors"].append("unsupported collection plugin: " + str(name))
        path = (
            inspect.getsourcefile(plugin)
            if inspect.ismodule(plugin) or inspect.isclass(plugin)
            else inspect.getsourcefile(plugin.__class__)
        )
        source_hash = (
            artifact(Path(path).resolve(), maximum=4 * 1024**2)["sha256"] if path else None
        )
        plugins.append(
            {
                "name": "al-harness-recipe-observer-v1"
                if is_ours
                else "pytest-config-instance-v1"
                if plugin is config
                else "pytest-plugin-manager-instance-v1"
                if plugin is config.pluginmanager
                else str(name),
                "module": module,
                "sha256": source_hash,
            }
        )
    for hook in (
        "pytest_collection_modifyitems",
        "pytest_ignore_collect",
        "pytest_collect_file",
        "pytest_pycollect_makeitem",
    ):
        for implementation in getattr(config.pluginmanager.hook, hook).get_hookimpls():
            if str(implementation.plugin_name).endswith("conftest.py"):
                STATE["errors"].append("unsupported repository collection hook: " + hook)
    python = artifact(Path(sys.executable).resolve(), maximum=256 * 1024**2)
    return {
        "schema_version": 1,
        "python": {
            "path": str(Path(sys.executable).resolve()),
            "sha256": python["sha256"],
            "identity": python["identity"],
        },
        "python_version": sys.version,
        "pytest_version": pytest.__version__,
        "plugins": sorted(plugins, key=lambda p: (p["name"], p["module"])),
        "config": {
            "root": str(config.rootpath),
            "inipath": str(config.inipath),
            "addopts": config.getini("addopts"),
        },
        "autoload_disabled": os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1",
        "inherited_options_ignored": True,
    }


def pytest_configure(config):
    if STATE is None:
        raise pytest.UsageError("recipe plugin requires an authenticated assignment")
    try:
        STATE["environment"] = _environment(config)
    except (OSError, ValueError, TypeError):
        STATE["errors"].append("unresolved plugin or interpreter identity")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    try:
        before = [_record(item) for item in items]
        C.unique_nodes(before)
        STATE["inventory"] = before
    except (OSError, ValueError, TypeError):
        STATE["errors"].append("unresolved complete source/fixture inventory")
    result = yield
    try:
        STATE["selected"] = [_record(item) for item in items]
        inventory = C.unique_nodes(STATE["inventory"])
        selected = C.unique_nodes(STATE["selected"])
        deselected = C.unique_nodes(STATE["deselected"])
        if inventory != selected | deselected or selected.keys() & deselected.keys():
            STATE["errors"].append("selection did not reconcile exactly")
        if STATE["assignment"]["kind"] == "partition" and inventory != C.unique_nodes(
            STATE["expected"]
        ):
            STATE["errors"].append("assigned source/fixture inventory changed")
        if any("browser" in n["markers"] for n in selected.values()) or any(
            "browser" not in n["markers"] for n in deselected.values()
        ):
            STATE["errors"].append("unexpected marker selection")
    except (OSError, ValueError, TypeError):
        STATE["errors"].append("invalid selected/deselected source inventory")
    if STATE["errors"]:
        raise pytest.UsageError("; ".join(STATE["errors"]))
    return result


def pytest_deselected(items):
    try:
        STATE["deselected"].extend(_record(item) for item in items)
    except (OSError, ValueError, TypeError):
        STATE["errors"].append("unresolved deselected inventory")


def pytest_collectreport(report):
    if report.failed:
        STATE["errors"].append("collection failed: " + report.nodeid)


def pytest_runtest_logreport(report):
    STATE["reports"].setdefault(report.nodeid, []).append(
        {
            "when": report.when,
            "outcome": report.outcome,
            "wasxfail": bool(getattr(report, "wasxfail", False)),
            "duration": report.duration,
        }
    )


def pytest_sessionfinish(session, exitstatus):
    assignment = STATE["assignment"]
    header = {
        "schema_version": 1,
        "assignment_digest": assignment["assignment_digest"],
        "source_digest": assignment["source_digest"],
        "recipe_digest": assignment["recipe_digest"],
        "launch_token": STATE["token"],
        "finalized": True,
        "exitstatus": int(exitstatus),
        "pytest_version": pytest.__version__,
        "environment": STATE["environment"],
        "errors": STATE["errors"],
    }
    selected = {n["nodeid"] for n in STATE["selected"]}
    items = [
        {
            "node": node,
            "selected": node["nodeid"] in selected,
            "reports": STATE["reports"].get(node["nodeid"], []),
        }
        for node in STATE["inventory"]
    ]
    if set(STATE["reports"]) - {n["nodeid"] for n in STATE["inventory"]}:
        header["errors"].append("extra executed node observations")
    pages, current = [], []

    def publish():
        if not current:
            return
        number = len(pages)
        if number >= C.MAX_PAGES:
            raise StorageError("recipe_report_page_limit")
        name = "page" + str(number).zfill(3)
        value = {"schema_version": 1, "items": list(current)}
        reference = publish_json(STATE["output"][name], value)
        pages.append({"name": name, "sha256": reference["sha256"], "nodes": len(current)})
        current.clear()

    for item in items:
        if len(canonical({"schema_version": 1, "items": [item]})) > C.PAGE_BYTES:
            raise StorageError("recipe_node_metadata_limit")
        if (
            current
            and len(canonical({"schema_version": 1, "items": [*current, item]})) > C.PAGE_BYTES
        ):
            publish()
        current.append(item)
    publish()
    publish_json(
        STATE["output"]["report"],
        {
            "schema_version": 1,
            "kind": "harness-pytest-report-v1",
            "header": header,
            "pages": pages,
            "nodes": len(items),
        },
    )


@pytest.hookimpl(tryfirst=True)
def pytest_ignore_collect(collection_path, config):
    # pytest may enumerate root siblings while resolving tests. They are outside
    # this fixed recipe, and protected control metadata must not be probed.
    root = Path(config.rootpath)
    try:
        relative = Path(collection_path).relative_to(root)
    except ValueError:
        return None
    if relative.parts and relative.parts[0] != "tests":
        return True
    return None
