"""Explicit evaluator-source bootstrap; authenticated Harness selection stays separate."""

import hashlib
import importlib.machinery
import json
import sys
import types
from pathlib import Path


def hashes(engine):
    scripts = Path(engine).resolve(strict=True) / "scripts"
    paths = [
        scripts / "knowledge_native_eval.py",
        *sorted((scripts / "knowledge_evaluation").glob("*.py")),
    ]
    return {
        p.relative_to(scripts).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }


def verify_source(engine, expected):
    scripts = Path(engine).resolve(strict=True) / "scripts"
    if hashes(engine) != expected:
        raise ValueError("selected evaluator source hash binding changed")
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name == "scripts":
            paths = list(getattr(module, "__path__", ()))
            if not paths or any(Path(p).resolve() != scripts for p in paths):
                raise ValueError("foreign preimported evaluator namespace")
        elif name == "scripts.knowledge_native_eval" or name.startswith(
            "scripts.knowledge_evaluation"
        ):
            source = Path(getattr(module, "__file__", "")).resolve()
            if not source.is_relative_to(scripts):
                raise ValueError("foreign preimported evaluator module")
            relative = source.relative_to(scripts).as_posix()
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if expected.get(relative) != digest:
                raise ValueError("imported evaluator differs from selected source")
            modules[name] = {"path": str(source), "sha256": digest}
    return modules


def select_source(engine, expected, bridge=None):
    engine = Path(engine).resolve(strict=True)
    verify_source(engine, expected)
    # Pin the namespace as well as sys.path: a later regular package otherwise wins.
    if "scripts" not in sys.modules:
        namespace = types.ModuleType("scripts")
        namespace.__path__ = [str(engine / "scripts")]
        namespace.__package__ = "scripts"
        namespace.__spec__ = importlib.machinery.ModuleSpec("scripts", loader=None, is_package=True)
        namespace.__spec__.submodule_search_locations = namespace.__path__
        sys.modules["scripts"] = namespace
    preferred = ([str(Path(bridge).resolve(strict=True))] if bridge else []) + [str(engine)]
    sys.path[:] = preferred + [p for p in sys.path if p not in preferred]
    return verify_source(engine, expected)


def main(argv=None):
    bridge, engine, package, admission, isolation = sys.argv[1:] if argv is None else argv
    expected = json.loads(Path(isolation).read_bytes())["adapter_hashes"]
    select_source(engine, expected, bridge)
    from scripts.knowledge_evaluation.live import run

    verify_source(engine, expected)
    run(package, admission, bridge, evaluator_source=(engine, expected))


if __name__ == "__main__":
    main()
