from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict


class RepoConfig(BaseModel):
    name: str = Field(min_length=1)
    visibility: Literal["public", "private"] = "public"


class DeploymentConfig(BaseModel):
    enable_pages: bool = True
    branch: str = Field(default="main", min_length=1)
    path: str = "/"


class JobCreatePayload(BaseModel):
    model_config = SettingsConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(min_length=1, max_length=12_000)
    delivery_mode: Literal["github", "zip"] = "zip"
    repo: Optional[RepoConfig] = None
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)


class GitHubDeployPayload(BaseModel):
    repo: RepoConfig
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)


class LLMIntegrationRequest(BaseModel):
    provider: Literal["perplexity", "aipipe", "openai", "anthropic", "gemini"]
    api_key: str = Field(min_length=1)
    model: Optional[str] = None


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
    files: list[ManifestFile]
    readme: Optional[str] = None
    commands: list[str] = Field(default_factory=list)

    @classmethod
    def from_response(cls, payload: str | dict[str, Any]) -> "Manifest":
        if isinstance(payload, dict):
            return cls(**payload)
        cleaned = _extract_json(payload)
        data = json.loads(cleaned)
        return cls(**data)


def _extract_json(candidate: str) -> str:
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if not match:
        raise ValueError("LLM response did not contain JSON manifest")
    return match.group(0)


@dataclass
class JobEventRecord:
    id: int
    job_id: str
    level: str
    message: str
    created_at: datetime


@dataclass
class JobRecord:
    id: str
    session_id: str
    title: str
    brief: str
    payload: dict[str, Any]
    status: str
    llm_provider: str
    llm_model: Optional[str]
    delivery_mode: str
    repo_name: Optional[str]
    repo_visibility: Optional[str]
    repo_full_name: Optional[str]
    repo_url: Optional[str]
    pages_url: Optional[str]
    commit_sha: Optional[str]
    artifact_path: Optional[str]
    artifact_name: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
