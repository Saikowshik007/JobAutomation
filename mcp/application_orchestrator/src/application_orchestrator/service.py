from __future__ import annotations

from typing import Any

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

    def checkpoint(
        self,
        session_id: str,
        *,
        current_step: str,
        current_action: str | None = None,
        last_checkpoint: str | None = None,
        workflow_name: str | None = None,
        workflow_version: str | None = None,
        action_payload: dict[str, Any] | None = None,
        error_context: dict[str, Any] | None = None,
        attempt_count: int | None = None,
        status: SessionStatus | None = None,
    ) -> ApplicationSession:
        updates: dict[str, Any] = {"current_step": current_step}
        if current_action is not None:
            updates["current_action"] = current_action
        if last_checkpoint is not None:
            updates["last_checkpoint"] = last_checkpoint
        if workflow_name is not None:
            updates["workflow_name"] = workflow_name
        if workflow_version is not None:
            updates["workflow_version"] = workflow_version
        if action_payload is not None:
            updates["action_payload"] = action_payload
        if error_context is not None:
            updates["error_context"] = error_context
        if attempt_count is not None:
            updates["attempt_count"] = attempt_count
        if status is not None:
            updates["status"] = status.value

        session = self.store.update_session(session_id, updates)
        self.store.append_event(
            session_id,
            SessionEvent(
                event_type="checkpoint_saved",
                payload={
                    "current_step": current_step,
                    "current_action": current_action,
                    "last_checkpoint": last_checkpoint,
                    "status": status.value if status is not None else session.status,
                },
            ),
        )
        return session

    def attach_external_thread(
        self,
        session_id: str,
        *,
        external_thread_id: str,
        telegram_chat_id: str | None = None,
    ) -> ApplicationSession:
        updates: dict[str, Any] = {"external_thread_id": external_thread_id}
        if telegram_chat_id is not None:
            updates["telegram_chat_id"] = telegram_chat_id
        session = self.store.update_session(session_id, updates)
        self.store.append_event(
            session_id,
            SessionEvent(
                event_type="external_thread_attached",
                payload={
                    "external_thread_id": external_thread_id,
                    "telegram_chat_id": telegram_chat_id,
                },
            ),
        )
        return session

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
        current_step: str | None = None,
        current_action: str | None = None,
        last_checkpoint: str | None = None,
        workflow_name: str | None = None,
        workflow_version: str | None = None,
        action_payload: dict[str, Any] | None = None,
        error_context: dict[str, Any] | None = None,
        attempt_count: int | None = None,
    ) -> PendingQuestion:
        if (
            current_step is not None
            or current_action is not None
            or last_checkpoint is not None
            or workflow_name is not None
            or workflow_version is not None
            or action_payload is not None
            or error_context is not None
            or attempt_count is not None
        ):
            self.checkpoint(
                session_id,
                current_step=current_step or "waiting_for_user",
                current_action=current_action,
                last_checkpoint=last_checkpoint,
                workflow_name=workflow_name,
                workflow_version=workflow_version,
                action_payload=action_payload,
                error_context=error_context,
                attempt_count=attempt_count,
            )
        question = PendingQuestion(field_key=field_key, prompt=prompt, metadata=metadata or {})
        self.store.create_pending_question(session_id, question)
        self.store.append_event(
            session_id,
            SessionEvent(event_type="question_asked", payload={"question_id": question.question_id, "field_key": field_key}),
        )
        return question

    def resume_with_answer(
        self,
        session_id: str,
        *,
        question_id: str,
        answer: str,
        status: SessionStatus | None = None,
        current_step: str | None = None,
        current_action: str | None = None,
        last_checkpoint: str | None = None,
        action_payload: dict[str, Any] | None = None,
        error_context: dict[str, Any] | None = None,
        attempt_count: int | None = None,
    ) -> PendingQuestion:
        question = self.store.resolve_pending_question(session_id, question_id, answer)
        if (
            current_step is not None
            or current_action is not None
            or last_checkpoint is not None
            or action_payload is not None
            or error_context is not None
            or attempt_count is not None
        ):
            updates: dict[str, Any] = {}
            if status is not None:
                updates["status"] = status.value
            if current_step is not None:
                updates["current_step"] = current_step
            if current_action is not None:
                updates["current_action"] = current_action
            if last_checkpoint is not None:
                updates["last_checkpoint"] = last_checkpoint
            if action_payload is not None:
                updates["action_payload"] = action_payload
            if error_context is not None:
                updates["error_context"] = error_context
            if attempt_count is not None:
                updates["attempt_count"] = attempt_count
            self.store.update_session(session_id, updates)
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
