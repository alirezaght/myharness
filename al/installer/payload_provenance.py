"""Explicit tagged/workspace snapshot discrimination without provenance coercion."""

from .manifest import verify_cache
from .workspace_payload import validate_payload, verify_payload


def payload_kind(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("missing installation payload provenance")
    if "kind" not in manifest:
        return "tagged"
    if manifest["kind"] == "approved-workspace-payload-v1":
        validate_payload(manifest)
        return "workspace"
    raise ValueError("unknown installation payload provenance")


def display_version(manifest):
    if payload_kind(manifest) == "workspace":
        return "workspace-" + manifest["source_content_digest"][:12]
    return manifest["version"]


def verify_expected(cache, expected):
    kind = payload_kind(expected)
    actual = (
        verify_payload(cache, expected["digest"])
        if kind == "workspace"
        else verify_cache(cache, expected["digest"])
    )
    if actual != expected:
        raise ValueError("snapshot payload identity mismatch")
    return actual
