from __future__ import annotations

from redis import Redis
from rq import Queue


QUEUE_NAME = "task-runner"


def create_queue(redis_url: str, default_timeout: int = 600) -> Queue:
    connection = Redis.from_url(redis_url)
    return Queue(name=QUEUE_NAME, connection=connection, default_timeout=default_timeout)
