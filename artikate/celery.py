"""
Celery application configuration for the artikate project.

This module initializes the Celery app, binds it to Django settings,
and auto-discovers tasks from all installed apps.
"""
import os
from celery import Celery

# Set the default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'artikate.settings')

app = Celery('artikate')

# Pull configuration from Django settings, using the CELERY_ namespace
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py in every installed app
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Simple debug task to verify Celery connectivity."""
    print(f'Request: {self.request!r}')
