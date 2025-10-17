from __future__ import annotations

import json
import logging
from typing import List

import requests

from ..models import Attachment, Manifest, TaskRequest

logger = logging.getLogger(__name__)

API_URL = "https://api.perplexity.ai/chat/completions"


class PerplexityClient:
    def __init__(self, api_key: str, model: str, timeout: int, max_retries: int):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def generate_manifest(self, request: TaskRequest, attachments: List[Attachment]) -> Manifest:
        prompt = self._build_prompt(request, attachments)

        for attempt in range(1, self.max_retries + 1):
            logger.info("Requesting manifest from Perplexity (attempt %d)", attempt)
            response = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are an autonomous senior software engineer focused on static site generation. Always respond with strict JSON that matches the requested schema.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 2000,
                },
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

    def _build_prompt(self, request: TaskRequest, attachments: List[Attachment]) -> str:
        attachment_sections = []
        for attachment in attachments:
            try:
                data = attachment.decode()
            except ValueError:
                data = b""
            preview = data[:500].decode("utf-8", errors="replace")
            attachment_sections.append(
                f"- {attachment.name} ({attachment.media_type()}, {len(data)} bytes)\n```\n{preview}\n```"
            )

        attachment_text = "\n".join(attachment_sections) if attachment_sections else "None provided."
        checks_text = "\n".join(f"- {check}" for check in request.checks) or "- None specified."

        prompt = f"""
Project Brief:
{request.brief}

Evaluation Checks:
{checks_text}

Round: {request.round}
Task ID: {request.task}

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
- Preserve placeholder tokens exactly as provided (e.g., `${{seed}}`, `${{result}}`, `${{nonce}}`) without substituting example values.
- Exclude evaluator checks from README or other user-facing docs; instead provide a professional README with sections for Overview, Getting Started, Usage, and task-specific notes.
- Write code defensively when consuming attachments (e.g., trim blank rows, guard against malformed data) to prevent runtime errors.
- All HTML assets must be self-contained (no server runtime for the deployed site).
- Include an MIT LICENSE file if not already provided.
- Ensure the site works when hosted on GitHub Pages (root path, relative assets).
- Keep the response under 120k characters.
"""
        return prompt
