from django.urls import path
from . import views

urlpatterns = [
    path('submit/', views.submit_email_job, name='submit-email-job'),
    path('status/', views.queue_status, name='queue-status'),
    path('dlq/', views.dead_letter_status, name='dlq-status'),
]
