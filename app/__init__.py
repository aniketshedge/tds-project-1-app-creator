from __future__ import annotations

from flask import Flask
from dotenv import load_dotenv

from .config import Settings
from .jobqueue import create_queue, create_redis
from .logger import configure_logging
from .services.session_store import SessionStore
from .storage import TaskRepository


def create_app() -> Flask:
    """Application factory."""
    load_dotenv()
    settings = Settings()
    settings.resolve_paths()

    configure_logging(settings.log_file)

    app = Flask(
        __name__,
        static_folder=settings.frontend_dist,
        static_url_path="",
    )
    app.config["settings"] = settings
    app.config["MAX_CONTENT_LENGTH"] = settings.max_request_bytes

    redis = create_redis(settings.redis_url)
    app.config["redis"] = redis
    app.config["repository"] = TaskRepository(settings.database_path)
    app.config["queue"] = create_queue(
        redis, default_timeout=settings.request_timeout_seconds * 6
    )
    app.config["session_store"] = SessionStore(redis, settings)

    from .routes import register_routes

    register_routes(app)
    return app
