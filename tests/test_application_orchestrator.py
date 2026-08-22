from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str((Path(__file__).resolve().parent.parent / "mcp" / "application_orchestrator" / "src").resolve()))

from application_orchestrator.config import FirestoreSettings
from application_orchestrator.firestore_store import FirestoreSessionStore
from application_orchestrator.models import ArtifactKind, ArtifactReference, SessionStatus
from application_orchestrator.service import SessionOrchestrator
from application_orchestrator.telegram_adapter import (
    TelegramCommandRouter,
    build_telegram_user_id,
    poll_telegram_updates,
)


class FakeSnapshot:
    def __init__(self, payload, doc_id=None):
        self._payload = payload
        self.id = doc_id

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
        return FakeSnapshot(None if payload is None else dict(payload), doc_id=self.path[-1])

    def collection(self, name: str):
        return FakeCollectionReference(self.store, self.path + (name,))


class FakeCollectionReference:
    def __init__(self, store: dict, path: tuple[str, ...]):
        self.store = store
        self.path = path

    def document(self, doc_id: str):
        return FakeDocumentReference(self.store, self.path + (doc_id,))

    def stream(self):
        for path, payload in self.store.items():
            if len(path) == len(self.path) + 1 and path[:-1] == self.path:
                yield FakeSnapshot(dict(payload), doc_id=path[-1])


class FakeFirestoreClient:
    def __init__(self):
        self.store: dict[tuple[str, ...], dict] = {}

    def collection(self, name: str):
        return FakeCollectionReference(self.store, (name,))


class FakeTelegramBotClient:
    def __init__(self, updates: list[dict]):
        self.updates = list(updates)
        self.sent_messages: list[tuple[str, str]] = []
        self.requested_offsets: list[int | None] = []

    def get_updates(self, *, offset=None, timeout=30):
        self.requested_offsets.append(offset)
        if self.updates:
            return [self.updates.pop(0)]
        raise KeyboardInterrupt()

    def send_message(self, chat_id: str, text: str):
        self.sent_messages.append((chat_id, text))
        return {"ok": True}


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

    session = orchestrator.attach_external_thread(
        session.session_id,
        external_thread_id="telegram-chat-42",
        telegram_chat_id="42",
    )
    assert session.external_thread_id == "telegram-chat-42"
    assert session.telegram_chat_id == "42"

    checkpointed = orchestrator.checkpoint(
        session.session_id,
        current_step="application_form",
        current_action="fill_work_authorization",
        last_checkpoint="before_work_authorization_question",
        workflow_name="job_application",
        workflow_version="v1",
        action_payload={"field": "visa_sponsorship"},
        error_context={"reason": "missing_user_input"},
        attempt_count=1,
        status=SessionStatus.APPLICATION_STARTED,
    )
    assert checkpointed.current_step == "application_form"
    assert checkpointed.current_action == "fill_work_authorization"
    assert checkpointed.last_checkpoint == "before_work_authorization_question"
    assert checkpointed.workflow_name == "job_application"
    assert checkpointed.workflow_version == "v1"
    assert checkpointed.action_payload["field"] == "visa_sponsorship"
    assert checkpointed.error_context["reason"] == "missing_user_input"
    assert checkpointed.attempt_count == 1

    question = orchestrator.pause_for_question(
        session.session_id,
        field_key="visa_sponsorship",
        prompt="Do you require visa sponsorship now or in the future?",
        current_step="application_form",
        current_action="await_user_answer",
        last_checkpoint="question_sent_to_user",
        action_payload={"field": "visa_sponsorship"},
        error_context={"reason": "missing_user_input"},
        attempt_count=2,
    )

    paused_session = store.get_session(session.session_id)
    assert paused_session is not None
    assert paused_session.status == SessionStatus.WAITING_FOR_USER
    assert paused_session.pending_question_id == question.question_id
    assert paused_session.current_action == "await_user_answer"
    assert paused_session.last_checkpoint == "question_sent_to_user"
    assert paused_session.attempt_count == 2

    answered = orchestrator.resume_with_answer(
        session.session_id,
        question_id=question.question_id,
        answer="No",
        current_step="application_form",
        current_action="resume_after_user_answer",
        last_checkpoint="answer_received_from_user",
        action_payload={"field": "visa_sponsorship", "answer": "No"},
        error_context={},
        attempt_count=3,
    )
    assert answered.answer == "No"

    resumed_session = store.get_session(session.session_id)
    assert resumed_session is not None
    assert resumed_session.pending_question_id is None
    assert resumed_session.known_answers["visa_sponsorship"] == "No"
    assert resumed_session.status == SessionStatus.APPLICATION_STARTED
    assert resumed_session.current_action == "resume_after_user_answer"
    assert resumed_session.last_checkpoint == "answer_received_from_user"
    assert resumed_session.action_payload["answer"] == "No"
    assert resumed_session.error_context == {}
    assert resumed_session.attempt_count == 3

    event_keys = [path for path in client.store if len(path) == 4 and path[2] == "events"]
    assert len(event_keys) >= 6


def test_telegram_router_starts_and_resumes_session() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    router = TelegramCommandRouter(orchestrator)

    start_messages = router.handle_update(
        {
            "message": {
                "chat": {"id": 42},
                "text": "/job https://example.com/jobs/telegram-1",
            }
        }
    )
    assert len(start_messages) == 1
    assert "Started job session for this chat" in start_messages[0].text

    session_docs = [
        payload
        for path, payload in client.store.items()
        if len(path) == 2 and path[0] == settings.sessions_collection
    ]
    assert len(session_docs) == 1
    created_session = session_docs[0]
    session_id = created_session["session_id"]

    created = store.get_session(session_id)
    assert created is not None
    assert created.user_id == build_telegram_user_id("42")
    assert created.telegram_chat_id == "42"
    assert created.workflow_name == "job_application"
    assert created.last_checkpoint == "telegram_job_received"

    question = orchestrator.pause_for_question(
        session_id,
        field_key="work_authorization",
        prompt="Are you legally authorized to work in the United States?",
        current_step="application_form",
        current_action="await_user_answer",
        last_checkpoint="question_sent_to_user",
    )
    waiting = store.get_session(session_id)
    assert waiting is not None
    assert waiting.pending_question_id == question.question_id

    reply_messages = router.handle_update(
        {
            "message": {
                "chat": {"id": 42},
                "text": "/reply Yes",
            }
        }
    )
    assert len(reply_messages) == 1
    assert "Recorded answer for work_authorization" in reply_messages[0].text

    resumed = store.get_session(session_id)
    assert resumed is not None
    assert resumed.pending_question_id is None
    assert resumed.known_answers["work_authorization"] == "Yes"
    assert resumed.last_checkpoint == "telegram_answer_received"

    status_messages = router.handle_update(
        {
            "message": {
                "chat": {"id": 42},
                "text": "/status",
            }
        }
    )
    assert len(status_messages) == 1
    assert "Chat: 42" in status_messages[0].text
    assert "Checkpoint: telegram_answer_received" in status_messages[0].text

    explicit_status_messages = router.handle_update(
        {
            "message": {
                "chat": {"id": 42},
                "text": f"/status {session_id}",
            }
        }
    )
    assert len(explicit_status_messages) == 1
    assert "Chat: 42" in explicit_status_messages[0].text


def test_telegram_polling_consumes_updates_and_sends_replies() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    router = TelegramCommandRouter(orchestrator)
    bot_client = FakeTelegramBotClient(
        updates=[
            {
                "update_id": 101,
                "message": {
                    "chat": {"id": 42},
                    "text": "/help",
                },
            }
        ]
    )

    try:
        poll_telegram_updates(
            router,
            bot_client,
            poll_timeout_seconds=1,
            idle_sleep_seconds=0,
        )
    except KeyboardInterrupt:
        pass

    assert bot_client.requested_offsets == [None, 102]
    assert len(bot_client.sent_messages) == 1
    assert bot_client.sent_messages[0][0] == "42"
    assert "/job <job_url>" in bot_client.sent_messages[0][1]


def test_telegram_status_picks_waiting_session_for_chat() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    router = TelegramCommandRouter(orchestrator)

    first = orchestrator.create_session("https://example.com/jobs/older", user_id=build_telegram_user_id("42"))
    orchestrator.attach_external_thread(first.session_id, external_thread_id="42", telegram_chat_id="42")
    orchestrator.checkpoint(
        first.session_id,
        current_step="application_started",
        current_action="fill_form",
        last_checkpoint="older_checkpoint",
        status=SessionStatus.APPLICATION_STARTED,
    )

    second = orchestrator.create_session("https://example.com/jobs/newer", user_id=build_telegram_user_id("42"))
    orchestrator.attach_external_thread(second.session_id, external_thread_id="42", telegram_chat_id="42")
    orchestrator.pause_for_question(
        second.session_id,
        field_key="work_authorization",
        prompt="Are you authorized to work?",
        current_step="application_form",
        current_action="await_user_answer",
        last_checkpoint="question_sent_to_user",
    )

    status_messages = router.handle_update(
        {
            "message": {
                "chat": {"id": 42},
                "text": "/status",
            }
        }
    )
    assert len(status_messages) == 1
    assert "Status: waiting_for_user" in status_messages[0].text
    assert "Pending question:" in status_messages[0].text
