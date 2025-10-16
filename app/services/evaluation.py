from __future__ import annotations

import logging
import time
from typing import Dict

import requests

logger = logging.getLogger(__name__)


def notify_evaluation(url: str, payload: Dict[str, str], timeout: int, max_retries: int) -> None:
    delay = 1
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            if response.status_code < 400:
                logger.info("Evaluation callback succeeded on attempt %d", attempt)
                return
            logger.warning(
                "Evaluation callback attempt %d failed with status %s: %s",
                attempt,
                response.status_code,
                response.text,
            )
        except requests.RequestException as exc:
            logger.warning("Evaluation callback attempt %d raised %s", attempt, exc)

        time.sleep(delay)
        delay *= 2

    raise RuntimeError("Failed to notify evaluation API after retries")
