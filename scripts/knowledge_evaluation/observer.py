"""Native observation is disabled until a safe qualified source is approved."""

UNAVAILABLE = "qualified native observer unavailable"


def records(native_root, identity, *, require_complete=False):
    """Legacy entry point refuses without opening any host session records."""
    raise ValueError(UNAVAILABLE)


def observe_return(
    native_root, root_id, child_id, role, *, expected_wait=None, expected_profile=None
):
    """No transcript-derived return, settlement, or clean-session authority."""
    raise ValueError(UNAVAILABLE)
