from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import EmailStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    flask_env: str = "production"

    accepted_secret: str

    perplexity_api_key: str
    perplexity_model: str = "pplx-70b-online"

    github_token: str
    github_username: str
    github_email: EmailStr
    github_org: Optional[str] = None
    github_default_branch: str = "main"

    redis_url: str = "redis://127.0.0.1:6379/0"
    database_path: str = "./data/tasks.db"
    workspace_root: str = "/tmp/task-runner"

    log_file: str = "./server.log"

    request_timeout_seconds: int = 30
    max_retries: int = 3
    attachment_max_bytes: int = 1_048_576  # 1 MB
    evaluation_callback_timeout: int = 15

    def resolve_paths(self) -> None:
        """Expand user-relative paths for filesystem resources."""
        db_path = Path(self.database_path).expanduser().resolve()
        workspace = Path(self.workspace_root).expanduser().resolve()
        log_path = Path(self.log_file).expanduser().resolve()

        self.database_path = str(db_path)
        self.workspace_root = str(workspace)
        self.log_file = str(log_path)

        workspace.mkdir(parents=True, exist_ok=True)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
