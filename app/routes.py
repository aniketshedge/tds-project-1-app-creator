from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import shutil
import zipfile
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from flask import (
    Flask,
    Response,
    current_app,
    jsonify,
    redirect,
    request,
    send_file,
    send_from_directory,
)
from pydantic import ValidationError

from .config import Settings
from .models import GitHubDeployPayload, JobCreatePayload, LLMIntegrationRequest
from .services.github_app_auth import (
    build_user_authorization_url,
    exchange_code_for_user_token,
    fetch_user_profile,
)
from .services.generation import llm_provider_catalog, resolve_model_for_provider
from .services.session_store import SessionStore
from .storage import TaskRepository

logger = logging.getLogger(__name__)


def register_routes(app: Flask) -> None:
    settings: Settings = app.config["settings"]
    base_path = "" if settings.app_base_path == "/" else settings.app_base_path

    def scoped(rule: str) -> str:
        if not base_path:
            return rule
        if rule == "/":
            return f"{base_path}/"
        return f"{base_path}{rule}"

    @app.after_request
    def add_cors_headers(response: Response) -> Response:
        allowed_origins = _parse_allowed_origins(settings)
        if not allowed_origins:
            return response

        origin = request.headers.get("Origin")
        if not origin:
            return response

        if "*" in allowed_origins:
            response.headers.setdefault("Access-Control-Allow-Origin", "*")
            response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
            response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            return response

        if origin in allowed_origins:
            response.headers.setdefault("Access-Control-Allow-Origin", origin)
            response.headers.setdefault("Access-Control-Allow-Credentials", "true")
            response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
            response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            vary = response.headers.get("Vary")
            response.headers["Vary"] = "Origin" if not vary else f"{vary}, Origin"
        return response

    @app.route(scoped("/api/<path:_path>"), methods=["OPTIONS"])
    def api_options(_path: str):
        return "", 204

    @app.get(scoped("/api/health"))
    def healthcheck() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.get(scoped("/api/session"))
    def get_session() -> Response:
        session_id, is_new = _get_or_create_session()
        response = jsonify(
            {
                "session_id": session_id,
                "expires_in_seconds": settings.session_ttl_seconds,
            }
        )
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.post(scoped("/api/session/reset"))
    def reset_session() -> Response:
        store: SessionStore = current_app.config["session_store"]

        old = request.cookies.get(settings.session_cookie_name)
        session_id = store.reset_session(old)

        response = jsonify(
            {
                "session_id": session_id,
                "expires_in_seconds": settings.session_ttl_seconds,
            }
        )
        _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/api/integrations"))
    def get_integrations() -> Response:
        session_id, is_new = _get_or_create_session()
        store: SessionStore = current_app.config["session_store"]

        response = jsonify(store.integration_state(session_id))
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/api/integrations/llm/catalog"))
    def get_llm_catalog() -> Response:
        session_id, is_new = _get_or_create_session()
        response = jsonify({"providers": llm_provider_catalog()})
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.post(scoped("/api/integrations/llm"))
    def configure_llm() -> Response:
        session_id, is_new = _get_or_create_session()
        store: SessionStore = current_app.config["session_store"]

        payload = request.get_json(silent=True) or {}
        try:
            model = LLMIntegrationRequest.model_validate(payload)
        except ValidationError as exc:
            return jsonify({"error": "validation_error", "details": exc.errors()}), 400

        try:
            llm_model = resolve_model_for_provider(model.provider, model.model)
        except ValueError as exc:
            return jsonify({"error": "validation_error", "message": str(exc)}), 400
        store.store_llm_credentials(
            session_id=session_id,
            provider=model.provider,
            api_key=model.api_key,
            model=llm_model,
        )

        response = jsonify(store.integration_state(session_id))
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/api/auth/github/start"))
    def github_auth_start() -> Response:
        session_id, is_new = _get_or_create_session()
        store: SessionStore = current_app.config["session_store"]

        state = uuid4().hex
        store.store_github_state(session_id, state)
        auth_url = build_user_authorization_url(
            client_id=settings.github_app_client_id,
            callback_url=settings.github_app_callback_url,
            scope=settings.github_app_scope,
            state=state,
        )

        payload: dict[str, object] = {"url": auth_url}
        if settings.github_app_slug:
            payload["install_url"] = f"https://github.com/apps/{settings.github_app_slug}/installations/new"

        response = jsonify(payload)
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/api/auth/github/callback"))
    def github_auth_callback() -> Response:
        store: SessionStore = current_app.config["session_store"]
        session_id = request.cookies.get(settings.session_cookie_name)

        if not session_id:
            return redirect(_frontend_redirect_url(settings, status="error", reason="no_session"))

        expected_state = store.consume_github_state(session_id)
        received_state = request.args.get("state")
        code = request.args.get("code")

        if not expected_state or not received_state or expected_state != received_state:
            return redirect(_frontend_redirect_url(settings, status="error", reason="invalid_state"))
        if not code:
            return redirect(_frontend_redirect_url(settings, status="error", reason="missing_code"))

        try:
            token_payload = exchange_code_for_user_token(
                client_id=settings.github_app_client_id,
                client_secret=settings.github_app_client_secret,
                code=code,
                callback_url=settings.github_app_callback_url,
                timeout=settings.request_timeout_seconds,
            )
            profile = fetch_user_profile(
                token_payload["access_token"],
                timeout=settings.request_timeout_seconds,
            )
            store.store_github_credentials(
                session_id=session_id,
                access_token=token_payload["access_token"],
                refresh_token=token_payload.get("refresh_token"),
                access_token_expires_in=token_payload.get("expires_in"),
                refresh_token_expires_in=token_payload.get("refresh_token_expires_in"),
                username=profile["username"],
            )
        except Exception:
            logger.exception("GitHub App callback failed")
            return redirect(_frontend_redirect_url(settings, status="error", reason="exchange_failed"))

        return redirect(_frontend_redirect_url(settings, status="connected"))

    @app.post(scoped("/api/auth/github/disconnect"))
    def github_auth_disconnect() -> Response:
        session_id, is_new = _get_or_create_session()
        store: SessionStore = current_app.config["session_store"]
        store.clear_github_credentials(session_id)

        response = jsonify(store.integration_state(session_id))
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.post(scoped("/api/jobs"))
    def submit_job() -> Response:
        session_id, is_new = _get_or_create_session()
        repository: TaskRepository = current_app.config["repository"]
        queue = current_app.config["queue"]
        store: SessionStore = current_app.config["session_store"]

        rate_limited = _enforce_rate_limit(
            session_id=session_id,
            action="job_submit",
            limit=settings.job_submit_limit_per_minute,
        )
        if rate_limited is not None:
            return rate_limited

        if queue.count >= settings.max_pending_queue_jobs:
            return (
                jsonify(
                    {
                        "error": "queue_overloaded",
                        "message": "Server queue is busy. Please try again in a minute.",
                    }
                ),
                503,
            )

        recent_jobs = repository.list_jobs_for_session(session_id, limit=200)
        active_jobs = [
            job for job in recent_jobs if job.status in {"queued", "in_progress", "deploying"}
        ]
        if len(active_jobs) >= settings.max_active_jobs_per_session:
            return (
                jsonify(
                    {
                        "error": "too_many_active_jobs",
                        "message": "You already have active jobs running. Wait for one to finish.",
                    }
                ),
                429,
            )

        try:
            payload_dict = request.get_json(silent=True)
            if not isinstance(payload_dict, dict):
                raise ValueError("Expected JSON object")
            payload = JobCreatePayload.model_validate(payload_dict)
        except ValueError as exc:
            return jsonify({"error": "bad_request", "message": str(exc)}), 400
        except ValidationError as exc:
            return jsonify({"error": "validation_error", "details": exc.errors()}), 400

        llm = store.get_llm_credentials(session_id)
        if not llm:
            return (
                jsonify(
                    {
                        "error": "integrations_required",
                        "message": "Configure an AI model before creating a job",
                    }
                ),
                400,
            )

        job_id = uuid4().hex
        repository.create_job(job_id, session_id, payload, llm["provider"], llm.get("model"))
        repository.append_event(job_id, "info", "Job queued")

        store.snapshot_job_secrets(
            job_id,
            session_id,
            include_llm=True,
            include_github=False,
        )
        queue.enqueue("app.workflows.runner.process_job", job_id)

        response = jsonify({"job_id": job_id, "status": "queued"})
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.post(scoped("/api/jobs/<job_id>/deploy"))
    def deploy_job(job_id: str) -> Response:
        session_id, is_new = _get_or_create_session()
        repository: TaskRepository = current_app.config["repository"]
        queue = current_app.config["queue"]
        store: SessionStore = current_app.config["session_store"]

        rate_limited = _enforce_rate_limit(
            session_id=session_id,
            action="deploy_submit",
            limit=settings.deploy_submit_limit_per_minute,
        )
        if rate_limited is not None:
            return rate_limited

        if queue.count >= settings.max_pending_queue_jobs:
            return (
                jsonify(
                    {
                        "error": "queue_overloaded",
                        "message": "Server queue is busy. Please try again in a minute.",
                    }
                ),
                503,
            )

        job = repository.fetch_job(job_id)
        if not job or job.session_id != session_id:
            return jsonify({"error": "not_found"}), 404
        if job.status in {"queued", "in_progress", "deploying"}:
            return jsonify({"error": "job_not_ready", "message": "Wait for generation to complete"}), 409
        artifact_path = _resolve_artifact_path(job, settings)
        if artifact_path is None:
            return (
                jsonify(
                    {
                        "error": "artifact_not_available",
                        "message": "Generate project files first before deploying to GitHub",
                    }
                ),
                400,
            )
        if not store.get_github_credentials(session_id):
            return (
                jsonify(
                    {
                        "error": "integrations_required",
                        "message": "Connect GitHub App before deploying",
                    }
                ),
                400,
            )

        try:
            deploy_payload = GitHubDeployPayload.model_validate(request.get_json(silent=True) or {})
        except ValidationError as exc:
            return jsonify({"error": "validation_error", "details": exc.errors()}), 400

        secret_ref = f"{job_id}:deploy:{uuid4().hex}"
        store.snapshot_job_secrets(
            secret_ref,
            session_id,
            include_llm=False,
            include_github=True,
        )

        repository.update_job(
            job_id,
            status="deploying",
            repo_name=deploy_payload.repo.name,
            repo_visibility=deploy_payload.repo.visibility,
            error_code="",
            error_message="",
        )
        repository.append_event(job_id, "info", "GitHub deployment queued")
        queue.enqueue(
            "app.workflows.runner.deploy_job_artifact",
            job_id,
            deploy_payload.model_dump(mode="json"),
            secret_ref,
        )

        response = jsonify({"job_id": job_id, "status": "deploying"})
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/api/jobs"))
    def list_jobs() -> Response:
        session_id, is_new = _get_or_create_session()
        repository: TaskRepository = current_app.config["repository"]

        jobs = repository.list_jobs_for_session(session_id)
        response = jsonify({"jobs": [_serialize_job(job, settings) for job in jobs]})
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/api/jobs/<job_id>"))
    def get_job(job_id: str) -> Response:
        session_id, is_new = _get_or_create_session()
        repository: TaskRepository = current_app.config["repository"]

        job = repository.fetch_job(job_id)
        if not job or job.session_id != session_id:
            return jsonify({"error": "not_found"}), 404

        response = jsonify(_serialize_job(job, settings))
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/api/jobs/<job_id>/events"))
    def get_job_events(job_id: str) -> Response:
        session_id, is_new = _get_or_create_session()
        repository: TaskRepository = current_app.config["repository"]

        job = repository.fetch_job(job_id)
        if not job or job.session_id != session_id:
            return jsonify({"error": "not_found"}), 404

        after = request.args.get("after", default="0")
        try:
            after_id = int(after)
        except ValueError:
            return jsonify({"error": "bad_request", "message": "Invalid 'after' parameter"}), 400

        events = repository.list_events(job_id, after_id=after_id)
        response = jsonify(
            {
                "events": [_serialize_event(event) for event in events],
                "next_after": events[-1].id if events else after_id,
            }
        )
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/api/jobs/<job_id>/download"))
    def download_job_artifact(job_id: str):
        session_id, is_new = _get_or_create_session()
        repository: TaskRepository = current_app.config["repository"]

        job = repository.fetch_job(job_id)
        if not job or job.session_id != session_id:
            return jsonify({"error": "not_found"}), 404
        artifact_path = _resolve_artifact_path(job, settings)
        if artifact_path is None:
            return jsonify({"error": "artifact_not_available"}), 404
        if not artifact_path.exists() or not artifact_path.is_file():
            return jsonify({"error": "artifact_not_found"}), 404

        response = send_file(
            artifact_path,
            as_attachment=True,
            download_name=job.artifact_name or artifact_path.name,
            mimetype="application/zip",
        )
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.post(scoped("/api/jobs/<job_id>/preview"))
    def create_job_preview(job_id: str) -> Response:
        session_id, is_new = _get_or_create_session()
        repository: TaskRepository = current_app.config["repository"]

        rate_limited = _enforce_rate_limit(
            session_id=session_id,
            action="preview_create",
            limit=settings.preview_create_limit_per_minute,
        )
        if rate_limited is not None:
            return rate_limited

        job = repository.fetch_job(job_id)
        if not job or job.session_id != session_id:
            return jsonify({"error": "not_found"}), 404
        artifact_path = _resolve_artifact_path(job, settings)
        if artifact_path is None:
            return jsonify({"error": "artifact_not_available"}), 404
        if not artifact_path.exists() or not artifact_path.is_file():
            return jsonify({"error": "artifact_not_found"}), 404

        _cleanup_expired_previews(settings)
        live_previews = _count_live_previews_for_session(settings, session_id)
        if live_previews >= settings.max_live_previews_per_session:
            return (
                jsonify(
                    {
                        "error": "too_many_live_previews",
                        "message": "You already have several active previews. Wait for expiry or reuse existing ones.",
                    }
                ),
                429,
            )

        token = uuid4().hex
        preview_root = Path(settings.preview_root).resolve()
        preview_dir = preview_root / token
        site_dir = preview_dir / "site"
        site_dir.mkdir(parents=True, exist_ok=True)

        try:
            _extract_zip_archive(artifact_path, site_dir)
        except Exception as exc:
            logger.exception("Failed to extract preview archive for job %s: %s", job_id, exc)
            shutil.rmtree(preview_dir, ignore_errors=True)
            return (
                jsonify(
                    {
                        "error": "preview_failed",
                        "message": "Unable to prepare preview files for this build",
                    }
                ),
                400,
            )

        if not (site_dir / "index.html").exists():
            shutil.rmtree(preview_dir, ignore_errors=True)
            return (
                jsonify(
                    {
                        "error": "preview_not_available",
                        "message": "Preview is available only for static builds containing index.html",
                    }
                ),
                400,
            )

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.preview_ttl_seconds)
        metadata = {
            "session_id": session_id,
            "job_id": job_id,
            "expires_at": expires_at.isoformat(),
        }
        (preview_dir / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")

        response = jsonify(
            {
                "preview_url": _app_url(settings, f"/preview/{token}/"),
                "expires_at": expires_at.isoformat(),
                "expires_in_seconds": settings.preview_ttl_seconds,
            }
        )
        if is_new:
            _set_session_cookie(response, session_id, settings)
        return response

    @app.get(scoped("/preview/<token>"))
    def serve_preview_root(token: str) -> Response:
        return redirect(_app_url(settings, f"/preview/{token}/"))

    @app.get(scoped("/preview/<token>/"))
    @app.get(scoped("/preview/<token>/<path:asset_path>"))
    def serve_preview_asset(token: str, asset_path: str = "index.html"):
        _cleanup_expired_previews(settings)

        site_dir = _resolve_preview_site(token, settings)
        if site_dir is None:
            return jsonify({"error": "preview_not_found"}), 404

        target = (site_dir / asset_path).resolve()
        try:
            target.relative_to(site_dir)
        except ValueError:
            return jsonify({"error": "invalid_preview_path"}), 400

        if target.exists() and target.is_file():
            return send_from_directory(str(site_dir), asset_path)

        index_file = site_dir / "index.html"
        if index_file.exists():
            return send_from_directory(str(site_dir), "index.html")
        return jsonify({"error": "preview_not_found"}), 404

    @app.get(scoped("/"))
    def serve_index() -> Response:
        static_root = current_app.static_folder
        if not static_root or not Path(static_root, "index.html").exists():
            return jsonify({"message": "Frontend is not built yet."}), 200
        return send_from_directory(static_root, "index.html")

    @app.get(scoped("/<path:path>"))
    def serve_spa(path: str) -> Response:
        if path.startswith("api/"):
            return jsonify({"error": "not_found"}), 404

        static_root = current_app.static_folder
        if not static_root:
            return jsonify({"message": "Frontend is not built yet."}), 200

        candidate = Path(static_root, path)
        if candidate.exists() and candidate.is_file():
            return send_from_directory(static_root, path)

        index_file = Path(static_root, "index.html")
        if index_file.exists():
            return send_from_directory(static_root, "index.html")
        return jsonify({"message": "Frontend is not built yet."}), 200

def _get_or_create_session() -> tuple[str, bool]:
    settings: Settings = current_app.config["settings"]
    store: SessionStore = current_app.config["session_store"]

    session_id = request.cookies.get(settings.session_cookie_name)
    return store.ensure_session(session_id)


def _set_session_cookie(response: Response, session_id: str, settings: Settings) -> None:
    cookie_path = settings.app_base_path if settings.app_base_path != "/" else "/"
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="Lax",
        path=cookie_path,
    )


def _frontend_redirect_url(settings: Settings, *, status: str, reason: str | None = None) -> str:
    path = (settings.frontend_callback_path or "").strip()
    if not path or path == "/":
        path = settings.app_base_path
    if not path.startswith("/"):
        path = f"/{path}"
    if path.startswith("/api/"):
        path = settings.app_base_path

    query = {"github": status}
    if reason:
        query["reason"] = reason
    return f"{path}?{urlencode(query)}"


def _app_url(settings: Settings, path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    if settings.app_base_path == "/":
        return path
    if path == "/":
        return f"{settings.app_base_path}/"
    if path.startswith(settings.app_base_path + "/"):
        return path
    return f"{settings.app_base_path}{path}"


def _resolve_artifact_path(job, settings: Settings) -> Path | None:
    if not job.artifact_path:
        return None

    artifact_path = Path(job.artifact_path).resolve()
    package_root = Path(settings.package_root).resolve()
    try:
        artifact_path.relative_to(package_root)
    except ValueError:
        logger.warning("Blocked artifact access outside package root: %s", artifact_path)
        return None
    return artifact_path


def _extract_zip_archive(artifact_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(artifact_path, "r") as archive:
        for member in archive.infolist():
            member_path = (target_dir / member.filename).resolve()
            try:
                member_path.relative_to(target_dir)
            except ValueError as exc:
                raise RuntimeError(f"ZIP archive contains invalid path: {member.filename}") from exc
        archive.extractall(target_dir)


def _cleanup_expired_previews(settings: Settings) -> None:
    preview_root = Path(settings.preview_root).resolve()
    if not preview_root.exists():
        return

    now = datetime.now(timezone.utc)
    for preview_dir in preview_root.iterdir():
        if not preview_dir.is_dir():
            continue

        metadata = _read_preview_metadata(preview_dir)
        if metadata is None:
            shutil.rmtree(preview_dir, ignore_errors=True)
            continue
        expires_at = metadata.get("expires_at")
        if not isinstance(expires_at, datetime) or expires_at <= now:
            shutil.rmtree(preview_dir, ignore_errors=True)


def _count_live_previews_for_session(settings: Settings, session_id: str) -> int:
    preview_root = Path(settings.preview_root).resolve()
    if not preview_root.exists():
        return 0

    count = 0
    now = datetime.now(timezone.utc)
    for preview_dir in preview_root.iterdir():
        if not preview_dir.is_dir():
            continue
        metadata = _read_preview_metadata(preview_dir)
        if metadata is None:
            continue
        expires_at = metadata.get("expires_at")
        if not isinstance(expires_at, datetime) or expires_at <= now:
            continue
        if metadata.get("session_id") == session_id:
            count += 1
    return count


def _enforce_rate_limit(
    session_id: str, action: str, limit: int, window_seconds: int = 60
) -> tuple[Response, int] | None:
    if limit <= 0:
        return None
    redis = current_app.config["redis"]
    settings: Settings = current_app.config["settings"]
    client_marker = request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown"
    key = f"rate:{action}:{settings.session_cookie_name}:{session_id}:{client_marker}"
    count = redis.incr(key)
    if count == 1:
        redis.expire(key, window_seconds)
    if count <= limit:
        return None
    retry_after = redis.ttl(key)
    response = jsonify(
        {
            "error": "rate_limited",
            "message": "Too many requests for this action. Try again shortly.",
            "retry_after_seconds": retry_after if retry_after and retry_after > 0 else window_seconds,
        }
    )
    return response, 429


def _resolve_preview_site(token: str, settings: Settings) -> Path | None:
    if not token or "/" in token or ".." in token:
        return None

    preview_root = Path(settings.preview_root).resolve()
    preview_dir = (preview_root / token).resolve()
    try:
        preview_dir.relative_to(preview_root)
    except ValueError:
        return None
    if not preview_dir.exists() or not preview_dir.is_dir():
        return None

    metadata = _read_preview_metadata(preview_dir)
    if metadata is None:
        shutil.rmtree(preview_dir, ignore_errors=True)
        return None

    expires_at = metadata.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.now(timezone.utc):
        shutil.rmtree(preview_dir, ignore_errors=True)
        return None

    site_dir = (preview_dir / "site").resolve()
    try:
        site_dir.relative_to(preview_dir)
    except ValueError:
        return None
    if not site_dir.exists() or not site_dir.is_dir():
        return None
    return site_dir


def _read_preview_metadata(preview_dir: Path) -> dict[str, object] | None:
    metadata_file = preview_dir / "meta.json"
    if not metadata_file.exists() or not metadata_file.is_file():
        return None
    try:
        payload = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    raw_expiry = payload.get("expires_at")
    if not isinstance(raw_expiry, str):
        return None
    try:
        expires_at = datetime.fromisoformat(raw_expiry)
    except ValueError:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    payload["expires_at"] = expires_at
    return payload


def _parse_allowed_origins(settings: Settings) -> set[str]:
    raw = settings.cors_allowed_origins.strip() if settings.cors_allowed_origins else ""
    if not raw:
        raw = settings.cors_allow_origin.strip() if settings.cors_allow_origin else ""
    if not raw:
        return set()
    if raw == "*":
        return {"*"}
    return {origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()}


def _serialize_job(job, settings: Settings) -> dict[str, object]:
    return {
        "job_id": job.id,
        "status": job.status,
        "title": job.title,
        "brief": job.brief,
        "llm_provider": job.llm_provider,
        "llm_model": job.llm_model,
        "delivery_mode": job.delivery_mode,
        "repo_name": job.repo_name,
        "repo_visibility": job.repo_visibility,
        "repo_full_name": job.repo_full_name,
        "repo_url": job.repo_url,
        "pages_url": job.pages_url,
        "commit_sha": job.commit_sha,
        "artifact_name": job.artifact_name,
        "download_url": _app_url(settings, f"/api/jobs/{job.id}/download") if job.artifact_path else None,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _serialize_event(event) -> dict[str, object]:
    return {
        "id": event.id,
        "level": event.level,
        "message": event.message,
        "created_at": event.created_at.isoformat(),
    }
