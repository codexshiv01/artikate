"""
Django settings for artikate project.
Artikate Studio — Backend Developer Technical Assessment
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-artikate-assessment-key-not-for-production'

DEBUG = True

ALLOWED_HOSTS = ['*']

# ---------- Installed Apps ----------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'silk',
    'corsheaders',

    # Assessment apps
    'section1_orders',
    'section2_queue',
    'section3_tenants',
]

# ---------- Middleware ----------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    # Silk profiler — captures every request
    'silk.middleware.SilkyMiddleware',

    # Section 3 — Multi-tenant middleware
    'section3_tenants.middleware.TenantMiddleware',
]

ROOT_URLCONF = 'artikate.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'artikate.wsgi.application'

# ---------- Database (Neon PostgreSQL) ----------
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'neondb',
        'USER': 'neondb_owner',
        'PASSWORD': 'npg_zUVL6JC0nRAc',
        'HOST': 'ep-long-lab-ammve2mr-pooler.c-5.us-east-1.aws.neon.tech',
        'PORT': '5432',
        'OPTIONS': {
            'sslmode': 'require',
        },
    }
}

# ---------- Auth ----------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------- Internationalization ----------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# ---------- Static ----------
STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------- REST Framework ----------
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

# ---------- Silk Profiler ----------
SILKY_PYTHON_PROFILER = False
SILKY_PYTHON_PROFILER_BINARY = False
SILKY_META = True
SILKY_INTERCEPT_PERCENT = 100  # Profile 100% of requests

# ---------- Celery ----------
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Kolkata'

# Worker acknowledges AFTER task execution, not before.
# This means if a worker is SIGKILL'd mid-task, the message
# remains unacknowledged and Redis/broker redelivers it.
CELERY_TASK_ACKS_LATE = True

# If the worker process is lost (SIGKILL), reject the message
# so the broker knows to redeliver it immediately.
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# Visibility timeout: if a task isn't ack'd within this window,
# the broker considers it lost and redelivers. Must be longer
# than the longest expected task duration.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': 3600,  # 1 hour
}

# ---------- Redis ----------
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# ---------- CORS ----------
CORS_ALLOW_ALL_ORIGINS = True
