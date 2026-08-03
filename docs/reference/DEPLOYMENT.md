# Deployment

> `involo-trendy-poc` backend is deployed to the `involo-poc` EC2 instance through GitHub Actions, AWS ECR, AWS Systems Manager (SSM), and an Application Load Balancer (ALB). The frontend remains on Vercel.

## Environments

| Environment | Domain | Infrastructure |
|-------------|--------|----------------|
| Staging | `trendy-staging.involo.co` | `i-0154019e4120737bb` (`t3.micro`, `eu-west-3`) + ALB `involo-trendy-poc-staging-lb` |
| Production | `trendy-api.involo.co` (future) | Separate EC2 + ALB (not implemented yet) |

## CI/CD pipeline

### Pull requests and `main`

`.github/workflows/ci.yml` runs on every PR and `main` push:

1. `secret-scan` via `gitleaks`.
2. `backend-quality`: `ruff`, `mypy`, `pytest`.
3. `frontend-quality`: lint, typecheck, test, build.
4. `build-backend-image`: builds the `backend/Dockerfile` (no push).

### Staging deploy

`.github/workflows/deploy-staging.yml` runs on `main` pushes and `workflow_dispatch`:

1. Builds the backend image with `INSTALL_PLAYWRIGHT=true`.
2. Pushes it to `013049518751.dkr.ecr.eu-west-3.amazonaws.com/involo-trendy-poc/backend`:
   - tag: full Git SHA
   - tag: `staging`
3. Sends an SSM `AWS-RunShellScript` command to `i-0154019e4120737bb`.
4. On the server, `scripts/deploy-backend-staging.sh` logs in to ECR, pulls the image, and restarts `backend`, `worker`, and `beat` using `docker-compose.server.yml`.
5. Tags the deploy as `deploy/staging/<timestamp>-<short-sha>`.

### Trigger a staging deploy manually

```bash
git checkout main
git pull origin main
git commit --allow-empty -m "deploy: staging [deploy]"
git push origin main
```

A `[deploy]` marker in the commit message is not strictly required because `deploy-staging.yml` runs on every `main` push that touches non-frontend/non-doc paths. It can be used to make the intent explicit.

## Server-side files

- `docker-compose.server.yml` — production-like compose file that uses the ECR image and starts only `backend`, `worker`, `beat`, and the `mongodb`/`redis`/`qdrant` infrastructure.
- `scripts/deploy-backend-staging.sh` — executed by SSM to pull and restart the backend services.
- `backend/app/core/config.py` — `environment` supports `development`, `test`, `production`, and `staging`.
- `.env.example` — template for local development and staging `.env`.

## Environment variables on the staging server

Key `.env` values on `involo-poc`:

```ini
INVOLO_ENVIRONMENT=staging
INVOLO_JWT_SECRET=<32+ random chars>
INVOLO_INSTAGRAM_TOKEN_ENCRYPTION_KEY=<32+ random chars>
INVOLO_COOKIE_SECURE=true
INVOLO_COOKIE_SAMESITE=none
INVOLO_CORS_ORIGINS=https://trendy-staging.involo.co,https://frontend-alpha-rust-17.vercel.app,http://localhost:8020
INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI=https://trendy-staging.involo.co/api/v1/instagram/oauth/callback
INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL=https://frontend-alpha-rust-17.vercel.app/instagram/callback
INVOLO_AWS_REGION=us-east-1
INVOLO_TRANSCRIBE_S3_BUCKET=involo-trendy-poc-staging-transcribe
INVOLO_MEDIA_S3_BUCKET=involo-trendy-poc-staging-media
INVOLO_MEDIA_S3_REGION=us-east-1
INVOLO_EMBEDDING_MEDIA_S3_BUCKET=involo-trendy-poc-staging-embedding-media
INVOLO_EMBEDDING_MEDIA_S3_REGION=us-east-1
INVOLO_BEDROCK_GENERATION_REGION=us-east-1
INVOLO_BEDROCK_EMBEDDING_REGION=us-east-1
INVOLO_PROVIDER_READINESS_PROBES_ENABLED=true
```

Meta app credentials (`INVOLO_INSTAGRAM_APP_ID`, `INVOLO_INSTAGRAM_APP_SECRET`, etc.) are sourced from the Meta app dashboard and the existing server `.env`.

## AWS resources (staging)

| Resource | Name / Value |
|----------|--------------|
| EC2 instance | `i-0154019e4120737bb` (`t3.micro`, Ubuntu 26.04, `eu-west-3`) |
| ECR repository | `involo-trendy-poc/backend` in `eu-west-3` |
| ALB | `involo-trendy-poc-staging-lb` (default VPC, `eu-west-3a/b/c`) |
| ALB security group | `involo-trendy-poc-staging-alb-sg` (inbound 80/443 from `0.0.0.0/0`) |
| Target group | `involo-trendy-poc-staging-backend-tg` → `i-0154019e4120737bb:8021` |
| Health check | `/health/live` on `8021` |
| ACM certificate | `arn:aws:acm:eu-west-3:013049518751:certificate/a6d827a2-40c5-435a-a5ef-8fb3cc88c89d` (`*.involo.co`) |
| DNS record | `trendy-staging.involo.co` CNAME → ALB DNS name |
| S3 buckets | `involo-trendy-poc-staging-transcribe`, `involo-trendy-poc-staging-media`, `involo-trendy-poc-staging-embedding-media` in `us-east-1` |
| IAM OIDC image pusher | `github-actions-trendy-poc-staging-image-pusher` |
| IAM OIDC deployer | `github-actions-trendy-poc-staging-deployer` |
| EC2 instance profile | `involo-trendy-poc-staging-server-access` (SSM, ECR read, scoped S3, Bedrock, Transcribe) |

## Vercel configuration

- `NEXT_PUBLIC_API_URL=https://trendy-staging.involo.co` for Production and Preview.
- Meta app OAuth redirect URI: `https://trendy-staging.involo.co/api/v1/instagram/oauth/callback`.

## Post-deploy checks

```bash
# From any machine with the DNS record resolving
curl -s https://trendy-staging.involo.co/health/live

# On the server
docker ps --filter name=involo
curl -s http://localhost:8021/health/live
```

## Rollback

1. Find the previous deploy tag: `git tag -l 'deploy/staging/*' | sort | tail -n 2`.
2. Extract the short SHA from the tag.
3. On the server run `scripts/deploy-backend-staging.sh <short-sha>`.
4. Verify `/health/live`.

## Future production

Production will follow the same pattern with:

- A second EC2 instance (or the existing `involo-VPC` after migration).
- `involo-trendy-poc-prod-lb` and target group.
- Domain `trendy-api.involo.co`.
- `github-actions-trendy-poc-prod-deployer` role with a GitHub `environment: production` approval gate.
- Promotion of the same SHA-tagged ECR image by moving a `prod` tag.
