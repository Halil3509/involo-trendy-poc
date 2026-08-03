# Backend Rules

- Target Python 3.12. Keep Ruff's 100-character limit and strict mypy compliance.
- Use async clients in request/service paths. Do not call blocking network or
  database APIs from the event loop.
- Keep routers thin and dependencies explicit. Business logic belongs in services;
  external I/O belongs behind provider interfaces.
- Use Pydantic models at HTTP and provider trust boundaries. Avoid untyped
  dictionaries in new public contracts.
- Raise domain-specific service errors and map them to stable HTTP statuses at the
  API boundary. Do not expose provider exceptions or secrets.
- Use `app.db.utcnow()` and UTC-aware datetimes.
- Celery tasks must persist job state, be retry-safe, close resources, and preserve
  the relevant Redis lock. Retry only transient errors.
- MongoDB writes and Qdrant upserts must be idempotent. Use deterministic IDs where
  the existing workflow does.
- Add/update pytest coverage for success, validation, authorization, retry/failure,
  and idempotency behavior as applicable.
- Run focused pytest first; before broad handoff run Ruff, strict mypy, and pytest.
