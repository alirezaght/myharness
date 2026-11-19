"""Closed native diagnostics; provider prose and rejected values never become messages."""

CATALOG = {
    "NATIVE_USAGE_LIMIT": (
        "Provider reported a usage limit. Stop this attempt; check availability through the "
        "native application before an explicitly authorized retry."
    ),
    "NATIVE_AUTHENTICATION_REQUIRED": (
        "Native authentication failed. Stop; resolve through the existing native account workflow."
    ),
    "NATIVE_PERMISSION_DENIED": (
        "Native permission was denied. Stop; inspect the permitted target and existing "
        "account permissions."
    ),
    "NATIVE_PROVIDER_FAILED": (
        "Provider reported failure. Inspect native application status; this attempt did not "
        "establish success."
    ),
    "NATIVE_STREAM_INVALID": (
        "Native protocol input was malformed, oversized or incomplete. Preserve the failed "
        "attempt and inspect installed-host compatibility."
    ),
    "NATIVE_STDERR_SUPPRESSED": (
        "Native stderr was emitted; content was not retained. Inspect execution status and "
        "fixed failure diagnostics."
    ),
    "NATIVE_SETUP_FAILED": (
        "Inspect current launch readiness; startup failed before native spawn."
    ),
    "NATIVE_SPAWN_FAILED": (
        "Native process creation failed; inspect local executable and execution prerequisites."
    ),
    "WORKER_SPAWN_FAILED": ("Managed worker creation failed; inspect local worker prerequisites."),
    "NATIVE_SUPERVISION_FAILED": (
        "Native supervision failed; inspect recorded lifecycle and current readiness."
    ),
    "NATIVE_REPLAY_FAILED": (
        "Guarded execution-state replay failed; preserve the attempt and inspect "
        "authority/runtime readiness."
    ),
    "NATIVE_SETTLEMENT_UNAVAILABLE": (
        "Native settlement could not be established; do not infer closure or retry safety."
    ),
}


def message(code):
    if not isinstance(code, str) or code not in CATALOG:
        raise ValueError("unknown native diagnostic code")
    return "[" + code + "] " + CATALOG[code]


def preserve(value, fallback="NATIVE_SUPERVISION_FAILED"):
    """Only exact authored messages survive; do not stringify arbitrary exceptions."""
    if isinstance(value, BaseException):
        value = value.args[0] if len(value.args) == 1 else None
    if isinstance(value, str) and value in {message(code) for code in CATALOG}:
        return value
    return message(fallback)


def startup(code):
    return "Startup failed before native spawn: " + message(code)


def failure_code(identifiers, texts):
    """Transient bounded classification only inside an established failure envelope."""
    groups = (
        (
            "NATIVE_USAGE_LIMIT",
            {"rate_limit", "rate_limit_error", "usage_limit", "quota_exceeded"},
            ("usage limit", "rate limit", "hit your 5-hour limit"),
        ),
        (
            "NATIVE_AUTHENTICATION_REQUIRED",
            {"authentication_error", "authentication_required", "unauthorized", "invalid_api_key"},
            (
                "authentication required",
                "authentication failed",
                "invalid api key",
                "not authenticated",
                "login required",
                "please log in",
            ),
        ),
        (
            "NATIVE_PERMISSION_DENIED",
            {"permission_denied", "permission_error", "forbidden"},
            ("permission denied", "access denied", "insufficient permissions"),
        ),
    )
    for code, allowed, _ in groups:
        if any(isinstance(value, str) and value in allowed for value in identifiers):
            return code
    for value in texts:
        if isinstance(value, str):
            bounded = value[:4096].lower()
            for code, _, markers in groups:
                if any(marker in bounded for marker in markers):
                    return code
    return "NATIVE_PROVIDER_FAILED"


STAGES = frozenset(
    {
        "supervision",
        "failure-signal",
        "failure-wait",
        "final-signal",
        "final-wait",
        "runtime-close",
        "closure-record",
        "source-replay",
        "status-publication",
        "receipt-publication",
    }
)
CATEGORIES = frozenset({"os-error", "timeout", "closure-refused", "unexpected-error"})


class NativeStageFailure(RuntimeError):
    """Internal closed failure carrier; never retain the originating exception."""

    def __init__(self, receipt):
        fields = {
            "kind",
            "execution_id",
            "attempt_id",
            "failures",
            "returncode_known",
            "closure_validated",
            "status_publication_reached",
        }
        if not isinstance(receipt, dict) or set(receipt) != fields:
            raise ValueError("closed native stage receipt required")
        if receipt["kind"] != "native-failure-stage-v1":
            raise ValueError("closed native stage receipt required")
        for field in ("execution_id", "attempt_id"):
            value = receipt[field]
            if field == "attempt_id" and value is None:
                continue
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 128
                or not value.isascii()
                or not all(ch.isalnum() or ch in "_-" for ch in value)
            ):
                raise ValueError("closed native stage identity required")
        for field in ("returncode_known", "closure_validated", "status_publication_reached"):
            if type(receipt[field]) is not bool:
                raise ValueError("closed native stage observation required")
        failures = receipt["failures"]
        if not isinstance(failures, list) or not 1 <= len(failures) <= 32:
            raise ValueError("bounded native stage failures required")
        for item in failures:
            if not isinstance(item, dict) or set(item) != {"stage", "category", "diagnostic"}:
                raise ValueError("closed native stage failure required")
            if (
                not isinstance(item["stage"], str)
                or item["stage"] not in STAGES
                or not isinstance(item["category"], str)
                or item["category"] not in CATEGORIES
                or not isinstance(item["diagnostic"], str)
                or item["diagnostic"] not in CATALOG
            ):
                raise ValueError("closed native stage failure required")
        self.receipt = dict(receipt, failures=[dict(item) for item in failures])
        super().__init__(message("NATIVE_SETTLEMENT_UNAVAILABLE"))
