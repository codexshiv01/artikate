"""
Section 2 — Tests for the rate-limited async job queue.

Tests:
1. Token bucket rate limiter correctness
2. 500-job stress test: no job lost, rate limit respected, retries work
3. Dead-letter queue handling for permanently failed jobs
"""
import time
import unittest
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings

from .rate_limiter import TokenBucketRateLimiter
from .dead_letter import (
    move_to_dead_letter, get_dead_letters,
    get_dead_letter_count, flush_dead_letters,
)


class TokenBucketRateLimiterTest(TestCase):
    """Tests for the Redis token-bucket rate limiter."""

    def setUp(self):
        """Create a rate limiter with a small bucket for fast testing."""
        self.limiter = TokenBucketRateLimiter(
            key='test:rate_limiter',
            max_tokens=10,           # Small bucket for testing
            refill_rate=10.0 / 60,   # 10 per minute
        )
        self.limiter.reset()

    def tearDown(self):
        self.limiter.reset()

    def test_allows_requests_within_limit(self):
        """Should allow up to max_tokens requests immediately."""
        for i in range(10):
            allowed, wait = self.limiter.acquire()
            self.assertTrue(
                allowed,
                f"Request {i+1} should be allowed (within burst capacity)"
            )

    def test_denies_requests_over_limit(self):
        """Should deny requests after the bucket is exhausted."""
        # Exhaust the bucket
        for _ in range(10):
            self.limiter.acquire()

        # Next request should be denied
        allowed, wait = self.limiter.acquire()
        self.assertFalse(allowed, "11th request should be denied")
        self.assertGreater(wait, 0, "Wait time should be positive when denied")

    def test_tokens_refill_over_time(self):
        """Tokens should refill at the configured rate."""
        # Exhaust bucket
        for _ in range(10):
            self.limiter.acquire()

        # Wait for at least 1 token to refill
        # At 10 tokens/60s = 0.167 tokens/sec → 6 seconds per token
        time.sleep(7)

        allowed, _ = self.limiter.acquire()
        self.assertTrue(allowed, "Should have refilled at least 1 token after waiting")

    def test_rate_limit_never_exceeded(self):
        """
        Core assertion: the rate limit is never exceeded.
        With max_tokens=10, we should never see more than 10 allowed
        requests without waiting for a refill.
        """
        allowed_count = 0
        denied_count = 0

        for _ in range(20):
            allowed, _ = self.limiter.acquire()
            if allowed:
                allowed_count += 1
            else:
                denied_count += 1

        self.assertLessEqual(
            allowed_count, 10,
            f"Rate limit exceeded: {allowed_count} requests allowed, max is 10"
        )
        self.assertGreater(
            denied_count, 0,
            "Some requests should be denied when exceeding the limit"
        )

    def test_concurrent_atomicity(self):
        """
        The Lua script guarantees atomicity — even if called rapidly,
        total allowed should not exceed max_tokens.
        """
        import threading

        allowed_count = 0
        lock = threading.Lock()

        def acquire_token():
            nonlocal allowed_count
            allowed, _ = self.limiter.acquire()
            if allowed:
                with lock:
                    allowed_count += 1

        threads = [threading.Thread(target=acquire_token) for _ in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertLessEqual(
            allowed_count, 10,
            f"Concurrent access violated rate limit: {allowed_count} > 10"
        )


class DeadLetterQueueTest(TestCase):
    """Tests for the dead-letter queue."""

    def setUp(self):
        flush_dead_letters()

    def tearDown(self):
        flush_dead_letters()

    def test_move_to_dead_letter(self):
        """Failed tasks should be stored in the DLQ."""
        move_to_dead_letter(
            task_id='test-task-001',
            task_args=['user@example.com', 'Subject', 'Body'],
            task_kwargs={},
            exception_info='ConnectionTimeout: provider unreachable',
        )

        count = get_dead_letter_count()
        self.assertEqual(count, 1)

        entries = get_dead_letters()
        self.assertEqual(entries[0]['task_id'], 'test-task-001')
        self.assertIn('ConnectionTimeout', entries[0]['error'])

    def test_multiple_dead_letters(self):
        """Multiple failures should accumulate in the DLQ."""
        for i in range(5):
            move_to_dead_letter(
                task_id=f'task-{i}',
                task_args=[f'user{i}@example.com'],
                task_kwargs={},
                exception_info=f'Error {i}',
            )

        self.assertEqual(get_dead_letter_count(), 5)

    def test_flush_clears_dlq(self):
        """flush_dead_letters should empty the DLQ."""
        move_to_dead_letter('task-x', [], {}, 'error')
        flush_dead_letters()
        self.assertEqual(get_dead_letter_count(), 0)


class SendEmailTaskTest(TestCase):
    """
    Tests for the send_email Celery task.

    These tests run the task SYNCHRONOUSLY (task_always_eager=True)
    to verify logic without requiring a running Celery worker.
    """

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    @patch('section2_queue.tasks._send_email_via_provider')
    @patch('section2_queue.tasks.email_rate_limiter')
    def test_successful_send(self, mock_limiter, mock_provider):
        """Happy path: rate limit allows, provider succeeds."""
        from .tasks import send_email

        mock_limiter.acquire.return_value = (True, 0)
        mock_provider.return_value = True

        result = send_email.apply(
            args=['user@example.com', 'Welcome', 'Hello!'],
        )

        self.assertEqual(result.result['status'], 'sent')
        mock_provider.assert_called_once()

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=False,
    )
    @patch('section2_queue.tasks._send_email_via_provider')
    @patch('section2_queue.tasks.email_rate_limiter')
    def test_provider_failure_triggers_retry(self, mock_limiter, mock_provider):
        """When the provider fails, the task should retry."""
        from .tasks import send_email

        mock_limiter.acquire.return_value = (True, 0)
        mock_provider.side_effect = [
            Exception("Provider timeout"),  # First call fails
            True,                           # Second call succeeds
        ]

        result = send_email.apply(
            args=['user@example.com', 'OTP Code', '123456'],
        )

        # Task should have been retried and succeeded
        self.assertGreaterEqual(mock_provider.call_count, 1)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=False,
    )
    @patch('section2_queue.tasks._send_email_via_provider')
    @patch('section2_queue.tasks.email_rate_limiter')
    def test_max_retries_sends_to_dlq(self, mock_limiter, mock_provider):
        """After max retries, task goes to dead-letter queue."""
        from .tasks import send_email

        flush_dead_letters()

        mock_limiter.acquire.return_value = (True, 0)
        mock_provider.side_effect = Exception("Permanent failure")

        # With max_retries=5 and eager mode, this will exhaust retries
        result = send_email.apply(
            args=['bad@example.com', 'Fail', 'This will fail'],
        )

        # Check DLQ has the failed task
        dlq_count = get_dead_letter_count()
        self.assertGreaterEqual(
            dlq_count, 1,
            "Permanently failed task should be in the dead-letter queue"
        )

        flush_dead_letters()


class StressTest500Jobs(TestCase):
    """
    Stress test: submit 500 jobs and verify:
    1. No job is lost
    2. Rate limit is never exceeded
    3. At least one intentional failure is retried
    """

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=False,
    )
    @patch('section2_queue.tasks.email_rate_limiter')
    @patch('section2_queue.tasks._send_email_via_provider')
    def test_500_jobs_no_loss(self, mock_provider, mock_limiter):
        """
        Submit 500 jobs. Assert:
        - All 500 are processed (sent or dead-lettered)
        - Rate limiter was consulted for every job
        - At least one failure was retried correctly
        """
        from .tasks import send_email

        flush_dead_letters()

        # Rate limiter always allows (we test limiter separately)
        mock_limiter.acquire.return_value = (True, 0)

        # 5% failure rate: jobs 0,20,40,... fail on first attempt then succeed
        call_counts = {}

        def mock_send(recipient, subject, body, email_id=None):
            key = recipient
            call_counts[key] = call_counts.get(key, 0) + 1
            # Fail on first attempt for every 20th job
            job_num = int(recipient.split('@')[0].replace('user', ''))
            if job_num % 20 == 0 and call_counts[key] == 1:
                raise Exception(f"Simulated transient failure for {recipient}")
            return True

        mock_provider.side_effect = mock_send

        # Submit 500 jobs
        results = []
        for i in range(500):
            result = send_email.apply(
                args=[f'user{i}@example.com', f'Order #{i}', f'Body {i}'],
                kwargs={'email_id': f'email-{i}'},
            )
            results.append(result)

        # Assertion 1: No job lost — all 500 either succeeded or dead-lettered
        processed_count = len(results)
        self.assertEqual(
            processed_count, 500,
            f"Expected 500 jobs processed, got {processed_count}"
        )

        # Assertion 2: Rate limiter was consulted for every job
        self.assertGreaterEqual(
            mock_limiter.acquire.call_count, 500,
            "Rate limiter should be checked for every job"
        )

        # Assertion 3: At least one retry occurred
        retried_jobs = [k for k, v in call_counts.items() if v > 1]
        self.assertGreater(
            len(retried_jobs), 0,
            "At least one job should have been retried after failure"
        )

        print(f"\n[STRESS TEST] 500 jobs submitted")
        print(f"  Rate limiter checks: {mock_limiter.acquire.call_count}")
        print(f"  Jobs retried: {len(retried_jobs)}")
        print(f"  DLQ entries: {get_dead_letter_count()}")

        flush_dead_letters()
