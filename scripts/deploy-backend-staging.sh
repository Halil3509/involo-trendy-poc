#!/bin/bash
set -euo pipefail

BACKEND_IMAGE_TAG="${1:-}"
BACKEND_IMAGE_URI="013049518751.dkr.ecr.eu-west-3.amazonaws.com/involo-trendy-poc/backend"

if [[ -z "$BACKEND_IMAGE_TAG" ]]; then
  echo "Usage: $0 <image-tag>"
  exit 1
fi

export BACKEND_IMAGE_URI BACKEND_IMAGE_TAG

echo "Logging in to ECR..."
aws ecr get-login-password --region eu-west-3 \
  | docker login --username AWS --password-stdin "${BACKEND_IMAGE_URI%/*}"

cd /home/ubuntu/involo-trendy-poc

echo "Pulling backend image..."
docker compose -f docker-compose.server.yml pull backend worker beat

echo "Deploying backend services..."
docker compose -f docker-compose.server.yml up -d backend worker beat

echo "Stopping legacy in-host containers (frontend, minio) handled by Vercel / S3..."
for container in involo-frontend involo-minio-1; do
  if docker ps --format '{{.Names}}' | grep -qx "$container"; then
    docker stop "$container" || true
    docker rm "$container" || true
  fi
done

echo "Waiting for /health/live..."
for i in {1..30}; do
  if curl -fsS http://localhost:8021/health/live >/dev/null; then
    echo "Backend is healthy."
    break
  fi
  sleep 2
done

echo "Pruning old images..."
docker image prune -f

echo "Deploy complete: ${BACKEND_IMAGE_URI}:${BACKEND_IMAGE_TAG}"
