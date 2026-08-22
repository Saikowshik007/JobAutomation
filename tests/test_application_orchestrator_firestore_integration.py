from __future__ import annotations

import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parent.parent / "mcp" / "application_orchestrator" / "src").resolve()))

from application_orchestrator.config import FirestoreSettings
from application_orchestrator.firestore_store import FirestoreSessionStore, firestore
from application_orchestrator.models import ArtifactKind, ArtifactReference, SessionStatus
from application_orchestrator.service import SessionOrchestrator


pytestmark = pytest.mark.integration

FirestoreSettings.from_env()


def _integration_enabled() -> bool:
    return os.getenv("RUN_FIRESTORE_INTEGRATION", "").lower() in {"1", "true", "yes"}


@pytest.mark.skipif(
    (not _integration_enabled()) or firestore is None,
    reason="Requires RUN_FIRESTORE_INTEGRATION=1 and google-cloud-firestore installed.",
)
def test_firestore_session_orchestrator_round_trip() -> None:
    settings_from_env = FirestoreSettings.from_env()
    project_id = settings_from_env.project_id
    collection_name = settings_from_env.sessions_collection
    user_id = f"itest-user-{uuid4().hex[:8]}"

    settings = FirestoreSettings(
        project_id=project_id,
        database=settings_from_env.database,
        app_name=settings_from_env.app_name,
        sessions_collection=collection_name,
        user_id_field=settings_from_env.user_id_field,
        default_user_id=user_id,
        credentials_path=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        web_app_id=settings_from_env.web_app_id,
    )
    store = FirestoreSessionStore(settings)
    orchestrator = SessionOrchestrator(store, default_user_id=user_id)

    session = orchestrator.create_session(f"https://example.com/jobs/{uuid4().hex}")
    session_id = session.session_id

    try:
        loaded = store.get_session(session_id)
        assert loaded is not None
        assert loaded.job_url == session.job_url
        assert loaded.status == SessionStatus.CREATED

        parsed = orchestrator.record_job_parse(
            session_id,
            {
                "title": "Backend Engineer",
                "company": "OpenAI",
                "location": "Remote",
            },
        )
        assert parsed.status == SessionStatus.JOB_PARSED
        assert parsed.job_snapshot["company"] == "OpenAI"

        resumed = orchestrator.record_resume_artifact(
            session_id,
            ArtifactReference(
                kind=ArtifactKind.RESUME_PDF,
                path=f"resume/OpenAI/Backend_Engineer/{session_id}.pdf",
                label="Integration test artifact",
            ),
        )
        assert resumed.status == SessionStatus.RESUME_GENERATED
        assert resumed.resume_artifact is not None
        assert resumed.resume_artifact.kind == ArtifactKind.RESUME_PDF

        question = orchestrator.pause_for_question(
            session_id,
            field_key="work_authorization",
            prompt="Are you legally authorized to work in the United States?",
            metadata={"source": "integration-test"},
        )
        waiting = store.get_session(session_id)
        assert waiting is not None
        assert waiting.status == SessionStatus.WAITING_FOR_USER
        assert waiting.pending_question_id == question.question_id

        answered = orchestrator.resume_with_answer(
            session_id,
            question_id=question.question_id,
            answer="Yes",
        )
        assert answered.answer == "Yes"
        assert answered.metadata["source"] == "integration-test"

        final_session = store.get_session(session_id)
        assert final_session is not None
        assert final_session.status == SessionStatus.APPLICATION_STARTED
        assert final_session.known_answers["work_authorization"] == "Yes"
        assert final_session.pending_question_id is None

        questions = list(
            store.client.collection(collection_name)
            .document(session_id)
            .collection("questions")
            .stream()
        )
        events = list(
            store.client.collection(collection_name)
            .document(session_id)
            .collection("events")
            .stream()
        )
        assert len(questions) == 1
        assert len(events) >= 4
    finally:
        _delete_session_tree(store, collection_name, session_id)


def _delete_session_tree(store: FirestoreSessionStore, collection_name: str, session_id: str) -> None:
    session_ref = store.client.collection(collection_name).document(session_id)
    for child_collection in session_ref.collections():
        for document in child_collection.stream():
            document.reference.delete()
    session_ref.delete()
