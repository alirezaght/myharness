"""Immutable public knowledge contracts. These describe context, never execution authority."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECTION_CONTRACT_VERSION = 2
PROJECTION_READER_NOTICE = (
    "This project's knowledge projection requires the current reader. "
    "The pinned execution remains authorized; its older knowledge capability is unavailable."
)
EXTRACTOR_VERSION = "2"
MAX_SOURCE_BYTES = 1_048_576
MAX_REFRESH_BYTES = 67_108_864


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity(*parts: str) -> str:
    return digest(canonical(parts).encode())


def projection_identity(repository: Path) -> str:
    """Version the derived projection contract without rekeying project/history/authority IDs.

    Older sealed readers require a raw path identity, and refuse this representation.
    Their execution remains authorized; only incompatible knowledge access is unavailable.
    """
    return canonical(
        ["harness-knowledge-projection", PROJECTION_CONTRACT_VERSION, str(repository.resolve())]
    )


@dataclass(frozen=True)
class KnowledgeScope:
    project_id: str
    repository: Path
    principal_id: str
    purpose: str = "operator"
    task_id: str | None = None
    execution_id: str | None = None
    epoch_id: str | None = None
    authority_generation: int = 0
    policy_generation: int = 0
    role: str = "operator"
    permitted_source_versions: frozenset[str] | None = None
    packet_hash: str | None = None
    snapshot_id: str | None = None
    permitted_digest: str | None = None
    review_id: str | None = None


@dataclass(frozen=True)
class RefreshRequest:
    max_files: int = 10000
    max_file_bytes: int = MAX_SOURCE_BYTES
    included_paths: tuple[str, ...] = ()
    commit: str | None = None


@dataclass(frozen=True)
class SourceVersion:
    source_id: str
    version_id: str
    locator: str
    kind: str
    content_sha256: str
    raw: bytes
    observation: dict
    material_class: str = "repository"
    visibility: str = "project"


@dataclass(frozen=True)
class SourceInventory:
    sources: tuple[SourceVersion, ...]
    complete: bool
    coverage: tuple[dict, ...]
    observation: dict
    names: frozenset[str] | None = None
    names_complete: bool = True


@dataclass(frozen=True)
class Assertion:
    subject: str
    predicate: str
    object: str | None
    value: Any
    text: str
    start_byte: int
    end_byte: int
    origin: str = "structured"
    verification: str = "source-backed"
    confidence: float = 1.0
    applicability: dict = field(default_factory=dict)
    derivation_parents: tuple[str, ...] = ()


@dataclass(frozen=True)
class Extraction:
    assertions: tuple[Assertion, ...]
    coverage: tuple[dict, ...] = ()


@dataclass(frozen=True)
class PreparedSnapshot:
    scope: KnowledgeScope
    inventory: SourceInventory
    extractions: tuple[Extraction, ...]
    expected_policy_generation: int
    unknown: tuple[dict, ...] = ()
    names: frozenset[str] | None = None
    names_complete: bool = True
    cursor: dict | None = None
    rows: tuple | None = None
    reused: frozenset[str] = frozenset()
    carry_forward: bool = False
    contribution: tuple | None = None


@dataclass(frozen=True)
class RefreshResult:
    snapshot_id: str | None
    status: str
    coverage: tuple[dict, ...]


@dataclass(frozen=True)
class QueryLimits:
    bytes: int = 32768
    assertions: int = 40
    hops: int = 2
    examined_edges: int = 200

    def validate(self) -> None:
        if not (
            1024 <= self.bytes <= 32768
            and 1 <= self.assertions <= 40
            and 0 <= self.hops <= 2
            and 0 <= self.examined_edges <= 200
        ):
            raise ValueError("knowledge limits exceed supported bounds")


@dataclass(frozen=True)
class ContextRequest:
    query: str = ""
    entity_refs: tuple[str, ...] = ()
    as_of_snapshot: str | None = None
    limits: QueryLimits = field(default_factory=QueryLimits)


@dataclass(frozen=True)
class ContextBundle:
    bundle_hash: str
    payload: dict

    def to_dict(self) -> dict:
        return {"bundle_hash": self.bundle_hash, **self.payload}
