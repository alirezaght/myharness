#!/usr/bin/env python3
"""AL phase vocabulary — the names, order, owning role and display label of
every phase in the AL state machine, the per-classification phase plan, and the
hard correction-iteration cap.

Kept in one leaf module so the state writer, the renderers and the status line
can never disagree about what a phase is called, who owns it, or whether the
current task's path runs it at all.
"""

MAX_CORRECTION_ITERATIONS = 6

MAIN_PHASES = ["PLANNING", "PSEUDOCODE", "IMPLEMENTATION", "FINALIZATION", "VERIFICATION", "REVIEW"]
AUX_PHASES = ["REWORK_REASSESSMENT", "PLAN_CONFLICT", "INVESTIGATION", "REWORK", "CORRECTION", "FINAL_VERIFICATION", "DONE"]
# Pre-parallelism phase names, still accepted so an in-flight run keeps working.
PHASE_ALIASES = {"CORE_IMPLEMENTATION": "IMPLEMENTATION", "TESTS_AND_CLEANUP": "FINALIZATION"}
# VERIFICATION is deliberately absent: AL runs the barrier itself, no agent owns it.
PHASE_AGENT = {
    "REWORK_REASSESSMENT": "al-architect",
    "PLANNING": "al-architect", "PSEUDOCODE": "al-designer", "PLAN_CONFLICT": "al-architect",
    "IMPLEMENTATION": "al-engineer", "FINALIZATION": "al-builder", "REVIEW": "al-architect",
}
PHASE_LABEL = {
    "REWORK_REASSESSMENT": "Rework reassessment",
    "PLANNING": "Planning", "PSEUDOCODE": "Design", "PLAN_CONFLICT": "Plan conflict",
    "INVESTIGATION": "Investigation", "IMPLEMENTATION": "Implementation",
    "FINALIZATION": "Builder finalization", "VERIFICATION": "Verification",
    "REVIEW": "Review", "REWORK": "Rework", "CORRECTION": "Correction", "FINAL_VERIFICATION": "Final verification",
    "DONE": "Done",
}
FANOUT_TITLE = {"investigation": "Investigation", "analysis": "Review analysis"}
FANOUT_FOOTER = {
    "investigation": "Read-only · running in parallel · findings return to AL-Architect",
    "analysis": "Read-only · running in parallel · AL-Architect alone issues STATUS",
}

CLASSIFICATIONS = ["SMALL", "STANDARD", "COMPLEX"]
# The named route AL prints and reports, per classification. `<designer>` and
# `<finalization>` are filled in from the actual routing decision.
PATH_TEMPLATE = {
    "SMALL": "Architect → {impl} → Verify → Architect Review",
    "STANDARD": "Architect → {impl} → Verify → Architect Review",
    "COMPLEX": "Architect → Designer → {impl} → Builder finalization → Verify → Architect Review",
}


def phase_plan(classification, designer):
    """Which MAIN_PHASES this task's path runs.

    Returns (required, optional, skipped). A phase in `skipped` is a deliberate
    routing decision, not an omission: SMALL and STANDARD tasks skip the Designer
    unless AL-Architect asked for one, and only COMPLEX tasks always need the
    Builder finalization barrier (elsewhere it runs only if work is left over).
    """
    required, optional, skipped = [], [], []
    for phase in MAIN_PHASES:
        if phase == "PSEUDOCODE":
            (required if designer else skipped).append(phase)
        elif phase == "FINALIZATION":
            (required if classification == "COMPLEX" else optional).append(phase)
        else:
            required.append(phase)
    return required, optional, skipped
