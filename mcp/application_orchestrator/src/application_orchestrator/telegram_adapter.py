from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from application_orchestrator.models import AgentPhase, SessionStatus
from application_orchestrator.runner import ApplicationAgentRunner, CodexQuestion, SessionQuestionNotifier
from application_orchestrator.service import SessionOrchestrator

logger = logging.getLogger(__name__)


def build_telegram_user_id(chat_id: str) -> str:
    return f"telegram:{chat_id}"


@dataclass(slots=True)
class OutboundTelegramMessage:
    chat_id: str
    text: str


class TelegramBotClient:
    def __init__(self, bot_token: str, *, api_base: str = "https://api.telegram.org") -> None:
        self.bot_token = bot_token
        self.api_base = api_base.rstrip("/")

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        return self._post("sendMessage", {"chat_id": chat_id, "text": text})

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        response = self._post("getUpdates", payload, timeout=timeout + 5)
        result = response.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Telegram getUpdates returned an unexpected payload.")
        return [item for item in result if isinstance(item, dict)]

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return self._post("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    def _post(
        self,
        method_name: str,
        payload_dict: dict[str, Any],
        *,
        timeout: int = 15,
    ) -> dict[str, Any]:
        payload = json.dumps(payload_dict).encode("utf-8")
        request = Request(
            url=f"{self.api_base}/bot{self.bot_token}/{method_name}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"Telegram API error for {method_name}: {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Telegram API request failed for {method_name}") from exc


class TelegramQuestionNotifier(SessionQuestionNotifier):
    def __init__(self, bot_client: TelegramBotClient) -> None:
        self.bot_client = bot_client

    def notify_question(self, session, question: CodexQuestion) -> None:
        chat_id = session.telegram_chat_id
        if not chat_id:
            logger.info("telegram_question_not_sent session_id=%s reason=no_chat_id", session.session_id)
            return
        self.bot_client.send_message(chat_id, self._format_question_message(session.session_id, question))

    @staticmethod
    def _format_question_message(session_id: str, question: CodexQuestion) -> str:
        return (
            f"Question for session {session_id}\n"
            f"{question.prompt}\n"
            "Reply with:\n"
            "/reply your answer here\n"
            "or simply:\n"
            "/answer your answer here"
        )


class TelegramCommandRouter:
    def __init__(
        self,
        orchestrator: SessionOrchestrator,
        *,
        workflow_name: str = "job_application",
        workflow_version: str = "v1",
        runner: ApplicationAgentRunner | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.workflow_name = workflow_name
        self.workflow_version = workflow_version
        self.runner = runner

    def handle_update(self, update: dict[str, Any]) -> list[OutboundTelegramMessage]:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return []

        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()
        text = str(message.get("text", "")).strip()
        if not chat_id or not text:
            return []

        if text in {"/start", "/help"}:
            return [OutboundTelegramMessage(chat_id=chat_id, text=self._help_text())]

        if text.startswith("/job ") or text.startswith("/startjob "):
            return [self._handle_start_job(chat_id, text.split(maxsplit=1)[1].strip())]

        if text.startswith("/reply "):
            return [self._handle_reply(chat_id, text)]

        if text == "/status" or text.startswith("/status "):
            return [self._handle_status(chat_id, text)]

        if text.startswith("/answer "):
            return [self._handle_answer(chat_id, text.split(maxsplit=1)[1].strip())]

        return [
            OutboundTelegramMessage(
                chat_id=chat_id,
                text=(
                    "Unsupported command.\n"
                    f"{self._help_text()}"
                ),
            )
        ]

    def _handle_start_job(self, chat_id: str, job_url: str) -> OutboundTelegramMessage:
        if not job_url:
            return OutboundTelegramMessage(chat_id=chat_id, text="Usage: /job <job_url>")

        session = self.orchestrator.create_session(
            job_url,
            user_id=build_telegram_user_id(chat_id),
        )
        self.orchestrator.attach_external_thread(
            session.session_id,
            external_thread_id=chat_id,
            telegram_chat_id=chat_id,
        )
        self.orchestrator.checkpoint(
            session.session_id,
            current_step="session_created",
            current_action="await_job_parse",
            last_checkpoint="telegram_job_received",
            workflow_name=self.workflow_name,
            workflow_version=self.workflow_version,
            action_payload={"job_url": job_url},
            status=SessionStatus.CREATED,
        )
        if self.runner is not None:
            self.runner.start(session.session_id)
        return OutboundTelegramMessage(
            chat_id=chat_id,
            text=(
                f"Started job session for this chat\n"
                f"Status: created\n"
                f"Checkpoint: telegram_job_received\n"
                "Use /status to inspect progress."
            ),
        )

    def _handle_reply(self, chat_id: str, text: str) -> OutboundTelegramMessage:
        parts = text.split(maxsplit=2)
        if len(parts) == 2:
            return self._resume_active_session(chat_id, parts[1].strip())
        if len(parts) < 3:
            return OutboundTelegramMessage(
                chat_id=chat_id,
                text="Usage: /reply <answer> or /reply <session_id> <answer>",
            )

        session_id = parts[1].strip()
        answer = parts[2].strip()
        session = self._get_session_for_chat(chat_id, session_id)
        if session is None:
            return OutboundTelegramMessage(chat_id=chat_id, text=f"Session not found: {session_id}")
        return self._resume_session(chat_id, session.session_id, answer)

    def _handle_answer(self, chat_id: str, answer: str) -> OutboundTelegramMessage:
        if not answer:
            return OutboundTelegramMessage(chat_id=chat_id, text="Usage: /answer <answer>")
        return self._resume_active_session(chat_id, answer)

    def _resume_active_session(self, chat_id: str, answer: str) -> OutboundTelegramMessage:
        session = self._find_active_session_for_chat(chat_id)
        if session is None:
            return OutboundTelegramMessage(
                chat_id=chat_id,
                text="No active session found for this chat. Start one with /job <job_url>.",
            )
        return self._resume_session(chat_id, session.session_id, answer)

    def _resume_session(self, chat_id: str, session_id: str, answer: str) -> OutboundTelegramMessage:
        session = self._get_session_for_chat(chat_id, session_id)
        if session is None:
            return OutboundTelegramMessage(chat_id=chat_id, text=f"Session not found: {session_id}")
        if not session.pending_question_id:
            return OutboundTelegramMessage(chat_id=chat_id, text="This session is not waiting for a user answer.")

        question = self.orchestrator.resume_with_answer(
            session.session_id,
            question_id=session.pending_question_id,
            answer=answer,
            status=self._status_after_user_answer(session),
            current_step=self._step_after_user_answer(session),
            current_action="resume_after_user_answer",
            last_checkpoint="telegram_answer_received",
            error_context={},
        )
        self.orchestrator.checkpoint(
            session.session_id,
            current_step=self._step_after_user_answer(session),
            current_action="resume_after_user_answer",
            last_checkpoint="telegram_answer_received",
            action_payload={"field_key": question.field_key, "answer": answer},
            error_context={},
            status=self._status_after_user_answer(session),
        )
        if self.runner is not None:
            self.runner.resume(session.session_id)
        return OutboundTelegramMessage(
            chat_id=chat_id,
            text=(
                f"Recorded answer for {question.field_key}\n"
                f"Checkpoint: telegram_answer_received"
            ),
        )

    def _handle_status(self, chat_id: str, text: str) -> OutboundTelegramMessage:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            session = self._find_active_session_for_chat(chat_id)
            if session is None:
                return OutboundTelegramMessage(
                    chat_id=chat_id,
                    text="No active session found for this chat. Start one with /job <job_url>.",
                )
        else:
            session_id = parts[1].strip()
            session = self._get_session_for_chat(chat_id, session_id)
        if session is None:
            return OutboundTelegramMessage(chat_id=chat_id, text="Session not found.")

        lines = [
            f"Chat: {chat_id}",
            f"Status: {session.status}",
            f"Step: {session.current_step}",
            f"Action: {session.current_action or '-'}",
            f"Checkpoint: {session.last_checkpoint or '-'}",
            f"Attempt: {session.attempt_count}",
        ]
        if session.pending_question_id:
            lines.append(f"Pending question: {session.pending_question_id}")
        return OutboundTelegramMessage(chat_id=chat_id, text="\n".join(lines))

    def _get_session_for_chat(self, chat_id: str, session_id: str):
        session = self.orchestrator.store.get_session(session_id)
        if session is None:
            return None
        if session.telegram_chat_id not in {None, chat_id}:
            return None
        return session

    def _find_active_session_for_chat(self, chat_id: str):
        sessions = [
            session
            for session in self.orchestrator.store.list_sessions()
            if session.telegram_chat_id == chat_id
        ]
        if not sessions:
            return None

        def sort_key(session):
            status_priority = {
                SessionStatus.WAITING_FOR_USER: 0,
                SessionStatus.APPLICATION_STARTED: 1,
                SessionStatus.RESUME_GENERATED: 2,
                SessionStatus.JOB_PARSED: 3,
                SessionStatus.CREATED: 4,
                SessionStatus.READY_FOR_SUBMIT: 5,
                SessionStatus.SUBMITTED: 6,
                SessionStatus.FAILED: 7,
            }
            updated_at = session.updated_at
            if isinstance(updated_at, datetime):
                updated_value = updated_at.timestamp()
            else:
                updated_value = 0
            return (status_priority.get(session.status, 99), -updated_value)

        sessions.sort(key=sort_key)
        return sessions[0]

    @staticmethod
    def _status_after_user_answer(session) -> SessionStatus:
        if session.active_codex_phase == AgentPhase.RESUME:
            return SessionStatus.JOB_PARSED if session.job_snapshot else SessionStatus.CREATED
        return SessionStatus.APPLICATION_STARTED

    @staticmethod
    def _step_after_user_answer(session) -> str:
        if session.active_codex_phase == AgentPhase.RESUME:
            return "resume_generation"
        return "application_started"

    @staticmethod
    def _help_text() -> str:
        return (
            "Commands:\n"
            "/job <job_url>\n"
            "/reply <answer>\n"
            "/answer <answer>\n"
            "/status\n"
            "Optional advanced forms:\n"
            "/reply <session_id> <answer>\n"
            "/status <session_id>"
        )


def build_webhook_handler(
    router: TelegramCommandRouter,
    bot_client: TelegramBotClient | None = None,
) -> type[BaseHTTPRequestHandler]:
    class TelegramWebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
                logger.info(
                    "telegram_webhook_received path=%s update_id=%s keys=%s",
                    self.path,
                    payload.get("update_id"),
                    sorted(payload.keys()),
                )
                messages = router.handle_update(payload)
                logger.info(
                    "telegram_webhook_processed path=%s outbound_messages=%s",
                    self.path,
                    len(messages),
                )
                if bot_client is not None:
                    for message in messages:
                        bot_client.send_message(message.chat_id, message.text)
                self._write_json(200, {"ok": True, "messages": len(messages)})
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.exception("telegram_webhook_bad_request path=%s", self.path)
                self._write_json(400, {"ok": False, "error": str(exc)})
            except RuntimeError as exc:
                logger.exception("telegram_webhook_upstream_error path=%s", self.path)
                self._write_json(502, {"ok": False, "error": str(exc)})

        def do_GET(self) -> None:  # noqa: N802
            logger.info("telegram_webhook_healthcheck path=%s", self.path)
            self._write_json(200, {"ok": True, "service": "telegram-webhook"})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return TelegramWebhookHandler


def run_telegram_webhook_server(
    host: str,
    port: int,
    *,
    router: TelegramCommandRouter,
    bot_client: TelegramBotClient | None = None,
) -> None:
    logger.info("telegram_webhook_server_starting host=%s port=%s", host, port)
    server = ThreadingHTTPServer((host, port), build_webhook_handler(router, bot_client))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def poll_telegram_updates(
    router: TelegramCommandRouter,
    bot_client: TelegramBotClient,
    *,
    poll_timeout_seconds: int = 30,
    idle_sleep_seconds: float = 1.0,
    start_offset: int | None = None,
    error_sleep_seconds: float = 5.0,
) -> None:
    logger.info(
        "telegram_polling_started poll_timeout_seconds=%s start_offset=%s error_sleep_seconds=%s",
        poll_timeout_seconds,
        start_offset,
        error_sleep_seconds,
    )
    offset = start_offset
    while True:
        try:
            updates = bot_client.get_updates(offset=offset, timeout=poll_timeout_seconds)
        except RuntimeError:
            logger.exception(
                "telegram_polling_get_updates_failed offset=%s poll_timeout_seconds=%s",
                offset,
                poll_timeout_seconds,
            )
            if error_sleep_seconds > 0:
                time.sleep(error_sleep_seconds)
            continue
        if not updates:
            if idle_sleep_seconds > 0:
                time.sleep(idle_sleep_seconds)
            continue

        for update in updates:
            update_id = update.get("update_id")
            logger.info(
                "telegram_polling_received update_id=%s keys=%s",
                update_id,
                sorted(update.keys()),
            )
            messages = router.handle_update(update)
            logger.info(
                "telegram_polling_processed update_id=%s outbound_messages=%s",
                update_id,
                len(messages),
            )
            for message in messages:
                try:
                    bot_client.send_message(message.chat_id, message.text)
                except RuntimeError:
                    logger.exception(
                        "telegram_polling_send_message_failed update_id=%s chat_id=%s",
                        update_id,
                        message.chat_id,
                    )

            if isinstance(update_id, int):
                offset = update_id + 1
