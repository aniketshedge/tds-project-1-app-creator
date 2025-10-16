from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import unquote

from pydantic import AnyHttpUrl, EmailStr, Field, ValidationError
from pydantic_settings import SettingsConfigDict
from pydantic import BaseModel


class Attachment(BaseModel):
    name: str
    url: str

    def decode(self) -> bytes:
        if not self.url.startswith("data:"):
            raise ValueError(f"Unsupported attachment URL for {self.name}")
        header, data = self.url.split(",", 1)
        if ";base64" in header:
            return base64.b64decode(data)
        return unquote(data).encode("utf-8")

    def media_type(self) -> str:
        if self.url.startswith("data:"):
            header = self.url.split(",", 1)[0]
            return header[5:].split(";")[0] or "application/octet-stream"
        return "application/octet-stream"


class TaskRequest(BaseModel):
    model_config = SettingsConfigDict(extra="ignore")

    email: EmailStr
    secret: str
    task: str = Field(min_length=1)
    round: int = Field(ge=1)
    nonce: str = Field(min_length=1)
    brief: str = Field(min_length=1)
    checks: List[str] = Field(default_factory=list)
    evaluation_url: AnyHttpUrl
    attachments: List[Attachment] = Field(default_factory=list)

    def to_json(self) -> str:
        return self.model_dump_json()


class ManifestFile(BaseModel):
    path: str
    content: str
    encoding: Literal["text", "base64"] = "text"
    executable: bool = False

    def bytes_content(self) -> bytes:
        if self.encoding == "base64":
            return base64.b64decode(self.content)
        return self.content.encode("utf-8")


class Manifest(BaseModel):
    files: List[ManifestFile]
    readme: Optional[str] = None
    commands: List[str] = Field(default_factory=list)

    @classmethod
    def from_response(cls, payload: str | Dict[str, Any]) -> "Manifest":
        if isinstance(payload, dict):
            return cls(**payload)
        cleaned = _extract_json(payload)
        data = json.loads(cleaned)
        return cls(**data)


def _extract_json(candidate: str) -> str:
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if not match:
        raise ValueError("Perplexity response did not contain JSON manifest")
    return match.group(0)


@dataclass
class TaskRecord:
    job_id: str
    task: str
    round: int
    status: str
    created_at: datetime
    updated_at: datetime
    payload: Dict[str, Any]
    repo_url: Optional[str] = None
    commit_sha: Optional[str] = None
    pages_url: Optional[str] = None
    error: Optional[str] = None
    evaluation_status: Optional[str] = None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
