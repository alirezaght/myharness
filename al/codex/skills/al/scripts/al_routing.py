#!/usr/bin/env python3
"""AL routing — the task classification and the phase plan it implies.

`/al` means "use AL's quality controls", never "spend every agent on every
task". AL-Architect classifies the work SMALL / STANDARD / COMPLEX and says
whether pseudocode is genuinely needed; this module records that decision and
derives which phases the run will actually execute, so the renderers can show a
skipped stage as a deliberate route rather than a missing step.

The specification validity gate that runs before this lives in al_guards.py
(`spec-gate`), with the rest of the mechanical refusals.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_models  # noqa: E402
from al_phases import CLASSIFICATIONS, MAIN_PHASES, PATH_TEMPLATE, phase_plan  # noqa: E402
from al_store import load, save  # noqa: E402


def cmd_classify(a, sid):
    """Record the classification, the routing decision and the path AL will run."""
    st = load(sid)
    if a.classification not in CLASSIFICATIONS:
        sys.exit(f"al_state: classification must be one of {', '.join(CLASSIFICATIONS)}")
    from al_schema import refuse
    if st["phase"] != "PLANNING": refuse("classification can only change during planning")
    designer = a.designer == "yes"
    import al_artifacts as A
    package = A.read_json(st['task_package'])
    inherited = package.get('implementation_risk')
    override = package.get('routing_override')
    assessment = None
    if inherited and not getattr(a, 'assessment_file', None):
        import al_store
        from al_task_risk import assess
        assessment = inherited['assessment']
        try:
            expected = assess(assessment, al_store.REPO_ROOT)
        except (ValueError, OSError) as exc:
            refuse(str(exc))
        if not override and a.classification != expected:
            refuse('classification differs from current clarified Harness risk: '+expected)
    if override:
        if override.get('source') != 'explicit developer classification' or override.get('classification') != a.classification:
            refuse('classification differs from explicit developer routing')
    if getattr(a, 'assessment_file', None):
        import json
        import al_store
        from al_task_risk import assess
        assessment = json.loads(Path(a.assessment_file).read_bytes())
        try:
            expected = assess(assessment, al_store.REPO_ROOT)
        except (ValueError, OSError) as exc:
            refuse(str(exc))
        if not override and a.classification != expected:
            refuse('classification differs from inspected residual implementation risk: '+expected)
        if a.classification == 'COMPLEX' and not designer:
            refuse('COMPLEX requires a useful pseudocode step')
    if a.classification == "COMPLEX" and not designer: refuse("COMPLEX requires Designer")
    if a.classification == "SMALL" and len((a.implementers or "al-engineer").split(",")) != 1: refuse("SMALL requires one writer")
    _, optional, skipped = phase_plan(a.classification, designer)
    impl = [x.strip() for x in (a.implementers or "").split(",") if x.strip()]
    impl_label = " + ".join(al_models.display(i) for i in impl) or "Engineer/Builder"
    st["classification"] = a.classification
    st["classification_reason"] = a.reason or (assessment["reason"] if assessment else "")
    if assessment is not None:
        st["classification_assessment"] = assessment
    st["designer_required"] = designer
    st["designer_reason"] = a.designer_reason
    st["skipped_phases"] = skipped
    st["optional_phases"] = optional
    st["implementers"] = impl
    st["path"] = PATH_TEMPLATE[a.classification].format(impl=impl_label)
    st["completed_phases"] = [p for p in st["completed_phases"] if p not in skipped]
    st["pending_phases"] = [p for p in MAIN_PHASES
                            if p not in st["completed_phases"] and p not in skipped]
    save(sid, st)
    print(f"AL · Task classification: {a.classification}")
    if a.reason:
        print(f"Reason: {a.reason}")
    print(f"Path: {st['path']}")
    if not designer:
        print(f"AL · Designer skipped — {a.designer_reason or 'Architect plan is sufficiently concrete'}")
