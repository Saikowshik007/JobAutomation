from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str((Path(__file__).resolve().parent.parent / "mcp" / "application_orchestrator" / "src").resolve()))

from application_orchestrator.config import FirestoreSettings
from application_orchestrator.firestore_store import FirestoreSessionStore
from application_orchestrator.models import AgentPhase, ArtifactKind, ArtifactReference, SessionStatus
from application_orchestrator.runner import (
    BackgroundApplicationAgentRunner,
    CodexAgentResult,
    CodexCliAgentExecutor,
    CodexQuestion,
)
from application_orchestrator.service import SessionOrchestrator
from application_orchestrator.telegram_adapter import (
    TelegramCommandRouter,
    TelegramQuestionNotifier,
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


class FlakyTelegramBotClient(FakeTelegramBotClient):
    def __init__(
        self,
        updates: list[dict],
        *,
        get_updates_failures: int = 0,
        send_message_failures: int = 0,
    ):
        super().__init__(updates)
        self.get_updates_failures = get_updates_failures
        self.send_message_failures = send_message_failures

    def get_updates(self, *, offset=None, timeout=30):
        self.requested_offsets.append(offset)
        if self.get_updates_failures > 0:
            self.get_updates_failures -= 1
            raise RuntimeError("Telegram API request failed for getUpdates")
        if self.updates:
            return [self.updates.pop(0)]
        raise KeyboardInterrupt()

    def send_message(self, chat_id: str, text: str):
        if self.send_message_failures > 0:
            self.send_message_failures -= 1
            raise RuntimeError("Telegram API request failed for sendMessage")
        return super().send_message(chat_id, text)


class FakeRunner:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.resumed: list[str] = []

    def start(self, session_id: str) -> None:
        self.started.append(session_id)

    def resume(self, session_id: str) -> None:
        self.resumed.append(session_id)


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
        status=SessionStatus.APPLICATION_STARTED,
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
    runner = FakeRunner()
    router = TelegramCommandRouter(orchestrator, runner=runner)

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
    assert runner.started == [session_id]

    question = orchestrator.pause_for_question(
        session_id,
        field_key="work_authorization",
        prompt="Are you legally authorized to work in the United States?",
        current_step="application_form",
        current_action="await_user_answer",
        last_checkpoint="question_sent_to_user",
    )
    store.update_session(session_id, {"active_codex_phase": AgentPhase.APPLICATION.value})
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
    assert runner.resumed == [session_id]

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


def test_telegram_polling_retries_after_get_updates_failure() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    router = TelegramCommandRouter(orchestrator)
    bot_client = FlakyTelegramBotClient(
        updates=[
            {
                "update_id": 101,
                "message": {
                    "chat": {"id": 42},
                    "text": "/help",
                },
            }
        ],
        get_updates_failures=1,
    )

    try:
        poll_telegram_updates(
            router,
            bot_client,
            poll_timeout_seconds=1,
            idle_sleep_seconds=0,
            error_sleep_seconds=0,
        )
    except KeyboardInterrupt:
        pass

    assert bot_client.requested_offsets == [None, None, 102]
    assert len(bot_client.sent_messages) == 1
    assert bot_client.sent_messages[0][0] == "42"


def test_telegram_polling_continues_after_send_message_failure() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    router = TelegramCommandRouter(orchestrator)
    bot_client = FlakyTelegramBotClient(
        updates=[
            {
                "update_id": 101,
                "message": {
                    "chat": {"id": 42},
                    "text": "/help",
                },
            }
        ],
        send_message_failures=1,
    )

    try:
        poll_telegram_updates(
            router,
            bot_client,
            poll_timeout_seconds=1,
            idle_sleep_seconds=0,
            error_sleep_seconds=0,
        )
    except KeyboardInterrupt:
        pass

    assert bot_client.requested_offsets == [None, 102]
    assert bot_client.sent_messages == []


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


def test_background_runner_persists_codex_result_with_resume_and_checkpoint() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    session = orchestrator.create_session("https://example.com/jobs/123")

    class FakeExecutor:
        def run(self, current_session, *, phase, thread_id) -> CodexAgentResult:
            assert current_session.session_id == session.session_id
            assert phase == AgentPhase.RESUME
            assert thread_id is None
            return CodexAgentResult(
                status=SessionStatus.APPLICATION_STARTED,
                current_step="application_pending",
                codex_thread_id="resume-thread-1",
                current_action="await_codex_browser_actions",
                last_checkpoint="codex_application_pending",
                job_snapshot={"job_url": current_session.job_url, "title": "Software Engineer", "company": "OpenAI"},
                resume_artifact=ArtifactReference(
                    kind=ArtifactKind.RESUME_PDF,
                    path="resume/OpenAI/Software_Engineer/Sai.pdf",
                    label="Tailored for OpenAI",
                ),
                action_payload={"source": "codex"},
            )

    runner = BackgroundApplicationAgentRunner(
        orchestrator,
        executor=FakeExecutor(),
    )

    runner.run_once(session.session_id)

    updated = store.get_session(session.session_id)
    assert updated is not None
    assert updated.status == SessionStatus.APPLICATION_STARTED
    assert updated.active_codex_phase == AgentPhase.RESUME
    assert updated.resume_codex_thread_id == "resume-thread-1"
    assert updated.application_codex_thread_id is None
    assert updated.job_snapshot["company"] == "OpenAI"
    assert updated.resume_artifact is not None
    assert updated.last_checkpoint == "codex_application_pending"
    assert updated.current_action == "await_codex_browser_actions"
    assert updated.action_payload["source"] == "codex"


def test_background_runner_persists_codex_question_as_pause() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    session = orchestrator.create_session("https://example.com/jobs/456")

    class FakeExecutor:
        def run(self, current_session, *, phase, thread_id) -> CodexAgentResult:
            assert phase == AgentPhase.RESUME
            assert thread_id is None
            return CodexAgentResult(
                status=SessionStatus.WAITING_FOR_USER,
                current_step="application_form",
                codex_thread_id="resume-thread-2",
                current_action="await_user_answer",
                last_checkpoint="application_question_blocked",
                question=CodexQuestion(
                    field_key="work_authorization",
                    prompt="Are you legally authorized to work in the United States?",
                    metadata={"source": "codex"},
                ),
            )

    runner = BackgroundApplicationAgentRunner(
        orchestrator,
        executor=FakeExecutor(),
    )

    runner.run_once(session.session_id)

    updated = store.get_session(session.session_id)
    assert updated is not None
    assert updated.status == SessionStatus.WAITING_FOR_USER
    assert updated.current_step == "waiting_for_user"
    assert updated.active_codex_phase == AgentPhase.RESUME
    assert updated.resume_codex_thread_id == "resume-thread-2"
    assert updated.pending_question_id is not None
    assert updated.last_checkpoint == "application_question_blocked"


def test_background_runner_sends_telegram_question_notification() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    session = orchestrator.create_session("https://example.com/jobs/notify")
    orchestrator.attach_external_thread(
        session.session_id,
        external_thread_id="42",
        telegram_chat_id="42",
    )
    bot_client = FakeTelegramBotClient(updates=[])

    class FakeExecutor:
        def run(self, current_session, *, phase, thread_id) -> CodexAgentResult:
            assert phase == AgentPhase.RESUME
            return CodexAgentResult(
                status=SessionStatus.WAITING_FOR_USER,
                current_step="application_form",
                codex_thread_id="resume-thread-3",
                current_action="await_user_answer",
                last_checkpoint="application_question_blocked",
                question=CodexQuestion(
                    field_key="work_authorization",
                    prompt="Are you legally authorized to work in the United States?",
                    metadata={"source": "codex"},
                ),
            )

    runner = BackgroundApplicationAgentRunner(
        orchestrator,
        executor=FakeExecutor(),
        notifier=TelegramQuestionNotifier(bot_client),
    )

    runner.run_once(session.session_id)

    assert len(bot_client.sent_messages) == 1
    assert bot_client.sent_messages[0][0] == "42"
    assert "Question for session" in bot_client.sent_messages[0][1]
    assert "Are you legally authorized to work in the United States?" in bot_client.sent_messages[0][1]
    assert "/answer your answer here" in bot_client.sent_messages[0][1]


def test_background_runner_pauses_for_cancelled_mcp_tool_calls() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    session = orchestrator.create_session("https://example.com/jobs/cancelled")
    orchestrator.attach_external_thread(
        session.session_id,
        external_thread_id="42",
        telegram_chat_id="42",
    )
    bot_client = FakeTelegramBotClient(updates=[])

    class FakeExecutor:
        def run(self, current_session, *, phase, thread_id) -> CodexAgentResult:
            raise RuntimeError(
                "Required MCP tools could not be executed: "
                "mcp__job_intelligence.get_llm_text_for_this_url returned "
                "'user cancelled MCP tool call'"
            )

    runner = BackgroundApplicationAgentRunner(
        orchestrator,
        executor=FakeExecutor(),
        notifier=TelegramQuestionNotifier(bot_client),
    )

    runner.run_once(session.session_id)

    updated = store.get_session(session.session_id)
    assert updated is not None
    assert updated.status == SessionStatus.WAITING_FOR_USER
    assert updated.current_step == "waiting_for_user"
    assert updated.current_action == "await_mcp_retry"
    assert updated.last_checkpoint == "mcp_tool_call_cancelled"
    assert updated.pending_question_id is not None
    assert updated.active_codex_phase == AgentPhase.RESUME
    assert len(bot_client.sent_messages) == 1
    assert "Required MCP tool calls were cancelled." in bot_client.sent_messages[0][1]


def test_background_runner_uses_separate_application_codex_thread_after_resume_phase() -> None:
    client = FakeFirestoreClient()
    settings = FirestoreSettings(project_id="jobautomation-bc798")
    store = FirestoreSessionStore(settings, client=client)
    orchestrator = SessionOrchestrator(store, default_user_id="user-123")
    session = orchestrator.create_session("https://example.com/jobs/789")
    orchestrator.record_job_parse(session.session_id, {"title": "Backend Engineer", "company": "OpenAI"})
    orchestrator.record_resume_artifact(
        session.session_id,
        ArtifactReference(
            kind=ArtifactKind.RESUME_PDF,
            path="resume/OpenAI/Backend_Engineer/Sai.pdf",
            label="Tailored resume",
        ),
    )
    store.update_session(
        session.session_id,
        {
            "resume_codex_thread_id": "resume-thread-existing",
            "active_codex_phase": AgentPhase.RESUME.value,
        },
    )

    class FakeExecutor:
        def run(self, current_session, *, phase, thread_id) -> CodexAgentResult:
            assert phase == AgentPhase.APPLICATION
            assert thread_id is None
            assert current_session.resume_codex_thread_id == "resume-thread-existing"
            return CodexAgentResult(
                status=SessionStatus.READY_FOR_SUBMIT,
                current_step="ready_for_submit",
                codex_thread_id="application-thread-1",
                current_action="await_final_review",
                last_checkpoint="application_ready_for_submit",
            )

    runner = BackgroundApplicationAgentRunner(
        orchestrator,
        executor=FakeExecutor(),
    )

    runner.run_once(session.session_id)

    updated = store.get_session(session.session_id)
    assert updated is not None
    assert updated.status == SessionStatus.READY_FOR_SUBMIT
    assert updated.active_codex_phase == AgentPhase.APPLICATION
    assert updated.resume_codex_thread_id == "resume-thread-existing"
    assert updated.application_codex_thread_id == "application-thread-1"


def test_codex_cli_executor_writes_jsonl_logs() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        log_dir = root / "codex-logs"
        executor = CodexCliAgentExecutor(workspace_root=root, log_dir=log_dir)

        completed = subprocess.CompletedProcess(
            args=["codex", "exec", "--json"],
            returncode=0,
            stdout='{"type":"thread.started","thread_id":"abc"}\n',
            stderr="warning text\n",
        )

        executor._write_run_logs(
            session_id="session-123",
            phase=AgentPhase.RESUME,
            prompt="Tailor the resume.",
            command=["codex", "exec", "--json"],
            completed=completed,
        )

        json_logs = list(log_dir.glob("*-session-123-resume.jsonl"))
        stderr_logs = list(log_dir.glob("*-session-123-resume.stderr.log"))
        prompt_logs = list(log_dir.glob("*-session-123-resume.prompt.txt"))
        meta_logs = list(log_dir.glob("*-session-123-resume.meta.json"))

        assert len(json_logs) == 1
        assert len(stderr_logs) == 1
        assert len(prompt_logs) == 1
        assert len(meta_logs) == 1
        assert '"type":"thread.started"' in json_logs[0].read_text(encoding="utf-8")
        assert "warning text" in stderr_logs[0].read_text(encoding="utf-8")
        assert "Tailor the resume." in prompt_logs[0].read_text(encoding="utf-8")


def test_codex_cli_executor_uses_untrusted_approval_mode_by_default() -> None:
    executor = CodexCliAgentExecutor(workspace_root=Path("D:/projects/JobAutomation"))

    command = executor._build_command(
        schema_path=Path("schema.json"),
        output_path=Path("output.json"),
        thread_id=None,
    )

    assert "-a" in command
    assert "untrusted" in command
