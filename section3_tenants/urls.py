from django.urls import path
from . import views

urlpatterns = [
    path('orders/', views.tenant_orders, name='tenant-orders'),
]
