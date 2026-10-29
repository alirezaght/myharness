from harness.dashboard.diagrams_view import diagrams_section
from harness.design import ArchitectureDesigner
from harness.design_models import DesignSection, EntryStatus
from harness.diagrams import DiagramService
from harness.phases import Phase


def test_prose_proposal_effective_and_sealed_guidance(controller):
    task = controller.create_task("Explain diagram provenance")
    controller.advance(task.id)
    controller.advance(task.id)
    designer = ArchitectureDesigner(controller, task.id)
    designer.add(DesignSection.COMPONENTS, "The dashboard talks to its local service.")
    view = diagrams_section(controller, controller.get_task(task.id), None)
    assert view["state"] == "prose_only" and view["current"] is None
    assert "independently of AL" in view["provenance"]
    proposal = designer.propose(
        DesignSection.COMPONENTS,
        "component Dashboard\ncomponent Service\nconnect Dashboard -> Service",
        "Make the existing design visible",
    )
    view = diagrams_section(controller, controller.get_task(task.id), None)
    assert view["state"] == "proposed" and view["current"] is None
    assert view["proposals"][0]["status"] == "proposed" and view["proposals"][0]["diagrams"]
    assert DiagramService(controller, task.id).versions() == []
    designer.decide(proposal.id, EntryStatus.ACCEPTED, None)
    DiagramService(controller, task.id).sync()
    later = designer.propose(
        DesignSection.COMPONENTS, "component ProposedLater", "A pending design change"
    )
    view = diagrams_section(controller, controller.get_task(task.id), None)
    assert view["state"] == "effective" and view["current"]["version"] == 1
    assert view["proposals"][0]["entry_id"] == later.id
    controller.conn.execute("UPDATE tasks SET phase=? WHERE id=?", (str(Phase.APPROVAL), task.id))
    before = controller.conn.total_changes
    view = diagrams_section(controller, controller.get_task(task.id), None)
    assert (
        view["design_revision_required"]
        and "sealed specification is not changed" in view["guidance"]
    )
    assert controller.conn.total_changes == before
