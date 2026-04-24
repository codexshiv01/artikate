# Artikate Studio 





### Prerequisites

- Python 3.10+
- Docker (for Redis)
- Git

### 1. Clone and set up

```bash
git clone <repository-url>
cd artikate-studio

# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (macOS/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Start Redis

```bash
docker-compose up -d redis
```

### 3. Run migrations and seed data

```bash
python manage.py migrate
python manage.py seed_orders  # Seeds 300 orders with 900 items
```

### 4. Run all tests

```bash
python manage.py test section1_orders section2_queue section3_tenants -v2
```

### 5. Start the server (for manual exploration)

```bash
python manage.py runserver 8000
```

Available endpoints:

| Endpoint | Description |
|---|---|
| `GET /api/orders/summary/` | Section 1 — Broken endpoint (N+1 queries) |
| `GET /api/orders/summary/fixed/` | Section 1 — Fixed endpoint (3 queries) |
| `POST /api/queue/submit/` | Section 2 — Submit email jobs |
| `GET /api/queue/status/` | Section 2 — Rate limiter status |
| `GET /api/queue/dlq/` | Section 2 — Dead-letter queue |
| `GET /api/tenants/orders/` | Section 3 — Tenant-scoped orders (requires `X-Tenant-ID` header) |
| `GET /silk/` | django-silk profiler dashboard |

### 6. Start Celery worker (for Section 2 live demo)

```bash
celery -A artikate worker -l info
```

---

## Project Structure

```
├── README.md              ← You are here
├── DESIGN.md              ← Section 2 architecture decisions
├── ANSWERS.md             ← Written answers for all sections
├── requirements.txt
├── manage.py
├── docker-compose.yml     ← Redis for Celery/rate limiter
│
├── artikate/              ← Django project configuration
│   ├── settings.py        ← DB, Celery, Silk config
│   ├── celery.py          ← Celery app initialization
│   └── urls.py
│
├── section1_orders/       ← Section 1: Diagnose a Broken System
│   ├── models.py          ← Product, Order, OrderItem
│   ├── serializers.py     ← Broken vs Fixed serializers
│   ├── views.py           ← N+1 problem + fix
│   ├── tests.py           ← Query count assertions
│   └── management/commands/seed_orders.py
│
├── section2_queue/        ← Section 2: Rate-Limited Job Queue
│   ├── rate_limiter.py    ← Token bucket (Redis Lua script)
│   ├── tasks.py           ← Celery send_email task
│   ├── dead_letter.py     ← DLQ handler
│   ├── views.py           ← Submit/monitor endpoints
│   └── tests.py           ← 500-job stress test
│
└── section3_tenants/      ← Section 3: Multi-Tenant Isolation
    ├── context.py         ← Thread-local tenant context
    ├── managers.py        ← TenantManager (auto-scoping)
    ├── middleware.py       ← Tenant extraction middleware
    ├── models.py          ← Tenant, TenantOrder
    └── tests.py           ← Isolation + bypass tests
```

---

## Section Overview

### Section 1 — Diagnose a Broken System

- **Root cause:** N+1 query from serializer accessing related objects without `prefetch_related`
- **Fix:** `select_related('user')` + `Prefetch('items', queryset=OrderItem.objects.select_related('product'))`
- **Evidence:** django-silk profiler at `/silk/`, plus automated tests asserting query counts
- **Details:** See `ANSWERS.md` → Section 1

### Section 2 — Rate-Limited Async Job Queue

- **Architecture:** Celery + Redis (see `DESIGN.md`)
- **Rate limiter:** Custom token-bucket using Redis Lua script for atomicity
- **Crash safety:** `acks_late=True`, `reject_on_worker_lost=True`
- **Tests:** 500-job stress test, rate limiter atomicity, DLQ handling
- **Details:** See `DESIGN.md` and `ANSWERS.md` → Section 2

### Section 3 — Multi-Tenant Data Isolation

- **Approach:** Custom `TenantManager` + thread-local middleware
- **Scoping:** `Order.objects.all()` automatically filters by current tenant
- **Tests:** Positive isolation, negative bypass prevention, middleware cleanup
- **Async safety:** Documented `contextvars.ContextVar` migration path
- **Details:** See `ANSWERS.md` → Section 3

### Section 4 — Written Architecture Review

- **Question A:** Django Admin performance (3 root causes + fixes)
- **Question B:** Offset vs. cursor pagination trade-offs
- **Details:** See `ANSWERS.md` → Section 4



---

## Database

This project uses **Neon PostgreSQL** (cloud-hosted). The connection is configured in `artikate/settings.py`.

**Redis** runs locally via Docker (`docker-compose up -d redis`) and serves as:
- Celery message broker
- Rate limiter state store
- Dead-letter queue backend

---

## Test Results Summary

All **29 tests** pass from a clean environment:

| Section | Tests | Status |
|---|---|---|
| Section 1 — Orders | 3 tests (query counts, data parity) | ✅ Pass |
| Section 2 — Queue | 12 tests (rate limiter, tasks, DLQ, 500-job stress) | ✅ Pass |
| Section 3 — Tenants | 14 tests (isolation, bypass, middleware, context) | ✅ Pass |
