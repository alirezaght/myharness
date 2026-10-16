"""Records persisted in a task workspace and their row mappers."""

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness.phases import Phase


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class QuestionSource(StrEnum):
    INVESTIGATION = "investigation"
    INTERVIEW = "interview"


class QuestionKind(StrEnum):
    QUESTION = "question"
    DECISION = "decision"


class SkipSource(StrEnum):
    DECISION = "decision"
    CODEBASE = "codebase"
    NOT_RELEVANT = "not_relevant"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"


@dataclass(frozen=True)
class Question:
    id: int
    task_id: str
    topic: str
    text: str
    source: QuestionSource
    status: QuestionStatus
    answer: str | None
    skip_reason: str | None
    created_at: str
    updated_at: str
    kind: QuestionKind = QuestionKind.QUESTION
    asked_at: str | None = None
    skip_source: SkipSource | None = None
    catalogue_key: str | None = None


@dataclass(frozen=True)
class Proposal:
    id: int
    task_id: str
    question_id: int | None
    title: str
    recommendation: str
    rationale: str
    status: ProposalStatus
    response: str | None
    created_at: str
    resolved_at: str | None
    impact: str | None = None


@dataclass(frozen=True)
class Event:
    id: int
    task_id: str
    kind: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class Checkpoint:
    id: int
    task_id: str
    phase: Phase
    label: str
    payload: dict[str, Any]
    created_at: str


def question_from_row(row: sqlite3.Row) -> Question:
    return Question(
        id=row["id"],
        task_id=row["task_id"],
        topic=row["topic"],
        text=row["text"],
        source=QuestionSource(row["source"]),
        status=QuestionStatus(row["status"]),
        answer=row["answer"],
        skip_reason=row["skip_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        kind=QuestionKind(row["kind"]),
        asked_at=row["asked_at"],
        skip_source=SkipSource(row["skip_source"]) if row["skip_source"] else None,
        catalogue_key=row["catalogue_key"],
    )


def proposal_from_row(row: sqlite3.Row) -> Proposal:
    return Proposal(
        id=row["id"],
        task_id=row["task_id"],
        question_id=row["question_id"],
        title=row["title"],
        recommendation=row["recommendation"],
        rationale=row["rationale"],
        status=ProposalStatus(row["status"]),
        response=row["response"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        impact=row["impact"],
    )


def event_from_row(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        task_id=row["task_id"],
        kind=row["kind"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
    )


def checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        id=row["id"],
        task_id=row["task_id"],
        phase=Phase(row["phase"]),
        label=row["label"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
    )
