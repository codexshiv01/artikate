"""
Section 2 — API views for triggering email jobs and monitoring queue state.
"""
import uuid
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .tasks import send_email
from .rate_limiter import email_rate_limiter
from .dead_letter import get_dead_letters, get_dead_letter_count


@api_view(['POST'])
def submit_email_job(request):
    """
    Submit a transactional email to the job queue.

    POST /api/queue/submit/
    {
        "recipient": "user@example.com",
        "subject": "Order Confirmation",
        "body": "Your order has been confirmed.",
        "count": 1  // optional: submit multiple jobs
    }
    """
    recipient = request.data.get('recipient', 'test@example.com')
    subject = request.data.get('subject', 'Test Email')
    body = request.data.get('body', 'This is a test email.')
    count = min(int(request.data.get('count', 1)), 2000)

    task_ids = []
    for i in range(count):
        email_id = str(uuid.uuid4())
        task = send_email.delay(
            recipient=f'{i}_{recipient}' if count > 1 else recipient,
            subject=subject,
            body=body,
            email_id=email_id,
        )
        task_ids.append(task.id)

    return Response({
        'submitted': count,
        'task_ids': task_ids[:10],  # Return first 10 IDs
        'message': f'{count} email(s) queued for delivery',
    })


@api_view(['GET'])
def queue_status(request):
    """
    Get current rate limiter and queue status.

    GET /api/queue/status/
    """
    limiter_status = email_rate_limiter.get_status()
    return Response({
        'rate_limiter': limiter_status,
        'dlq_count': get_dead_letter_count(),
    })


@api_view(['GET'])
def dead_letter_status(request):
    """
    View dead-lettered tasks.

    GET /api/queue/dlq/
    """
    entries = get_dead_letters(count=50)
    return Response({
        'count': len(entries),
        'entries': entries,
    })
