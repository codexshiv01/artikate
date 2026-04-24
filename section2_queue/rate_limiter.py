"""
Section 2 — Redis-based Token Bucket Rate Limiter.

Implements a token-bucket algorithm using a Lua script for atomicity.
No time.sleep() — if the rate limit is exceeded, the caller gets a
denial with the number of seconds to wait before retrying.

Why token bucket over alternatives:
- Fixed window: suffers from boundary burst (2x rate at window edges)
- Sliding window (sorted set): more memory-intensive, ZRANGEBYSCORE is O(log N)
- Token bucket: smooths bursts, O(1) operations, single Lua script

Atomicity guarantee:
- The entire check-and-consume operation runs as a single Lua script.
- Redis executes Lua scripts atomically — no other command can interleave.
- This eliminates race conditions between multiple Celery workers.

Redis failure mode: FAIL OPEN
- If Redis is unreachable, we allow the email to be sent rather than
  silently dropping it. Rationale: transactional emails (OTP, order
  confirmations) are critical — exceeding the rate limit temporarily
  is preferable to losing customer communications. The email provider
  will return 429 errors, which our retry logic handles gracefully.
"""
import time
import redis
from django.conf import settings


# Lua script for atomic token bucket operation.
# This script runs entirely within Redis — no race conditions possible.
#
# Algorithm:
#   1. Read current bucket state (tokens remaining, last refill timestamp)
#   2. Calculate how many tokens to add based on elapsed time
#   3. If tokens available: consume one and return allowed=1
#   4. If empty: return allowed=0 with seconds until next token
#
TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local max_tokens = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])  -- tokens per second
local now = tonumber(ARGV[3])

-- Read current state
local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

-- Initialize bucket if it doesn't exist
if tokens == nil then
    tokens = max_tokens
    last_refill = now
end

-- Calculate tokens to add since last refill
local elapsed = now - last_refill
local new_tokens = elapsed * refill_rate
tokens = math.min(max_tokens, tokens + new_tokens)
last_refill = now

-- Try to consume a token
if tokens >= 1 then
    tokens = tokens - 1
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    redis.call('EXPIRE', key, 120)  -- TTL: 2 minutes (cleanup)
    return {1, 0}  -- {allowed, wait_seconds}
else
    -- Calculate wait time until next token
    local wait = (1 - tokens) / refill_rate
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    redis.call('EXPIRE', key, 120)
    return {0, math.ceil(wait)}  -- {denied, seconds_to_wait}
end
"""


class TokenBucketRateLimiter:
    """
    Rate limiter using Redis token bucket algorithm.

    Configuration for 200 emails/minute:
        max_tokens = 200   (burst capacity)
        refill_rate = 200/60 ≈ 3.33 tokens/second
    """

    def __init__(
        self,
        key='rate_limiter:email',
        max_tokens=200,
        refill_rate=None,
        redis_url=None,
    ):
        self.key = key
        self.max_tokens = max_tokens
        # Default: 200 tokens per 60 seconds = 3.333.../sec
        self.refill_rate = refill_rate or (max_tokens / 60.0)
        self.redis_url = redis_url or getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0')
        self._redis = None
        self._script = None

    @property
    def redis_client(self):
        """Lazy-initialize the Redis connection."""
        if self._redis is None:
            self._redis = redis.Redis.from_url(self.redis_url)
        return self._redis

    @property
    def script(self):
        """Register the Lua script once, then reuse the SHA."""
        if self._script is None:
            self._script = self.redis_client.register_script(TOKEN_BUCKET_LUA)
        return self._script

    def acquire(self):
        """
        Try to acquire a token.

        Returns:
            (allowed: bool, wait_seconds: int)
            - allowed=True: token consumed, proceed with sending
            - allowed=False: rate limit hit, retry after wait_seconds
        """
        try:
            now = time.time()
            result = self.script(
                keys=[self.key],
                args=[self.max_tokens, self.refill_rate, now],
            )
            allowed = bool(result[0])
            wait_seconds = int(result[1])
            return allowed, wait_seconds

        except redis.ConnectionError:
            # FAIL OPEN: if Redis is down, allow the email through.
            # The email provider will rate-limit us with 429 responses,
            # and our retry logic will handle those gracefully.
            return True, 0

        except redis.RedisError:
            # Any other Redis error: also fail open for same reason.
            return True, 0

    def reset(self):
        """Reset the bucket (for testing)."""
        try:
            self.redis_client.delete(self.key)
        except redis.RedisError:
            pass

    def get_status(self):
        """Get current bucket state (for monitoring/debugging)."""
        try:
            data = self.redis_client.hgetall(self.key)
            if data:
                return {
                    'tokens': float(data.get(b'tokens', 0)),
                    'last_refill': float(data.get(b'last_refill', 0)),
                }
            return {'tokens': self.max_tokens, 'last_refill': 0}
        except redis.RedisError:
            return {'tokens': -1, 'last_refill': -1, 'error': 'Redis unavailable'}


# Module-level singleton for use by Celery tasks
email_rate_limiter = TokenBucketRateLimiter()
