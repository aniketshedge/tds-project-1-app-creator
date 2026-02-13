from __future__ import annotations

import logging
from typing import Any

import requests

from ..models import Manifest

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You generate production-ready static web apps that are directly deployable. "
    "Never require server-side runtime, package installation, or build commands. "
    "Always respond with strict JSON that matches the requested schema."
)

LLM_PROVIDER_MODELS: dict[str, list[str]] = {
    "aipipe": [
        "openai/gpt-5",
        "openai/gpt-5-mini",
        "openai/gpt-5-nano",
        "anthropic/claude-3.5-sonnet",
        "google/gemini-2.5-flash",
    ],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ],
    "perplexity": [
        "sonar-pro",
        "sonar-reasoning-pro",
        "sonar",
    ],
    "openai": [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
    ],
    "anthropic": [
        "claude-sonnet-4-20250514",
        "claude-opus-4-1-20250805",
        "claude-opus-4-20250514",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-haiku-20241022",
    ],
}

LLM_PROVIDER_LABELS: dict[str, str] = {
    "perplexity": "Perplexity",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "aipipe": "AI Pipe",
}

OTHER_MODEL_SENTINELS = {"other", "__other__"}


def llm_provider_catalog() -> list[dict[str, object]]:
    return [
        {
            "id": provider,
            "label": LLM_PROVIDER_LABELS.get(provider, provider.title()),
            "models": LLM_PROVIDER_MODELS[provider],
            "allow_other": True,
        }
        for provider in LLM_PROVIDER_MODELS
    ]


def default_model_for_provider(provider: str) -> str:
    models = LLM_PROVIDER_MODELS.get(provider)
    if not models:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    return models[0]


def resolve_model_for_provider(provider: str, requested_model: str | None) -> str:
    if requested_model and requested_model.strip():
        candidate = requested_model.strip()
        if candidate.lower() in OTHER_MODEL_SENTINELS:
            raise ValueError("Model name must be provided when 'Other' is selected")
        return candidate
    return default_model_for_provider(provider)


class UnifiedGenerationService:
    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        timeout: int,
        max_retries: int,
    ):
        if provider not in LLM_PROVIDER_MODELS:
            raise ValueError(f"Unsupported LLM provider: {provider}")
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def generate_manifest(self, brief: str) -> Manifest:
        prompt = self._build_prompt(brief)

        for attempt in range(1, self.max_retries + 1):
            logger.info(
                "Requesting manifest from %s with model %s (attempt %d)",
                self.provider,
                self.model,
                attempt,
            )

            try:
                content = self._request_content(prompt)
                manifest = Manifest.from_response(content)
                logger.info("Received manifest with %d files", len(manifest.files))
                return manifest
            except Exception as exc:
                logger.warning(
                    "Generation failed for provider=%s model=%s attempt=%d: %s",
                    self.provider,
                    self.model,
                    attempt,
                    exc,
                )
                if attempt == self.max_retries:
                    raise RuntimeError(
                        f"{self.provider} API failed to produce a valid manifest"
                    ) from exc

        raise RuntimeError(f"{self.provider} API failed to produce a valid manifest")

    def _request_content(self, prompt: str) -> str | dict[str, Any]:
        if self.provider == "perplexity":
            return self._chat_completion_request("https://api.perplexity.ai/chat/completions", prompt)
        if self.provider == "openai":
            return self._chat_completion_request(
                "https://api.openai.com/v1/chat/completions",
                prompt,
                max_tokens_key="max_completion_tokens",
            )
        if self.provider == "aipipe":
            return self._chat_completion_request("https://aipipe.org/openrouter/v1/chat/completions", prompt)
        if self.provider == "anthropic":
            return self._anthropic_messages_request(prompt)
        if self.provider == "gemini":
            return self._gemini_generate_content_request(prompt)
        raise ValueError(f"Unsupported LLM provider: {self.provider}")

    def _chat_completion_request(
        self,
        url: str,
        prompt: str,
        max_tokens_key: str = "max_tokens",
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        payload[max_tokens_key] = 3200
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        message = data.get("choices", [{}])[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text_value = item.get("text")
                    if isinstance(text_value, str):
                        text_chunks.append(text_value)
            if text_chunks:
                return "\n".join(text_chunks)
        raise ValueError("Chat completion response missing assistant content")

    def _anthropic_messages_request(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 3200,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        content = data.get("content")
        if not isinstance(content, list):
            raise ValueError("Anthropic response missing content array")
        text_chunks: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text_chunks.append(block["text"])
        if not text_chunks:
            raise ValueError("Anthropic response missing text output")
        return "\n".join(text_chunks)

    def _gemini_generate_content_request(self, prompt: str) -> str:
        encoded_model = requests.utils.quote(self.model, safe="-._")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{encoded_model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": prompt}]}],
        }
        response = requests.post(
            url,
            params={"key": self.api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Gemini response missing candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not isinstance(parts, list):
            raise ValueError("Gemini response missing content parts")

        text_chunks: list[str] = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_chunks.append(part["text"])
        if not text_chunks:
            raise ValueError("Gemini response missing text output")
        return "\n".join(text_chunks)

    def _build_prompt(self, brief: str) -> str:
        return f"""
Build a static frontend project from this brief:
{brief}

Requirements:
- Return ONLY JSON matching this schema:
{{
  "files": [
    {{
      "path": "relative/path/to/file.ext",
      "content": "file contents as string or base64",
      "encoding": "text|base64",
      "executable": false
    }}
  ],
  "readme": "optional README.md content",
  "commands": []
}}
- Output must run as a static site on GitHub Pages without any build step.
- Include an `index.html` entry point and all required static assets in `files`.
- `commands` must always be an empty array because shell/build execution is disabled.
- Do not assume `npm`, `pnpm`, `yarn`, `vite`, `webpack`, or any CI/CD step will run after generation.
- Use browser-safe JavaScript only. Do not use server/runtime APIs (`require`, `module.exports`, `process`, `fs`, `path`).
- Do not depend on environment variables or backend-only secrets.
- Keep code and README concise, clear, and maintainable.
- Include a LICENSE file only if one is not already present.
- If the brief is ambiguous, prefer a simple vanilla HTML/CSS/JS implementation that works immediately.
""".strip()
