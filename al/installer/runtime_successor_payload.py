"""Resolve immutable successor bytes only through protected transaction references."""

from .runtime_pin import verify_pin
from .runtime_pin_transaction import storage


def requested_distribution(platform, request, current, *, predecessor, reservation=None):
    current_pin, current_verified = current
    candidates = [reservation] if reservation is not None else [predecessor]
    for transaction in candidates:
        if transaction["platform"] != platform:
            raise ValueError("requested successor platform differs")
        pin = transaction["pin"]
        verified = verify_pin(storage() / "pins", pin)
        manifest = verified["manifest"]
        if manifest["platform"] != platform:
            raise ValueError("requested successor pin platform differs")
        if manifest["closure_digest"] == request["distribution_identity"]:
            if manifest["interpreter"] != current_verified["manifest"]["interpreter"]:
                raise ValueError("requested successor interpreter differs")
            return pin, verified
        if reservation is not None:
            raise ValueError("reserved successor payload differs")
    if current_verified["manifest"]["closure_digest"] == request["distribution_identity"]:
        return current_pin, current_verified
    raise ValueError("requested successor payload unavailable through protected references")
