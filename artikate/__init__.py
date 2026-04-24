"""
artikate project __init__.

Import Celery app so it is available when Django starts.
"""
from .celery import app as celery_app

__all__ = ('celery_app',)
