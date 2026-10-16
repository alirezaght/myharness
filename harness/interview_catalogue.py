"""Interview question catalogue: concrete questions per SPEC §3 Phase 2 topic, never templated."""

from collections.abc import Iterable
from dataclasses import dataclass

from harness.classification import INTERVIEW_TOPICS
from harness.state_models import QuestionKind

Q, D = QuestionKind.QUESTION, QuestionKind.DECISION


@dataclass(frozen=True)
class CatalogueQuestion:
    key: str
    topic: str
    text: str
    kind: QuestionKind


_ENTRIES: tuple[tuple[str, str, QuestionKind, str], ...] = (
    ("business_requirements", "goal", Q, "What business problem does this solve, and for whom?"),
    ("business_requirements", "scope", D, "What is explicitly out of scope for this change?"),
    ("functional_behavior", "success_path", Q, "What exactly should happen on the success path?"),
    ("functional_behavior", "existing", D, "How should existing behaviour change, if at all?"),
    ("inputs_outputs", "inputs", Q, "Which inputs does it take, with which formats and limits?"),
    ("inputs_outputs", "outputs", D, "Which outputs or side effects does it produce?"),
    ("edge_cases", "boundaries", Q, "Which boundary or empty/oversized inputs must be handled?"),
    ("edge_cases", "conflicts", D, "How should concurrent or conflicting operations behave?"),
    (
        "component_responsibilities",
        "owner",
        D,
        "Which component owns this behaviour, and what must it not do?",
    ),
    ("component_responsibilities", "new", Q, "Does this need a new component or module?"),
    (
        "component_communication",
        "mechanism",
        D,
        "How do the involved components communicate (calls, events, queues)?",
    ),
    ("component_communication", "ordering", Q, "Are there ordering or timing requirements?"),
    ("data_schemas", "change", D, "Which data schemas or stored formats change, if any?"),
    ("data_schemas", "migration", Q, "How must existing data be migrated or stay compatible?"),
    ("api_contracts", "change", D, "Which API contracts are added or changed?"),
    ("api_contracts", "compatibility", Q, "Which existing callers must keep working unchanged?"),
    ("configuration", "settings", Q, "Which settings are needed, with which defaults?"),
    ("configuration", "flags", D, "Should the change be behind a feature flag or switch?"),
    ("error_handling", "policy", D, "How should failures be handled (retry, fail fast, degrade)?"),
    ("error_handling", "messages", Q, "What should the user or caller see when it fails?"),
    ("performance", "targets", Q, "Which latency, throughput or size targets apply?"),
    ("performance", "limits", D, "Which limits, caching or batching should be applied?"),
    ("security", "authz", D, "Who is allowed to do this, and how is it enforced?"),
    ("security", "data", Q, "Which sensitive data is touched, and how must it be protected?"),
    ("observability", "signals", Q, "Which logs, metrics or traces are required?"),
    ("observability", "alerts", D, "Which failures should alert, and whom?"),
    ("acceptance_criteria", "list", Q, "What are the acceptance criteria for calling this done?"),
    ("acceptance_criteria", "verification", D, "How will each criterion be verified?"),
)

CATALOGUE: tuple[CatalogueQuestion, ...] = tuple(
    CatalogueQuestion(f"{topic}.{slug}", topic, text, kind)
    for topic, slug, kind, text in sorted(_ENTRIES, key=lambda e: INTERVIEW_TOPICS.index(e[0]))
)


def catalogue_for(topics: Iterable[str]) -> tuple[CatalogueQuestion, ...]:
    """Catalogue questions for the given topics, in INTERVIEW_TOPICS order."""
    selected = set(topics)
    return tuple(entry for entry in CATALOGUE if entry.topic in selected)
