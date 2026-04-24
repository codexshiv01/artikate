"""
Section 1 — Views demonstrating the N+1 query problem and its fix.

/api/orders/summary/        → Broken endpoint (N+1 queries)
/api/orders/summary/fixed/  → Fixed endpoint (3 queries)

Both return identical JSON. The only difference is how the queryset
is constructed before being passed to the serializer.
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.contrib.auth.models import User
from django.db.models import Prefetch

from .models import Order, OrderItem
from .serializers import BrokenOrderSerializer, FixedOrderSerializer


@api_view(['GET'])
def order_summary_broken(request):
    """
    BROKEN endpoint — triggers N+1 queries.

    What happens at the database level:
    1. One query fetches all orders for the user.
    2. For EACH order, DRF's serializer accesses `order.items.all()`,
       which Django's ORM lazy-loads with a separate SELECT.
    3. For EACH item, accessing `item.product` triggers yet another SELECT.

    With 300 orders × 3 items = 1 + 300 + 900 = ~1,201 queries.
    Each query has network round-trip overhead to Neon PostgreSQL,
    turning 80ms into 30+ seconds.

    Scenario context: This was fast before deployment because a previous
    version of the serializer did not include nested `items`. A developer
    added the `items` field to the serializer in a different file, without
    updating the view's queryset — hence "no code change to the view."
    """
    user = User.objects.first()
    if not user:
        return Response({'error': 'No users found. Run: python manage.py seed_orders'}, status=404)

    # BAD: Plain queryset with no prefetching.
    # Django will lazy-load every related object on access.
    orders = Order.objects.filter(user=user)

    serializer = BrokenOrderSerializer(orders, many=True)
    return Response({
        'user': user.username,
        'order_count': orders.count(),
        'orders': serializer.data,
    })


@api_view(['GET'])
def order_summary_fixed(request):
    """
    FIXED endpoint — exactly 3 queries regardless of order count.

    Fix applied at the queryset level:

    1. select_related('user'):
       Performs a SQL JOIN to fetch the user in the same query as orders.
       This avoids a separate query when the serializer accesses order.user.username.

    2. prefetch_related(Prefetch('items', queryset=...)):
       After fetching orders, Django issues ONE query:
         SELECT * FROM order_items WHERE order_id IN (1, 2, 3, ...)
       and caches the results in memory. When the serializer iterates
       order.items.all(), it reads from the cache — zero extra queries.

    3. The inner select_related('product') on the Prefetch queryset:
       Joins products into the items query:
         SELECT items.*, products.* FROM order_items
         JOIN products ON items.product_id = products.id
         WHERE order_id IN (...)
       So accessing item.product also hits the cache.

    Total: 3 queries (orders+user JOIN, items+product JOIN, count).
    Time: ~80ms regardless of order count.
    """
    user = User.objects.first()
    if not user:
        return Response({'error': 'No users found. Run: python manage.py seed_orders'}, status=404)

    # FIXED: Eager-load all related objects in advance.
    orders = (
        Order.objects
        .filter(user=user)
        .select_related('user')
        .prefetch_related(
            Prefetch(
                'items',
                queryset=OrderItem.objects.select_related('product'),
            )
        )
    )

    serializer = FixedOrderSerializer(orders, many=True)
    return Response({
        'user': user.username,
        'order_count': orders.count(),
        'orders': serializer.data,
    })
