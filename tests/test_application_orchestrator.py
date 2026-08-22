from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str((Path(__file__).resolve().parent.parent / "mcp" / "application_orchestrator" / "src").resolve()))

from application_orchestrator.config import FirestoreSettings
from application_orchestrator.firestore_store import FirestoreSessionStore
from application_orchestrator.models import ArtifactKind, ArtifactReference, SessionStatus
from application_orchestrator.service import SessionOrchestrator


class FakeSnapshot:
    def __init__(self, payload):
        self._payload = payload

    @property
    def exists(self) -> bool:
        return self._payload is not None

    def to_dict(self):
        return self._payload


class FakeDocumentReference:
    def __init__(self, store: dict, path: tuple[str, ...]):
        self.store = store
        self.path = path

    def set(self, payload, merge=False):
        current = self.store.get(self.path)
        if merge and isinstance(current, dict):
            merged = dict(current)
            merged.update(payload)
            self.store[self.path] = merged
            return
        self.store[self.path] = dict(payload)

    def get(self):
        payload = self.store.get(self.path)
        return FakeSnapshot(None if payload is None else dict(payload))

    def collection(self, name: str):
        return FakeCollectionReference(self.store, self.path + (name,))


class FakeCollectionReference:
    def __init__(self, store: dict, path: tuple[str, ...]):
        self.store = store
        self.path = path

    def document(self, doc_id: str):
        return FakeDocumentReference(self.store, self.path + (doc_id,))


class FakeFirestoreClient:
    def __init__(self):
        self.store: dict[tuple[str, ...], dict] = {}

    def collection(self, name: str):
        return FakeCollectionReference(self.store, (name,))


def test_firestore_settings_can_be_created_from_web_config() -> None:
    settings = FirestoreSettings.from_web_config(
        project_id="jobautomation-bc798",
        app_id="1:388107292609:web:a9cfdaa78074034b73a940",
    )

    assert settings.project_id == "jobautomation-bc798"
    assert settings.web_app_id == "1:388107292609:web:a9cfdaa78074034b73a940"


def test_session_orchestrator_persists_pause_resume_flow() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")

    session = orchestrator.create_session("https://example.com/jobs/123")
    assert session.status == SessionStatus.CREATED

    session = orchestrator.record_job_parse(
        session.session_id,
        {"title": "Software Engineer", "company": "OpenAI"},
    )
    assert session.status == SessionStatus.JOB_PARSED
    assert session.job_snapshot["title"] == "Software Engineer"

    session = orchestrator.record_resume_artifact(
        session.session_id,
        ArtifactReference(
            kind=ArtifactKind.RESUME_PDF,
            path="resume/OpenAI/Software_Engineer/Sai.pdf",
            label="Tailored resume",
        ),
    )
    assert session.status == SessionStatus.RESUME_GENERATED
    assert session.resume_artifact is not None

    question = orchestrator.pause_for_question(
        session.session_id,
        field_key="visa_sponsorship",
        prompt="Do you require visa sponsorship now or in the future?",
    )

    paused_session = store.get_session(session.session_id)
    assert paused_session is not None
    assert paused_session.status == SessionStatus.WAITING_FOR_USER
    assert paused_session.pending_question_id == question.question_id

    answered = orchestrator.resume_with_answer(
        session.session_id,
        question_id=question.question_id,
        answer="No",
    )
    assert answered.answer == "No"

    resumed_session = store.get_session(session.session_id)
    assert resumed_session is not None
    assert resumed_session.pending_question_id is None
    assert resumed_session.known_answers["visa_sponsorship"] == "No"
    assert resumed_session.status == SessionStatus.APPLICATION_STARTED

    event_keys = [path for path in client.store if len(path) == 4 and path[2] == "events"]
    assert len(event_keys) >= 4
