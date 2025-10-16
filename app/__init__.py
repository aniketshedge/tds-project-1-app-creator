from __future__ import annotations

from flask import Flask
from dotenv import load_dotenv

from .config import Settings
from .jobqueue import create_queue
from .logger import configure_logging
from .storage import TaskRepository


def create_app() -> Flask:
    """Application factory."""
    load_dotenv()
    settings = Settings()
    settings.resolve_paths()

    configure_logging(settings.log_file)

    app = Flask(__name__)
    app.config["settings"] = settings
    app.config["repository"] = TaskRepository(settings.database_path)
    app.config["queue"] = create_queue(
        settings.redis_url, default_timeout=settings.request_timeout_seconds * 4
    )

    from .routes import register_routes  # Local import to avoid circular dependency

    register_routes(app)
    return app
