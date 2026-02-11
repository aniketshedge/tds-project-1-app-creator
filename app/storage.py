from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from .models import JobAttachmentRecord, JobCreatePayload, JobEventRecord, JobRecord


class TaskRepository:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    brief TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    llm_provider TEXT NOT NULL,
                    llm_model TEXT,
                    repo_name TEXT,
                    repo_visibility TEXT,
                    repo_full_name TEXT,
                    repo_url TEXT,
                    pages_url TEXT,
                    commit_sha TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_session_created
                    ON jobs(session_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_status_updated
                    ON jobs(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS job_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    media_type TEXT,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_attachments_job
                    ON job_attachments(job_id);

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_events_job_id
                    ON job_events(job_id, id);
                """
            )
            conn.commit()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def create_job(
        self,
        job_id: str,
        session_id: str,
        payload: JobCreatePayload,
        llm_provider: str,
        llm_model: Optional[str],
    ) -> None:
        now = self._now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, session_id, title, brief, payload_json, status,
                    llm_provider, llm_model, repo_name, repo_visibility,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    session_id,
                    payload.title,
                    payload.brief,
                    payload.model_dump_json(),
                    "queued",
                    llm_provider,
                    llm_model,
                    payload.repo.name,
                    payload.repo.visibility,
                    now,
                    now,
                ),
            )
            conn.commit()

    def add_attachment(
        self,
        job_id: str,
        file_name: str,
        media_type: Optional[str],
        size_bytes: int,
        sha256: str,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO job_attachments (job_id, file_name, media_type, size_bytes, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, file_name, media_type, size_bytes, sha256, self._now_iso()),
            )
            conn.commit()

    def append_event(self, job_id: str, level: str, message: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO job_events (job_id, level, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, level, message, self._now_iso()),
            )
            conn.commit()

    def update_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        repo_full_name: Optional[str] = None,
        repo_url: Optional[str] = None,
        pages_url: Optional[str] = None,
        commit_sha: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        updates: dict[str, Any] = {"updated_at": self._now_iso()}
        if status is not None:
            updates["status"] = status
        if repo_full_name is not None:
            updates["repo_full_name"] = repo_full_name
        if repo_url is not None:
            updates["repo_url"] = repo_url
        if pages_url is not None:
            updates["pages_url"] = pages_url
        if commit_sha is not None:
            updates["commit_sha"] = commit_sha
        if error_code is not None:
            updates["error_code"] = error_code
        if error_message is not None:
            updates["error_message"] = error_message
        if started_at is not None:
            updates["started_at"] = started_at
        if completed_at is not None:
            updates["completed_at"] = completed_at

        assignment = ", ".join(f"{key} = :{key}" for key in updates)
        updates["job_id"] = job_id

        with self._connection() as conn:
            conn.execute(f"UPDATE jobs SET {assignment} WHERE id = :job_id", updates)
            conn.commit()

    def fetch_job(self, job_id: str) -> Optional[JobRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None

    def list_jobs_for_session(self, session_id: str, limit: int = 50) -> list[JobRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def list_events(self, job_id: str, after_id: int = 0, limit: int = 200) -> list[JobEventRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM job_events
                WHERE job_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (job_id, after_id, limit),
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def list_attachments(self, job_id: str) -> list[JobAttachmentRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM job_attachments
                WHERE job_id = ?
                ORDER BY id ASC
                """,
                (job_id,),
            ).fetchall()
            return [self._row_to_attachment(row) for row in rows]

    def _row_to_job(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            brief=row["brief"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            llm_provider=row["llm_provider"],
            llm_model=row["llm_model"],
            repo_name=row["repo_name"],
            repo_visibility=row["repo_visibility"],
            repo_full_name=row["repo_full_name"],
            repo_url=row["repo_url"],
            pages_url=row["pages_url"],
            commit_sha=row["commit_sha"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        )

    def _row_to_attachment(self, row: sqlite3.Row) -> JobAttachmentRecord:
        return JobAttachmentRecord(
            id=row["id"],
            job_id=row["job_id"],
            file_name=row["file_name"],
            media_type=row["media_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_event(self, row: sqlite3.Row) -> JobEventRecord:
        return JobEventRecord(
            id=row["id"],
            job_id=row["job_id"],
            level=row["level"],
            message=row["message"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
