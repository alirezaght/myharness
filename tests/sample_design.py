"""Test fixture: a sample design in diagram notation, taken from the approved Phase 2 spec.

Every notation line restates docs/specs/t-a3752edf/v1.md: Q9 (responsibilities), Q11
(communication), Q13 (storage, comment states) and E4 (data flow). One entry is a modified
recommendation, so its response (not its statement) is what counts.
"""

from harness.controller import HarnessController
from harness.db import transaction
from harness.design_models import DesignEntry, DesignSection, EntryStatus

COMPONENTS = """The Diagram Service owns diagram generation; the Web Dashboard owns review (Q9).
- component Web Dashboard: review, comments and approvals
- component Diagram Service: diagram generation and revision
- component Local Server: HTTP on localhost
- component Harness Engines: spec, decisions, review, approval
- component SQLite State: task state, diagram versions, comments"""

INTERACTIONS = """The browser talks to a local server over HTTP on localhost (Q11).
connect Browser -> Local Server: HTTP on localhost
connect Local Server -> Harness Engines: in-process
connect Local Server -> SQLite State: in-process
connect Local Server -> Browser: live updates
message Browser -> Local Server: read task, spec, decisions and diagrams
message Local Server -> Harness Engines: call in-process
message Harness Engines -> SQLite State: read and write task state
message Local Server -> Browser: push live update"""

DATA_FLOW_STATEMENT = "Design entries become a structured model (E4)."
DATA_FLOW = """Design entries become a structured model with stable ids (E4).
flow Design Entries -> Diagram Service: structured model with stable ids
flow Diagram Service -> Web Dashboard: versioned diagrams
flow Web Dashboard -> SQLite State: comments with element id and diagram version
connect Web Dashboard -> Diagram Service: reads versioned diagrams"""

COMMENT_STATES = """Comments move open, addressed, resolved and can be re-opened (Q13).
state Comment: open -> addressed: harness addresses it
state Comment: addressed -> resolved: developer resolves it
state Comment: resolved -> open: developer re-opens it"""

DEPLOYMENT = """Everything runs locally; the server calls engines and SQLite in-process (Q11).
deploy Local Server @ localhost
deploy Web Dashboard @ localhost
deploy Diagram Service @ localhost
deploy Harness Engines @ localhost
deploy SQLite State @ localhost
deploy Browser @ developer machine"""

TYPES = ["component", "sequence", "data_flow", "state", "deployment"]


def add_sample_design(controller: HarnessController, task_id: str) -> list[DesignEntry]:
    """State the sample design on the task (workspace methods do not check the phase)."""
    ws = controller.workspace(task_id)
    with transaction(controller.conn):
        entries = [
            ws.state_design(DesignSection.COMPONENTS, COMPONENTS),
            ws.state_design(DesignSection.INTERACTIONS, INTERACTIONS),
        ]
        proposed = ws.propose_design(DesignSection.DATA_FLOW, DATA_FLOW_STATEMENT, "E4")
        entries.append(ws.resolve_design(proposed.id, EntryStatus.MODIFIED, DATA_FLOW))
        entries.append(ws.state_design(DesignSection.REQUIRED_BEHAVIOUR, COMMENT_STATES))
        entries.append(ws.state_design(DesignSection.CONFIGURATION, DEPLOYMENT))
    return entries
