from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from application_orchestrator.config import FirestoreSettings, load_local_env
from application_orchestrator.firestore_store import FirestoreSessionStore
from application_orchestrator.runner import BackgroundApplicationAgentRunner, CodexCliAgentExecutor
from application_orchestrator.service import SessionOrchestrator
from application_orchestrator.telegram_adapter import (
    TelegramBotClient,
    TelegramCommandRouter,
    TelegramQuestionNotifier,
    poll_telegram_updates,
    run_telegram_webhook_server,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the JobAutomation Telegram adapter.")
    parser.add_argument(
        "--mode",
        choices=["polling", "webhook"],
        default=os.getenv("TELEGRAM_MODE", "polling"),
    )
    parser.add_argument("--host", default=os.getenv("TELEGRAM_WEBHOOK_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TELEGRAM_WEBHOOK_PORT", "8080")))
    parser.add_argument(
        "--poll-timeout-seconds",
        type=int,
        default=int(os.getenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "30")),
    )
    parser.add_argument(
        "--idle-sleep-seconds",
        type=float,
        default=float(os.getenv("TELEGRAM_IDLE_SLEEP_SECONDS", "1")),
    )
    parser.add_argument(
        "--drop-pending-updates",
        action="store_true",
        default=(os.getenv("TELEGRAM_DROP_PENDING_UPDATES", "").lower() in {"1", "true", "yes"}),
    )
    parser.add_argument(
        "--workflow-name",
        default=os.getenv("JOBAUTOMATION_WORKFLOW_NAME", "job_application"),
    )
    parser.add_argument(
        "--workflow-version",
        default=os.getenv("JOBAUTOMATION_WORKFLOW_VERSION", "v1"),
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    load_local_env()
    logging.basicConfig(
        level=os.getenv("JOBAUTOMATION_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN environment variable.")

    settings = FirestoreSettings.from_env()
    store = FirestoreSessionStore(settings)
    orchestrator = SessionOrchestrator(store, default_user_id=settings.default_user_id)
    bot_client = TelegramBotClient(bot_token)
    runner = BackgroundApplicationAgentRunner(
        orchestrator,
        executor=CodexCliAgentExecutor(workspace_root=WORKSPACE_ROOT),
        notifier=TelegramQuestionNotifier(bot_client),
    )
    router = TelegramCommandRouter(
        orchestrator,
        workflow_name=args.workflow_name,
        workflow_version=args.workflow_version,
        runner=runner,
    )
    if args.mode == "webhook":
        run_telegram_webhook_server(
            args.host,
            args.port,
            router=router,
            bot_client=bot_client,
        )
        return

    bot_client.delete_webhook(drop_pending_updates=args.drop_pending_updates)
    poll_telegram_updates(
        router,
        bot_client,
        poll_timeout_seconds=args.poll_timeout_seconds,
        idle_sleep_seconds=args.idle_sleep_seconds,
    )


if __name__ == "__main__":
    main()
