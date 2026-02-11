from __future__ import annotations

from redis import Redis
from rq import Queue

QUEUE_NAME = "task-runner"


def create_redis(redis_url: str) -> Redis:
    return Redis.from_url(redis_url)


def create_queue(redis: Redis, default_timeout: int = 600) -> Queue:
    return Queue(name=QUEUE_NAME, connection=redis, default_timeout=default_timeout)
