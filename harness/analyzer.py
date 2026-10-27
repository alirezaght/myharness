"""Repository Analyzer: a read-only investigation of a target repository for one request.

Approved decisions are loaded first, then the probes run; nothing is written to the repository.
"""

from pathlib import Path

from harness.decisions import (
    DEFAULT_DECISIONS_DIR,
    DecisionParseError,
    DecisionRecord,
    DecisionStatus,
    decision_files,
    parse_decision,
)
from harness.investigation import FactCategory, InvestigationReport
from harness.repo_manifests import dependency_facts
from harness.repo_probes import (
    component_summary,
    config_facts,
    convention_facts,
    interface_facts,
    language_summary,
    schema_facts,
    test_facts,
)
from harness.repo_relevance import relevance
from harness.repo_scan import Findings, git_facts, list_files
from harness.repository_audit import audit


def _shown(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def load_approved(root: Path, directory: Path) -> tuple[list[DecisionRecord], Findings]:
    out, approved = Findings(), []
    files = decision_files(directory)
    if not files:
        where = _shown(root, directory)
        out.fact(FactCategory.DECISIONS, f"no decision records under {where}", where)
    for path in files:
        where = _shown(root, path)
        try:
            record = parse_decision(path)
        except DecisionParseError as error:
            out.ask("decisions", f"Decision record {where} is malformed ({error}); is it valid?")
            continue
        if record.status is DecisionStatus.APPROVED:
            approved.append(record)
            text = f"{record.id} v{record.version} approved — {record.title}"
            out.fact(FactCategory.DECISIONS, f"{text} (scope: {record.scope or 'unstated'})", where)
        else:
            out.fact(FactCategory.DECISIONS, f"{record.id} is {record.status}", where)
    return approved, out


def analyze(
    repo_root: Path, request: str, decisions_dir: Path | None = None
) -> InvestigationReport:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {repo_root}")
    text = (request or "").strip()
    if not text:
        raise ValueError("request must not be empty")
    directory = Path(decisions_dir).resolve() if decisions_dir else root / DEFAULT_DECISIONS_DIR
    decisions, decision_findings = load_approved(root, directory)

    files, source = list_files(root)
    repository = Findings()
    repository.fact(FactCategory.REPOSITORY, f"{len(files)} files enumerated via {source}", source)
    facts, revision = git_facts(root)
    repository.facts.extend(facts)
    ownership = audit(root, text, files=files)
    for unit in ownership["selected_units"]:
        repository.fact(
            FactCategory.COMPONENTS,
            f"scoped package `{unit['root']}/`",
            f"manifest {unit['manifest']}; explicit path/package identity",
        )
    for comparison in ownership["main_comparisons"]:
        repository.fact(
            FactCategory.REPOSITORY,
            f"{comparison['ahead']} ahead and {comparison['behind']} behind {comparison['ref']}",
            "local Git ref comparison; remote freshness unverified (no fetch)",
        )
    if ownership["ownership"] == "UNRESOLVED":
        repository.ask(
            "scope",
            "Ownership is unresolved; which package or implementation path "
            "does this request target? Keyword and test counts do not establish ownership.",
        )
    if not ownership["inventory_complete"]:
        repository.ask(
            "scope",
            "Repository inventory reached its bound; provide the target package "
            "before drawing ownership or coverage conclusions.",
        )
    for edge in ownership["call_sites"][:25]:
        repository.fact(
            FactCategory.FILES,
            f"call site `{edge['from']}` imports `{edge['to']}`",
            edge["evidence"],
        )
    for instruction in ownership["instructions"]:
        repository.fact(
            FactCategory.CONSTRAINTS,
            f"scoped agent instructions `{instruction['path']}`",
            f"{instruction['path']} sha256:{instruction['sha256']}",
        )
    scoped = [p for p in files if p.as_posix() in ownership["relevant_paths"]]
    # Repository inventories remain useful context. Material config questions and
    # test placement must come from scoped ownership rather than global volume.
    probe_files = scoped if ownership["ownership"] == "SCOPED" else files
    probes = [
        repository,
        decision_findings,
        language_summary(files),
        dependency_facts(root, probe_files),
        component_summary(files),
        relevance(root, files, text),
        interface_facts(files),
        schema_facts(files),
        config_facts(files, relevant=set(ownership["relevant_paths"])),
        test_facts(probe_files),
        convention_facts(root, files),
    ]
    questions = {(q.topic, q.text): q for probe in probes for q in probe.questions}
    return InvestigationReport(
        repo_root=str(root),
        revision=revision,
        request=text,
        decisions=tuple(decisions),
        facts=tuple(fact for probe in probes for fact in probe.facts),
        assumptions=tuple(item for probe in probes for item in probe.assumptions),
        questions=tuple(questions.values()),
    )
