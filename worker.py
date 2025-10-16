from __future__ import annotations

import logging

from dotenv import load_dotenv
from redis import Redis
from rq import Connection, Worker

from app.config import Settings
from app.jobqueue import QUEUE_NAME
from app.logger import configure_logging


def main() -> None:
    load_dotenv()
    settings = Settings()
    settings.resolve_paths()
    configure_logging(settings.log_file)

    redis_connection = Redis.from_url(settings.redis_url)
    logger = logging.getLogger(__name__)
    logger.info("Starting RQ worker for queue=%s", QUEUE_NAME)

    with Connection(redis_connection):
        worker = Worker([QUEUE_NAME])
        worker.work()


if __name__ == "__main__":
    main()
