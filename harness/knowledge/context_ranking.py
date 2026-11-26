"""Pure bounded relevance and presentation helpers; inferred relevance is not authority."""

import re
from collections import defaultdict, deque
from pathlib import PurePosixPath

from harness.knowledge.models import canonical

RANKING_VERSION = "task-context-3"
STAGE_PREDICATES = {
    "investigation": ("defines", "references", "imports"),
    "planning": ("governed-by", "designed-by", "imports", "references"),
    "implementation": ("decision", "status", "defines", "binding", "imports"),
    "verification": ("governed-by", "defines", "references", "imports"),
    "recovery": ("event-kind", "observed-status", "observed-at", "phase"),
}


def tokens(request):
    return sorted(set(re.findall(r"[\w.-]+", (request.query + " " + request.objective).lower())))[
        :64
    ]


def affected_paths(request):
    paths = []
    for ref in request.affected_entities:
        if ref.startswith("file:"):
            path = PurePosixPath(ref[5:])
            if not path.is_absolute() and ".." not in path.parts and path.as_posix() == ref[5:]:
                paths.append(path)
    return tuple(paths)


def guidance_scope(locator, request):
    path = PurePosixPath(locator)
    if path.name not in {"AGENTS.md", "CLAUDE.md"}:
        return False
    return any(
        path.parent == target.parent or path.parent in target.parents
        for target in affected_paths(request)
    )


def reasons(row, request, locators=()):
    result = []
    if {row["subject"], row.get("object")} & set(request.affected_entities):
        result.append("exact-affected-entity")
    if set(locators) & {str(path) for path in affected_paths(request)}:
        result.append("exact-affected-source")
    if any(guidance_scope(name, request) for name in locators):
        result.append("directory-guidance-candidate")
    if row["predicate"] in STAGE_PREDICATES.get(request.stage, ()):
        result.append("stage-predicate")
    text = (request.query + " " + request.objective).lower()
    if any(word in text for word in ("test", "regression", "verification")) and any(
        PurePosixPath(name).name.startswith("test_") or "tests" in PurePosixPath(name).parts
        for name in locators
    ):
        result.append("test-focused-objective")
    return result


def name_relevance(row, request):
    """Symbol-name hints, including approximate word forms; never behavioral coverage."""
    if row["predicate"] != "defines" or not row.get("object"):
        return 0
    words = set(re.findall(r"[a-z0-9]+", row["object"].rsplit(":", 1)[-1].lower()))
    wanted = set(re.findall(r"[a-z0-9]+", (request.query + " " + request.objective).lower()))
    wanted -= {"test", "tests", "the", "and", "for", "with", "from"}
    return sum(
        any(
            term == word or (len(term) >= 5 and len(word) >= 5 and term[:4] == word[:4])
            for word in words
        )
        for term in wanted
    )


def diverse_sources(ordered, located):
    groups = defaultdict(deque)
    for aid in ordered:
        groups[tuple(sorted(located.get(aid, ())))].append(aid)
    result = []
    while groups:
        for source in list(groups):
            result.append(groups[source].popleft())
            if not groups[source]:
                del groups[source]
    return result


def rank_key(row, request, locators=()):
    why = reasons(row, request, locators)
    text = f"{row['subject']} {row.get('object') or ''} {row['text']}".lower()
    priorities = STAGE_PREDICATES.get(request.stage, ())
    return (
        -int(row.get("_reference_definition", False)),
        -int(row.get("_resolved_reference", False) or "exact-affected-entity" in why),
        -int("directory-guidance-candidate" in why),
        -int("test-focused-objective" in why),
        -int("exact-affected-source" in why),
        -int(request.stage == "implementation" and row.get("_approved_decision", False)),
        -int(
            row["predicate"] == "defines"
            and ("test-focused-objective" in why or "exact-affected-source" in why)
        ),
        -name_relevance(row, request),
        -sum(token in text for token in tokens(request)),
        priorities.index(row["predicate"]) if row["predicate"] in priorities else len(priorities),
        row["assertion_id"],
    )


def ranked_candidates(rows, request, located, exclusions=None):
    ordered = sorted(rows, key=lambda aid: rank_key(rows[aid], request, located.get(aid, ())))
    tests, support, prior, other = [], [], [], []
    for aid in ordered:
        row = rows[aid]
        names = located.get(aid, ())
        why = reasons(row, request, names)
        if (
            affected_paths(request)
            and names
            and all(PurePosixPath(name).name in {"AGENTS.md", "CLAUDE.md"} for name in names)
            and "directory-guidance-candidate" not in why
            and not row.get("_resolved_reference")
            and "exact-affected-entity" not in why
        ):
            if exclusions is not None:
                exclusions["guidance-locator-outside-preferred-directories"] += 1
            continue
        if (
            row.get("_resolved_reference")
            or "exact-affected-entity" in why
            or "directory-guidance-candidate" in why
        ):
            prior.append(aid)
        elif "test-focused-objective" in why:
            tests.append(aid)
        elif "exact-affected-source" in why:
            support.append(aid)
        else:
            other.append(aid)
    # A test objective needs both check evidence and the affected implementation.
    # Reserve no assertions: alternate bounded candidates, leaving normal packing intact.
    if tests and support:
        tests = diverse_sources(tests, located)
        mixed = []
        while tests or support:
            mixed.extend(tests[:2])
            del tests[:2]
            mixed.extend(support[:1])
            del support[:1]
        return prior + mixed + other
    return prior + tests + support + other


def presentation(payload, request):
    """An additive deduplicated view; canonical assertions/citations stay lossless."""
    locators = {}
    for citation in payload["citations"]:
        locators.setdefault(citation["assertion_id"], set()).add(citation["locator"])
    groups, selection = {}, []
    recorded = {}
    for fact in payload["assertions"]:
        if (
            fact["subject"].startswith("decision:")
            and fact["predicate"] == "status"
            and not fact.get("historical", False)
        ):
            recorded.setdefault(fact["subject"], []).append(fact)
    approvals = {
        subject: {"assertion_id": facts[0]["assertion_id"], "state": "recorded-approved"}
        for subject, facts in recorded.items()
        if {fact["value"] for fact in facts} == {"approved"}
    }
    for assertion in payload["assertions"]:
        aid = assertion["assertion_id"]
        names = sorted(locators.get(aid, ()))
        why = reasons(assertion, request, names)
        if name_relevance(assertion, request):
            why.append("approximate-symbol-name-match")
        selection.append(
            {
                "assertion_id": aid,
                "reasons": why or ["bounded-lexical-or-typed-evidence"],
                "evidence_state": assertion["verification"],
                "approval_reference": approvals.get(assertion["subject"]),
            }
        )
        guidance = [name for name in names if guidance_scope(name, request)]
        if assertion["predicate"] == "described-by" and guidance:
            # Group only identical text AND actual recorded applicability. Keep every scope.
            key = (assertion["text"], canonical(assertion["applicability"]))
            group = groups.setdefault(key, {"text": assertion["text"], "members": []})
            group["members"].append(
                {
                    "assertion_id": aid,
                    "locators": guidance,
                    "applicability": assertion["applicability"],
                }
            )
    return {
        "selection": selection,
        "guidance_presentation": [groups[key] for key in sorted(groups)],
        "presentation_only": True,
        "uncertainty": [
            "Directory guidance relevance uses locator ancestry, "
            "not an extracted applicability edge."
        ]
        if groups
        else [],
    }


def fit_context(payload, request, fit, limit, exclusions, units=None):
    # Bytes/4 with a 50% safety margin is a heuristic, never a tokenizer upper bound.
    effective = min(limit, request.estimated_tokens * 8 // 3 if request.estimated_tokens else limit)
    if request.estimated_tokens is not None:
        payload["budget"]["effective_max_bytes"] = effective

    def refresh():
        if (
            request.stage
            or request.objective
            or request.affected_entities
            or request.estimated_tokens
        ):
            payload["context"]["presentation"] = presentation(payload, request)

    fit(payload, effective, exclusions, units, refresh=refresh)
