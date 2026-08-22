from __future__ import annotations

from application_orchestrator.models import (
    ApplicationSession,
    ArtifactReference,
    PendingQuestion,
    SessionEvent,
    SessionStatus,
)
from application_orchestrator.store import SessionStore


class SessionOrchestrator:
    def __init__(self, store: SessionStore, *, default_user_id: str = "local-user") -> None:
        self.store = store
        self.default_user_id = default_user_id

    def create_session(self, job_url: str, *, user_id: str | None = None) -> ApplicationSession:
        session = ApplicationSession(job_url=job_url, user_id=user_id or self.default_user_id)
        return self.store.create_session(session)

    def record_job_parse(self, session_id: str, job_snapshot: dict) -> ApplicationSession:
        session = self.store.update_job_snapshot(session_id, job_snapshot)
        self.store.append_event(
            session_id,
            SessionEvent(event_type="job_parsed", payload={"keys": sorted(job_snapshot.keys())}),
        )
        return session

    def record_resume_artifact(self, session_id: str, artifact: ArtifactReference) -> ApplicationSession:
        session = self.store.set_resume_artifact(session_id, artifact)
        self.store.append_event(
            session_id,
            SessionEvent(event_type="resume_generated", payload={"artifact_id": artifact.artifact_id}),
        )
        return session

    def pause_for_question(
        self,
        session_id: str,
        *,
        field_key: str,
        prompt: str,
        metadata: dict | None = None,
    ) -> PendingQuestion:
        question = PendingQuestion(field_key=field_key, prompt=prompt, metadata=metadata or {})
        self.store.create_pending_question(session_id, question)
        self.store.append_event(
            session_id,
            SessionEvent(event_type="question_asked", payload={"question_id": question.question_id, "field_key": field_key}),
        )
        return question

    def resume_with_answer(self, session_id: str, *, question_id: str, answer: str) -> PendingQuestion:
        question = self.store.resolve_pending_question(session_id, question_id, answer)
        self.store.append_event(
            session_id,
            SessionEvent(event_type="question_answered", payload={"question_id": question_id, "field_key": question.field_key}),
        )
        return question

    def set_ready_for_submit(self, session_id: str) -> ApplicationSession:
        session = self.store.set_status(session_id, SessionStatus.READY_FOR_SUBMIT, "ready_for_submit")
        self.store.append_event(session_id, SessionEvent(event_type="ready_for_submit"))
        return session

    def mark_submitted(self, session_id: str) -> ApplicationSession:
        session = self.store.set_status(session_id, SessionStatus.SUBMITTED, "submitted")
        self.store.append_event(session_id, SessionEvent(event_type="submitted"))
        return session

    def mark_failed(self, session_id: str, reason: str) -> ApplicationSession:
        session = self.store.set_status(session_id, SessionStatus.FAILED, "failed")
        self.store.append_event(session_id, SessionEvent(event_type="failed", payload={"reason": reason}))
        return session
