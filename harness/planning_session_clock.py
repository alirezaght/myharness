"""Validated boot continuity; failed observations are operational, never authority."""

import ctypes
import errno
import hashlib
import platform
import uuid
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

REASONS = frozenset(
    {"permission_denied", "unsupported_platform", "invalid_observation", "read_failed"}
)


@dataclass(frozen=True)
class BootObservation:
    identity: str | None = field(repr=False)
    reason: str | None = None


class _Unavailable(Exception):
    def __init__(self, reason):
        self.reason = reason


def _failed(error):
    return BootObservation(
        None, "permission_denied" if error in {errno.EPERM, errno.EACCES} else "read_failed"
    )


def _read_observation():
    try:
        system = platform.system()
        if system == "Linux":
            with Path("/proc/sys/kernel/random/boot_id").open() as stream:
                value = stream.read(64).strip()
            try:
                if str(uuid.UUID(value)) != value:
                    return BootObservation(None, "invalid_observation")
            except ValueError:
                return BootObservation(None, "invalid_observation")
        elif system == "Darwin":

            class Timeval(ctypes.Structure):
                _fields_ = [("seconds", ctypes.c_long), ("microseconds", ctypes.c_int)]

            libc = ctypes.CDLL(None, use_errno=True)
            syscall = libc.sysctlbyname
            syscall.argtypes = [
                ctypes.c_char_p,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_void_p,
                ctypes.c_size_t,
            ]
            syscall.restype = ctypes.c_int
            observed = Timeval()
            length = ctypes.c_size_t(ctypes.sizeof(observed))
            ctypes.set_errno(0)
            code = syscall(b"kern.boottime", ctypes.byref(observed), ctypes.byref(length), None, 0)
            if code != 0:
                error = ctypes.get_errno()
                return _failed(error)
            if (
                length.value != ctypes.sizeof(observed)
                or observed.seconds <= 0
                or not 0 <= observed.microseconds < 1000000
            ):
                return BootObservation(None, "invalid_observation")
            value = str(observed.seconds) + ":" + str(observed.microseconds)
        else:
            return BootObservation(None, "unsupported_platform")
        return BootObservation(hashlib.sha256(value.encode()).hexdigest())
    except OSError as error:
        return _failed(error.errno)
    except (AttributeError, ValueError, UnicodeError):
        return BootObservation(None, "read_failed")


@cache
def _validated_observation():
    observation = _read_observation()
    if observation.identity is None:
        raise _Unavailable(observation.reason)
    return observation


def boot_observation():
    try:
        return _validated_observation()
    except _Unavailable as error:
        return BootObservation(None, error.reason)


def boot_identity():
    return boot_observation().identity


def unavailable_error(observation):
    from harness.planning_session_contract import SessionError

    return SessionError("SESSION_CLOCK_UNAVAILABLE", reason=observation.reason)


def require_boot_identity(observation=None):
    observation = observation if observation is not None else boot_observation()
    if observation.identity is None:
        raise unavailable_error(observation)
    return observation.identity


def public_unavailable(observation):
    return {
        "operation": "read_host_boot_identity",
        "reason": observation.reason,
        "permissionRetry": observation.reason == "permission_denied",
    }
