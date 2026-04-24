"""
Section 3 — Tenant extraction middleware.

Extracts the tenant from incoming requests and binds it to thread-local
storage for the duration of the request lifecycle.

Tenant identification strategy (in order of precedence):
1. X-Tenant-ID header — for API clients / mobile apps
2. Subdomain extraction — for web browser access (acme.app.com)

The middleware ensures cleanup in the finally block, preventing
tenant leakage between requests on the same thread.
"""
import logging
from django.http import JsonResponse

from .context import set_current_tenant, clear_current_tenant

logger = logging.getLogger(__name__)

# Paths that should bypass tenant scoping entirely
TENANT_EXEMPT_PATHS = (
    '/admin/',
    '/silk/',
    '/api/orders/',  # Section 1 uses auth user, not tenants
    '/api/queue/',   # Section 2 is not tenant-scoped
)


class TenantMiddleware:
    """
    Middleware that extracts tenant context from each request.

    Lifecycle:
    1. __call__ is invoked for every request
    2. Extract tenant from header or subdomain
    3. Set tenant in thread-local storage
    4. Process the request (tenant is available via get_current_tenant())
    5. Clear tenant in the finally block (prevents leakage)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip tenant scoping for exempt paths
        if any(request.path.startswith(p) for p in TENANT_EXEMPT_PATHS):
            return self.get_response(request)

        tenant = None

        try:
            tenant = self._extract_tenant(request)

            if tenant is not None:
                set_current_tenant(tenant)
            elif request.path.startswith('/api/tenants/'):
                # Tenant-scoped endpoints MUST have a tenant
                return JsonResponse(
                    {'error': 'Tenant identification required. '
                              'Provide X-Tenant-ID header or use subdomain.'},
                    status=400,
                )

            response = self.get_response(request)
            return response

        finally:
            # CRITICAL: Always clear tenant context, even if an exception
            # occurred. This prevents tenant data from leaking to the next
            # request handled by this thread (WSGI thread reuse).
            clear_current_tenant()

    def _extract_tenant(self, request):
        """
        Extract tenant from the request.

        Priority:
        1. X-Tenant-ID header (explicit, used by API clients)
        2. Subdomain (implicit, used by browser clients)
        """
        # Strategy 1: HTTP header
        tenant_id = request.META.get('HTTP_X_TENANT_ID')
        if tenant_id:
            return self._resolve_tenant_by_id(tenant_id)

        # Strategy 2: Subdomain
        host = request.get_host().split(':')[0]  # Remove port
        parts = host.split('.')
        if len(parts) > 2:
            subdomain = parts[0]
            return self._resolve_tenant_by_subdomain(subdomain)

        return None

    def _resolve_tenant_by_id(self, tenant_id):
        """
        Look up the tenant by primary key.
        We import here to avoid circular imports.
        """
        from .models import Tenant
        try:
            return Tenant.objects.get(pk=tenant_id)
        except (Tenant.DoesNotExist, ValueError):
            logger.warning(f"Tenant not found for ID: {tenant_id}")
            return None

    def _resolve_tenant_by_subdomain(self, subdomain):
        """Look up the tenant by subdomain."""
        from .models import Tenant
        try:
            return Tenant.objects.get(subdomain=subdomain)
        except Tenant.DoesNotExist:
            logger.warning(f"Tenant not found for subdomain: {subdomain}")
            return None
