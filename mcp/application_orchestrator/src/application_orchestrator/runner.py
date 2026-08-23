from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any, Protocol

from application_orchestrator.models import (
    AgentPhase,
    ApplicationSession,
    ArtifactKind,
    ArtifactReference,
    SessionStatus,
)
from application_orchestrator.service import SessionOrchestrator

logger = logging.getLogger(__name__)


class ApplicationAgentRunner(Protocol):
    def start(self, session_id: str) -> None: ...

    def resume(self, session_id: str) -> None: ...

    def run_once(self, session_id: str) -> None: ...


class CodexAgentExecutor(Protocol):
    def run(
        self,
        session: ApplicationSession,
        *,
        phase: AgentPhase,
        thread_id: str | None,
    ) -> "CodexAgentResult": ...


class SessionQuestionNotifier(Protocol):
    def notify_question(self, session: ApplicationSession, question: CodexQuestion) -> None: ...


@dataclass(slots=True)
class CodexQuestion:
    field_key: str
    prompt: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class CodexAgentResult:
    status: SessionStatus
    current_step: str
    codex_thread_id: str | None = None
    current_action: str | None = None
    last_checkpoint: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    action_payload: dict[str, Any] | None = None
    error_context: dict[str, Any] | None = None
    attempt_count: int | None = None
    job_snapshot: dict[str, Any] | None = None
    resume_artifact: ArtifactReference | None = None
    question: CodexQuestion | None = None


class CodexCliAgentExecutor:
    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        workspace_root: Path | None = None,
        model: str | None = None,
        sandbox: str = "workspace-write",
        approval_policy: str = "untrusted",
        log_dir: Path | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.model = model or os.getenv("JOBAUTOMATION_CODEX_MODEL")
        self.sandbox = sandbox
        self.approval_policy = os.getenv("JOBAUTOMATION_CODEX_APPROVAL_POLICY", approval_policy)
        configured_log_dir = log_dir or os.getenv("JOBAUTOMATION_CODEX_LOG_DIR")
        self.log_dir = (
            Path(configured_log_dir).resolve()
            if configured_log_dir
            else (self.workspace_root / "logs" / "codex-exec").resolve()
        )

    def run(
        self,
        session: ApplicationSession,
        *,
        phase: AgentPhase,
        thread_id: str | None,
    ) -> CodexAgentResult:
        prompt = self._build_prompt(session, phase)
        schema = self._output_schema()
        with tempfile.TemporaryDirectory(prefix="jobautomation-codex-") as temp_dir:
            temp_root = Path(temp_dir)
            schema_path = temp_root / "agent-result-schema.json"
            output_path = temp_root / "agent-result.json"
            schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")

            command = self._build_command(
                schema_path=schema_path,
                output_path=output_path,
                thread_id=thread_id,
            )
            if self.model:
                command.extend(["-m", self.model])

            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=self.workspace_root,
                check=False,
            )
            self._write_run_logs(
                session_id=session.session_id,
                phase=phase,
                prompt=prompt,
                command=command,
                completed=completed,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Codex CLI failed with exit code "
                    f"{completed.returncode}: {(completed.stderr or completed.stdout).strip()}"
                )
            if not output_path.exists():
                raise RuntimeError("Codex CLI did not produce a structured result file.")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            observed_thread_id = self._extract_thread_id(completed.stdout) or thread_id
        return self._parse_result(payload, codex_thread_id=observed_thread_id)

    def _build_command(self, *, schema_path: Path, output_path: Path, thread_id: str | None) -> list[str]:
        command = [self.codex_bin, "-a", self.approval_policy, "exec"]
        if thread_id:
            command.extend(["resume", thread_id])
        command.extend(
            [
                "--json",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-C",
                str(self.workspace_root),
                "-s",
                self.sandbox,
                "-",
            ]
        )
        return command

    def _write_run_logs(
        self,
        *,
        session_id: str,
        phase: AgentPhase,
        prompt: str,
        command: list[str],
        completed: subprocess.CompletedProcess[str],
    ) -> None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = f"{timestamp}-{session_id}-{phase.value}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        event_log_path = self.log_dir / f"{stem}.jsonl"
        stderr_log_path = self.log_dir / f"{stem}.stderr.log"
        prompt_log_path = self.log_dir / f"{stem}.prompt.txt"
        meta_log_path = self.log_dir / f"{stem}.meta.json"

        event_log_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_log_path.write_text(completed.stderr or "", encoding="utf-8")
        prompt_log_path.write_text(prompt, encoding="utf-8")
        meta_log_path.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "phase": phase.value,
                    "returncode": completed.returncode,
                    "command": command,
                    "workspace_root": str(self.workspace_root),
                    "event_log_path": str(event_log_path),
                    "stderr_log_path": str(stderr_log_path),
                    "prompt_log_path": str(prompt_log_path),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(
            "codex_cli_run_logged session_id=%s phase=%s event_log=%s stderr_log=%s",
            session_id,
            phase.value,
            event_log_path,
            stderr_log_path,
        )

    def _build_prompt(self, session: ApplicationSession, phase: AgentPhase) -> str:
        session_json = json.dumps(session.model_dump(mode="json"), indent=2, sort_keys=True)
        if phase == AgentPhase.RESUME:
            phase_instructions = """
            You are the JobAutomation resume agent.

            Your job is to continue exactly one resume-creation or resume-editing session using the MCP tools
            already available to Codex in this environment. Use those tools directly when needed for job parsing,
            resume generation, and resume rendering.

            Requirements:
            - Continue from the current session state instead of restarting blindly.
            - Focus only on parsing the job and producing or refining the tailored resume artifacts.
            - Do not start the application website workflow in this phase.
            - Always parse the current job URL with the MCP job parsing tool before tailoring the resume.
            - If you need user input for resume tailoring, return a waiting-for-user result with a single question.
            - If you generate or locate a resume artifact, return its path and metadata.
            - For job_snapshot, action_payload, error_context, and metadata fields, return JSON-encoded strings when values are present.
            - Return only the structured final result matching the provided schema.

            Expected behavior:
            - Parse the job URL and refresh job_snapshot from the MCP result.
            - If a tailored resume artifact is missing or needs edits, create or update it.
            - When the resume phase is complete, use status resume_generated.
            """
        else:
            phase_instructions = """
            You are the JobAutomation application agent.

            Your job is to continue exactly one job-application execution session using the MCP tools already available
            to Codex in this environment. Use those tools directly when needed for browsing, field inspection, uploads,
            and application progress.

            Requirements:
            - Continue from the current session state instead of restarting blindly.
            - Read the session context carefully, including known answers, pending question state, checkpoints,
              job snapshot, and resume artifact state.
            - Use MCP tools when action is needed. Do not invent tool results.
            - If you need user input for a high-risk or unknown field, return a waiting-for-user result with a single question.
            - For action_payload, error_context, and metadata fields, return JSON-encoded strings when values are present.
            - Return only the structured final result matching the provided schema.

            Expected behavior:
            - Use the existing job snapshot and resume artifact.
            - If the application can proceed, inspect/fill the site and advance it.
            - If it reaches a user-input blocker, pause with a specific question.
            - If it reaches submit review state, use ready_for_submit.
            - Only use submitted if the application was actually submitted.
            """
        return textwrap.dedent(
            f"""
            {phase_instructions}

            Session JSON:
            {session_json}
            """
        ).strip()

    @staticmethod
    def _output_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status",
                "current_step",
                "current_action",
                "last_checkpoint",
                "workflow_name",
                "workflow_version",
                "action_payload",
                "error_context",
                "attempt_count",
                "job_snapshot",
                "resume_artifact",
                "question",
            ],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [status.value for status in SessionStatus],
                },
                "current_step": {"type": "string"},
                "current_action": {"type": ["string", "null"]},
                "last_checkpoint": {"type": ["string", "null"]},
                "workflow_name": {"type": ["string", "null"]},
                "workflow_version": {"type": ["string", "null"]},
                "action_payload": {"type": ["string", "null"]},
                "error_context": {"type": ["string", "null"]},
                "attempt_count": {"type": ["integer", "null"]},
                "job_snapshot": {"type": ["string", "null"]},
                "resume_artifact": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["kind", "path", "label", "metadata"],
                    "properties": {
                        "kind": {"type": "string", "enum": [kind.value for kind in ArtifactKind]},
                        "path": {"type": "string"},
                        "label": {"type": ["string", "null"]},
                        "metadata": {"type": ["string", "null"]},
                    },
                },
                "question": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["field_key", "prompt", "metadata"],
                    "properties": {
                        "field_key": {"type": "string"},
                        "prompt": {"type": "string"},
                        "metadata": {"type": ["string", "null"]},
                    },
                },
            },
        }

    @staticmethod
    def _parse_result(payload: dict[str, Any], *, codex_thread_id: str | None) -> CodexAgentResult:
        artifact_payload = payload.get("resume_artifact")
        question_payload = payload.get("question")
        artifact = None
        if isinstance(artifact_payload, dict):
            artifact = ArtifactReference(
                kind=ArtifactKind(artifact_payload["kind"]),
                path=artifact_payload["path"],
                label=artifact_payload.get("label"),
                metadata=_parse_json_object(artifact_payload.get("metadata")),
            )
        question = None
        if isinstance(question_payload, dict):
            question = CodexQuestion(
                field_key=question_payload["field_key"],
                prompt=question_payload["prompt"],
                metadata=_parse_json_object(question_payload.get("metadata")),
            )
        return CodexAgentResult(
            status=SessionStatus(payload["status"]),
            current_step=payload["current_step"],
            codex_thread_id=codex_thread_id,
            current_action=payload.get("current_action"),
            last_checkpoint=payload.get("last_checkpoint"),
            workflow_name=payload.get("workflow_name"),
            workflow_version=payload.get("workflow_version"),
            action_payload=_parse_json_object(payload.get("action_payload")),
            error_context=_parse_json_object(payload.get("error_context")),
            attempt_count=payload.get("attempt_count"),
            job_snapshot=_parse_json_object(payload.get("job_snapshot")),
            resume_artifact=artifact,
            question=question,
        )

    @staticmethod
    def _extract_thread_id(stdout: str) -> str | None:
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "thread.started" and isinstance(payload.get("thread_id"), str):
                return payload["thread_id"]
        return None


class BackgroundApplicationAgentRunner:
    def __init__(
        self,
        orchestrator: SessionOrchestrator,
        *,
        executor: CodexAgentExecutor,
        notifier: SessionQuestionNotifier | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.executor = executor
        self.notifier = notifier

    def start(self, session_id: str) -> None:
        self._spawn(session_id, trigger="start")

    def resume(self, session_id: str) -> None:
        self._spawn(session_id, trigger="resume")

    def run_once(self, session_id: str) -> None:
        session = self._require_session(session_id)
        logger.info(
            "application_runner_cycle_started session_id=%s status=%s checkpoint=%s",
            session_id,
            session.status,
            session.last_checkpoint,
        )
        try:
            phase = self._determine_phase(session)
            result = self.executor.run(
                session,
                phase=phase,
                thread_id=self._thread_id_for_phase(session, phase),
            )
            self._persist_result(session, phase, result)
        except Exception as exc:
            if self._pause_for_mcp_cancellation(session, phase, exc):
                logger.info("application_runner_cycle_paused_for_mcp_cancellation session_id=%s", session_id)
                return
            logger.exception("application_runner_cycle_failed session_id=%s", session_id)
            self.orchestrator.checkpoint(
                session_id,
                current_step="failed",
                current_action="runner_error",
                last_checkpoint="application_runner_failed",
                error_context={"message": str(exc), "type": exc.__class__.__name__},
                attempt_count=session.attempt_count + 1,
                status=SessionStatus.FAILED,
            )
            self.orchestrator.mark_failed(session_id, str(exc))
            return

        logger.info("application_runner_cycle_completed session_id=%s", session_id)

    def _pause_for_mcp_cancellation(
        self,
        session: ApplicationSession,
        phase: AgentPhase,
        exc: Exception,
    ) -> bool:
        message = str(exc)
        if "user cancelled MCP tool call" not in message:
            return False

        self.orchestrator.store.update_session(
            session.session_id,
            {"active_codex_phase": phase.value},
        )
        question = CodexQuestion(
            field_key="mcp_retry",
            prompt=(
                "Required MCP tool calls were cancelled. Reply `retry` to try the run again, "
                "or send the job description text manually if URL parsing keeps failing."
            ),
            metadata={"reason": "mcp_tool_call_cancelled"},
        )
        self.orchestrator.pause_for_question(
            session.session_id,
            field_key=question.field_key,
            prompt=question.prompt,
            metadata=question.metadata,
            current_step="waiting_for_user",
            current_action="await_mcp_retry",
            last_checkpoint="mcp_tool_call_cancelled",
            workflow_name=session.workflow_name,
            workflow_version=session.workflow_version,
            action_payload=session.action_payload,
            error_context={"message": message, "type": exc.__class__.__name__},
            attempt_count=session.attempt_count + 1,
        )
        if self.notifier is not None:
            refreshed_session = self.orchestrator.store.get_session(session.session_id)
            if refreshed_session is not None:
                self.notifier.notify_question(refreshed_session, question)
        return True

    def _persist_result(self, session: ApplicationSession, phase: AgentPhase, result: CodexAgentResult) -> None:
        session_updates: dict[str, Any] = {"active_codex_phase": phase.value}
        if result.codex_thread_id:
            session_updates[self._thread_field_for_phase(phase)] = result.codex_thread_id
        self.orchestrator.store.update_session(session.session_id, session_updates)
        if result.job_snapshot:
            self.orchestrator.record_job_parse(session.session_id, result.job_snapshot)
        if result.resume_artifact is not None:
            self.orchestrator.record_resume_artifact(session.session_id, result.resume_artifact)
        if result.question is not None:
            self.orchestrator.pause_for_question(
                session.session_id,
                field_key=result.question.field_key,
                prompt=result.question.prompt,
                metadata=result.question.metadata,
                current_step=result.current_step,
                current_action=result.current_action,
                last_checkpoint=result.last_checkpoint,
                workflow_name=result.workflow_name or session.workflow_name,
                workflow_version=result.workflow_version or session.workflow_version,
                action_payload=result.action_payload,
                error_context=result.error_context,
                attempt_count=result.attempt_count or (session.attempt_count + 1),
            )
            if self.notifier is not None:
                refreshed_session = self.orchestrator.store.get_session(session.session_id)
                if refreshed_session is not None:
                    self.notifier.notify_question(refreshed_session, result.question)
            return
        self.orchestrator.checkpoint(
            session.session_id,
            current_step=result.current_step,
            current_action=result.current_action,
            last_checkpoint=result.last_checkpoint,
            workflow_name=result.workflow_name or session.workflow_name,
            workflow_version=result.workflow_version or session.workflow_version,
            action_payload=result.action_payload,
            error_context=result.error_context,
            attempt_count=result.attempt_count or (session.attempt_count + 1),
            status=result.status,
        )

    def _spawn(self, session_id: str, *, trigger: str) -> None:
        logger.info("application_runner_scheduled session_id=%s trigger=%s", session_id, trigger)
        worker = Thread(target=self.run_once, args=(session_id,), daemon=True)
        worker.start()

    def _require_session(self, session_id: str) -> ApplicationSession:
        session = self.orchestrator.store.get_session(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        return session

    @staticmethod
    def _determine_phase(session: ApplicationSession) -> AgentPhase:
        if session.pending_question_id and session.active_codex_phase is not None:
            return session.active_codex_phase
        if session.resume_artifact is None:
            return AgentPhase.RESUME
        return AgentPhase.APPLICATION

    @staticmethod
    def _thread_id_for_phase(session: ApplicationSession, phase: AgentPhase) -> str | None:
        if phase == AgentPhase.RESUME:
            return session.resume_codex_thread_id
        return session.application_codex_thread_id

    @staticmethod
    def _thread_field_for_phase(phase: AgentPhase) -> str:
        if phase == AgentPhase.RESUME:
            return "resume_codex_thread_id"
        return "application_codex_thread_id"


def _parse_json_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object string.")
    return parsed
