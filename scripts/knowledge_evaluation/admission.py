"""Ordinary Harness admission from a concrete supervisor-supplied case specification."""

import os
from contextlib import closing, contextmanager
from dataclasses import asdict
from pathlib import Path

from harness.analyzer import analyze
from harness.approval import ApprovalGate
from harness.controller import HarnessController
from harness.db import connect
from harness.design import ArchitectureDesigner
from harness.interview import InterviewEngine
from harness.phases import Phase
from harness.review import FinalReviewer
from harness.spec_store import SpecStore
from harness.testplan import TestPlanner

from .package import PROVENANCE, canonical, sha, text


@contextmanager
def environment(state):
    previous = os.environ.get("HARNESS_HOME")
    os.environ["HARNESS_HOME"] = str(state / "harness-home")
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HARNESS_HOME", None)
        else:
            os.environ["HARNESS_HOME"] = previous


def admit(package):
    root = Path(package["corpus"]["root"])
    state = Path(package["state_directory"])
    supplied = package["specification"]
    request = supplied["request"] + "\n\n" + PROVENANCE
    request += "\nPrior authorization: " + str(package["provenance"])
    with environment(state), closing(connect(state / "harness.db")) as conn:
        controller = HarnessController(conn)
        # Do not assert a fabricated developer complexity choice.
        task = controller.create_task(request)
        report = analyze(root, supplied["request"])
        controller.record_investigation(task.id, report)
        controller.advance(task.id)
        engine = InterviewEngine(controller, task.id)
        while questions := engine.next(group=True):
            for question in questions:
                answer = supplied["answers"].get(question.text)
                if answer is None:
                    raise ValueError("case specification has no answer for: " + question.text)
                engine.answer(question.id, text(answer))
        if engine.checklist().blocking:
            raise ValueError("concrete interview is incomplete")
        controller.advance(task.id)
        designer = ArchitectureDesigner(controller, task.id)
        for section, value in supplied["design"].items():
            designer.add(section, value)
        if package["role"] == "al-engineer":
            designer.add(
                "required_behaviour",
                "Engineer evaluation task source scope: "
                + canonical(supplied["task_files"])
                + "; read-only operations only; no writer, task admission/start, "
                "dependency preparation or automatic assignment context.",
            )
        controller.advance(task.id)
        planner = TestPlanner(controller, task.id)
        requirements = [
            f"Q{r.id}" for r in controller.workspace(task.id).questions() if r.status == "answered"
        ]
        for scenario in supplied["scenarios"]:
            planner.add(scenario["category"], scenario["text"], covers=requirements)
        planner.approve()
        controller.advance(task.id)
        review = FinalReviewer(controller, task.id).report()
        if review.blocking:
            raise ValueError("ordinary final review refused the evaluation specification")
        controller.advance(task.id)
        if controller.get_task(task.id).phase != Phase.APPROVAL:
            raise ValueError("ordinary approval phase unavailable")
        store = SpecStore(state / "specs" / task.id)
        approval, sealed, _ = ApprovalGate(controller, task.id, store, root).approve()
        return {
            "task_id": task.id,
            "database": str(state / "harness.db"),
            "specs": str(state / "specs"),
            "approval": asdict(approval),
            "sealed_spec_sha256": sha(Path(sealed.path).read_bytes()),
            "review_state_hash": review.state_hash,
            "provenance": package["provenance"],
            "approval_actor": "supervisor under existing mission authorization",
            "new_user_approval_event": False,
        }
