"""Current source-acquisition eligibility; retained history grants no fresh authority."""

from harness.authoring_contract import AuthoringConflict
from harness.phases import Phase


def require_eligible(service):
    service._refuse_execution()
    if service.controller.get_task(service.task_id).phase not in {
        Phase.INVESTIGATION,
        Phase.INTERVIEW,
        Phase.DESIGN,
        Phase.TEST_PLAN,
        Phase.FINAL_REVIEW,
    }:
        raise AuthoringConflict(
            "Source refresh requires an explicit planning back operation to interview "
            "with a reason, or specification reconciliation, before leaving approval"
        )
