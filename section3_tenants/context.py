"""
Section 3 — Thread-local tenant context management.

Stores the current tenant in thread-local storage so that the
TenantManager can automatically scope all queries without
requiring explicit .filter(tenant=...) calls.

Thread-local storage (threading.local):
- Each thread gets its own independent copy of the data.
- In synchronous Django (WSGI), each request runs in a single thread,
  so setting the tenant at the start and clearing it at the end
  provides per-request isolation.

IMPORTANT — Failure mode in async Django:
  Thread-locals are per-THREAD, not per-COROUTINE. In async Django
  (ASGI), multiple requests can share the same thread. If coroutine A
  sets tenant to "Acme" and yields, coroutine B on the same thread
  would see tenant="Acme" — a critical data leak.

  Fix: Replace threading.local() with Python's contextvars.ContextVar.
  ContextVar creates a separate copy per async task/coroutine context.
  When Django runs an async view, each coroutine gets its own Context,
  so ContextVar values are isolated even on the same thread.

  Example fix:
      import contextvars
      _current_tenant: contextvars.ContextVar = contextvars.ContextVar(
          'current_tenant', default=None
      )
      def set_current_tenant(tenant):
          _current_tenant.set(tenant)
      def get_current_tenant():
          return _current_tenant.get()

  We use threading.local() here because the assessment specifies
  synchronous Django views, but document the async fix above.
"""
import threading

_thread_locals = threading.local()


def set_current_tenant(tenant):
    """
    Set the tenant for the current request lifecycle.
    Called by TenantMiddleware at the start of each request.
    """
    _thread_locals.tenant = tenant


def get_current_tenant():
    """
    Get the current tenant. Returns None if not set.
    Used by TenantManager.get_queryset() to auto-scope queries.
    """
    return getattr(_thread_locals, 'tenant', None)


def clear_current_tenant():
    """
    Clear the tenant context. Called by TenantMiddleware after the
    response is generated. Essential for preventing tenant leakage
    between requests on the same thread (thread reuse in WSGI pools).
    """
    _thread_locals.tenant = None
