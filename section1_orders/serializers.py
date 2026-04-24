"""
Section 1 — Serializers demonstrating the N+1 problem and its fix.

BrokenOrderSerializer: Accesses related objects (items, product) without
    ensuring they are prefetched — each access triggers a new DB query.

FixedOrderSerializer: Identical output, but the view supplies a queryset
    with select_related / prefetch_related so no extra queries are made.
"""
from rest_framework import serializers
from .models import Order, OrderItem, Product


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name', 'sku', 'price', 'category']


class OrderItemSerializer(serializers.ModelSerializer):
    """
    Serializes each order item INCLUDING the nested product.

    Problem: accessing `item.product` here triggers a lazy load if
    the queryset was not annotated with select_related('product').
    With 300 orders × 3 items each, this causes ~900 extra queries.
    """
    product = ProductSerializer(read_only=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity', 'unit_price', 'line_total']


class BrokenOrderSerializer(serializers.ModelSerializer):
    """
    This serializer looks correct but causes an N+1 explosion.

    When DRF iterates over the `items` field, it calls
    `order.items.all()` for EACH order in the queryset — that's
    1 query per order. Then for each item, accessing `item.product`
    adds another query. Total: 1 + N + N*M queries.

    Root cause: The queryset passed to this serializer does NOT use
    prefetch_related('items', 'items__product').
    """
    items = OrderItemSerializer(many=True, read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'username', 'status', 'total_amount',
            'created_at', 'items',
        ]


class FixedOrderSerializer(serializers.ModelSerializer):
    """
    Identical output to BrokenOrderSerializer.

    The fix is NOT in the serializer — it's in the VIEW's queryset.
    The view must supply:
        Order.objects.select_related('user')
            .prefetch_related(
                Prefetch('items', queryset=OrderItem.objects.select_related('product'))
            )

    This collapses hundreds of queries into exactly 3:
      1. SELECT orders WHERE user_id = ?
      2. SELECT order_items WHERE order_id IN (...)
      3. SELECT products WHERE id IN (...)
    """
    items = OrderItemSerializer(many=True, read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'username', 'status', 'total_amount',
            'created_at', 'items',
        ]
