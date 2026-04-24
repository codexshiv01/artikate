"""
artikate URL configuration.
Routes for all assessment sections + django-silk profiler.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),

    # Section 1 — Order summary endpoints (broken + fixed)
    path('api/', include('section1_orders.urls')),

    # Section 2 — Job queue trigger endpoints
    path('api/queue/', include('section2_queue.urls')),

    # Section 3 — Tenant-scoped endpoints
    path('api/tenants/', include('section3_tenants.urls')),

    # Silk profiler dashboard
    path('silk/', include('silk.urls', namespace='silk')),
]
