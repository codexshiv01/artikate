"""
Section 1 — Models for the Order Summary system.

These models represent a typical e-commerce order structure:
User → Order → OrderItem → Product

The N+1 query problem arises when serializing orders with their
items and products without using select_related / prefetch_related.
"""
from django.conf import settings
from django.db import models


class Product(models.Model):
    """A product in the catalog."""
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=100, default='general')

    class Meta:
        db_table = 'section1_product'

    def __str__(self):
        return f"{self.name} ({self.sku})"


class Order(models.Model):
    """An order placed by a user."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='orders',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'section1_order'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at'], name='idx_order_user_created'),
        ]

    def __str__(self):
        return f"Order #{self.pk} by {self.user.username}"


class OrderItem(models.Model):
    """A line item within an order."""
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='order_items',
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'section1_order_item'

    def __str__(self):
        # This __str__ accesses self.product.name — if called in a loop
        # without select_related, it triggers an additional query per item.
        return f"{self.quantity}x {self.product.name}"

    @property
    def line_total(self):
        return self.quantity * self.unit_price
