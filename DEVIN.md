# Involo — Devin Workspace Entrypoint

Involo is a multimodal Instagram creator-intelligence platform. It discovers public
hashtag trends through Meta, profiles connected creators from owned media and Insights,
retrieves V2 multimodal evidence, and generates evidence-backed shoot briefs with
feedback and outcome tracking.

This file is the primary entry point for Devin. For detailed architecture, conventions,
and operational runbooks, see the `.devin/` folder.

## Quick commands

```bash
# Copy and configure environment
cp .env.example .env
# Edit .env: set INVOLO_JWT_SECRET and any provider credentials.

# Start everything (infrastructure + API + worker + beat + frontend)
./run.sh up

# Stop everything
./run.sh down

# View live logs
make logs
```

## Local URLs

- Frontend: http://localhost:8020
- API: http://localhost:8021
- OpenAPI docs: http://localhost:8021/docs
- Liveness: http://localhost:8021/health/live
- Readiness: http://localhost:8021/health/ready
- MinIO console: http://localhost:8027

## Verification

```bash
# Full gate
make verify

# Backend only
cd backend
uv run ruff check .
uv run mypy app
uv run pytest

# Frontend only
cd frontend
npm run lint
npm run typecheck
npm test -- --run
npm run build
```

## Documentation map

- `.devin/architecture.md` — module hierarchy, data flow, API standards, database models, SOLID boundaries.
- `.devin/mcp_guidelines.md` — when and which MCP servers to query (Supabase, plus placeholders for Linear/Jira/GitHub/Figma/Browser/Playwright/Sentry).
- `.devin/conventions.md` — Clean Code and SOLID rules customized for the Involo stack.
- `.devin/testing.md` — unit, integration, and E2E testing instructions.
- `.devin/debugging.md` — scientific RCA SOP and log inspection rules.
- `.devin/status.md` — living task status, technical-debt ledger, and progress log.

## Authoritative sources

- `AGENTS.md` (root, backend, frontend) is the source of truth for engineering rules.
- `docs/SYSTEM_ARCHITECTURE.md` is the source of truth for production architecture, data dictionary, security, and operational boundaries.
- `docs/PROJECT_ARCHITECTURE.md` is the concise Turkish project guide.
- `docs/API_EXAMPLES.md` contains cookie-authenticated curl examples and payload shapes.
- `backend/README.md` and `frontend/README.md` contain backend/frontend-specific setup maps.
- `Makefile` contains the canonical run/test/lint targets.

## Definition of Done

- Requested behavior is implemented with regression coverage.
- Errors are actionable and do not leak secrets or raw provider data.
- Public behavior, environment keys, data lifecycle, and operational steps are documented.
- Backend passes `ruff`, `mypy`, and `pytest`; frontend passes `lint`, `typecheck`, `test`, and `build`.
- No generated artifacts (`.next`, caches, virtualenvs, coverage output) are added.
- `.devin/status.md` is updated at the end of the session.
