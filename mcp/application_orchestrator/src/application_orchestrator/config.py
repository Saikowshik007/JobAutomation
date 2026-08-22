from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional at import time
    load_dotenv = None


def load_local_env(start_path: Path | None = None) -> None:
    if load_dotenv is None:
        return

    current = (start_path or Path.cwd()).resolve()
    candidates = [current, *current.parents]
    for directory in candidates:
        env_path = directory / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
            return


class FirestoreSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    database: str = "(default)"
    app_name: str = "jobautomation"
    sessions_collection: str = "sessions"
    use_emulator: bool = False
    emulator_host: str | None = None
    credentials_path: str | None = None
    user_id_field: str = "userId"
    default_user_id: str = "local-user"
    web_app_id: str | None = Field(default=None, description="Optional frontend app id for traceability.")

    @classmethod
    def from_env(cls) -> "FirestoreSettings":
        load_local_env()
        project_id = (
            os.getenv("FIREBASE_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCLOUD_PROJECT")
        )
        if not project_id:
            raise ValueError(
                "Missing Firebase project id. Set FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT."
            )
        return cls(
            project_id=project_id,
            database=os.getenv("FIRESTORE_DATABASE", "(default)"),
            app_name=os.getenv("FIREBASE_APP_NAME", "jobautomation"),
            sessions_collection=os.getenv("FIRESTORE_SESSIONS_COLLECTION", "sessions"),
            use_emulator=(os.getenv("FIRESTORE_USE_EMULATOR", "").lower() in {"1", "true", "yes"}),
            emulator_host=os.getenv("FIRESTORE_EMULATOR_HOST"),
            credentials_path=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
            user_id_field=os.getenv("FIRESTORE_USER_ID_FIELD", "userId"),
            default_user_id=os.getenv("DEFAULT_JOBAUTOMATION_USER_ID", "local-user"),
            web_app_id=os.getenv("FIREBASE_WEB_APP_ID"),
        )

    @classmethod
    def from_web_config(
        cls,
        *,
        project_id: str,
        app_id: str | None = None,
    ) -> "FirestoreSettings":
        return cls(project_id=project_id, web_app_id=app_id)
