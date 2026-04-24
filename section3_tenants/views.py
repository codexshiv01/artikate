"""
Section 3 — Tenant-scoped API views.

These views do NOT need to call .filter(tenant=...) — the TenantManager
handles scoping automatically. This is the whole point: developers
cannot accidentally forget the tenant filter.
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import TenantOrder
from .context import get_current_tenant


@api_view(['GET'])
def tenant_orders(request):
    """
    List orders for the current tenant.

    Note: We simply call TenantOrder.objects.all() — the TenantManager
    automatically applies .filter(tenant=current_tenant). No manual
    filtering needed.
    """
    tenant = get_current_tenant()
    if not tenant:
        return Response({'error': 'No tenant context'}, status=400)

    # This .all() is auto-scoped by TenantManager
    orders = TenantOrder.objects.all().values(
        'id', 'order_number', 'description', 'amount', 'created_at'
    )

    return Response({
        'tenant': tenant.name,
        'order_count': orders.count(),
        'orders': list(orders),
    })
