from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Optional

from .models import TaskRecord, TaskRequest


class TaskRepository:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    job_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    task_round INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    repo_url TEXT,
                    commit_sha TEXT,
                    pages_url TEXT,
                    error TEXT,
                    evaluation_status TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def record_task(self, job_id: str, request: TaskRequest) -> None:
        payload = json.loads(request.model_dump_json())
        now = self._now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    job_id, task_id, task_round, email, status, payload,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request.task,
                    request.round,
                    request.email,
                    "queued",
                    json.dumps(payload),
                    now,
                    now,
                ),
            )
            conn.commit()

    def update_status(
        self,
        job_id: str,
        status: str,
        *,
        repo_url: Optional[str] = None,
        commit_sha: Optional[str] = None,
        pages_url: Optional[str] = None,
        error: Optional[str] = None,
        evaluation_status: Optional[str] = None,
    ) -> None:
        fields: Dict[str, Any] = {"status": status, "updated_at": self._now_iso()}
        if repo_url is not None:
            fields["repo_url"] = repo_url
        if commit_sha is not None:
            fields["commit_sha"] = commit_sha
        if pages_url is not None:
            fields["pages_url"] = pages_url
        if error is not None:
            fields["error"] = error
        if evaluation_status is not None:
            fields["evaluation_status"] = evaluation_status

        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["job_id"] = job_id
        with self._connection() as conn:
            conn.execute(f"UPDATE tasks SET {assignments} WHERE job_id = :job_id", fields)
            conn.commit()

    def fetch_task(self, job_id: str) -> Optional[TaskRecord]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def find_latest_with_repo(self, task_id: str) -> Optional[TaskRecord]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE task_id = ? AND repo_url IS NOT NULL
                ORDER BY task_round DESC, updated_at DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row_to_record(self, row: sqlite3.Row) -> TaskRecord:
        payload = json.loads(row["payload"])
        return TaskRecord(
            job_id=row["job_id"],
            task=row["task_id"],
            round=row["task_round"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            payload=payload,
            repo_url=row["repo_url"],
            commit_sha=row["commit_sha"],
            pages_url=row["pages_url"],
            error=row["error"],
            evaluation_status=row["evaluation_status"],
        )
