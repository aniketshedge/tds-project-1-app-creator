from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    flask_env: str = "production"

    # Session / auth
    session_cookie_name: str = "app_session"
    session_ttl_seconds: int = 86_400
    session_cookie_secure: bool = False

    github_oauth_client_id: str
    github_oauth_client_secret: str
    github_oauth_redirect_uri: str
    github_oauth_scope: str = "repo read:user user:email"
    frontend_callback_path: str = "/integrations"

    # Queue / storage
    redis_url: str = "redis://127.0.0.1:6379/0"
    database_path: str = "./data/tasks.db"
    workspace_root: str = "/tmp/task-runner"
    attachment_root: str = "./data/attachments"
    frontend_dist: str = "./frontend/dist"

    # Logging
    log_file: str = "./server.log"

    # Runtime behavior
    request_timeout_seconds: int = 30
    max_retries: int = 3
    attachment_max_bytes: int = 5_242_880  # 5 MB
    job_secret_ttl_seconds: int = 7_200
    allow_manifest_commands: bool = False

    # Defaults
    perplexity_default_model: str = "sonar-pro"

    # Dev convenience (for Vite dev server)
    cors_allow_origin: str = "*"

    def resolve_paths(self) -> None:
        db_path = Path(self.database_path).expanduser().resolve()
        workspace = Path(self.workspace_root).expanduser().resolve()
        attach_root = Path(self.attachment_root).expanduser().resolve()
        log_path = Path(self.log_file).expanduser().resolve()
        frontend_dist = Path(self.frontend_dist).expanduser().resolve()

        self.database_path = str(db_path)
        self.workspace_root = str(workspace)
        self.attachment_root = str(attach_root)
        self.log_file = str(log_path)
        self.frontend_dist = str(frontend_dist)

        workspace.mkdir(parents=True, exist_ok=True)
        attach_root.mkdir(parents=True, exist_ok=True)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
