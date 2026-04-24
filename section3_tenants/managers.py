"""
Section 3 — Custom TenantManager for automatic query scoping.

Every model that uses TenantManager as its default manager will
automatically have all queries scoped to the current tenant.

This means:
    Order.objects.all()           → SELECT * FROM orders WHERE tenant_id = ?
    Order.objects.filter(...)     → SELECT * FROM orders WHERE tenant_id = ? AND ...
    Order.objects.get(pk=1)       → SELECT * FROM orders WHERE tenant_id = ? AND id = 1

The developer CANNOT accidentally forget to filter by tenant —
it happens automatically in get_queryset().

Design decisions:
1. The scoping only applies when a tenant is set in context.
   During migrations, management commands, and admin, tenant may be None.
   In those cases, the full unscoped queryset is returned.

2. We provide a separate `unscoped` manager for explicit admin/migration
   use cases that need to see all tenants' data.
"""
from django.db import models
from .context import get_current_tenant


class TenantQuerySet(models.QuerySet):
    """Custom QuerySet that maintains tenant scoping through chaining."""
    pass


class TenantManager(models.Manager):
    """
    Manager that automatically scopes all queries to the current tenant.

    Usage:
        class Order(models.Model):
            tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
            objects = TenantManager()       # Auto-scoped
            unscoped = models.Manager()     # For admin/migrations
    """

    def get_queryset(self):
        """
        Override the base queryset to apply tenant filtering.

        How it works:
        1. Get the tenant from thread-local storage (set by middleware)
        2. If a tenant exists, add .filter(tenant=tenant) to the queryset
        3. Every subsequent .filter(), .exclude(), .get(), .all() etc.
           chains on top of this already-scoped queryset

        This is why the scoping cannot be bypassed by calling .all() —
        Django's ORM always starts from get_queryset(), so our filter
        is always the base of the chain.
        """
        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return qs
