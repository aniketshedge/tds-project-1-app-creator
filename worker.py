from __future__ import annotations

import logging

from dotenv import load_dotenv
from rq import Worker

from app.config import Settings
from app.jobqueue import QUEUE_NAME, create_redis
from app.logger import configure_logging


def main() -> None:
    load_dotenv()
    settings = Settings()
    settings.resolve_paths()
    configure_logging(settings.log_file)

    redis_connection = create_redis(settings.redis_url)
    logger = logging.getLogger(__name__)
    logger.info("Starting RQ worker for queue=%s", QUEUE_NAME)

    worker = Worker([QUEUE_NAME], connection=redis_connection)
    worker.work()


if __name__ == "__main__":
    main()
