# Written Answers — Artikate Studio Backend Assessment

---

## Section 1 — Incident Investigation Log

### Step 1: Confirm the symptom pattern

The endpoint `/api/orders/summary/` is slow **only for users with 200+ orders**. This is a load-dependent regression — performance degrades linearly with data volume. This rules out infrastructure issues (DNS, network, server capacity) which would affect all users equally.

### Step 2: Check what changed in the deployment

The problem statement says "no code change was made to that view." But the deployment included other changes. My first hypothesis: **a change in a related file** — a serializer, a model's `__str__` method, or a related model's field — introduced a lazy-load path that didn't exist before.

### Step 3: Identify the query pattern

Since the slowdown correlates with order count, I check for **N+1 queries**. An N+1 occurs when:
1. One query fetches N parent objects
2. For each parent, a separate query fetches related children

With 200 orders × 3 items × 1 product lookup = 1 + 200 + 600 = **801 queries**. Each query has network round-trip to the database, so 801 × ~5ms = ~4 seconds locally, but with a remote database (like Neon PostgreSQL), each round-trip is ~30-50ms, giving 801 × 40ms = **~32 seconds** — matching the 30-second timeout.

### Step 4: Root cause identification

**Root cause category: N+1 query caused by ORM lazy loading after a serializer change.**

The view's queryset was `Order.objects.filter(user=user)` — a plain queryset with no eager loading. Previously, the serializer only included scalar fields (`id`, `status`, `total_amount`), so no related objects were accessed. A developer added an `items` field (nested `OrderItemSerializer` with `ProductSerializer`) to the serializer in a different PR. This caused Django's ORM to **lazy-load** each order's items and each item's product with individual SQL queries.

Django's ORM uses lazy loading by default: `order.items.all()` triggers `SELECT * FROM order_items WHERE order_id = ?` only when accessed. The serializer accesses this for every order in the loop, creating the N+1 pattern.

### Step 5: The fix

```python
# BEFORE (broken):
orders = Order.objects.filter(user=user)

# AFTER (fixed):
orders = (
    Order.objects.filter(user=user)
    .select_related('user')
    .prefetch_related(
        Prefetch('items', queryset=OrderItem.objects.select_related('product'))
    )
)
```

**Why this works at the database level:**

1. **`select_related('user')`**: Generates a SQL `JOIN` — `SELECT orders.*, users.* FROM orders INNER JOIN users ON orders.user_id = users.id`. One query returns both orders and their users. Works for `ForeignKey` and `OneToOneField` relationships.

2. **`prefetch_related('items')`**: After the main query, Django issues **one** batched query: `SELECT * FROM order_items WHERE order_id IN (1, 2, 3, ..., 300)`. It then caches the results in a Python dictionary keyed by `order_id`. When the serializer accesses `order.items.all()`, Django reads from this cache instead of hitting the database.

3. **`Prefetch('items', queryset=OrderItem.objects.select_related('product'))`**: The inner `select_related('product')` adds a JOIN to the prefetch query: `SELECT items.*, products.* FROM order_items JOIN products ON items.product_id = products.id WHERE order_id IN (...)`. This fetches items AND their products in a single query.

**Result**: 801+ queries → **3 queries**. Response time: 30+ seconds → **~80ms**.

### Profiler Evidence

The project integrates **django-silk** (`silk.middleware.SilkyMiddleware`) which records every request's query count, query time, and individual SQL statements. After hitting both endpoints:

- **Broken endpoint** (`/api/orders/summary/`): django-silk records 800+ SQL queries with total DB time exceeding 20 seconds
- **Fixed endpoint** (`/api/orders/summary/fixed/`): django-silk records 3-4 SQL queries with total DB time under 50ms

The silk dashboard is accessible at `/silk/` when the server is running, showing per-request query breakdown.

Additionally, the test suite (`section1_orders/tests.py`) uses Django's `CaptureQueriesContext` to programmatically verify:
- Broken view: **>50 queries** (test asserts `assertGreater(query_count, 50)`)
- Fixed view: **≤6 queries** (test asserts `assertLessEqual(query_count, 6)`)

Both tests pass, providing automated evidence of the fix.

---

## Section 2 — Written Answers

### Rate Limiter: Why Token Bucket?

See `DESIGN.md` for the full comparison. Summary: Token bucket was chosen over fixed window (boundary burst problem) and sliding window (O(log N) memory overhead). The Lua script guarantees atomicity — Redis executes it as a single operation, preventing race conditions between concurrent Celery workers.

### SIGKILL Answer

See `DESIGN.md`, section "Crash Recovery: SIGKILL Scenario" for the detailed answer covering `acks_late`, `reject_on_worker_lost`, `visibility_timeout`, and at-least-once delivery semantics.

---

## Section 3 — Written Answers

### Failure modes of thread-local tenant scoping in async Django views

**Thread-local storage (`threading.local()`) is per-THREAD, not per-COROUTINE.** This is the critical failure mode in async Django:

1. In synchronous Django (WSGI with Gunicorn/uWSGI), each request runs in its own thread. Thread-local storage provides natural per-request isolation — `set_current_tenant(A)` in Thread-1 is invisible to Thread-2.

2. In async Django (ASGI with Uvicorn/Daphne), multiple coroutines share the **same thread**. When coroutine A sets `_thread_locals.tenant = "Acme"` and then `await`s (yielding control), coroutine B running on the same thread sees `_thread_locals.tenant == "Acme"` — **a critical data leak**.

3. The failure is intermittent and hard to reproduce: it only occurs when two async requests happen to share a thread and interleave at an `await` point. In testing (single request at a time), it works perfectly.

**Fix: Replace `threading.local()` with Python's `contextvars.ContextVar`:**

```python
import contextvars

_current_tenant: contextvars.ContextVar = contextvars.ContextVar(
    'current_tenant', default=None
)

def set_current_tenant(tenant):
    _current_tenant.set(tenant)

def get_current_tenant():
    return _current_tenant.get()

def clear_current_tenant():
    _current_tenant.set(None)
```

**Why `ContextVar` works for async:**

- `ContextVar` is part of Python's `contextvars` module (PEP 567, Python 3.7+).
- Each coroutine/task gets its own **Context** — a snapshot of all `ContextVar` values.
- When `asyncio` creates a new `Task`, it copies the current context. Changes made by one coroutine are invisible to others, even on the same thread.
- Django's ASGI handler already uses `asyncio.Task` per request, so `ContextVar` provides per-request isolation automatically.

The current implementation uses `threading.local()` because the assessment specifies synchronous views, but the code includes detailed documentation of this failure mode in `section3_tenants/context.py`.

---

## Section 4 — Written Architecture Review

### Question A — Django Admin Performance (500,000+ records)

**Root Cause 1: `list_display` accessing related fields without `list_select_related`**

When `ModelAdmin.list_display` includes fields from related models (e.g., `order.customer.name`), Django's admin generates a separate query per row to fetch the related object. With 500,000 records and default pagination of 100, that's 100 extra queries per page load.

**Fix:** Add `list_select_related` to the `ModelAdmin`:

```python
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'customer_name', 'status', 'created_at']
    list_select_related = ['customer']  # JOIN instead of N+1
```

This tells Django's admin to use `select_related()` on the changelist queryset, collapsing N+1 queries into a single JOIN.

**Root Cause 2: Unindexed columns in `list_filter` and `search_fields`**

`list_filter = ['status', 'created_at']` generates `WHERE status = ?` queries. Without a database index on `status`, PostgreSQL performs a sequential scan of all 500,000 rows. Similarly, `search_fields = ['customer__email']` generates `LIKE '%query%'` which cannot use a B-tree index.

**Fix:**

```python
class Order(models.Model):
    status = models.CharField(max_length=20, db_index=True)  # Add index

    class Meta:
        indexes = [
            models.Index(fields=['status', '-created_at']),  # Compound index
        ]
```

For `search_fields`, use `^` prefix for startswith lookup (`search_fields = ['^customer__email']`) which generates `LIKE 'query%'` — this CAN use a B-tree index. Alternatively, add a `GinIndex` with `SearchVector` for full-text search on PostgreSQL.

**Root Cause 3: `ModelAdmin.get_queryset()` not optimized — count query on large table**

Django admin's changelist calls `queryset.count()` to display "500,000 results". On PostgreSQL, `COUNT(*)` requires a full table scan (MVCC means the DB cannot store a cached count). This single query can take 2-3 seconds on 500K rows.

**Fix:** Override `get_queryset()` and set `show_full_result_count = False`:

```python
class OrderAdmin(admin.ModelAdmin):
    show_full_result_count = False  # Disables the expensive COUNT(*)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.only('id', 'status', 'created_at', 'customer_id')  # Defer unused columns
```

`show_full_result_count = False` skips the unfiltered count query, showing "100 results (first 100)" instead of "100 of 500,000". The `only()` call reduces the data transferred per row by deferring large text/JSON columns.

---

### Question B — Pagination Trade-offs (Offset vs. Cursor)

**Offset-based pagination** (`LIMIT 50 OFFSET 10000`):

At the database level, PostgreSQL must **scan and discard** 10,000 rows before returning the next 50. The query cost grows linearly with page number — page 1 scans 50 rows, page 200 scans 10,000 rows. On a 10,000-record table this is tolerable, but if the data grows to 1M records and users reach page 5000, the database is scanning 250,000 rows per request.

**Real-world consequence for mobile infinite scroll:** As the user scrolls deeper, each subsequent page load gets slower. This creates a degrading user experience — the first few pages load in 50ms, page 100 takes 500ms, page 500 takes 2 seconds.

**Data mutation during pagination:** If a row is inserted or deleted between page requests, offset pagination produces **skipped or duplicated** items. For example: user loads page 1 (items 1-50), a new item is inserted at position 25, user loads page 2 — item 50 appears again because everything shifted by one position. For an orders dashboard where new orders arrive continuously, this means users see duplicate or missing orders.

**Cursor-based pagination** (`WHERE id > last_seen_id ORDER BY id LIMIT 50`):

The database **seeks directly** to the cursor position using the index on `id`. The query cost is constant O(1) regardless of how deep the user has scrolled — page 1 and page 500 take the same time, because the B-tree index locates `id > 25000` in logarithmic time.

**Data mutation during pagination:** Cursor pagination is **stable under mutations**. If a new item is inserted with `id=25001` and the cursor is at `id=25000`, the new item appears naturally on the next page. No duplicates, no skips. This is why Twitter, Instagram, and Slack all use cursor-based pagination for their feeds.

**Trade-off: Cursor pagination cannot jump to arbitrary pages.** There is no "go to page 50" — only "next" and "previous". For an admin dashboard where users need random page access (e.g., "show me page 347"), offset pagination is more appropriate despite its performance cost.

**When I would choose each:**

- **Cursor-based:** Mobile app infinite scroll, real-time feeds, any API where data mutates frequently and users consume data sequentially. This covers 90% of consumer-facing use cases.

- **Offset-based:** Admin panels with paginated tables, reports where users need to jump to specific pages, or datasets that are static/rarely mutated (e.g., historical analytics).

For this assessment's scenario (an existing API returning 10,000 records), I would implement **cursor-based pagination** using Django REST Framework's `CursorPagination` with `ordering='-created_at'`, because the endpoint is likely consumed by a mobile or web client that scrolls through results sequentially.
