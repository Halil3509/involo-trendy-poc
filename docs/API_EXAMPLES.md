# Involo API examples

These examples match the implemented `/api/v1` routes. Replace local URLs and
placeholder values; never paste real access tokens or secrets into requests or logs.
Authentication uses HttpOnly cookies, so curl examples use a cookie jar.

## Health and authentication

```bash
curl -sS http://localhost:8021/health/live
curl -sS http://localhost:8021/health/ready

curl -sS -c cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"email":"creator@example.com","password":"replace-with-a-strong-password"}' \
  http://localhost:8021/api/v1/auth/register

curl -sS -b cookies.txt http://localhost:8021/api/v1/auth/me

curl -sS -b cookies.txt -c cookies.txt -X POST \
  http://localhost:8021/api/v1/auth/refresh
```

Readiness performs cached live provider probes. A healthy response includes granular,
sanitized checks:

```json
{
  "status": "ready",
  "checks": {
    "mongo": {"ok": true, "reason": "ok", "required": true},
    "redis": {"ok": true, "reason": "ok", "required": true},
    "qdrant": {"ok": true, "reason": "ok", "required": true},
    "media_s3": {
      "ok": true, "reason": "ok", "required": true, "region": "eu-central-1"
    },
    "embedding_media_s3": {
      "ok": true, "reason": "ok", "required": true, "region": "us-east-1"
    },
    "transcribe_s3": {
      "ok": true, "reason": "ok", "required": true, "region": "eu-central-1"
    },
    "meta_token": {"ok": true, "reason": "ok", "required": true},
    "meta_account": {"ok": true, "reason": "ok", "required": true},
    "bedrock_embedding": {
      "ok": true, "reason": "ok", "required": true, "region": "us-east-1"
    },
    "bedrock_vision": {
      "ok": true, "reason": "ok", "required": true, "region": "eu-central-1"
    },
    "bedrock_profile": {
      "ok": true, "reason": "ok", "required": true, "region": "eu-central-1"
    },
    "bedrock_recommendation": {
      "ok": true, "reason": "ok", "required": true, "region": "eu-central-1"
    }
  }
}
```

S3 checks use `HeadBucket`; AWS S3 checks additionally compare `GetBucketLocation`
with the expected region. Bedrock uses foundation-model metadata in `us-east-1` for
Nova 2 embedding and the EU Nova Pro inference profile in `eu-central-1`; Meta checks
token `/me` and the configured account ID. Results expose expected regions but never
bucket/model/account identifiers, credentials, or provider error text.

A failed required provider check leaves the API live but returns HTTP 503:

```json
{
  "detail": {
    "status": "not_ready",
    "checks": {
      "embedding_media_s3": {
        "ok": false,
        "reason": "region_mismatch",
        "required": true,
        "region": "us-east-1"
      }
    }
  }
}
```

The real response includes every check. Results are cached per process (30 seconds
default), with a 3-second default timeout per concurrent provider probe. Development
may disable provider probes, yielding a non-required `provider_live_probes` result
with reason `disabled_by_configuration`; production rejects that setting.

Browser clients must set `credentials: "include"`.

## Preferences and Instagram onboarding

```bash
curl -sS -b cookies.txt \
  -H 'Content-Type: application/json' \
  -X PUT \
  -d '{
    "target_countries": ["TR"],
    "target_cities": ["Istanbul"],
    "content_languages": ["tr"],
    "timezone": "Europe/Istanbul",
    "niches": ["fitness"],
    "goals": ["qualified engagement"],
    "constraints": ["no medical claims"]
  }' \
  http://localhost:8021/api/v1/preferences

curl -sS -b cookies.txt -X POST \
  http://localhost:8021/api/v1/instagram/oauth/start
```

Open the returned `authorization_url` in the same user journey. The provider
redirects to the backend callback; do not manufacture callback codes.

```bash
curl -sS -b cookies.txt http://localhost:8021/api/v1/instagram/status

curl -sS -b cookies.txt -X POST \
  http://localhost:8021/api/v1/profile/sync

curl -sS -b cookies.txt http://localhost:8021/api/v1/profile/analytics
```

`POST /profile/sync` returns HTTP 202. Poll Instagram status or use admin job views;
the profiling work is asynchronous.

## Recommendations and evidence

```bash
curl -sS -b cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"count":3}' \
  http://localhost:8021/api/v1/recommendations

curl -sS -b cookies.txt \
  'http://localhost:8021/api/v1/recommendations?limit=10'
```

The creator profile must be `ready`, and V2 trend vectors must exist. A card includes
the shoot brief and server-hydrated evidence:

```json
{
  "id": "batch-object-id",
  "created_at": "2026-07-17T18:00:00Z",
  "recommendations": [
    {
      "id": "card-id",
      "title": "A specific production concept",
      "hook": "A concrete opening hook",
      "cta": "A concrete call to action",
      "content_format": "reels",
      "reasoning": "Why it fits the creator and current evidence",
      "objective": "qualified engagement",
      "target_audience": "Defined audience",
      "first_frame": "Visible first-frame direction",
      "hook_0_3s": "Spoken and visual direction",
      "script_beats": [{"at_seconds": 0, "direction": "Open on result"}],
      "shot_list": [
        {"order": 1, "framing": "close-up", "action": "show result", "duration_seconds": 3}
      ],
      "evidence_ids": ["qdrant-point-id"],
      "evidence": [
        {
          "evidence_id": "qdrant-point-id",
          "trend_id": "mongo-object-id",
          "permalink": "https://www.instagram.com/reel/example/",
          "similarity": 0.81,
          "lifecycle": "rising",
          "confidence": 0.72,
          "snapshot_at": "2026-07-17T17:00:00Z",
          "score_components": {"velocity": 10.2}
        }
      ]
    }
  ],
  "usage": {
    "input_tokens": 1000,
    "output_tokens": 500,
    "cache_read_input_tokens": 0,
    "cache_write_input_tokens": 0
  }
}
```

The values above illustrate shape only; they are not real provider output.

## Feedback, post outcomes, and experiments

Use a new stable idempotency key for each intended event. Repeating the same key for
the same recommendation returns the existing event; reusing it for another
recommendation returns 409.

```bash
curl -sS -b cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{
    "state":"saved",
    "reason":"fits-next-week",
    "note":"Prepare a short version",
    "idempotency_key":"ui-event-018f5b12"
  }' \
  http://localhost:8021/api/v1/recommendations/CARD_ID/events

curl -sS -b cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"media_id":"INSTAGRAM_MEDIA_ID"}' \
  http://localhost:8021/api/v1/recommendations/CARD_ID/post-link

curl -sS -b cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{
    "recommendation_id":"CARD_ID",
    "name":"Opening hook test",
    "variants":["question hook","result-first hook"]
  }' \
  http://localhost:8021/api/v1/recommendation-experiments

curl -sS -b cookies.txt \
  -H 'Content-Type: application/json' \
  -X PATCH \
  -d '{"state":"running","note":"Started with two variants"}' \
  http://localhost:8021/api/v1/recommendation-experiments/EXPERIMENT_OBJECT_ID
```

## Admin jobs

Use cookies for an account whose current Mongo user role is `admin`.

```bash
curl -sS -b admin-cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"keywords":["fitness","healthy recipes"]}' \
  -X POST http://localhost:8021/api/v1/admin/scraper/runs

curl -sS -b admin-cookies.txt \
  -X POST http://localhost:8021/api/v1/admin/pipeline/enrich

# Default full S3 + Nova vision + segmented multimodal V2 processing:
curl -sS -b admin-cookies.txt \
  -X POST http://localhost:8021/api/v1/admin/pipeline/embed

# Repair/backfill records with old schema or missing visual/segment data:
curl -sS -b admin-cookies.txt \
  -X POST http://localhost:8021/api/v1/admin/pipeline/multimodal-backfill

curl -sS -b admin-cookies.txt \
  'http://localhost:8021/api/v1/admin/pipeline/runs/latest?kind=multimodal_backfill'

curl -sS -b admin-cookies.txt \
  http://localhost:8021/api/v1/admin/observability

curl -sS -b admin-cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{
    "model_version":"retrieval-filtered-fused-mmr-v2",
    "data_cutoff":"2026-07-17T18:00:00Z",
    "k":10
  }' \
  http://localhost:8021/api/v1/admin/evaluations/run
```

`embed` and `multimodal-backfill` use the same full media implementation. The former
processes newly enriched trends; the latter repairs or migrates existing records.

Offline evaluation reads historical `ranking_predictions` at or before the cutoff.
It returns NDCG@K, Precision@K, Brier score, reliability buckets, p95 latency,
cost/prediction, configured thresholds, `passed`, and `rollback_recommended`.
If no explicit or later-snapshot labels can be hydrated, the endpoint returns 409.
The rollback recommendation is advisory and does not change deployment state.

## Disconnect and derived-data deletion

```bash
curl -i -sS -b cookies.txt -X DELETE \
  http://localhost:8021/api/v1/instagram/connection
```

This returns 204 only after collected primary-media and mirrored embedding-media
S3 objects, user profile/content/segment Qdrant points, and Instagram-derived Mongo
records are erased.
Deletion steps are safe to retry. A partial or failed store operation returns:

```json
{
  "detail": "instagram_disconnect_erasure_unavailable"
}
```

with HTTP 503, so clients should surface an incomplete-erasure state and offer retry.
The Involo `users` and `auth_sessions` records are intentionally preserved; this is
Instagram disconnect and derived-data erasure, not account closure.
