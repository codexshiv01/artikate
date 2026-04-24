# Section 2 — Rate-Limited Async Job Queue: Design Document

## Problem Statement

A transactional email system must handle burst traffic of **2,000 requests in under 10 seconds** during flash sales, while respecting a third-party provider limit of **200 emails per minute**. The system must:

1. Queue emails reliably
2. Enforce the rate limit without dropping jobs
3. Retry failed sends with exponential backoff
4. Never lose a job, even if a worker crashes mid-execution

---

## Architecture Options Evaluated

### Option A: Celery + Redis

| Aspect | Detail |
|---|---|
| **Broker** | Redis (pub/sub + list-based queues) |
| **Task execution** | Celery workers with prefork pool |
| **Retry** | Built-in `retry_backoff`, `max_retries`, `retry_jitter` |
| **Crash recovery** | `acks_late=True` + `reject_on_worker_lost=True` |
| **Pros** | Battle-tested in production at scale; native exponential backoff; visibility timeout for unacked messages; large ecosystem and documentation |
| **Cons** | Operational complexity (broker + worker processes); Redis is in-memory so requires AOF/RDB persistence configuration; visibility timeout requires tuning |

### Option B: Django Q

| Aspect | Detail |
|---|---|
| **Broker** | Django ORM (database-backed) or Redis |
| **Task execution** | `qcluster` management command |
| **Retry** | Manual implementation needed |
| **Pros** | Simpler setup; database-backed mode means no Redis dependency; Django-native |
| **Cons** | Smaller community; no built-in exponential backoff; rate limiting must be fully custom; less control over acknowledgment semantics; database-backed mode has higher latency under burst load |

### Option C: Custom Implementation (DB + cron)

| Aspect | Detail |
|---|---|
| **Broker** | PostgreSQL table as job queue |
| **Task execution** | Cron job or management command polling the table |
| **Retry** | Custom `retry_count` column + scheduled re-processing |
| **Pros** | Full control; no external dependencies beyond the database; simplest deployment |
| **Cons** | Must build retry logic, backoff, crash recovery, and worker management from scratch; polling introduces latency; database contention under burst load; no built-in concurrency control |

### Decision: Celery + Redis

**Rationale:**

The core requirements — crash recovery, exponential backoff, and rate limiting under burst load — are exactly what Celery was designed for. Specifically:

1. **`acks_late=True`** gives us crash safety without writing any custom recovery code. This single configuration option solves the "worker crashes mid-run" requirement.

2. **Built-in retry with `retry_backoff=True`** provides exponential backoff with jitter out of the box. Django Q would require us to implement this manually.

3. **Redis as broker** handles the burst scenario well — 2,000 `LPUSH` operations take milliseconds. A database-backed queue (Django Q or custom) would face write contention under this load.

4. **The rate limiter is custom regardless of choice** — none of these options provide a built-in Redis-atomic rate limiter that meets the assessment's requirements.

The operational overhead of running Celery + Redis is the main trade-off. For a startup or small team, Django Q's simplicity might win. But for a system that must handle flash-sale bursts without data loss, Celery's maturity and explicit crash recovery semantics are worth the complexity.

---

## Rate Limiter Design

### Algorithm: Token Bucket

**Chosen over:**

| Algorithm | Why not chosen |
|---|---|
| **Fixed window** (`INCR` + `EXPIRE`) | Suffers from the boundary burst problem. At the edge of two windows, a client can send 2× the rate (200 at 0:59, 200 at 1:00 = 400 in 2 seconds). Unacceptable for a hard rate limit. |
| **Sliding window** (`ZADD` + `ZREMRANGEBYSCORE`) | More accurate than fixed window, but each operation is O(log N) on the sorted set. With 200 entries/minute and multiple workers, memory and CPU overhead is higher. Also requires storing individual timestamps. |
| **Token bucket** (`HMGET` + `HMSET` in Lua) | Smooths bursts naturally (allows up to `max_tokens` burst, then limits to refill rate). O(1) operations. Single hash key with two fields — minimal memory. |

### Implementation Details

```
Key:   rate_limiter:email (Redis Hash)
Fields: tokens (float), last_refill (float timestamp)

max_tokens = 200       → burst capacity
refill_rate = 200/60   → 3.33 tokens/second
```

**Lua script (atomic operation):**

```lua
-- 1. Read current bucket state
-- 2. Calculate tokens to add based on elapsed time
-- 3. If tokens >= 1: consume one, return {1, 0} (allowed)
-- 4. If tokens < 1: return {0, wait_seconds} (denied)
```

### Atomicity Guarantee

The entire check-and-consume operation is a **single Lua script** executed by Redis. Redis runs Lua scripts atomically — no other command can interleave during execution. This eliminates race conditions between multiple Celery workers competing for tokens.

We chose Lua over `MULTI/EXEC` because:
- `MULTI/EXEC` cannot read a value and make a decision based on it within the same transaction (no conditional logic)
- `WATCH/MULTI/EXEC` (optimistic locking) requires retry loops on the client side
- Lua scripts run server-side with full conditional logic in a single round-trip

### Redis Failure Behavior: FAIL OPEN

If Redis is unreachable:

```python
except redis.ConnectionError:
    return True, 0  # Allow the email through
```

**Rationale:** Transactional emails (OTP codes, order confirmations) are **critical for business operations**. If Redis is down:

- **Fail closed** (block all emails) → customers can't verify OTP, don't receive order confirmations → direct revenue loss and support tickets
- **Fail open** (allow all emails) → we temporarily exceed the provider's rate limit → provider returns 429 errors → our retry logic handles those gracefully

The provider's own rate limiting acts as a safety net. Temporary over-sending is recoverable; not sending is not.

---

## Crash Recovery: SIGKILL Scenario

### What happens when a Celery worker is SIGKILL'd mid-task?

1. **SIGKILL** terminates the process immediately. No signal handlers run, no `finally` blocks execute, no cleanup occurs.

2. **With `acks_late=True`** (configured in `settings.py` as `CELERY_TASK_ACKS_LATE`): the message is acknowledged **after** task execution completes, not before. Since the worker died before completing, the message was **never acknowledged**.

3. **Redis broker behavior**: The unacknowledged message sits in Redis's "unacked" set (internally, a sorted set keyed by `visibility_timeout`). After `visibility_timeout` expires (configured as 3600 seconds in our `CELERY_BROKER_TRANSPORT_OPTIONS`), Redis automatically moves the message back to the pending queue.

4. **With `reject_on_worker_lost=True`** (configured as `CELERY_TASK_REJECT_ON_WORKER_LOST`): If the Celery worker **pool manager** (the parent process using prefork) detects that a child worker process died, it explicitly rejects the message. This causes immediate redelivery without waiting for `visibility_timeout`.

5. **Another worker picks up the redelivered message** and re-executes the task.

6. **Risk: At-least-once delivery**. The task may execute **twice** — once partially (before SIGKILL) and once fully (after redelivery). For our email use case, we mitigate this by passing an `email_id` parameter that the email provider can use for **idempotent delivery** — if the same `email_id` is sent twice, the provider deduplicates it.

### Relevant Celery configuration in our implementation:

```python
# settings.py
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': 3600,  # 1 hour
}
```

```python
# tasks.py — per-task override
@shared_task(
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_email(self, ...):
    ...
```

---

## Dead-Letter Queue

Tasks that fail after exhausting all 5 retries are moved to a Redis list (`dead_letter_queue:emails`) containing:

- Task ID
- Original arguments (recipient, subject, body)
- Error message
- Failure timestamp

This ensures **no job is silently lost**. Operations teams can inspect the DLQ via the `/api/queue/dlq/` endpoint and manually replay failed tasks after resolving the underlying issue.

---

## Test Strategy

The test suite (`section2_queue/tests.py`) covers:

1. **Rate limiter unit tests**: burst capacity, denial after exhaustion, refill over time, concurrent atomicity via threading
2. **Task tests**: successful send, provider failure triggers retry, max retries → DLQ
3. **500-job stress test**: submits 500 jobs with 5% failure rate, asserts all are processed, rate limiter is consulted for every job, and at least one retry occurs

Tests run synchronously using `CELERY_TASK_ALWAYS_EAGER=True` for deterministic assertions.
