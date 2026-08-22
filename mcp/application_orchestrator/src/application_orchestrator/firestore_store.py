from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from application_orchestrator.config import FirestoreSettings
from application_orchestrator.models import (
    ApplicationSession,
    ArtifactReference,
    PendingQuestion,
    QuestionStatus,
    SessionEvent,
    SessionStatus,
)
from application_orchestrator.store import SessionStore

try:
    from google.cloud import firestore
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    firestore = None


def _serialize_model(model: Any) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    return _replace_none_with_delete_sentinel(payload, keep_none=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _replace_none_with_delete_sentinel(value: Any, *, keep_none: bool) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_none_with_delete_sentinel(nested, keep_none=keep_none)
            for key, nested in value.items()
            if keep_none or nested is not None
        }
    if isinstance(value, list):
        return [_replace_none_with_delete_sentinel(item, keep_none=keep_none) for item in value]
    return value


class FirestoreSessionStore(SessionStore):
    def __init__(self, settings: FirestoreSettings, client: Any | None = None) -> None:
        self.settings = settings
        if settings.use_emulator and settings.emulator_host:
            os.environ["FIRESTORE_EMULATOR_HOST"] = settings.emulator_host
        self.client = client or self._build_client(settings)

    @staticmethod
    def _build_client(settings: FirestoreSettings) -> Any:
        if firestore is None:
            raise ImportError(
                "google-cloud-firestore is required to use FirestoreSessionStore"
            )
        return firestore.Client(project=settings.project_id, database=settings.database)

    def create_session(self, session: ApplicationSession) -> ApplicationSession:
        doc_ref = self._session_doc(session.session_id)
        doc_ref.set(_serialize_model(session))
        return session

    def get_session(self, session_id: str) -> ApplicationSession | None:
        snapshot = self._session_doc(session_id).get()
        if not snapshot.exists:
            return None
        return self._to_session(snapshot.to_dict(), session_id=session_id)

    def list_sessions(self) -> list[ApplicationSession]:
        sessions: list[ApplicationSession] = []
        for snapshot in self.client.collection(self.settings.sessions_collection).stream():
            payload = snapshot.to_dict() or {}
            session_id = payload.get("session_id") or getattr(snapshot, "id", None)
            if not session_id:
                continue
            sessions.append(self._to_session(payload, session_id=session_id))
        return sessions

    def update_session(self, session_id: str, updates: dict[str, Any]) -> ApplicationSession:
        payload = dict(updates)
        payload["updated_at"] = _utc_now().isoformat()
        self._session_doc(session_id).set(payload, merge=True)
        return self._require_session(session_id)

    def set_status(self, session_id: str, status: SessionStatus, current_step: str) -> ApplicationSession:
        updates = {
            "status": status.value,
            "current_step": current_step,
        }
        return self.update_session(session_id, updates)

    def update_job_snapshot(self, session_id: str, job_snapshot: dict) -> ApplicationSession:
        updates = {
            "job_snapshot": job_snapshot,
            "status": SessionStatus.JOB_PARSED.value,
            "current_step": "job_parsed",
        }
        return self.update_session(session_id, updates)

    def set_resume_artifact(self, session_id: str, artifact: ArtifactReference) -> ApplicationSession:
        updates = {
            "resume_artifact": _serialize_model(artifact),
            "status": SessionStatus.RESUME_GENERATED.value,
            "current_step": "resume_generated",
        }
        return self.update_session(session_id, updates)

    def upsert_answer(self, session_id: str, field_key: str, answer: str) -> ApplicationSession:
        session = self._require_session(session_id)
        answers = dict(session.known_answers)
        answers[field_key] = answer
        updates = {
            "known_answers": answers,
        }
        return self.update_session(session_id, updates)

    def create_pending_question(self, session_id: str, question: PendingQuestion) -> PendingQuestion:
        session_ref = self._session_doc(session_id)
        session_ref.collection("questions").document(question.question_id).set(_serialize_model(question))
        session_ref.set(
            {
                "pending_question_id": question.question_id,
                "status": SessionStatus.WAITING_FOR_USER.value,
                "current_step": "waiting_for_user",
                "updated_at": _utc_now().isoformat(),
            },
            merge=True,
        )
        return question

    def resolve_pending_question(self, session_id: str, question_id: str, answer: str) -> PendingQuestion:
        question_ref = self._session_doc(session_id).collection("questions").document(question_id)
        snapshot = question_ref.get()
        if not snapshot.exists:
            raise KeyError(f"Question not found: {question_id}")

        payload = dict(snapshot.to_dict() or {})
        payload["status"] = QuestionStatus.ANSWERED.value
        payload["answer"] = answer
        payload["answered_at"] = _utc_now().isoformat()
        question_ref.set(payload, merge=False)

        self.upsert_answer(session_id, payload["field_key"], answer)
        self._session_doc(session_id).set(
            {
                "pending_question_id": None,
                "status": SessionStatus.APPLICATION_STARTED.value,
                "current_step": "application_started",
                "updated_at": _utc_now().isoformat(),
            },
            merge=True,
        )
        return PendingQuestion.model_validate(payload)

    def append_event(self, session_id: str, event: SessionEvent) -> SessionEvent:
        self._session_doc(session_id).collection("events").document(event.event_id).set(_serialize_model(event))
        self._session_doc(session_id).set({"updated_at": _utc_now().isoformat()}, merge=True)
        return event

    def _session_doc(self, session_id: str) -> Any:
        return self.client.collection(self.settings.sessions_collection).document(session_id)

    def _require_session(self, session_id: str) -> ApplicationSession:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        return session

    @staticmethod
    def _to_session(payload: dict[str, Any], *, session_id: str) -> ApplicationSession:
        normalized = dict(payload)
        normalized["session_id"] = payload.get("session_id") or session_id
        return ApplicationSession.model_validate(normalized)
