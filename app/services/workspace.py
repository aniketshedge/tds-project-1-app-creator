from __future__ import annotations

import logging
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Iterable, List

from ..models import Attachment, Manifest

logger = logging.getLogger(__name__)


class WorkspaceManager:
    def __init__(self, root: str, job_id: str):
        self.root = Path(root)
        self.job_id = job_id
        self.path = self.root / job_id
        self.path.mkdir(parents=True, exist_ok=True)
        self._license_written = False

    def write_manifest(self, manifest: Manifest) -> None:
        for item in manifest.files:
            target = self.path / item.path
            if self._is_duplicate_license(target):
                logger.info("Skipping duplicate license file %s", target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.bytes_content())
            if item.executable:
                target.chmod(target.stat().st_mode | stat.S_IEXEC)
            logger.info("Wrote manifest file %s", target)
            if self._looks_like_license(target):
                self._license_written = True

        if manifest.readme:
            readme_path = self.path / "README.md"
            readme_path.write_text(manifest.readme, encoding="utf-8")
            logger.info("Wrote README.md from manifest")

    def write_attachments(self, attachments: Iterable[Attachment], limit_bytes: int) -> None:
        for attachment in attachments:
            payload = attachment.decode()
            if len(payload) > limit_bytes:
                raise ValueError(f"Attachment {attachment.name} exceeds limit of {limit_bytes} bytes")

            attachment_path = self.path / attachment.name
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            attachment_path.write_bytes(payload)
            logger.info("Added attachment %s (%d bytes)", attachment.name, len(payload))

    def run_commands(self, commands: List[str]) -> None:
        for command in commands:
            logger.info("Executing workspace command: %s", command)
            subprocess.run(command, shell=True, check=True, cwd=self.path)

    def collect_files(self) -> List[Path]:
        files: List[Path] = []
        for entry in self.path.rglob("*"):
            if entry.is_file():
                files.append(entry)
        return files

    def ensure_readme(self, content: str) -> None:
        readme_path = self.path / "README.md"
        if readme_path.exists():
            return
        readme_path.write_text(content, encoding="utf-8")
        logger.info("Generated fallback README.md")

    def cleanup(self) -> None:
        if self.path.exists():
            shutil.rmtree(self.path)
            logger.info("Cleaned workspace %s", self.path)

    def _looks_like_license(self, path: Path) -> bool:
        normalized = path.name.lower()
        return normalized in {
            "license",
            "license.md",
            "license.txt",
            "mit-license",
            "mit.txt",
        }

    def _is_duplicate_license(self, target: Path) -> bool:
        return self._license_written and self._looks_like_license(target)
