from __future__ import annotations

import json
import logging

import requests

from ..models import Manifest, PromptAttachment

logger = logging.getLogger(__name__)

API_URL = "https://api.perplexity.ai/chat/completions"


class PerplexityClient:
    def __init__(self, api_key: str, model: str, timeout: int, max_retries: int):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def generate_manifest(self, brief: str, attachments: list[PromptAttachment]) -> Manifest:
        prompt = self._build_prompt(brief, attachments)

        for attempt in range(1, self.max_retries + 1):
            logger.info("Requesting manifest from Perplexity (attempt %d)", attempt)
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You generate production-ready static web apps. Always respond with strict JSON that matches the requested schema.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 3200,
            }

            response = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )

            if response.status_code >= 400:
                logger.warning(
                    "Perplexity API returned status %s: %s",
                    response.status_code,
                    response.text,
                )
                continue

            data = response.json()
            try:
                content = data["choices"][0]["message"]["content"]
                manifest = Manifest.from_response(content)
                logger.info("Received manifest with %d files", len(manifest.files))
                return manifest
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                logger.exception("Failed to parse Perplexity response: %s", exc)
                continue

        raise RuntimeError("Perplexity API failed to produce a valid manifest")

    def _build_prompt(self, brief: str, attachments: list[PromptAttachment]) -> str:
        attachment_sections: list[str] = []
        for attachment in attachments:
            preview = attachment.data[:500].decode("utf-8", errors="replace")
            attachment_sections.append(
                f"- {attachment.file_name} ({attachment.media_type}, {len(attachment.data)} bytes)\n```\n{preview}\n```"
            )

        attachment_text = "\n".join(attachment_sections) if attachment_sections else "None provided."

        return f"""
Build a static frontend project from this brief:
{brief}

Attachments:
{attachment_text}

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
  "commands": ["optional shell command to run before deployment"]
}}
- Output must run as a static site on GitHub Pages.
- Use browser-safe JavaScript only. Do not use server runtime APIs (`require`, `module.exports`, `process`, `fs`).
- Keep code and README concise, clear, and maintainable.
- Include a LICENSE file only if one is not already present.
""".strip()
