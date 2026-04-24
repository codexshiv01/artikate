from django.urls import path
from . import views

urlpatterns = [
    # Broken endpoint — demonstrates N+1 problem
    path('orders/summary/', views.order_summary_broken, name='order-summary-broken'),
    # Fixed endpoint — uses select_related / prefetch_related
    path('orders/summary/fixed/', views.order_summary_fixed, name='order-summary-fixed'),
]
