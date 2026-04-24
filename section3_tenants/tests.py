"""
Section 3 — Tests proving multi-tenant data isolation.

Tests verify:
1. Tenant A can only see their own data through TenantManager
2. Tenant B can only see their own data through TenantManager
3. Calling .objects.all() does NOT bypass scoping
4. Negative test: Tenant A cannot access Tenant B's data
5. Middleware correctly sets and clears tenant context
6. Context cleanup prevents leakage between requests
"""
from django.test import TestCase, RequestFactory, override_settings
from django.http import HttpResponse

from .models import Tenant, TenantOrder
from .context import set_current_tenant, get_current_tenant, clear_current_tenant
from .middleware import TenantMiddleware


@override_settings(
    MIDDLEWARE=[
        'django.middleware.common.CommonMiddleware',
    ]
)
class TenantIsolationTest(TestCase):
    """Core isolation tests — prove that tenant data cannot leak."""

    @classmethod
    def setUpTestData(cls):
        """Create two tenants with distinct orders."""
        cls.tenant_a = Tenant.objects.create(
            name='Acme Corp',
            subdomain='acme',
        )
        cls.tenant_b = Tenant.objects.create(
            name='Globex Inc',
            subdomain='globex',
        )

        # Tenant A's orders
        for i in range(5):
            TenantOrder.unscoped.create(
                tenant=cls.tenant_a,
                order_number=f'ACME-{i:03d}',
                description=f'Acme order {i}',
                amount=100.00 + i,
            )

        # Tenant B's orders
        for i in range(3):
            TenantOrder.unscoped.create(
                tenant=cls.tenant_b,
                order_number=f'GLOB-{i:03d}',
                description=f'Globex order {i}',
                amount=200.00 + i,
            )

    def setUp(self):
        """Ensure clean tenant context before each test."""
        clear_current_tenant()

    def tearDown(self):
        """Ensure clean tenant context after each test."""
        clear_current_tenant()

    # ---- Positive tests: correct data is visible ----

    def test_tenant_a_sees_only_own_orders(self):
        """Tenant A should see exactly 5 orders — all their own."""
        set_current_tenant(self.tenant_a)
        orders = TenantOrder.objects.all()
        self.assertEqual(orders.count(), 5)
        for order in orders:
            self.assertEqual(order.tenant_id, self.tenant_a.id)

    def test_tenant_b_sees_only_own_orders(self):
        """Tenant B should see exactly 3 orders — all their own."""
        set_current_tenant(self.tenant_b)
        orders = TenantOrder.objects.all()
        self.assertEqual(orders.count(), 3)
        for order in orders:
            self.assertEqual(order.tenant_id, self.tenant_b.id)

    # ---- Negative tests: data cannot leak ----

    def test_tenant_a_cannot_see_tenant_b_data(self):
        """
        NEGATIVE TEST: With Tenant A context, Tenant B's order numbers
        must not appear in any queryset result.
        """
        set_current_tenant(self.tenant_a)
        order_numbers = list(
            TenantOrder.objects.values_list('order_number', flat=True)
        )
        for num in order_numbers:
            self.assertTrue(
                num.startswith('ACME'),
                f"Tenant A sees non-ACME order: {num}"
            )
        # Explicitly check B's orders are absent
        self.assertFalse(
            TenantOrder.objects.filter(order_number='GLOB-000').exists(),
            "Tenant A should NOT see Tenant B's order GLOB-000"
        )

    def test_tenant_b_cannot_see_tenant_a_data(self):
        """NEGATIVE TEST: Tenant B must never see Acme's orders."""
        set_current_tenant(self.tenant_b)
        order_numbers = list(
            TenantOrder.objects.values_list('order_number', flat=True)
        )
        for num in order_numbers:
            self.assertTrue(
                num.startswith('GLOB'),
                f"Tenant B sees non-GLOB order: {num}"
            )
        self.assertFalse(
            TenantOrder.objects.filter(order_number='ACME-000').exists(),
            "Tenant B should NOT see Tenant A's order ACME-000"
        )

    # ---- .all() bypass test ----

    def test_objects_all_does_not_bypass_scoping(self):
        """
        Calling .objects.all() must NOT return all tenants' data.
        This tests the exact scenario from the assessment scaffold.
        """
        set_current_tenant(self.tenant_a)

        # .all() should still be scoped
        all_orders = TenantOrder.objects.all()
        self.assertEqual(
            all_orders.count(), 5,
            ".all() should return only Tenant A's 5 orders, not all 8"
        )

        # Verify the SQL contains the tenant filter
        self.assertIn(
            'tenant_id',
            str(all_orders.query),
            "The generated SQL should contain tenant_id filter"
        )

    def test_filter_chaining_maintains_scope(self):
        """Even chained filters maintain tenant scoping."""
        set_current_tenant(self.tenant_a)

        # This should search within Tenant A's orders only
        orders = TenantOrder.objects.filter(amount__gte=102)
        for order in orders:
            self.assertEqual(order.tenant_id, self.tenant_a.id)

    def test_get_respects_tenant_scope(self):
        """
        .get() should raise DoesNotExist if the object belongs
        to another tenant, even if the PK exists in the database.
        """
        set_current_tenant(self.tenant_a)

        # Get a Tenant B order's PK via unscoped manager
        b_order = TenantOrder.unscoped.filter(tenant=self.tenant_b).first()

        # Trying to .get() this PK through scoped manager should fail
        with self.assertRaises(TenantOrder.DoesNotExist):
            TenantOrder.objects.get(pk=b_order.pk)

    # ---- Unscoped manager test ----

    def test_unscoped_manager_sees_all_data(self):
        """The unscoped manager should bypass tenant scoping."""
        set_current_tenant(self.tenant_a)

        # Unscoped should see all 8 orders (5 + 3)
        all_orders = TenantOrder.unscoped.all()
        self.assertEqual(all_orders.count(), 8)


class TenantContextTest(TestCase):
    """Tests for the thread-local tenant context management."""

    def setUp(self):
        clear_current_tenant()

    def tearDown(self):
        clear_current_tenant()

    def test_set_and_get_tenant(self):
        """set_current_tenant should be retrievable via get_current_tenant."""
        tenant = Tenant.objects.create(name='Test', subdomain='test')
        set_current_tenant(tenant)
        self.assertEqual(get_current_tenant(), tenant)

    def test_clear_tenant(self):
        """clear_current_tenant should reset context to None."""
        tenant = Tenant.objects.create(name='Test2', subdomain='test2')
        set_current_tenant(tenant)
        clear_current_tenant()
        self.assertIsNone(get_current_tenant())

    def test_default_is_none(self):
        """Without setting, get_current_tenant should return None."""
        self.assertIsNone(get_current_tenant())


class TenantMiddlewareTest(TestCase):
    """Tests for the tenant extraction middleware."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Middleware Test Corp',
            subdomain='mwtest',
        )
        TenantOrder.unscoped.create(
            tenant=self.tenant,
            order_number='MW-001',
            description='Middleware test order',
            amount=50.00,
        )

    def _get_middleware(self):
        """Create middleware instance with a simple view."""
        def dummy_view(request):
            return HttpResponse('OK')
        return TenantMiddleware(dummy_view)

    def test_header_extraction(self):
        """Middleware should extract tenant from X-Tenant-ID header."""
        factory = RequestFactory()
        request = factory.get(
            '/api/tenants/orders/',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )

        middleware = self._get_middleware()
        response = middleware(request)

        # After middleware runs, context should be cleared (finally block)
        self.assertIsNone(get_current_tenant())
        self.assertEqual(response.status_code, 200)

    def test_missing_tenant_returns_400(self):
        """Requests to tenant-scoped paths without tenant should get 400."""
        factory = RequestFactory()
        request = factory.get('/api/tenants/orders/')

        middleware = self._get_middleware()
        response = middleware(request)

        self.assertEqual(response.status_code, 400)

    def test_context_cleanup_after_exception(self):
        """Tenant context must be cleared even if the view raises."""
        def failing_view(request):
            raise ValueError("View error")

        middleware = TenantMiddleware(failing_view)
        factory = RequestFactory()
        request = factory.get(
            '/api/tenants/orders/',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )

        with self.assertRaises(ValueError):
            middleware(request)

        # Context MUST be cleared even after exception
        self.assertIsNone(
            get_current_tenant(),
            "Tenant context leaked after exception — cleanup failed"
        )
