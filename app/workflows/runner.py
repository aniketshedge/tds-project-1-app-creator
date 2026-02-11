from __future__ import annotations

import logging
import shutil
from pathlib import Path

from dotenv import load_dotenv
from redis import Redis

from ..config import Settings
from ..models import JobCreatePayload, PromptAttachment, iso_now
from ..services.github import GitHubClient
from ..services.perplexity import PerplexityClient
from ..services.session_store import SessionStore
from ..services.workspace import WorkspaceManager
from ..storage import TaskRepository

logger = logging.getLogger(__name__)


def process_job(job_id: str) -> None:
    load_dotenv()
    settings = Settings()
    settings.resolve_paths()

    repository = TaskRepository(settings.database_path)
    redis = Redis.from_url(settings.redis_url)
    session_store = SessionStore(redis, settings)

    record = repository.fetch_job(job_id)
    if not record:
        logger.error("No job record found for id=%s", job_id)
        return

    repository.update_job(job_id, status="in_progress", started_at=iso_now())
    repository.append_event(job_id, "info", "Job started")

    workspace = WorkspaceManager(settings.workspace_root, job_id)
    attachment_dir = Path(settings.attachment_root) / job_id

    try:
        job_payload = JobCreatePayload.model_validate(record.payload)
        attachment_records = repository.list_attachments(job_id)
        attachment_files: list[tuple[str, bytes]] = []
        prompt_attachments: list[PromptAttachment] = []

        for attachment in attachment_records:
            file_path = attachment_dir / attachment.file_name
            data = file_path.read_bytes()
            attachment_files.append((attachment.file_name, data))
            prompt_attachments.append(
                PromptAttachment(
                    file_name=attachment.file_name,
                    media_type=attachment.media_type or "application/octet-stream",
                    data=data,
                )
            )

        secrets = session_store.get_job_secrets(job_id)
        if not secrets:
            raise RuntimeError("Job credentials expired before processing started")

        llm_secret = secrets["llm"]
        github_secret = secrets["github"]
        github_token = github_secret.get("access_token")
        github_username = github_secret.get("username")
        if not github_token or not github_username:
            raise RuntimeError("Missing GitHub App access token for this job")

        model = llm_secret.get("model") or settings.perplexity_default_model
        repository.append_event(job_id, "info", f"Generating app files with {llm_secret['provider']}")
        llm = PerplexityClient(
            api_key=llm_secret["api_key"],
            model=model,
            timeout=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )
        manifest = llm.generate_manifest(job_payload.brief, prompt_attachments)

        workspace.write_manifest(manifest)
        workspace.write_attachment_files(attachment_files)

        if manifest.commands and settings.allow_manifest_commands:
            repository.append_event(job_id, "info", "Running manifest commands")
            workspace.run_commands(manifest.commands)

        github = GitHubClient(
            token=github_token,
            username=github_username,
            default_branch=job_payload.deployment.branch,
            timeout=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )

        repository.append_event(job_id, "info", "Deploying to GitHub")
        deployment = github.deploy(
            workspace=workspace.path,
            repo_name=job_payload.repo.name,
            description=job_payload.brief,
            visibility=job_payload.repo.visibility,
            enable_pages=job_payload.deployment.enable_pages,
            branch=job_payload.deployment.branch,
            pages_path=job_payload.deployment.path,
        )

        repository.update_job(
            job_id,
            status="completed",
            repo_full_name=deployment.repo_full_name,
            repo_url=deployment.repo_url,
            pages_url=deployment.pages_url,
            commit_sha=deployment.commit_sha,
            completed_at=iso_now(),
        )
        repository.append_event(job_id, "info", "Job completed")

    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        repository.update_job(
            job_id,
            status="failed",
            error_code="job_failed",
            error_message=str(exc),
            completed_at=iso_now(),
        )
        repository.append_event(job_id, "error", f"Job failed: {exc}")
        raise
    finally:
        session_store.clear_job_secrets(job_id)
        workspace.cleanup()
        if attachment_dir.exists():
            shutil.rmtree(attachment_dir)
