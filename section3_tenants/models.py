"""
Section 3 — Multi-tenant models.

Tenant: Represents a client organization in the SaaS platform.
TenantOrder: An order scoped to a specific tenant.

The TenantOrder model uses TenantManager as its default manager,
which automatically applies .filter(tenant=current_tenant) to every
queryset. A separate `unscoped` manager provides raw access for
admin and migration use cases.
"""
from django.db import models
from .managers import TenantManager


class Tenant(models.Model):
    """
    A client organization in the SaaS platform.
    Each tenant's data is isolated from every other tenant.
    """
    name = models.CharField(max_length=200)
    subdomain = models.CharField(max_length=63, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'section3_tenant'

    def __str__(self):
        return self.name


class TenantOrder(models.Model):
    """
    An order belonging to a specific tenant.

    Key design:
    - `objects = TenantManager()`: Every call through `TenantOrder.objects`
      is automatically scoped to the current tenant. Even .all() returns
      only the current tenant's orders.

    - `unscoped = models.Manager()`: Provides raw, unscoped access for
      admin, migrations, and management commands. This is a deliberate
      escape hatch — named `unscoped` to make its usage explicit and
      auditable in code reviews.
    """
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='orders',
    )
    order_number = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    # Default manager: auto-scoped to current tenant
    objects = TenantManager()

    # Explicit unscoped manager for admin/migration use
    unscoped = models.Manager()

    class Meta:
        db_table = 'section3_tenant_order'
        # The default manager (objects) is listed first, so Django
        # uses it as the default for related lookups as well.
        default_manager_name = 'objects'

    def __str__(self):
        return f"Order {self.order_number} ({self.tenant.name})"
