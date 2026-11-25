"""Admitted AL runs in their key states, built once per pytest process and copied per test (#36).

Building an admitted run (start -> spec gate -> classify -> gate plan and base run -> planning
admission -> implementation) costs seconds of real runtime work. A test that only needs to
*start from* such a run asks the `run_state` fixture for it instead:

    def test_x(cli, run_state):
        run_state("implemented")      # the same run start_implementation(cli, tmp_path) produces
        ...

The first request in a process builds the state with the real public commands, exactly as the
helpers do, and records a template: the repository (worktree, Git data and the AL runtime
directory), the run's protected launcher transaction and the analysis index. Every later request
restores a fresh, private copy of that template. Nothing is shared between tests:

* the template is private to this module and never handed to a test; each test gets new copies;
* an opted-in test's repository lives at one fixed per-process path (the run identity binds the
  absolute repository path, so a copy is only valid there) that is deleted and recreated per test;
* in-process memo tables of the AL scripts are emptied before each copy, so no cached repository
  observation of an earlier test at the same path can be reused;
* every restored copy is verified to be byte-identical (content, mode, symlink target) to the
  template before the test continues, and the build itself is verified against its own record.

States form a chain; each is built from a copy of the previous one:
  planned      STANDARD run, N/A gates planned and base-run, planning admitted (phase PLANNING)
  implemented  planned + `phase IMPLEMENTATION` (== evidence_helpers.start_implementation)
  finalizing   implemented + `phase FINALIZATION` + test_rework's red repro run (== test_rework.started)
  verified     SMALL run implemented by one finished writer, candidate gates run, phase VERIFICATION
               with passing broad evidence (== hook_regression_helpers.begin(cli, tmp_path, repo))
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import types

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SPEC = "Verify a scoped implementation and preserve its repository evidence."
ANTICIPATED = ("svc/app.py", "svc/fixed")

_TEMPLATES = {}  # name -> private template directory (this process only)
_CACHES = None   # [(module, name)] of the scripts' in-process memo tables


def _empty_cache(value):
    return type(value) in (dict, set, list) and not value


def register_memo_tables():
    """Find the memo tables; the conftests call this at import, before any test can fill one."""
    return _memo_tables()


def _memo_tables():
    """Every scripts module's module-level memo table, found once before any test ran.

    All scripts are imported eagerly; a module-level dict/set/list that is empty right after
    import is a memo table filled at run time (constant tables are non-empty at import).
    functools caches are found by their cache_clear method."""
    global _CACHES
    if _CACHES is None:
        found = []
        for path in sorted(SCRIPTS.glob("al_*.py")):
            try:
                module = importlib.import_module(path.stem)
            except Exception:  # noqa: BLE001 - a script that cannot be imported here holds no cache here
                continue
            for name, value in vars(module).items():
                if name.startswith("__") or isinstance(value, types.ModuleType):
                    continue
                if _empty_cache(value) or hasattr(value, "cache_clear"):
                    found.append((module, name))
                elif type(value).__name__ == "AnalysisCache":
                    found.append((module, name))
        _CACHES = found
    return _CACHES


def reset_process_caches():
    for module, name in _memo_tables():
        value = getattr(module, name)
        if hasattr(value, "cache_clear"):
            value.cache_clear()
        elif hasattr(value, "clear"):
            value.clear()


def tree_digest(root):
    """Digest of a directory tree: relative paths, file bytes, permission bits and symlink targets."""
    root = Path(root)
    h = hashlib.sha256()
    if not root.exists():
        h.update(b"absent")
        return h.hexdigest()
    for directory, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(dirs + names):
            path = Path(directory) / name
            info = os.lstat(path)
            rel = path.relative_to(root).as_posix().encode()
            h.update(rel + b"\0" + oct(stat.S_IMODE(info.st_mode)).encode() + b"\0")
            if stat.S_ISLNK(info.st_mode):
                h.update(b"L" + os.readlink(path).encode())
            elif stat.S_ISREG(info.st_mode):
                h.update(b"F" + hashlib.sha256(path.read_bytes()).digest())
            else:
                h.update(b"D")
            h.update(b"\n")
    return h.hexdigest()


class RunState:
    """The `run_state` fixture value: call it with a state name as the test's first action."""

    # state -> the state it is built from (None: built from the seed repository)
    BUILD = {"planned": None, "implemented": "planned", "finalizing": "implemented", "verified": None}

    def __init__(self, *, cli, repo, tmp_path, launcher, seed_digest, store):
        self.cli, self.repo, self.tmp_path, self.launcher = cli, repo, tmp_path, launcher
        self.seed_digest, self.store = seed_digest, store
        self.session = None
        self.used = None
        self.restored = None  # digest of the restored copy (None when this test built the state)

    # -- paths owned by one run -------------------------------------------------------------
    def _active_session(self):
        import al_store
        if self.cli.fixed_session:
            return self.cli.fixed_session
        return al_store.CURRENT.read_text().strip()

    def _transaction(self):
        from installer.runtime_pin_transaction import key, subject
        value = subject(self.repo, self.launcher.platform, self.session)
        return Path(self.launcher.storage) / "runs" / (key(value) + ".json")

    def _parts(self):
        """Everything one run owns: worktree + Git + runtime, its launcher transaction, the
        content-addressed initialization record it cites, and the analysis index."""
        transaction = self._transaction()
        parts = {"repo": self.repo, "index": Path(os.environ["AL_ANALYSIS_INDEX_DIR"]), "transaction": transaction}
        if transaction.is_file():
            cited = (json.loads(transaction.read_bytes()).get("initialization") or {}).get("stored_path")
            if cited:
                parts["initialization"] = Path(cited)
        return parts

    @staticmethod
    def _digest(parts):
        return {name: tree_digest(path) if path.is_dir() else
                (hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None)
                for name, path in sorted(parts.items())}

    # -- build ------------------------------------------------------------------------------
    def _steps(self, name):
        from evidence_helpers import plan_na_gates
        from launcher_context import admit_planning
        cli = self.cli

        def ok(*args):
            result, out, err = cli(*args)
            assert result == 0, f"{args[0]} failed: {result} {out}{err}"

        def planned():
            for args in (("start", "--spec", SPEC), ("spec-gate", "SPEC_READY"),
                         ("classify", "STANDARD", "--designer", "no", "--implementers", "al-engineer")):
                ok(*args)
            plan_na_gates(cli, self.tmp_path, ANTICIPATED)
            admit_planning(cli)

        def implemented():
            ok("phase", "IMPLEMENTATION")

        def verified():
            from hook_regression_helpers import begin
            begin(cli, self.tmp_path, self.repo)

        def finalizing():
            from test_rework import record_red
            ok("phase", "FINALIZATION")
            record_red(cli)
        return {"planned": planned, "implemented": implemented, "finalizing": finalizing, "verified": verified}[name]

    def _pristine(self):
        if tree_digest(self.repo) != self.seed_digest:
            raise AssertionError("run_state() must be the test's first action on its untouched repository")

    def _record(self, name):
        template = self.store / name
        if template.exists():
            shutil.rmtree(template)
        template.mkdir(parents=True)
        self.session = self._active_session()
        parts = self._parts()
        for part, path in parts.items():
            if path.is_dir():
                shutil.copytree(path, template / part, symlinks=True)
            elif path.exists():
                shutil.copy2(path, template / part)
        import al_store
        state = json.loads((al_store.RUNTIME_DIR / (self.session + ".json")).read_bytes())
        meta = {"session": self.session, "run_id": state["run_id"], "digest": self._digest(parts),
                "paths": {part: str(path) for part, path in parts.items()}}
        # The recorded copy must reproduce exactly what the build left behind.
        if self._digest({part: template / part for part in parts}) != meta["digest"]:
            raise AssertionError("run_state template differs from the run it recorded")
        (template / "meta.json").write_text(json.dumps(meta))
        _TEMPLATES[name] = template

    def _restore(self, name):
        template = _TEMPLATES[name]
        meta = json.loads((template / "meta.json").read_text())
        self.session = meta["session"]
        reset_process_caches()
        parts = self._parts()
        parts.update({part: Path(path) for part, path in meta["paths"].items() if part not in parts})
        # The run binds absolute paths; only the analysis index (a per-test cache directory) moves.
        if any(str(parts[part]) != path for part, path in meta["paths"].items() if part != "index"):
            raise AssertionError("run_state copy would not occupy the template's paths")
        if parts["transaction"].parent.is_dir():
            for stale in parts["transaction"].parent.glob(parts["transaction"].stem + ".*"):
                stale.unlink()
        for part, path in parts.items():
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            source = template / part
            if source.is_dir():
                shutil.copytree(source, path, symlinks=True)
            elif source.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, path)
        os.chdir(self.repo)  # the test's working directory is its repository (isolated_runtime)
        if self._digest(parts) != meta["digest"]:
            raise AssertionError("restored run state is not byte-identical to its template")
        self.cli.observer.runs[self.session] = meta["run_id"]
        self.launcher.enter(self.repo, self.session)
        self.restored = meta["digest"]

    def __call__(self, name):
        if name not in self.BUILD:
            raise ValueError("unknown run state " + repr(name))
        if self.used is not None:
            raise AssertionError("run_state() may be called once per test")
        self._pristine()
        self.used = name
        chain = [name]
        while chain[0] not in _TEMPLATES and self.BUILD[chain[0]] is not None:
            chain.insert(0, self.BUILD[chain[0]])
        reset_process_caches()
        if chain[0] in _TEMPLATES:
            self._restore(chain.pop(0))
        for step in chain:
            self._steps(step)()
            self._record(step)
        if name == "verified":  # like hook_regression_helpers.begin, which this state replaces
            return json.loads(self.cli("show")[1])
        return None


def fixed_repository(base):
    """The one repository path of this process's run-state tests (recreated per test)."""
    return Path(base) / "run-state" / "repo"


def template_store(base):
    return Path(base) / "run-state-templates"


__all__ = ["RunState", "fixed_repository", "reset_process_caches", "template_store", "tree_digest"]
