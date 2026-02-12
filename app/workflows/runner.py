from __future__ import annotations

import logging
import re
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from redis import Redis

from ..config import Settings
from ..models import GitHubDeployPayload, JobCreatePayload, PromptAttachment, iso_now
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

        repository.append_event(job_id, "info", "Packaging generated files as ZIP")
        package_root = Path(settings.package_root)
        artifact_name = _zip_artifact_name(job_payload.title, job_id)
        artifact_path = _create_zip_archive(workspace.path, package_root / artifact_name)
        repository.update_job(
            job_id,
            status="completed",
            artifact_path=str(artifact_path),
            artifact_name=artifact_name,
            completed_at=iso_now(),
        )
        repository.append_event(job_id, "info", "Build complete. ZIP package ready for download")

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


def deploy_job_artifact(job_id: str, deploy_payload_data: dict, secret_ref: str) -> None:
    load_dotenv()
    settings = Settings()
    settings.resolve_paths()

    repository = TaskRepository(settings.database_path)
    redis = Redis.from_url(settings.redis_url)
    session_store = SessionStore(redis, settings)
    workspace = WorkspaceManager(settings.workspace_root, f"{job_id}-deploy-{uuid4().hex[:8]}")

    try:
        record = repository.fetch_job(job_id)
        if not record:
            logger.error("No job record found for id=%s", job_id)
            return

        deploy_payload = GitHubDeployPayload.model_validate(deploy_payload_data)
        artifact_path = _resolve_artifact_path(record.artifact_path, settings.package_root)
        if artifact_path is None or not artifact_path.exists() or not artifact_path.is_file():
            raise RuntimeError("Generated ZIP artifact is not available for deployment")

        secrets = session_store.get_job_secrets(secret_ref)
        if not secrets:
            raise RuntimeError("GitHub credentials expired before deployment started")

        github_secret = secrets.get("github")
        if not github_secret:
            raise RuntimeError("Missing GitHub credentials for deployment")
        github_token = github_secret.get("access_token")
        github_username = github_secret.get("username")
        if not github_token or not github_username:
            raise RuntimeError("Missing GitHub App access token for deployment")

        repository.append_event(job_id, "info", "Preparing files for GitHub deployment")
        _extract_zip_archive(artifact_path, workspace.path)

        github = GitHubClient(
            token=github_token,
            username=github_username,
            default_branch=deploy_payload.deployment.branch,
            timeout=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )

        repository.append_event(job_id, "info", "Deploying to GitHub")
        deployment = github.deploy(
            workspace=workspace.path,
            repo_name=deploy_payload.repo.name,
            description=record.brief,
            visibility=deploy_payload.repo.visibility,
            enable_pages=deploy_payload.deployment.enable_pages,
            branch=deploy_payload.deployment.branch,
            pages_path=deploy_payload.deployment.path,
        )

        repository.update_job(
            job_id,
            status="completed",
            repo_name=deploy_payload.repo.name,
            repo_visibility=deploy_payload.repo.visibility,
            repo_full_name=deployment.repo_full_name,
            repo_url=deployment.repo_url,
            pages_url=deployment.pages_url,
            commit_sha=deployment.commit_sha,
            error_code="",
            error_message="",
            completed_at=iso_now(),
        )
        repository.append_event(job_id, "info", "GitHub deployment completed")
    except Exception as exc:
        logger.exception("GitHub deployment failed for job %s: %s", job_id, exc)
        repository.update_job(
            job_id,
            status="deploy_failed",
            error_code="deploy_failed",
            error_message=str(exc),
            completed_at=iso_now(),
        )
        repository.append_event(job_id, "error", f"GitHub deployment failed: {exc}")
        raise
    finally:
        session_store.clear_job_secrets(secret_ref)
        workspace.cleanup()


def _zip_artifact_name(title: str, job_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "generated-app"
    return f"{slug}-{job_id[:8]}.zip"


def _create_zip_archive(workspace: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(workspace.rglob("*")):
            if not file_path.is_file():
                continue
            relative_path = file_path.relative_to(workspace)
            archive.write(file_path, arcname=str(relative_path))
    return target


def _extract_zip_archive(artifact_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(artifact_path, "r") as archive:
        for member in archive.infolist():
            member_path = (target_dir / member.filename).resolve()
            try:
                member_path.relative_to(target_dir)
            except ValueError:
                raise RuntimeError(f"ZIP archive contains invalid path: {member.filename}")
        archive.extractall(target_dir)


def _resolve_artifact_path(artifact_path: str | None, package_root: str) -> Path | None:
    if not artifact_path:
        return None
    resolved_artifact = Path(artifact_path).resolve()
    resolved_root = Path(package_root).resolve()
    try:
        resolved_artifact.relative_to(resolved_root)
    except ValueError:
        logger.warning("Blocked artifact access outside package root: %s", resolved_artifact)
        return None
    return resolved_artifact
