from __future__ import annotations

import logging


def configure_logging(log_file: str) -> None:
    """Configure application logging to write to a single log file."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    handler_exists = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    if handler_exists:
        return

    file_handler = logging.FileHandler(log_file)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s :: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
