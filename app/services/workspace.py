from __future__ import annotations

import logging
import shutil
import stat
import subprocess
from pathlib import Path

from ..models import Manifest

logger = logging.getLogger(__name__)


class WorkspaceManager:
    def __init__(self, root: str, job_id: str):
        self.root = Path(root)
        self.job_id = job_id
        self.path = (self.root / job_id).resolve()
        self.path.mkdir(parents=True, exist_ok=True)

    def write_manifest(self, manifest: Manifest) -> None:
        for item in manifest.files:
            target = self._safe_target(item.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.bytes_content())
            if item.executable:
                target.chmod(target.stat().st_mode | stat.S_IEXEC)

        if manifest.readme:
            (self.path / "README.md").write_text(manifest.readme, encoding="utf-8")

    def run_commands(self, commands: list[str]) -> None:
        for command in commands:
            logger.info("Executing workspace command: %s", command)
            subprocess.run(command, shell=True, check=True, cwd=self.path)

    def cleanup(self) -> None:
        if self.path.exists():
            shutil.rmtree(self.path)

    def _safe_target(self, relative_path: str) -> Path:
        clean = Path(relative_path)
        target = (self.path / clean).resolve()
        if not str(target).startswith(str(self.path)):
            raise ValueError(f"Invalid file path outside workspace: {relative_path}")
        return target
