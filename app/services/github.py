from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
MIT_LICENSE_TEXT = """MIT License

Copyright (c) {year} {owner}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


@dataclass
class DeploymentResult:
    repo_full_name: str
    repo_url: str
    commit_sha: str
    pages_url: Optional[str]


def normalize_repo_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip()).strip("-.") or "app"


def _shorten_description(description: str) -> str:
    if not description:
        return ""
    collapsed = " ".join(description.split())
    return textwrap.shorten(collapsed, width=140, placeholder="…")


class GitHubClient:
    def __init__(
        self,
        token: str,
        username: str,
        default_branch: str,
        timeout: int,
        max_retries: int,
    ):
        self.username = username
        self.email = f"{username}@users.noreply.github.com"
        self.token = token
        self.default_branch = default_branch
        self.timeout = timeout
        self.max_retries = max_retries

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": f"app-creator/{self.username}",
            }
        )

    def create_repository(self, name: str, description: str, visibility: str) -> str:
        requested_name = normalize_repo_name(name)
        for attempt in range(1, 6):
            candidate = requested_name if attempt == 1 else f"{requested_name}-{uuid4().hex[:5]}"
            url = f"{API_BASE}/user/repos"
            payload = {
                "name": candidate,
                "description": _shorten_description(description),
                "private": visibility == "private",
                "auto_init": False,
            }
            response = self.session.post(url, json=payload, timeout=self.timeout)
            if response.status_code < 300:
                data = response.json()
                return data["full_name"]

            if response.status_code == 422:
                logger.info("Repository name collision for %s, retrying", candidate)
                continue

            if response.status_code == 403:
                try:
                    message = response.json().get("message", "")
                except ValueError:
                    message = response.text
                if "Resource not accessible by integration" in message:
                    raise RuntimeError(
                        "GitHub App token cannot create repositories for this account. "
                        "Verify the app has Repository Administration: Read and write, "
                        "the app is installed on this user account, installation access includes "
                        "new repositories (use All repositories for testing), and the user has "
                        "re-authorized after permission changes."
                    )

            raise RuntimeError(f"GitHub repo creation failed: {response.text}")

        raise RuntimeError("Could not create a unique GitHub repository name")

    def ensure_license(self, workspace: Path) -> None:
        license_candidates = {
            "license",
            "license.md",
            "license.txt",
            "mit-license",
            "mit_license",
        }
        if any(path.name.lower() in license_candidates for path in workspace.iterdir() if path.is_file()):
            return

        content = MIT_LICENSE_TEXT.format(year=time.strftime("%Y"), owner=self.username)
        (workspace / "LICENSE").write_text(content, encoding="utf-8")

    def push_workspace(self, workspace: Path, repo_full_name: str, branch: str) -> str:
        if shutil.which("git") is None:
            raise RuntimeError(
                "Git binary not found in runtime environment. Install git in the host/container before deployment."
            )

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        remote = f"https://x-access-token:{self.token}@github.com/{repo_full_name}.git"

        commands = [
            ["git", "init", "-b", branch],
            ["git", "config", "user.name", self.username],
            ["git", "config", "user.email", self.email],
            ["git", "add", "."],
            ["git", "commit", "-m", "Automated deployment"],
            ["git", "remote", "add", "origin", remote],
            ["git", "push", "-u", "origin", branch],
        ]

        for command in commands:
            safe_command = command[:-1] + ["***"] if command[:3] == ["git", "remote", "add"] else command
            logger.info("Running git command: %s", " ".join(safe_command))
            subprocess.run(command, cwd=workspace, check=True, env=env)

        commit_sha = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workspace)
            .decode("utf-8")
            .strip()
        )

        subprocess.run(["git", "remote", "remove", "origin"], cwd=workspace, check=True)
        git_dir = workspace / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)

        return commit_sha

    def configure_pages(self, repo_full_name: str, branch: str, path: str) -> str:
        url = f"{API_BASE}/repos/{repo_full_name}/pages"
        payload = {"source": {"branch": branch, "path": path}}
        response = self.session.post(url, json=payload, timeout=self.timeout)
        if response.status_code == 409:
            response = self.session.put(url, json=payload, timeout=self.timeout)

        if response.status_code not in (200, 201, 202, 204):
            if response.status_code == 422:
                try:
                    message = response.json().get("message", "")
                except ValueError:
                    message = response.text
                if "does not support GitHub Pages for this repository" in message:
                    raise RuntimeError(
                        "GitHub Pages is not available for this repository under your current plan. "
                        "Use a public repository, disable Pages for this job, or upgrade plan "
                        "for private-repo Pages support."
                    )
            raise RuntimeError(f"Failed to configure GitHub Pages: {response.text}")

        pages_url = f"https://{self.username}.github.io/{repo_full_name.split('/')[-1]}/"
        status_url = f"{API_BASE}/repos/{repo_full_name}/pages/builds/latest"

        for _ in range(self.max_retries):
            build_resp = self.session.get(status_url, timeout=self.timeout)
            if build_resp.status_code == 200:
                state = build_resp.json().get("status")
                if state in {"built", "ready"}:
                    break
            time.sleep(5)

        return pages_url

    def deploy(
        self,
        workspace: Path,
        repo_name: str,
        description: str,
        visibility: str,
        enable_pages: bool,
        branch: str,
        pages_path: str,
    ) -> DeploymentResult:
        repo_full_name = self.create_repository(repo_name, description, visibility)
        self.ensure_license(workspace)
        commit_sha = self.push_workspace(workspace, repo_full_name, branch)

        pages_url: Optional[str] = None
        if enable_pages:
            pages_url = self.configure_pages(repo_full_name, branch, pages_path)

        return DeploymentResult(
            repo_full_name=repo_full_name,
            repo_url=f"https://github.com/{repo_full_name}",
            commit_sha=commit_sha,
            pages_url=pages_url,
        )
