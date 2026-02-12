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

    github_app_client_id: str
    github_app_client_secret: str
    github_app_callback_url: str
    github_app_scope: str = "repo read:user user:email"
    github_app_slug: str = ""
    frontend_callback_path: str = "/"

    # Queue / storage
    redis_url: str = "redis://127.0.0.1:6379/0"
    database_path: str = "./data/tasks.db"
    workspace_root: str = "/tmp/task-runner"
    attachment_root: str = "./data/attachments"
    package_root: str = "./data/packages"
    preview_root: str = "./data/previews"
    frontend_dist: str = "./frontend/dist"

    # Logging
    log_file: str = "./server.log"

    # Runtime behavior
    request_timeout_seconds: int = 30
    max_retries: int = 3
    attachment_max_bytes: int = 5_242_880  # 5 MB
    job_secret_ttl_seconds: int = 7_200
    allow_manifest_commands: bool = False
    preview_ttl_seconds: int = 3_600

    # Dev convenience (for Vite dev server)
    cors_allow_origin: str = "*"

    def resolve_paths(self) -> None:
        db_path = Path(self.database_path).expanduser().resolve()
        workspace = Path(self.workspace_root).expanduser().resolve()
        attach_root = Path(self.attachment_root).expanduser().resolve()
        package_root = Path(self.package_root).expanduser().resolve()
        preview_root = Path(self.preview_root).expanduser().resolve()
        log_path = Path(self.log_file).expanduser().resolve()
        frontend_dist = Path(self.frontend_dist).expanduser().resolve()

        self.database_path = str(db_path)
        self.workspace_root = str(workspace)
        self.attachment_root = str(attach_root)
        self.package_root = str(package_root)
        self.preview_root = str(preview_root)
        self.log_file = str(log_path)
        self.frontend_dist = str(frontend_dist)

        workspace.mkdir(parents=True, exist_ok=True)
        attach_root.mkdir(parents=True, exist_ok=True)
        package_root.mkdir(parents=True, exist_ok=True)
        preview_root.mkdir(parents=True, exist_ok=True)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
