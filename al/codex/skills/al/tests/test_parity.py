"""Guard the separate copies with an exhaustive shared-file parity manifest."""
import json
from pathlib import Path

CODEX = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
CLAUDE = REPO / ("." + "claude") / "skills" / "al"

def test_manifest_is_exhaustive_and_only_names_platform_adapters():
    manifest = json.loads((CODEX / "reference" / "parity-manifest.json").read_text())
    codex_files = {p.name for p in (CODEX / "scripts").glob("*.py")}
    claude_files = {p.name for p in (CLAUDE / "scripts").glob("*.py")}
    adapters = manifest["platform_adapters"]
    codex_only = set(adapters["codex_only"])
    claude_only = {"_".join(adapter["stem"]) + adapter["extension"]
                   for adapter in adapters["claude_only"]}
    different = set(adapters["different_content"])
    shared = set(manifest["shared_files"])
    assert len(shared) == len(manifest["shared_files"])
    assert codex_files - claude_files == codex_only
    assert claude_files - codex_files == claude_only
    assert (codex_files & claude_files) == shared | different
    for name in shared:
        assert (CODEX / "scripts" / name).read_bytes() == (CLAUDE / "scripts" / name).read_bytes(), name
    shared_reference = set(manifest["shared_reference_files"])
    assert len(shared_reference) == len(manifest["shared_reference_files"])
    codex_reference = {p.relative_to(CODEX / "reference").as_posix()
                       for p in (CODEX / "reference").rglob("*") if p.is_file()}
    claude_reference = {p.relative_to(CLAUDE / "reference").as_posix()
                        for p in (CLAUDE / "reference").rglob("*") if p.is_file()}
    codex_reference_only = set(adapters["codex_only_references"])
    claude_reference_only = set(adapters["claude_only_references"])
    different_reference = set(adapters["different_reference_content"])
    assert codex_reference == shared_reference | codex_reference_only | different_reference
    assert claude_reference == shared_reference | claude_reference_only | different_reference
    assert codex_reference - claude_reference == codex_reference_only
    assert claude_reference - codex_reference == claude_reference_only
    assert different_reference == {"distribution.md", "handoffs.md", "parallelism.md", "routing.md"}
    rationales = adapters["reference_rationales"]
    assert set(rationales) == different_reference
    assert all(reason.strip() for reason in rationales.values())
    for name in shared_reference:
        assert (CODEX / "reference" / name).read_bytes() == (CLAUDE / "reference" / name).read_bytes(), name
    for name in different_reference:
        assert (CODEX / "reference" / name).read_bytes() != (CLAUDE / "reference" / name).read_bytes(), name
    for package in (CODEX, CLAUDE):
        distribution = (package / "reference" / "distribution.md").read_text()
        handoffs = (package / "reference" / "handoffs.md").read_text()
        parallelism = (package / "reference" / "parallelism.md").read_text().lower()
        assert "result_owned" in distribution
        for requirement in ("review-compare-start --plan", "--pseudocode", "inspected_artifacts"):
            assert requirement in handoffs
        assert "write ownership must be settled" in parallelism
    assert different == {"al_models.py", "al_state.py", "al_store.py"}
    def adapter_name(stem):
        return "_".join(("al", stem)) + ".py"
    assert claude_only == {
        adapter_name("readonly_guard"),
        adapter_name("statusline"),
        adapter_name("native_sessions"),
    }
    native_rationales = adapters["native_adapter_rationales"]
    assert set(native_rationales) == {adapter_name("native_sessions")}
    assert "conversation" in native_rationales[adapter_name("native_sessions")].lower()
    assert not codex_only
