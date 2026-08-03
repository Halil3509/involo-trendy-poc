# Involo

Involo is a multimodal Instagram creator-intelligence platform. It discovers public
hashtag trends through Meta, profiles connected creators from owned media and
Insights, retrieves V2 multimodal evidence, generates evidence-backed shoot
briefs with feedback and outcome tracking, and lets admins run brand reference
analysis on a competitor's recent posts to produce a Turkish markdown report.

## Documentation

- [Production system architecture](docs/SYSTEM_ARCHITECTURE.md) — authoritative
  component, provider, API, data, vector, media, security, operations, migration,
  failure, and deletion documentation.
- [Project architecture](docs/PROJECT_ARCHITECTURE.md) — concise Turkish repository
  and development guide.
- [API examples](docs/API_EXAMPLES.md) — cookie-authenticated curl and payload shapes.
- [Backend guide](backend/README.md) — backend-specific setup and implementation map.
- `instagram-ai-oneri-sistemi-teknik-dokuman.md` — original plan/history, not the
  implemented architecture source of truth.

## Stack

- Next.js 16, React 19, TypeScript
- FastAPI, Python 3.12
- Celery with Redis broker/result backend
- MongoDB 8
- Qdrant V2 named vectors
- S3/MinIO media storage
- Meta Instagram Login, Graph API, Hashtag Search, and Insights
- Amazon Bedrock Nova Pro and Nova 2 Multimodal Embeddings
- AWS Transcribe
- Optional official Google Trends, YouTube, and Reddit topic-signal connectors

Local development defaults to fixture/fake providers so the application runs
without Meta/AWS credentials. Set `INVOLO_SCRAPER_ADAPTER=fixture` and
`INVOLO_CREATOR_TRACKING_PROVIDER=fixture` to use JSON fixtures. Configure real
providers and credentials for production or explicit live smoke tests.

## Requirements

- Python 3.12
- Node.js 20+ and npm
- Docker with Compose
- ffmpeg/ffprobe for media processing
- `make` optionally

## Local setup

```bash
cp .env.example .env
```

Set a non-development JWT secret:

```dotenv
INVOLO_JWT_SECRET=replace-with-at-least-32-random-characters
```

For OAuth, discovery, media intelligence, profiling, or recommendations, also set
the required Meta, AWS, S3, and Bedrock access described in
`docs/SYSTEM_ARCHITECTURE.md`.

If your Meta app is in **Live** mode, Instagram requires an `https` redirect URI.
The repository can use `localtunnel` for local development, but it is optional and
only needed when testing live Meta OAuth/webhooks. Set the following in `.env`:

```dotenv
INVOLO_NGROK_DOMAIN=involo-app.loca.lt
INVOLO_TUNNEL_STRICT=false
```

`INVOLO_NGROK_DOMAIN` is the public host to request from localtunnel (only the first label is used with `--subdomain`).
Use `INVOLO_NGROK_DOMAIN=auto` for a random `loca.lt` URL each session.
If the fixed subdomain is already taken and `INVOLO_TUNNEL_STRICT=false`, `run.sh` / `run-local.sh` fall back to a random `loca.lt` URL and print a warning.
Set `INVOLO_TUNNEL_STRICT=true` to abort instead.
If localtunnel cannot start, `run-local.sh` continues with `http://localhost:8021`.

`run.sh` and `run-local.sh` start `localtunnel`, print the public URL, set
`INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI` to `https://<domain>/api/v1/instagram/oauth/callback`
and print `https://<domain>/api/v1/instagram/webhook` for Meta Webhooks.

Add the OAuth URI under **Valid OAuth Redirect URIs** and the webhook URL under
**Webhooks Callback URL** in the Meta App Dashboard. Set the same verify token in
both the dashboard and `.env`:

```dotenv
INVOLO_INSTAGRAM_WEBHOOK_VERIFY_TOKEN=your-random-verify-token
```

The app must be in **Live** mode for Meta to actually deliver webhook events.

The current regional model topology requires separate media buckets:

```dotenv
INVOLO_AWS_REGION=eu-central-1
INVOLO_BEDROCK_GENERATION_REGION=eu-central-1
INVOLO_BEDROCK_VISION_MODEL_ID=eu.amazon.nova-pro-v1:0
INVOLO_BEDROCK_PROFILE_MODEL_ID=eu.amazon.nova-pro-v1:0
INVOLO_BEDROCK_RECOMMENDATION_MODEL_ID=eu.amazon.nova-pro-v1:0
INVOLO_MEDIA_S3_BUCKET=<eu-primary-media-bucket>
INVOLO_MEDIA_S3_REGION=eu-central-1
INVOLO_MEDIA_S3_BUCKET_OWNER=<optional-12-digit-owner>

INVOLO_BEDROCK_EMBEDDING_REGION=us-east-1
INVOLO_BEDROCK_EMBEDDING_MODEL_ID=amazon.nova-2-multimodal-embeddings-v1:0
INVOLO_EMBEDDING_MEDIA_S3_BUCKET=<us-embedding-media-bucket>
INVOLO_EMBEDDING_MEDIA_S3_REGION=us-east-1
INVOLO_EMBEDDING_MEDIA_S3_ENDPOINT_URL=
INVOLO_EMBEDDING_MEDIA_S3_BUCKET_OWNER=<optional-12-digit-owner>
```

Production embedding-media storage must be regional AWS S3, not a custom endpoint.
It mirrors derived keyframes/segments to the US; review cross-region transfer and
data-residency requirements before enabling creator media processing.

Start infrastructure and host processes:

```bash
./run.sh up
```

This starts MongoDB, Redis, Qdrant, and MinIO with Compose, then runs FastAPI,
Celery worker, Celery Beat, and Next.js locally.

- Frontend: http://localhost:8020
- API: http://localhost:8021
- OpenAPI: http://localhost:8021/docs
- Live: http://localhost:8021/health/live
- Ready: http://localhost:8021/health/ready
- MinIO console: http://localhost:8027

Stop or inspect logs:

```bash
./run.sh down
make logs
```

## Manual development

```bash
docker compose -f docker-compose.infra.yml -p involo up -d --wait
docker compose -f docker-compose.infra.yml -p involo --profile init run --rm minio-init

cd backend
uv sync --extra dev --quiet
source .venv/bin/activate
playwright install chromium
uvicorn app.main:app --reload
```

In separate terminals:

```bash
cd backend
source .venv/bin/activate
celery -A app.tasks worker --loglevel=INFO

cd backend
source .venv/bin/activate
celery -A app.tasks beat --loglevel=INFO

cd frontend
npm install
printf 'NEXT_PUBLIC_API_URL=http://localhost:8021\n' > .env.local
npm run dev
```

## Checks

```bash
cd backend
uv run ruff check .
uv run mypy app
uv run pytest

cd ../frontend
npm run lint
npm run typecheck
npm test -- --run
npm run build

cd ..
docker compose -f docker-compose.infra.yml config --quiet
```

Credential-dependent smoke tests are skipped by normal verification. Enable only the
suite you intend to run with `INVOLO_RUN_REAL_AWS_SMOKE=1`,
`INVOLO_RUN_REAL_S3_SMOKE=1`, `INVOLO_RUN_REAL_MEDIA_SMOKE=1`,
`INVOLO_RUN_REAL_INSTAGRAM_SMOKE=1`,
`INVOLO_RUN_REAL_INSTAGRAM_PROFILE_SMOKE=1`, or
`INVOLO_RUN_REAL_META_SMOKE=1`. Each requires its own credentials and dependencies;
normal `pytest`/`make verify` does not prove live provider access.

## Important implementation notes

- MongoDB is authoritative; Qdrant and S3 are derived stores without a distributed
  transaction.
- V2 Qdrant defaults are `trend_content_v2`, `user_content_v2`,
  `user_profiles_v2`, and `content_segments_v2`.
- The regular `embed` job performs full S3 ingestion, Nova Pro vision, segmented
  Nova media embeddings, and fused-vector writes. `multimodal-backfill` uses the
  same pipeline for schema-old or incomplete trend records; bulk profiling
  regenerates user content/profile vectors.
- Recommendation generation records historical ranked predictions. Admins can run
  cutoff-safe offline NDCG@K, Precision@K, Brier/reliability, latency, and cost
  evaluation; configured regressions produce an advisory rollback recommendation.
- Admin observability exposes provider/model/stage run counts, failures, latency and
  media duration, recommendation token cost, quality gates, and evaluation controls.
- `/health/ready` uses cached, timed live probes for all three S3 buckets, Bedrock model
  metadata/inference profiles, and Meta token/account access. It validates AWS bucket
  locations against configured regions. Development may disable provider probes;
  production may not. A transient provider outage returns readiness 503 without
  preventing startup or `/health/live`.
- Nova Pro generation runs through `us.amazon.nova-pro-v1:0` in `us-east-1`.
  Nova 2 multimodal embedding uses the plain in-region model in `us-east-1`; derived
  keyframes/segments are mirrored from the EU primary bucket to a US embedding
  bucket. Provenance and telemetry persist actual processing regions, and erasure
  deletes assets from both buckets.
- Compose defines infrastructure only. `run.sh` orchestrates application processes.
- Compose uses floating Qdrant/MinIO image tags; pin tested digests in production.
- Disconnecting Instagram retry-safely erases derived S3, Qdrant, and Mongo data,
  returns 503 instead of 204 when incomplete, and intentionally preserves the
  Involo authentication account/session.

Live validation record (2026-07-17): AWS text embedding, profile generation,
recommendation generation, and Transcribe API smoke tests passed (**4 passed**).
Media S3/Nova image-video and Meta smoke suites were not run because required
buckets/Meta configuration were unavailable.
