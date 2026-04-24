"""
Section 2 — Celery task for sending transactional emails.

This task integrates:
1. Token-bucket rate limiter (Redis Lua script)
2. Exponential backoff retry with jitter
3. Dead-letter handling for permanently failed jobs
4. acks_late + reject_on_worker_lost for crash safety

Key Celery configuration (set in settings.py):
- CELERY_TASK_ACKS_LATE = True
    → Message is acknowledged AFTER task execution, not before.
    → If the worker is SIGKILL'd mid-task, the message remains
      unacknowledged in the broker and will be redelivered.

- CELERY_TASK_REJECT_ON_WORKER_LOST = True
    → If the parent worker process detects a child was killed,
      it explicitly rejects the message so the broker redelivers.

What happens if a Celery worker is SIGKILL'd:
    1. The OS terminates the process immediately — no cleanup runs.
    2. Because acks_late=True, the message was never acknowledged.
    3. Redis (the broker) holds the message in its "unacked" set.
    4. After `visibility_timeout` expires (default: 1 hour in our config),
       Redis moves the message back to the pending queue.
    5. Another worker picks it up and re-executes the task.
    6. Risk: the task may execute TWICE (at-least-once semantics).
       For emails, we mitigate this by including a unique `email_id`
       that the email provider can use for deduplication.
    7. With reject_on_worker_lost=True, if the worker pool manager
       (parent) detects the child died, it rejects immediately —
       without waiting for visibility_timeout.
"""
import logging
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from .rate_limiter import email_rate_limiter
from .dead_letter import move_to_dead_letter

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    """Raised when the email provider returns an error."""
    pass


class EmailRateLimitError(Exception):
    """Raised when the rate limiter denies the request."""
    pass


def _send_email_via_provider(recipient, subject, body, email_id=None):
    """
    Simulate sending an email through a third-party provider.

    In production, this would call the provider's API (e.g., SendGrid,
    Mailgun, SES). The email_id parameter enables idempotent delivery —
    if the same email_id is sent twice, the provider deduplicates it.

    For testing: this function can be monkeypatched to simulate failures.
    """
    logger.info(f"Email sent to {recipient}: {subject} [id={email_id}]")
    return True


@shared_task(
    bind=True,
    name='section2_queue.tasks.send_email',
    max_retries=5,
    # Exponential backoff: 2^retry * 1 second, with jitter
    # Retry 1: ~2s, Retry 2: ~4s, Retry 3: ~8s, Retry 4: ~16s, Retry 5: ~32s
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    # acks_late ensures crash safety (see module docstring)
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_email(self, recipient, subject, body, email_id=None):
    """
    Send a transactional email with rate limiting and retry logic.

    Flow:
    1. Check rate limiter — if denied, re-queue with countdown (no sleep!)
    2. Attempt to send via provider
    3. On failure: retry with exponential backoff
    4. After max retries: move to dead-letter queue
    """
    # Step 1: Check rate limiter
    allowed, wait_seconds = email_rate_limiter.acquire()

    if not allowed:
        # Rate limit exceeded — re-queue the task with a countdown.
        # This does NOT use time.sleep(). Instead, Celery schedules
        # the task to execute after `wait_seconds` via Redis/broker.
        logger.warning(
            f"Rate limited — re-queuing task {self.request.id} "
            f"with {wait_seconds}s countdown"
        )
        # Use countdown to delay re-execution without blocking the worker
        raise self.retry(
            exc=EmailRateLimitError(f"Rate limit exceeded, wait {wait_seconds}s"),
            countdown=wait_seconds,
            max_retries=None,  # Rate-limit retries don't count toward max_retries
        )

    # Step 2: Attempt to send
    try:
        _send_email_via_provider(
            recipient=recipient,
            subject=subject,
            body=body,
            email_id=email_id or self.request.id,
        )
        logger.info(f"Successfully sent email to {recipient} [task={self.request.id}]")
        return {
            'status': 'sent',
            'recipient': recipient,
            'task_id': self.request.id,
        }

    except Exception as exc:
        logger.error(
            f"Email send failed for {recipient}: {exc}. "
            f"Retry {self.request.retries}/{self.max_retries}"
        )

        # Step 3: Check if retries are exhausted BEFORE calling self.retry()
        # This ensures dead-letter handling works in both eager and worker mode.
        if self.request.retries >= self.max_retries:
            # Step 4: All retries exhausted — move to dead-letter queue
            logger.critical(
                f"Email permanently failed for {recipient} after "
                f"{self.max_retries} retries. Moving to DLQ."
            )
            move_to_dead_letter(
                task_id=self.request.id,
                task_args=self.request.args,
                task_kwargs=self.request.kwargs,
                exception_info=str(exc),
            )
            return {
                'status': 'dead_lettered',
                'recipient': recipient,
                'task_id': self.request.id,
                'error': str(exc),
            }

        # Still have retries left — retry with exponential backoff
        raise self.retry(exc=exc)
