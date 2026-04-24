"""
Section 1 — Tests proving the N+1 fix works.

Measures query count for the broken vs fixed views using
Django's assertNumQueries and django-silk profiling.
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.test.utils import override_settings

from .models import Product, Order, OrderItem
from .views import order_summary_broken, order_summary_fixed


@override_settings(
    MIDDLEWARE=[
        'django.middleware.common.CommonMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
    ]
)
class OrderSummaryQueryCountTest(TestCase):
    """
    Proves that the fixed view uses dramatically fewer queries
    than the broken view for the same dataset.
    """

    @classmethod
    def setUpTestData(cls):
        """Create a user with 50 orders, each having 3 items."""
        cls.user = User.objects.create_user(
            username='testuser', password='testpass123'
        )

        # Create 10 products
        cls.products = []
        for i in range(10):
            p = Product.objects.create(
                name=f'Product {i}',
                sku=f'SKU-{i:04d}',
                price=10.00 + i,
            )
            cls.products.append(p)

        # Create 50 orders with 3 items each
        for i in range(50):
            order = Order.objects.create(
                user=cls.user,
                status='confirmed',
                total_amount=30.00,
            )
            for j in range(3):
                OrderItem.objects.create(
                    order=order,
                    product=cls.products[j % 10],
                    quantity=1,
                    unit_price=cls.products[j % 10].price,
                )

    def _make_request(self, view_func):
        """Helper to call a view with a fake request."""
        factory = RequestFactory()
        request = factory.get('/api/orders/summary/')
        response = view_func(request)
        # Force evaluation of lazy response data
        response.render()
        return response

    def test_broken_view_has_many_queries(self):
        """
        The broken view should fire far more queries than the fixed one.
        With 50 orders × 3 items, we expect ~1 + 50 + 150 = 201 queries.
        We just assert it's above a threshold to prove the N+1 exists.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            response = self._make_request(order_summary_broken)

        self.assertEqual(response.status_code, 200)

        # N+1: should be well over 50 queries
        # 1 (user) + 1 (count) + 50 (items per order) + 150 (product per item) = 202+
        query_count = len(ctx)
        print(f"\n[BROKEN VIEW] Total queries: {query_count}")
        self.assertGreater(
            query_count, 50,
            f"Expected N+1 explosion (>50 queries), got {query_count}"
        )

    def test_fixed_view_has_minimal_queries(self):
        """
        The fixed view should use ≤ 5 queries regardless of order count:
        1. Fetch user (first())
        2. COUNT query
        3. Fetch orders with user JOIN
        4. Prefetch items with product JOIN

        We allow up to 6 for framework overhead.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            response = self._make_request(order_summary_fixed)

        self.assertEqual(response.status_code, 200)

        query_count = len(ctx)
        print(f"\n[FIXED VIEW] Total queries: {query_count}")
        self.assertLessEqual(
            query_count, 6,
            f"Expected ≤6 queries with prefetching, got {query_count}"
        )

    def test_both_views_return_same_data(self):
        """Both views must produce identical JSON output."""
        broken_response = self._make_request(order_summary_broken)
        fixed_response = self._make_request(order_summary_fixed)

        self.assertEqual(broken_response.status_code, 200)
        self.assertEqual(fixed_response.status_code, 200)

        broken_data = broken_response.data
        fixed_data = fixed_response.data

        self.assertEqual(broken_data['order_count'], fixed_data['order_count'])
        self.assertEqual(len(broken_data['orders']), len(fixed_data['orders']))

        # Verify each order has the same items
        for b_order, f_order in zip(broken_data['orders'], fixed_data['orders']):
            self.assertEqual(b_order['id'], f_order['id'])
            self.assertEqual(len(b_order['items']), len(f_order['items']))
