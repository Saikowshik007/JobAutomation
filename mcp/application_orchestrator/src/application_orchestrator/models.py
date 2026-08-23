from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class SessionStatus(StrEnum):
    CREATED = "created"
    JOB_PARSED = "job_parsed"
    RESUME_GENERATED = "resume_generated"
    APPLICATION_STARTED = "application_started"
    WAITING_FOR_USER = "waiting_for_user"
    READY_FOR_SUBMIT = "ready_for_submit"
    SUBMITTED = "submitted"
    FAILED = "failed"


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"


class ArtifactKind(StrEnum):
    RESUME_PDF = "resume_pdf"
    RESUME_YAML = "resume_yaml"
    SCREENSHOT = "screenshot"
    OTHER = "other"


class AgentPhase(StrEnum):
    RESUME = "resume"
    APPLICATION = "application"


class PendingQuestion(StrictModel):
    question_id: str = Field(default_factory=lambda: uuid4().hex)
    field_key: str
    prompt: str
    status: QuestionStatus = QuestionStatus.OPEN
    answer: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    asked_at: datetime = Field(default_factory=utc_now)
    answered_at: datetime | None = None


class ArtifactReference(StrictModel):
    artifact_id: str = Field(default_factory=lambda: uuid4().hex)
    kind: ArtifactKind = ArtifactKind.OTHER
    path: str
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class SessionEvent(StrictModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ApplicationSession(StrictModel):
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    job_url: str
    status: SessionStatus = SessionStatus.CREATED
    current_step: str = "created"
    workflow_name: str | None = None
    workflow_version: str | None = None
    current_action: str | None = None
    action_payload: dict[str, Any] = Field(default_factory=dict)
    last_checkpoint: str | None = None
    user_id: str = "local-user"
    external_thread_id: str | None = None
    telegram_chat_id: str | None = None
    active_codex_phase: AgentPhase | None = None
    resume_codex_thread_id: str | None = None
    application_codex_thread_id: str | None = None
    job_snapshot: dict[str, Any] = Field(default_factory=dict)
    known_answers: dict[str, str] = Field(default_factory=dict)
    pending_question_id: str | None = None
    resume_artifact: ArtifactReference | None = None
    attempt_count: int = 0
    error_context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
