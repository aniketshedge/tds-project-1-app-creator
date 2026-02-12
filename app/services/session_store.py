from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from redis import Redis

from ..config import Settings


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings):
        self.redis = redis
        self.settings = settings

    def ensure_session(self, session_id: str | None) -> tuple[str, bool]:
        if not session_id:
            new_id = uuid4().hex
            self._touch_session(new_id)
            return new_id, True

        if not self.redis.exists(self._session_meta_key(session_id)):
            self._touch_session(session_id)
            return session_id, True

        self._touch_session(session_id)
        self._refresh_session_keys(session_id)
        return session_id, False

    def reset_session(self, old_session_id: str | None) -> str:
        if old_session_id:
            self.delete_session(old_session_id)
        new_session_id = uuid4().hex
        self._touch_session(new_session_id)
        return new_session_id

    def delete_session(self, session_id: str) -> None:
        keys = [
            self._session_meta_key(session_id),
            self._github_key(session_id),
            self._llm_key(session_id),
            self._github_state_key(session_id),
        ]
        self.redis.delete(*keys)

    def store_github_state(self, session_id: str, state: str) -> None:
        self.redis.setex(self._github_state_key(session_id), self.settings.session_ttl_seconds, state)

    def consume_github_state(self, session_id: str) -> str | None:
        key = self._github_state_key(session_id)
        state = self.redis.get(key)
        if state:
            self.redis.delete(key)
            return state.decode("utf-8")
        return None

    def store_github_credentials(
        self,
        session_id: str,
        access_token: str,
        username: str,
        refresh_token: str | None = None,
        access_token_expires_in: int | None = None,
        refresh_token_expires_in: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        access_expires_at = (
            (now + timedelta(seconds=int(access_token_expires_in))).isoformat()
            if access_token_expires_in
            else None
        )
        refresh_expires_at = (
            (now + timedelta(seconds=int(refresh_token_expires_in))).isoformat()
            if refresh_token_expires_in
            else None
        )
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "access_expires_at": access_expires_at,
            "refresh_expires_at": refresh_expires_at,
            "username": username,
        }
        self.redis.setex(
            self._github_key(session_id),
            self.settings.session_ttl_seconds,
            json.dumps(payload),
        )
        self._touch_session(session_id)

    def get_github_credentials(self, session_id: str) -> dict[str, str | None] | None:
        raw = self.redis.get(self._github_key(session_id))
        if not raw:
            return None
        self._touch_session(session_id)
        self._refresh_session_keys(session_id)
        return json.loads(raw)

    def clear_github_credentials(self, session_id: str) -> None:
        self.redis.delete(self._github_key(session_id))

    def store_llm_credentials(
        self,
        session_id: str,
        provider: str,
        api_key: str,
        model: str,
    ) -> None:
        payload = {"provider": provider, "api_key": api_key, "model": model}
        self.redis.setex(
            self._llm_key(session_id),
            self.settings.session_ttl_seconds,
            json.dumps(payload),
        )
        self._touch_session(session_id)

    def get_llm_credentials(self, session_id: str) -> dict[str, str] | None:
        raw = self.redis.get(self._llm_key(session_id))
        if not raw:
            return None
        self._touch_session(session_id)
        self._refresh_session_keys(session_id)
        return json.loads(raw)

    def clear_llm_credentials(self, session_id: str) -> None:
        self.redis.delete(self._llm_key(session_id))

    def snapshot_job_secrets(
        self,
        job_id: str,
        session_id: str,
        *,
        include_llm: bool = True,
        include_github: bool = True,
    ) -> dict[str, dict[str, str | None]]:
        payload: dict[str, dict[str, str | None]] = {}
        if include_llm:
            llm = self.get_llm_credentials(session_id)
            if not llm:
                raise ValueError("Missing required integrations: LLM credentials must be configured")
            payload["llm"] = llm

        if include_github:
            github = self.get_github_credentials(session_id)
            if not github:
                raise ValueError("Missing required integrations: GitHub credentials must be configured")
            payload["github"] = github

        self.redis.setex(
            self._job_secret_key(job_id),
            self.settings.job_secret_ttl_seconds,
            json.dumps(payload),
        )
        return payload

    def get_job_secrets(self, job_id: str) -> dict[str, dict[str, str | None]] | None:
        raw = self.redis.get(self._job_secret_key(job_id))
        if not raw:
            return None
        return json.loads(raw)

    def clear_job_secrets(self, job_id: str) -> None:
        self.redis.delete(self._job_secret_key(job_id))

    def integration_state(self, session_id: str) -> dict[str, object]:
        github = self.get_github_credentials(session_id)
        llm = self.get_llm_credentials(session_id)

        return {
            "github": {
                "connected": bool(github),
                "username": github.get("username") if github else None,
                "mode": "github_app",
                "access_expires_at": github.get("access_expires_at") if github else None,
            },
            "llm": {
                "provider": llm.get("provider") if llm else None,
                "configured": bool(llm),
                "model": llm.get("model") if llm else None,
            },
        }

    def _refresh_session_keys(self, session_id: str) -> None:
        ttl = self.settings.session_ttl_seconds
        self.redis.expire(self._session_meta_key(session_id), ttl)
        self.redis.expire(self._github_key(session_id), ttl)
        self.redis.expire(self._llm_key(session_id), ttl)

    def _touch_session(self, session_id: str) -> None:
        self.redis.setex(self._session_meta_key(session_id), self.settings.session_ttl_seconds, "1")

    def _session_meta_key(self, session_id: str) -> str:
        return f"sess:{session_id}:meta"

    def _github_key(self, session_id: str) -> str:
        return f"sess:{session_id}:github"

    def _llm_key(self, session_id: str) -> str:
        return f"sess:{session_id}:llm"

    def _github_state_key(self, session_id: str) -> str:
        return f"sess:{session_id}:github_state"

    def _job_secret_key(self, job_id: str) -> str:
        return f"job:{job_id}:secrets"
