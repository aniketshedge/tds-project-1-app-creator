from __future__ import annotations

from __future__ import annotations

import logging
from uuid import uuid4

from flask import Flask, jsonify, request, current_app
from pydantic import ValidationError

from .models import TaskRecord, TaskRequest
from .storage import TaskRepository

logger = logging.getLogger(__name__)


def register_routes(app: Flask) -> None:
    """Attach HTTP routes to the Flask app."""

    @app.get("/health")
    def healthcheck() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.post("/tasks")
    def submit_task() -> tuple[dict[str, object], int]:
        settings = current_app.config["settings"]
        repository: TaskRepository = current_app.config["repository"]
        queue = current_app.config["queue"]

        try:
            payload = request.get_json(force=True)
        except Exception:
            logger.exception("Failed to parse JSON request body")
            return {"error": "bad_request", "message": "Invalid JSON payload"}, 400

        logger.info("Incoming task request: %s", _redact_secret(payload))

        try:
            task_request = TaskRequest.model_validate(payload)
        except ValidationError as exc:
            logger.warning("Task validation failed: %s", exc)
            return {"error": "validation_error", "details": exc.errors()}, 400

        if task_request.secret != settings.accepted_secret:
            logger.warning("Rejected task with invalid secret for task_id=%s", task_request.task)
            return {"error": "invalid_secret"}, 403

        job_id = str(uuid4())
        repository.record_task(job_id, task_request)
        queue.enqueue("app.workflows.runner.process_job", job_id)

        response = {"job_id": job_id, "status": "queued"}
        logger.info(
            "Queued job %s for task_id=%s round=%s", job_id, task_request.task, task_request.round
        )
        return response, 200

    @app.get("/tasks/<job_id>")
    def get_task(job_id: str) -> tuple[dict[str, object], int]:
        repository: TaskRepository = current_app.config["repository"]
        record = repository.fetch_task(job_id)
        if not record:
            return {"error": "not_found"}, 404
        return _serialize_record(record), 200


def _serialize_record(record: TaskRecord) -> dict[str, object]:
    return {
        "job_id": record.job_id,
        "task": record.task,
        "round": record.round,
        "status": record.status,
        "repo_url": record.repo_url,
        "commit_sha": record.commit_sha,
        "pages_url": record.pages_url,
        "error": record.error,
        "evaluation_status": record.evaluation_status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "payload": record.payload,
    }


def _redact_secret(payload: dict[str, object]) -> dict[str, object]:
    if not isinstance(payload, dict):
        return payload
    redacted = dict(payload)
    if "secret" in redacted:
        redacted["secret"] = "***"
    return redacted
