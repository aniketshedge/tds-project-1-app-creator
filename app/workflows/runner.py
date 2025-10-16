from __future__ import annotations

import logging
from textwrap import dedent

from dotenv import load_dotenv

from ..config import Settings
from ..models import TaskRequest
from ..services.evaluation import notify_evaluation
from ..services.github import GitHubClient, DeploymentResult, generate_repo_name
from ..services.perplexity import PerplexityClient
from ..services.workspace import WorkspaceManager
from ..storage import TaskRepository

logger = logging.getLogger(__name__)


def process_job(job_id: str) -> None:
    load_dotenv()
    settings = Settings()
    settings.resolve_paths()

    repository = TaskRepository(settings.database_path)
    record = repository.fetch_task(job_id)
    if not record:
        logger.error("No task record found for job_id=%s", job_id)
        return

    task_request = TaskRequest.model_validate(record.payload)
    repository.update_status(job_id, "in_progress")

    previous_repo = repository.find_latest_with_repo(task_request.task)
    existing_repo_full_name = None
    force_push = False

    if previous_repo and (previous_repo.round < task_request.round):
        if previous_repo.repo_url:
            existing_repo_full_name = _repo_full_name_from_url(previous_repo.repo_url)
            force_push = True

    workspace = WorkspaceManager(settings.workspace_root, job_id)

    try:
        _validate_attachments(task_request, settings.attachment_max_bytes)

        perplexity = PerplexityClient(
            settings.perplexity_api_key,
            settings.perplexity_model,
            settings.request_timeout_seconds,
            settings.max_retries,
        )
        manifest = perplexity.generate_manifest(task_request, task_request.attachments)

        workspace.write_manifest(manifest)
        workspace.write_attachments(task_request.attachments, settings.attachment_max_bytes)
        workspace.ensure_readme(_default_readme(task_request))

        if manifest.commands:
            workspace.run_commands(manifest.commands)

        github = GitHubClient(
            settings.github_token,
            settings.github_username,
            settings.github_email,
            settings.github_default_branch,
            settings.github_org,
            settings.request_timeout_seconds,
            settings.max_retries,
        )

        if existing_repo_full_name:
            repo_name = existing_repo_full_name.split("/")[-1]
        else:
            repo_name = generate_repo_name(task_request.task)

        deployment = github.deploy(
            workspace.path,
            manifest,
            repo_name,
            task_request.brief,
            existing_repo_full_name=existing_repo_full_name,
            force=force_push,
        )

        repository.update_status(
            job_id,
            "deployed",
            repo_url=deployment.repo_url,
            commit_sha=deployment.commit_sha,
            pages_url=deployment.pages_url,
        )

        notify_payload = {
            "email": task_request.email,
            "task": task_request.task,
            "round": task_request.round,
            "nonce": task_request.nonce,
            "repo_url": deployment.repo_url,
            "commit_sha": deployment.commit_sha,
            "pages_url": deployment.pages_url,
        }
        notify_evaluation(
            task_request.evaluation_url,
            notify_payload,
            settings.evaluation_callback_timeout,
            settings.max_retries,
        )

        repository.update_status(job_id, "completed", evaluation_status="success")
        logger.info("Job %s completed successfully", job_id)

    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        repository.update_status(job_id, "failed", error=str(exc))
        raise
    finally:
        workspace.cleanup()


def _validate_attachments(request: TaskRequest, limit: int) -> None:
    for attachment in request.attachments:
        payload = attachment.decode()
        if len(payload) > limit:
            raise ValueError(f"Attachment {attachment.name} exceeds limit of {limit} bytes")


def _default_readme(request: TaskRequest) -> str:
    checks = "\n".join(f"- {check}" for check in request.checks) or "- Not specified"
    attachments = "\n".join(f"- {att.name}" for att in request.attachments) or "- None"
    return dedent(
        f"""
        # Automated Deployment

        This repository was generated automatically for task `{request.task}` (round {request.round}).

        ## Brief
        {request.brief}

        ## Evaluation Checks
        {checks}

        ## Attachments Included
        {attachments}

        ## Deployment
        The site is deployed via GitHub Pages. Update the repository and push to trigger a new deployment.
        """
    ).strip()


def _repo_full_name_from_url(url: str) -> str:
    prefix = "https://github.com/"
    if url.startswith(prefix):
        return url[len(prefix) :].rstrip("/")
    return url.rstrip("/")
