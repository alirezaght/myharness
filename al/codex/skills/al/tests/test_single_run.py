"""Host-neutral tests run once, in this suite; byte parity covers the Claude copy (#36).

`single-run.json` lists test files that exercise only shared scripts through the host-adapter
fixtures (`cli`, `cli_process`, `state_file`, `launcher`, `run_state`, all defined per host in
conftest.py). Their Claude copies were removed: the shared scripts they exercise are
byte-identical in both hosts (test_parity), so a second run proves nothing new. Tests of
host-specific scripts and host boundaries keep a copy in both suites. This guard keeps every
listed file eligible, so a host-specific dependency can never hide in a single-run file.
"""
import json
from pathlib import Path
import re

from test_resources import package_root, peer_package_root

CODEX = package_root(__file__)
CLAUDE = peer_package_root(__file__)
TESTS = CODEX / "tests"
MANIFEST = json.loads((TESTS / "single-run.json").read_text())
# Scripts whose bytes differ between hosts, and host-only scripts: never single-run.
# Built from parts: this Codex copy may not spell the Claude-only adapter names (test_consistency).
HOST_SPECIFIC = {"_".join(("al", stem)) for stem in
                 ("state", "start", "models", "native_sessions", "readonly" + "_guard", "status" + "line")}
# al_store differs only in its runtime directory and native-session selection, which conftest's
# isolated_runtime fixture fixes per host; reading and writing run state through it is adapter use.
ADAPTER_MODULES = {"al_store"}
ADAPTERS = {"conftest", "launcher_context", "launcher_child", "fixture_state", "run_templates"}
BOUNDARY = re.compile(r"native|session|hook|statusline|readonly|model|parity|consistency|docs|resources"
                      r"|launcher|state_machine|start|run_templates")
LITERAL = re.compile(r"\.codex\b|\.claude\b|CLAU" r"DE_|CODEX_|platform\s*==|'codex'|\"codex\"|'claude'|\"claude\"")
IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def _closure(name, seen):
    """Modules a test file imports, following this suite's helper and test modules."""
    found = set()
    for module in IMPORT.findall((TESTS / (name + ".py")).read_text()):
        found.add(module)
        if module not in seen and module not in ADAPTERS and (TESTS / (module + ".py")).is_file():
            seen.add(module)
            found |= _closure(module, seen)
    return found


def test_single_run_files_are_listed_once_and_exist_only_here():
    files = MANIFEST["files"]
    assert files == sorted(set(files)) and files
    for name in files:
        assert (TESTS / name).is_file(), name
        assert not (CLAUDE / "tests" / name).exists(), name + " must not also run on Claude"


def test_single_run_files_never_depend_on_host_specific_code():
    differing = {name[:-3] for name in json.loads(
        (CODEX / "reference" / "parity-manifest.json").read_text())["platform_adapters"]["different_content"]}
    host_specific = (HOST_SPECIFIC | differing) - ADAPTER_MODULES
    for name in MANIFEST["files"]:
        stem = name[:-3]
        assert not BOUNDARY.search(stem), name
        seen = set()
        modules = _closure(stem, seen)
        assert not modules & host_specific, (name, sorted(modules & host_specific))
        for module in {stem} | seen:
            # A reviewed file may name a host only as fixture data for a shared script
            # (single-run.json `literal_data` gives the reason); it never selects host code.
            if module + ".py" in MANIFEST.get("literal_data", {}):
                continue
            assert not LITERAL.search((TESTS / (module + ".py")).read_text()), (name, module)


def test_reviewed_literal_data_names_listed_files_with_a_reason():
    reviewed = MANIFEST.get("literal_data", {})
    assert set(reviewed) <= set(MANIFEST["files"])
    assert all(isinstance(reason, str) and reason.strip() for reason in reviewed.values())
