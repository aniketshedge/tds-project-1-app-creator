from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

import requests

from ..models import Manifest

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
    pages_url: str


def generate_repo_name(task: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-") or "task"
    suffix = uuid4().hex[:6]
    return f"{slug}-{suffix}"


def _shorten_description(description: str) -> str:
    if not description:
        return ""
    # Collapse whitespace and shorten without cutting words mid-stream.
    collapsed = " ".join(description.split())
    return textwrap.shorten(collapsed, width=140, placeholder="…")


class GitHubClient:
    def __init__(
        self,
        token: str,
        username: str,
        email: str,
        default_branch: str,
        org: Optional[str],
        timeout: int,
        max_retries: int,
    ):
        self.username = username
        self.email = email
        self.token = token
        self.default_branch = default_branch
        self.org = org
        self.timeout = timeout
        self.max_retries = max_retries
        self.owner = org or username

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": f"llm-deployer/{self.username}",
            }
        )

    def create_repository(self, name: str, description: str) -> str:
        url = (
            f"{API_BASE}/orgs/{self.org}/repos"
            if self.org
            else f"{API_BASE}/user/repos"
        )
        payload = {
            "name": name,
            "description": _shorten_description(description),
            "private": False,
            "auto_init": False,
        }
        logger.info("Creating GitHub repository %s", name)
        response = self.session.post(url, json=payload, timeout=self.timeout)
        if response.status_code >= 300:
            raise RuntimeError(f"GitHub repo creation failed: {response.text}")
        data = response.json()
        return data["full_name"]

    def push_workspace(self, workspace: Path, repo_full_name: str, force: bool = False) -> str:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        remote = f"https://{self.username}:{self.token}@github.com/{repo_full_name}.git"

        commands = [
            ["git", "init", "-b", self.default_branch],
            ["git", "config", "user.name", self.username],
            ["git", "config", "user.email", self.email],
            ["git", "add", "."],
            ["git", "commit", "-m", "Automated deployment"],
            ["git", "remote", "add", "origin", remote],
        ]

        push_command = ["git", "push", "-u", "origin", self.default_branch]
        if force:
            push_command.append("--force")
        commands.append(push_command)

        for command in commands:
            logger.info("Running git command: %s", " ".join(command[:-1] if "@" in command[-1] else command))
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

    def ensure_license(self, workspace: Path) -> None:
        license_candidates = [
            "LICENSE",
            "LICENSE.md",
            "LICENSE.txt",
            "MIT-LICENSE",
            "MIT_LICENSE",
        ]
        if any((workspace / candidate).exists() for candidate in license_candidates):
            return
        license_path = workspace / "LICENSE"
        content = MIT_LICENSE_TEXT.format(
            year=time.strftime("%Y"),
            owner=self.owner,
        )
        license_path.write_text(content, encoding="utf-8")
        logger.info("Injected MIT LICENSE file")

    def configure_pages(self, repo_full_name: str) -> str:
        logger.info("Configuring GitHub Pages for %s", repo_full_name)
        url = f"{API_BASE}/repos/{repo_full_name}/pages"
        payload = {"source": {"branch": self.default_branch, "path": "/"}}
        response = self.session.post(url, json=payload, timeout=self.timeout)
        if response.status_code == 409:
            # Site already exists, update configuration instead.
            response = self.session.put(url, json=payload, timeout=self.timeout)

        if response.status_code not in (201, 202, 204, 200):
            raise RuntimeError(f"Failed to configure GitHub Pages: {response.text}")

        pages_url = f"https://{self.owner}.github.io/{repo_full_name.split('/')[-1]}/"
        # Poll builds to ensure readiness
        status_url = f"{API_BASE}/repos/{repo_full_name}/pages/builds/latest"
        for attempt in range(self.max_retries):
            build_resp = self.session.get(status_url, timeout=self.timeout)
            if build_resp.status_code == 200:
                state = build_resp.json().get("status")
                if state in {"built", "ready"}:
                    logger.info("GitHub Pages build ready (attempt %d)", attempt + 1)
                    break
            time.sleep(5)
        return pages_url

    def deploy(
        self,
        workspace: Path,
        manifest: Manifest,
        repo_name: str,
        description: str,
        existing_repo_full_name: Optional[str] = None,
        force: bool = False,
    ) -> DeploymentResult:
        if existing_repo_full_name:
            repo_full_name = existing_repo_full_name
        else:
            repo_full_name = self.create_repository(repo_name, description)

        self.ensure_license(workspace)
        commit_sha = self.push_workspace(workspace, repo_full_name, force=force)
        pages_url = self.configure_pages(repo_full_name)
        repo_url = f"https://github.com/{repo_full_name}"
        return DeploymentResult(
            repo_full_name=repo_full_name,
            repo_url=repo_url,
            commit_sha=commit_sha,
            pages_url=pages_url,
        )
