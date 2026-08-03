# Involo Engineering Rules

## Start here

- Read `docs/PROJECT_ARCHITECTURE.md` before architectural, API, data, provider,
  pipeline, deployment, or security work.
- Treat running code and tests as the source of truth. Update documentation when
  behavior, configuration, API contracts, or operations change.
- Keep changes scoped to the request. Do not mix feature work with unrelated
  cleanup or dependency upgrades.

## Architecture invariants

- Keep FastAPI handlers thin: authentication, validation, service invocation,
  error mapping, and job dispatch only.
- Put business orchestration in service modules and external integrations behind
  provider interfaces/factories.
- Preserve fixture/fake providers. Local development and default tests must not
  require Instagram, AWS, Bedrock, or other external credentials.
- Run long work through Celery; never block an API request with scraping,
  transcription, embedding, or bulk profiling.
- Preserve idempotency across MongoDB/Qdrant writes and retain Redis locks for
  mutually exclusive jobs.
- MongoDB is the business source of truth. Qdrant stores derived vectors and
  references Mongo records through payloads.
- Treat all timestamps and cron expressions as UTC unless a contract explicitly
  states otherwise.

## Security and data

- Never commit or print `.env`, credentials, tokens, Instagram browser state,
  production data, or secrets.
- Keep auth cookie-only and HttpOnly. Preserve refresh-token hashing, rotation,
  revocation, current-user DB checks, and admin RBAC.
- Never place secrets in `NEXT_PUBLIC_*`.
- Preserve single-use, TTL-bound OAuth state and authenticated encryption for
  Instagram access tokens.
- Do not bypass captcha, challenge, or 2FA. Return `needs_intervention`.
- Validate untrusted provider/LLM output with Pydantic and bound untrusted prompt
  context.
- Destructive data operations require explicit user intent and must clean related
  MongoDB and Qdrant records consistently.

## Contract changes

- For API changes, update the Pydantic model, endpoint/service tests,
  `frontend/src/lib/types.ts`, and `frontend/src/lib/api.ts` together.
- For environment changes, update `backend/app/core/config.py`, `.env.example`,
  Compose wiring when applicable, config tests, and architecture docs.
- Vector size/model or Qdrant schema changes require an explicit migration and
  reindex plan; initialization code is not a migration system.
- Keep job states and pipeline transitions backward compatible unless a migration
  is part of the task.

## Verification

- Backend: `cd backend && uv run ruff check . && uv run mypy app && uv run pytest`.
- Frontend: `cd frontend && npm run lint && npm run typecheck && npm test -- --run
  && npm run build`.
- Full gate: `make verify`.
- Start with focused tests, then expand in proportion to risk. Never claim checks
  passed unless they were run successfully.
- Real Instagram/AWS smoke tests are opt-in only; do not run them without explicit
  authorization and configured credentials.

## Definition of done

- Requested behavior is implemented with regression coverage.
- Errors are actionable and do not leak secrets or raw sensitive provider data.
- Public behavior, env keys, data lifecycle, and operational steps are documented.
- No generated artifacts (`.next`, caches, virtualenvs, coverage output) are added.
