"""
Section 2 — Dead Letter Queue (DLQ) Handler.

When a task exhausts all retries (max_retries exceeded), it is moved
to a Redis-backed dead-letter list for later inspection and manual replay.

This ensures no job is silently lost — even permanently failed jobs
are preserved for debugging and potential manual retry.
"""
import json
import time
import redis
from django.conf import settings


DLQ_KEY = 'dead_letter_queue:emails'


def get_redis_client():
    """Get a Redis client from Django settings."""
    url = getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0')
    return redis.Redis.from_url(url)


def move_to_dead_letter(task_id, task_args, task_kwargs, exception_info):
    """
    Move a permanently failed task to the dead-letter queue.

    Called by the task's on_failure handler after max_retries is exceeded.
    Stores enough context to investigate and manually replay the task.
    """
    client = get_redis_client()
    entry = {
        'task_id': task_id,
        'args': task_args,
        'kwargs': task_kwargs,
        'error': str(exception_info),
        'failed_at': time.time(),
        'failed_at_human': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    client.rpush(DLQ_KEY, json.dumps(entry))


def get_dead_letters(count=50):
    """Retrieve dead-letter entries for inspection."""
    client = get_redis_client()
    entries = client.lrange(DLQ_KEY, 0, count - 1)
    return [json.loads(e) for e in entries]


def get_dead_letter_count():
    """Get the number of entries in the DLQ."""
    client = get_redis_client()
    return client.llen(DLQ_KEY)


def flush_dead_letters():
    """Clear the DLQ (after manual review)."""
    client = get_redis_client()
    client.delete(DLQ_KEY)
