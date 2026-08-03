# Involo backend

Python 3.12 FastAPI API and Celery workloads for authentication, official Meta trend
discovery, Instagram creator profiling, multimodal content intelligence,
recommendations, feedback, outcomes, experiments, and administration.

For the complete implemented architecture, data dictionary, diagrams, production
risks, migration, and deletion boundary, read
[`../docs/SYSTEM_ARCHITECTURE.md`](../docs/SYSTEM_ARCHITECTURE.md). Request examples
are in [`../docs/API_EXAMPLES.md`](../docs/API_EXAMPLES.md).

## Package layout

- `app/api`: FastAPI app, routes, dependencies, response mapping
- `app/core`: settings, security, token crypto, cron, rate limits, errors
- `app/schemas`: Pydantic HTTP and domain contracts
- `app/services`: workflow orchestration and scoring/retrieval rules
- `app/providers`: Meta, AWS, Bedrock, S3, and topic connectors
- `app/infrastructure`: Mongo/Redis/Qdrant initialization, migrations, log bus
- `app/workers`: Celery app, runtime, locks, scheduler, tasks
- `app.main:app`: Uvicorn compatibility entrypoint
- `app.tasks`: Celery compatibility entrypoint

## Runtime providers

| Capability | Implemented provider |
|---|---|
| Public trend discovery | Official Meta hashtag search/recent media |
| Optional discovery adapter | Playwright Instagram browser flow |
| Owned account/media/audience | Instagram Login and Graph API Insights |
| Transcript | ffmpeg, S3, AWS Transcribe |
| Media persistence | EU primary S3 plus US embedding-media S3, SSE-S3 |
| Visual intelligence | EU Nova Pro inference profile in `eu-central-1` |
| Text/video/image embedding | Nova 2 in-region model in `us-east-1` |
| Profile summary and shoot briefs | EU Nova Pro inference profile in `eu-central-1` |
| External topic signals | official Google Trends, YouTube, Reddit APIs |

Provider factories instantiate production providers. Fake/fixture behavior is
limited to tests; provider-dependent local flows require credentials, buckets,
model access, and ffmpeg/ffprobe.

## Data plane

MongoDB is the durable record. Redis is the Celery broker/result backend and stores
ephemeral locks, rate limits, OAuth state, hourly scheduler keys, and live logs.
Qdrant stores derived vectors:

| Collection | Named vectors |
|---|---|
| `trend_content_v2` | `text`, `audio_video`, `fused` |
| `user_content_v2` | `text`, `audio_video`, `fused` |
| `user_profiles_v2` | `profile` |
| `content_segments_v2` | `segment` |

The normal `embed_trend_content` worker selects enriched trends with media URLs,
downloads media, stores S3 media/keyframes/segments, runs Nova Pro visual analysis,
embeds text and each segment/frame, pools media, and writes `text`, `audio_video`,
and `fused`. `multimodal_backfill` runs the same implementation for schema-old or
incomplete visual/segment records.

The primary media bucket and Nova Pro generation stages use `eu-central-1` with
`eu.amazon.nova-pro-v1:0`. Nova 2 multimodal embeddings use the plain
`amazon.nova-2-multimodal-embeddings-v1:0` model in `us-east-1`; this configuration
has no geographic embedding inference profile. Keyframes and video segments are
mirrored to a separate US embedding-media bucket before embedding. Source video
remains in the EU primary bucket for vision.

## Main workflows

### Trend intelligence

1. `scrape_instagram` uses the configured `meta` adapter by default and upserts by
   canonical URL.
2. `enrich_trend_content` fetches metadata, computes an initial score, applies the
   configured threshold, and transcribes eligible media.
3. `embed_trend_content` performs full multimodal V2 processing.
   `multimodal_backfill` repairs/migrates eligible existing records.
4. Hourly snapshots at default 6/24/48/72-hour offsets update velocity,
   acceleration, lifecycle, `public-trend-v2` score, and confidence.

### Creator profiling

Instagram OAuth state is random, Redis-backed, TTL-limited, and consumed with
`GETDEL`. Long-lived tokens are encrypted in Mongo and refreshed before expiry.
Profiling reads up to 30 media from the last 90 days and best-effort audience
Insights. Each media item is transcribed and fully multimodal-processed.

The profile vector is the mean of successful fused vectors; dispersion is RMS
Euclidean distance. Per-format performance residuals and K-Means semantic pillars
produce `creator-profile-v2`, followed by a Bedrock summary. Missing audience data
does not stop media profiling; all media failing does.

### Recommendation and learning

The API loads `user_profiles_v2/profile`, searches `trend_content_v2/fused`, filters
by schema/language/market, falls back to schema-only when localized results are
empty, removes low-confidence candidates, reranks similarity plus viral score, and
applies a cluster/source/format diversity heuristic.

Nova Pro returns forced-schema shoot briefs. The service hydrates evidence from
retrieved Mongo/Qdrant records and deduplicates generated cards by exact hash and
embedding cosine before inserting a complete batch.

Users can append idempotent recommendation-state events, link a recommendation to an
owned media ID, and manage experiment states. Hourly scheduling captures linked-post
outcomes at default 24/72-hour offsets.

Recommendation generation also stores ranked candidates and bounded probabilities in
`ranking_predictions`. `POST /admin/evaluations/run` evaluates historical predictions
at a caller-supplied cutoff with NDCG@K, Precision@K, Brier score, ten-bin
reliability, p95 latency, and cost/prediction gates. Explicit cutoff-safe labels take
precedence; otherwise later snapshot views above the historical ranking median label
outperformers. A material regression against the prior same-model evaluation stores
an advisory rollback recommendation.

## HTTP contract

All paths below are under `/api/v1`:

- Auth: `POST /auth/register|login|refresh|logout`, `GET /auth/me`
- Preferences: `GET|PUT /preferences`
- Instagram: `POST /instagram/oauth/start`, callback, status, disconnect
- Profile: `POST /profile/sync`, `GET /profile/analytics`
- Recommendations: `POST|GET /recommendations`
- Learning: recommendation event and post-link endpoints
- Experiments: create and patch
- Admin scraper: config, runs, latest/detail, WebSocket logs
- Admin pipeline: enrich, embed, multimodal backfill, runs, stats
- Admin profiling: config, estimate, bulk runs
- Admin overview/jobs/observability and `POST /admin/evaluations/run`

Unprefixed health endpoints are `/health/live` and `/health/ready`. Cookie auth uses
HttpOnly `involo_access` and `involo_refresh`; browser clients send
`credentials: "include"`. Long-running work returns HTTP 202 job records.
Instagram disconnect returns 204 only after S3, all user-related Qdrant collections,
and derived Mongo records are erased; incomplete deletion returns 503. Evaluation
returns 409 when no labeled historical rankings are available.

## Scheduler and job behavior

Celery Beat calls `scheduled_dispatch` every 60 seconds. Five-field DB cron values
are UTC. A Redis `SET NX` key queues snapshot, topic-signal, and outcome work once
per UTC hour.

Jobs progress through `queued`, `running`, and terminal `succeeded`, `failed`,
`needs_intervention`, or `skipped_locked`. Scraping and pipeline work use distributed
locks. `TransientError` tasks retry with exponential backoff, jitter, configured
maximum delay, and configured attempt count.

Job insertion and broker dispatch are not atomic. Production monitoring must detect
old queued records and reconcile them safely.

## Configuration

Settings use the `INVOLO_` prefix. `.env.example` lists available values. Production
validation requires:

- non-default JWT and Instagram token-encryption secrets;
- Meta app ID/secret;
- Meta trend access token and Instagram Business account ID;
- primary media, embedding-media, and transcription S3 buckets.

It also requires operationally correct HTTPS/CORS/cookies, private authenticated
Mongo/Redis/Qdrant, IAM least privilege, model access, S3 lifecycle, backups, and
image pinning.

Current regional settings:

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

The primary bucket region must equal the generation region; the embedding bucket
region must equal the embedding region. Different regions require separate buckets.
Production rejects a custom embedding-media endpoint, geographic-profile embedding
IDs, and the bare Nova Pro on-demand generation ID.

`/health/ready` validates Mongo, Redis, and Qdrant and performs cached live provider
probes. It calls S3 `HeadBucket` for primary media, embedding media, and transcription
buckets, Bedrock
`GetFoundationModel` or `GetInferenceProfile` for the configured embedding, vision,
profile, and recommendation stages, and Meta `/me` plus configured business-account
ID probes. Shared `(region, model ID)` pairs are probed once and mapped to each
stage. AWS S3 also uses `GetBucketLocation`; mismatch with the expected configured
region returns `region_mismatch`.

The response contains only granular `{ok, reason, required, region}` checks; it does not
return credentials, bucket/model/account identifiers, response bodies, or exception
text. Default timeout is 3 seconds per concurrent probe and cache TTL is 30 seconds
per API process. Configure these with:

```dotenv
INVOLO_PROVIDER_READINESS_PROBES_ENABLED=true
INVOLO_PROVIDER_READINESS_CACHE_TTL_SECONDS=30
INVOLO_PROVIDER_READINESS_TIMEOUT_SECONDS=3
```

Development may disable live probes, producing a non-required
`disabled_by_configuration` result. Production configuration rejects disabled
probes. Provider denial, throttling, timeout, missing configuration, or outage makes
readiness return 503 until a later cache refresh succeeds; it does not fail API
startup or `/health/live`.

Evaluation gates use `INVOLO_EVALUATION_*`: defaults are NDCG@K >= 0.5,
Precision@K >= 0.2, Brier <= 0.25, p95 latency <= 30 seconds, and
cost/prediction <= 1. Rollback tolerance defaults are 0.1 NDCG drop, 0.1 Precision
drop, or 0.05 Brier increase.

Instrumented transcription and Nova vision/text/video/image embedding telemetry
records sanitized provider, model ID, stage, state, duration, media seconds, subject
ID, optional user ID, and actual processing region. `GET /admin/observability` groups it
by provider/model/stage and returns run/failure counts and average latency alongside
token cost, latest evaluation, and thresholds. The admin UI renders this telemetry
and can submit offline evaluations.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium

uvicorn app.main:app --reload
celery -A app.tasks worker --loglevel=INFO
celery -A app.tasks beat --loglevel=INFO
```

Run MongoDB, Redis, Qdrant, and MinIO first with the root
`docker-compose.infra.yml`. ffmpeg and ffprobe must be on `PATH` for media and
transcription processing.

## Checks

```bash
uv run ruff check .
uv run mypy app
uv run pytest
```

Credential-dependent tests are skipped in normal `pytest` and `make verify` runs.
Enable only intended suites:

| Flag | Live checks |
|---|---|
| `INVOLO_RUN_REAL_AWS_SMOKE=1` | Bedrock embedding/profile/recommendation and Transcribe API |
| `INVOLO_RUN_REAL_S3_SMOKE=1` | media S3 put/get/delete |
| `INVOLO_RUN_REAL_MEDIA_SMOKE=1` | generated image/video upload to both regional buckets, Nova text/image/video embedding, Nova Pro vision |
| `INVOLO_RUN_REAL_INSTAGRAM_SMOKE=1` | Playwright Instagram discovery |
| `INVOLO_RUN_REAL_INSTAGRAM_PROFILE_SMOKE=1` | Graph profile with `INVOLO_INSTAGRAM_TEST_ACCESS_TOKEN` |
| `INVOLO_RUN_REAL_META_SMOKE=1` | official hashtag plus Graph profile (`INVOLO_META_SMOKE_HASHTAG`) |

Flags are independent and require matching credentials, buckets/model access,
network, and—for media—ffmpeg. Normal verification does not prove live provider
access.

Validation record, 2026-07-17: `INVOLO_RUN_REAL_AWS_SMOKE=1` exercised text
embedding, profile generation, recommendation generation, and Transcribe API access;
**4 passed**. The S3/media Nova image-video and Meta smoke suites were not run because
the required primary/embedding buckets and Meta configuration were unavailable.

## Current boundaries

- Mongo, Qdrant, and S3 writes are not transactional.
- S3 retention metadata/tag requires lifecycle rules in both media buckets.
- Instagram disconnect retry-safely deletes collected S3 objects, profile/content/
  segment Qdrant points, and user-derived Mongo records. Nested embedding assets are
  erased from the US mirror bucket. It intentionally preserves
  `users` and `auth_sessions`; it is not account closure.
- Topic signals are stored separately and are not currently part of recommendation
  ranking.
- Experiment state is tracked, but traffic assignment and significance are manual.
- V2 trend backfill is idempotent by schema/completeness; bulk `profile_all`
  regenerates connected-user content and profile vectors. Dual-read/write, automatic
  cutover, and automatic rollback are not implemented; offline rollback advice is
  operator-enforced.
- The EU/US topology transfers derived keyframes/segments and processes text/media
  embeddings in the US. It must not be described as EU-only data residency.
