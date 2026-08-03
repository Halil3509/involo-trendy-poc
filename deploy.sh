#!/usr/bin/env bash
set -euo pipefail

# Single-command deploy: push local Involo to main, then SSH into the remote
# server, pull, and restart infrastructure + backend + frontend + tunnel.
#
# Defaults assume an SSH alias named "involo-poc" configured in ~/.ssh/config.
#
# Environment overrides:
#   DEPLOY_REPO_NAME          GitHub repo name (default: involo-poc-2)
#   DEPLOY_GITHUB_ACCOUNT     GitHub account/org (default: Halil3509)
#   DEPLOY_SSH_HOST           SSH alias or host (default: involo-poc)
#   DEPLOY_SERVER_PATH        Server app directory (default: /home/ubuntu/involo-poc-2)
#   INVOLO_NGROK_DOMAIN       Public domain for Instagram OAuth and API (default: involo-poc.hellodesk.com.tr)
#   GITHUB_TOKEN              Required: PAT with 'repo' scope to create/push/pull the GitHub repo.
#   DEPLOY_REPO_PRIVATE       Set to 1 to create a private repo (default: public for simpler server pulls)

DEPLOY_REPO_NAME="${DEPLOY_REPO_NAME:-involo-poc-2}"
DEPLOY_GITHUB_ACCOUNT="${DEPLOY_GITHUB_ACCOUNT:-Halil3509}"
DEPLOY_SSH_HOST="${DEPLOY_SSH_HOST:-involo-poc}"
DEPLOY_SERVER_PATH="${DEPLOY_SERVER_PATH:-/home/ubuntu/involo-poc-2}"
INVOLO_NGROK_DOMAIN="${INVOLO_NGROK_DOMAIN:-involo-poc.hellodesk.com.tr}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
DEPLOY_REPO_PRIVATE="${DEPLOY_REPO_PRIVATE:-}"

LOCAL_REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

# Load local .env so INVOLO_NGROK_DOMAIN and other env values are passed to the server.
if [ -f "${LOCAL_REPO_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${LOCAL_REPO_ROOT}/.env"
  set +a
fi

# .env may leave INVOLO_NGROK_DOMAIN empty; default to auto so ssh passes a non-empty arg.
INVOLO_NGROK_DOMAIN="${INVOLO_NGROK_DOMAIN:-auto}"

log() { echo "[deploy] $*" >&2; }

run_ssh() {
  ssh -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=120 -o StrictHostKeyChecking=accept-new "$DEPLOY_SSH_HOST" "$@"
}

ensure_local_git() {
  cd "$LOCAL_REPO_ROOT"

  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    log "Initializing local git repository"
    git init -b main
  fi

  if [ -d frontend/.git ]; then
    log "Removing nested frontend/.git so root repo owns the whole project"
    rm -rf frontend/.git
  fi

  if ! grep -qxF '.env' .gitignore 2>/dev/null; then
    log "Patching .gitignore for secrets/runtime state"
    {
      echo ""
      echo "# Deploy: do not commit secrets or runtime state"
      echo ".env"
      echo ".env.local"
      echo ".env.bak"
      echo ".env.bak.*"
      echo "frontend/.env.local"
      echo "*.log"
      echo "logs/"
      echo ".run.pids"
    } >> .gitignore
  fi
}

ensure_remote() {
  cd "$LOCAL_REPO_ROOT"

  if [ -z "$GITHUB_TOKEN" ]; then
    log "GITHUB_TOKEN is required. Create a PAT with 'repo' scope at https://github.com/settings/tokens/new"
    exit 1
  fi

  local github_url
  github_url="https://${GITHUB_TOKEN}@github.com/${DEPLOY_GITHUB_ACCOUNT}/${DEPLOY_REPO_NAME}.git"

  local visibility is_private_json
  if [ -n "$DEPLOY_REPO_PRIVATE" ]; then
    visibility="private"
    is_private_json="true"
  else
    visibility="public"
    is_private_json="false"
  fi

  log "Creating ${visibility} GitHub repo ${DEPLOY_GITHUB_ACCOUNT}/${DEPLOY_REPO_NAME} if it does not exist"
  curl -fsS -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    -d "{\"name\":\"${DEPLOY_REPO_NAME}\",\"private\":${is_private_json},\"auto_init\":false}" \
    "https://api.github.com/user/repos" >/dev/null 2>&1 || true

  if git remote get-url origin >/dev/null 2>&1; then
    log "Updating origin to ${DEPLOY_GITHUB_ACCOUNT}/${DEPLOY_REPO_NAME}"
    git remote set-url origin "$github_url"
  else
    git remote add origin "$github_url"
  fi
}

local_push() {
  ensure_local_git
  ensure_remote

  cd "$LOCAL_REPO_ROOT"

  git add -A

  if git diff --cached --quiet; then
    log "No changes to commit"
  else
    git commit -m "deploy: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi

  git branch -M main
  git push -u origin main
}

server_setup_script() {
  cat <<'REMOTE'
set -euo pipefail
rm -f /tmp/involo-deploy.done
trap 'touch /tmp/involo-deploy.done' EXIT
DEPLOY_SERVER_PATH="$1"
DEPLOY_GITHUB_ACCOUNT="$2"
DEPLOY_REPO_NAME="$3"
GITHUB_TOKEN="$4"
INVOLO_NGROK_DOMAIN="${5:-auto}"
INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL="${6:-https://frontend-alpha-rust-17.vercel.app/instagram/callback}"
GITHUB_REPO_URL="https://${GITHUB_TOKEN}@github.com/${DEPLOY_GITHUB_ACCOUNT}/${DEPLOY_REPO_NAME}.git"

logr() { echo "[server] $*" >&2; }

if ! command -v docker >/dev/null 2>&1; then
  logr "Docker not found. Install Docker before running this deploy."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  logr "Docker Compose plugin not found. Install it before running this deploy."
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  logr "curl not found. Install curl before running this deploy."
  exit 1
fi
if ! command -v npx >/dev/null 2>&1; then
  logr "npx not found. Install Node.js before running this deploy."
  exit 1
fi

# Add swap on low-memory hosts so npm install / docker build do not OOM
ensure_swap() {
  local mem_mb swap_mb
  mem_mb=$(free -m | awk '/^Mem:/{print $2}')
  swap_mb=$(free -m | awk '/^Swap:/{print $2}')
  if [ "${swap_mb:-0}" -eq 0 ] && [ "${mem_mb:-0}" -lt 2048 ]; then
    logr "Memory is ${mem_mb}MB with no swap; adding 2GB swapfile"
    if [ -f /swapfile ]; then
      sudo swapoff /swapfile 2>/dev/null || true
      sudo rm -f /swapfile
    fi
    sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
  fi
}
ensure_swap

# Clean up disk before a build so Docker/git do not fail with "no space left".
ensure_disk_space() {
  local threshold_pct=85
  local min_free_gb=2
  local pct free_gb
  read -r pct free_gb < <(df -BG -P / | awk 'NR==2 {gsub(/[%G]/,""); print $(NF-1), $(NF-2)}')

  if [ "${pct:-100}" -ge "$threshold_pct" ] || [ "${free_gb:-0}" -lt "$min_free_gb" ]; then
    logr "Disk is ${pct}% full (${free_gb}GB free). Cleaning up before deploy ..."

    sudo apt-get clean >/dev/null 2>&1 || true
    sudo journalctl --vacuum-time=7d >/dev/null 2>&1 || true
    sudo find /tmp -mindepth 1 -delete 2>/dev/null || true
    docker system prune -f >/dev/null 2>&1 || true
    docker image prune -a -f >/dev/null 2>&1 || true
    docker builder prune -f >/dev/null 2>&1 || true
    docker volume prune -f >/dev/null 2>&1 || true

    # Remove stale git index.lock that can be left behind by an interrupted git command.
    rm -f "${DEPLOY_SERVER_PATH}/.git/index.lock" 2>/dev/null || true

    # Recheck
    read -r pct free_gb < <(df -BG -P / | awk 'NR==2 {gsub(/[%G]/,""); print $(NF-1), $(NF-2)}')
    if [ "${pct:-100}" -ge "$threshold_pct" ] && [ "${free_gb:-0}" -lt "$min_free_gb" ]; then
      logr "Disk still full after cleanup (${pct}% / ${free_gb}GB free). Free space manually before deploying."
      exit 1
    fi
    logr "Disk cleaned: ${pct}% full, ${free_gb}GB free"
  fi
}
ensure_disk_space

# Node.js / npm (required for npx localtunnel)
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  logr "Installing Node.js 20 LTS ..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi

# Clone or update app repo from GitHub
if [ ! -d "${DEPLOY_SERVER_PATH}/.git" ]; then
  logr "Cloning app repo from GitHub into ${DEPLOY_SERVER_PATH} ..."
  rm -rf "$DEPLOY_SERVER_PATH"
  git clone "$GITHUB_REPO_URL" "$DEPLOY_SERVER_PATH"
fi

cd "$DEPLOY_SERVER_PATH"
logr "Resetting local tree to avoid conflicts with generated runtime files ..."
git reset --hard HEAD
logr "Removing untracked files that would block the pull ..."
git clean -fd
logr "Pulling latest changes from GitHub ..."
git remote set-url origin "$GITHUB_REPO_URL"
git pull origin main

# Ensure .env exists and is safe
if [ ! -f .env ]; then
  logr "Creating .env from .env.example"
  cp .env.example .env
fi

# Load .env; the requested tunnel domain from the deploy command can override it.
set -a
# shellcheck disable=SC1091
source .env
set +a

REQUESTED_NGROK_DOMAIN="${5:-${INVOLO_NGROK_DOMAIN:-auto}}"
INVOLO_NGROK_DOMAIN="$REQUESTED_NGROK_DOMAIN"

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_hex(32))'
  fi
}

if ! grep -q '^INVOLO_JWT_SECRET=' .env || grep -q '^INVOLO_JWT_SECRET=replace' .env; then
  secret="$(random_secret)"
  sed -i '/^INVOLO_JWT_SECRET=/d' .env
  echo "INVOLO_JWT_SECRET=${secret}" >> .env
  logr "Generated INVOLO_JWT_SECRET"
fi

if ! grep -q '^INVOLO_INSTAGRAM_TOKEN_ENCRYPTION_KEY=' .env || grep -q '^INVOLO_INSTAGRAM_TOKEN_ENCRYPTION_KEY=development' .env; then
  key="$(random_secret)"
  sed -i '/^INVOLO_INSTAGRAM_TOKEN_ENCRYPTION_KEY=/d' .env
  echo "INVOLO_INSTAGRAM_TOKEN_ENCRYPTION_KEY=${key}" >> .env
  logr "Generated INVOLO_INSTAGRAM_TOKEN_ENCRYPTION_KEY"
fi

if ! grep -q '^INVOLO_NGROK_DOMAIN=' .env; then
  echo "INVOLO_NGROK_DOMAIN=${INVOLO_NGROK_DOMAIN:-auto}" >> .env
  logr "Set INVOLO_NGROK_DOMAIN=${INVOLO_NGROK_DOMAIN:-auto}"
fi
if ! grep -q '^INVOLO_TUNNEL_STRICT=' .env; then
  echo "INVOLO_TUNNEL_STRICT=false" >> .env
  logr "Set INVOLO_TUNNEL_STRICT=false"
fi

# Disable uvicorn/celery auto-reload on the server for stable startup
if ! grep -q '^INVOLO_NO_RELOAD=' .env; then
  echo "INVOLO_NO_RELOAD=1" >> .env
  logr "Set INVOLO_NO_RELOAD=1"
fi

# Remove empty optional values that Pydantic rejects (empty string != None)
sed -i '/^INVOLO_[A-Z0-9_]*=$/d' .env
sed -i '/^INVOLO_BRAND_ANALYSIS_KEYFRAME_OFFSETS_SECONDS=/d' .env
logr "Sanitized .env defaults"

# Stop any existing host-process run and localtunnel instances
if [ -s .run.pids ]; then
  while read -r pid; do
    kill -TERM "$pid" 2>/dev/null || true
  done < .run.pids
  rm -f .run.pids
fi
pkill -f './run.sh up' 2>/dev/null || true
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
pkill -f 'celery -A app.tasks worker' 2>/dev/null || true
pkill -f 'celery -A app.tasks beat' 2>/dev/null || true
pkill -f 'lt --port' 2>/dev/null || true
pkill -f 'next dev --port 8020' 2>/dev/null || true
pkill -f 'next-server' 2>/dev/null || true
pkill -f 'node server.js' 2>/dev/null || true
sleep 2

# Remove any previously created app containers so docker compose up does not fail
# with "container name already in use" when it tries to recreate them.
logr "Stopping and removing old app containers before recreate ..."
docker compose -f docker-compose.yml rm -f -s backend worker beat frontend >/dev/null 2>&1 || true
docker rm -f involo-backend involo-worker involo-beat involo-frontend 2>/dev/null || true

# Helper to keep NEXT_PUBLIC_API_URL in sync with the public tunnel domain.
set_next_public_api_url() {
  local domain="$1"
  if [ -z "$domain" ] || [ "$domain" = "auto" ]; then
    NEXT_PUBLIC_API_URL="http://localhost:8021"
  else
    NEXT_PUBLIC_API_URL="https://${domain}"
  fi
  export NEXT_PUBLIC_API_URL
  sed -i '/^NEXT_PUBLIC_API_URL=/d' .env
  echo "NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}" >> .env
}

set_next_public_api_url "$INVOLO_NGROK_DOMAIN"

# Propagate the deployed frontend's OAuth callback URL so Docker uses Vercel (not localhost).
sed -i '/^INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL=/d' .env
echo "INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL=${INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL}" >> .env

mkdir -p logs
USE_DOCKER=0

# Try Docker services first, fall back to run.sh host process if it fails
logr "Building and starting Docker services (backend/worker/beat/frontend + infra). This may take several minutes on first run ..."
if docker compose -f docker-compose.yml up -d --build > logs/docker-build.log 2>&1; then
  USE_DOCKER=1
  logr "Docker services started. Build log: logs/docker-build.log"
else
  logr "Docker compose failed; see logs/docker-build.log. Stopping any partial containers and falling back to host-process run.sh."
  docker compose rm -f -s backend worker beat frontend >/dev/null 2>&1 || true
fi

if [ "$USE_DOCKER" -ne 1 ]; then
  logr "Starting ./run.sh up in the background as fallback"
  if command -v setsid >/dev/null 2>&1; then
    setsid ./run.sh up > logs/deploy.log 2>&1 < /dev/null &
  else
    nohup ./run.sh up > logs/deploy.log 2>&1 < /dev/null &
  fi
  RUNSH_PID=$!
  disown $RUNSH_PID || true
  logr "Fallback run.sh PID ${RUNSH_PID}"
fi

# Wait a moment for backend to start listening before using the fixed domain
sleep 5

# Use the fixed public domain for Instagram OAuth and API
PUBLIC_URL="https://${INVOLO_NGROK_DOMAIN}"
INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI="${PUBLIC_URL}/api/v1/instagram/oauth/callback"
export PUBLIC_URL INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI
logr "Using fixed public domain: ${PUBLIC_URL}"
logr "Instagram OAuth redirect URI: ${INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI}"
logr "Instagram Webhooks callback URL: ${PUBLIC_URL}/api/v1/instagram/webhook"

# Update .env: keep the requested tunnel domain for future deploys, but record the actual OAuth callback URL.
_needs_recreate=0
if ! grep -q "^INVOLO_NGROK_DOMAIN=${REQUESTED_NGROK_DOMAIN}$" .env; then
  sed -i '/^INVOLO_NGROK_DOMAIN=/d' .env
  echo "INVOLO_NGROK_DOMAIN=${REQUESTED_NGROK_DOMAIN}" >> .env
  logr "Updated .env INVOLO_NGROK_DOMAIN to ${REQUESTED_NGROK_DOMAIN}"
  _needs_recreate=1
fi
if ! grep -q "^INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI=${PUBLIC_URL}/api/v1/instagram/oauth/callback$" .env; then
  sed -i '/^INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI=/d' .env
  echo "INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI=${PUBLIC_URL}/api/v1/instagram/oauth/callback" >> .env
  logr "Updated .env INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI to ${PUBLIC_URL}/api/v1/instagram/oauth/callback"
  _needs_recreate=1
fi

# Ensure CORS allows the public backend domain, the Vercel frontend and local dev origins.
if ! grep -q "^INVOLO_CORS_ORIGINS=.*${INVOLO_NGROK_DOMAIN}" .env; then
  _cors="http://localhost:8020,https://involo.loca.lt,https://${INVOLO_NGROK_DOMAIN},https://frontend-alpha-rust-17.vercel.app"
  sed -i '/^INVOLO_CORS_ORIGINS=/d' .env
  echo "INVOLO_CORS_ORIGINS=${_cors}" >> .env
  logr "Updated .env INVOLO_CORS_ORIGINS"
  _needs_recreate=1
fi

# Keep the browser-facing API URL in sync with the actual public tunnel domain.
set_next_public_api_url "${PUBLIC_URL#https://}"
if [ "$USE_DOCKER" -eq 1 ]; then
  if ! grep -q "^NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}$" .env; then
    _needs_recreate=1
  fi
fi

# If running Docker and the public URL changed, recreate services with the new env.
if [ "$USE_DOCKER" -eq 1 ] && [ "$_needs_recreate" -eq 1 ]; then
  logr "Recreating backend/worker/beat/frontend with updated public URL ..."
  docker compose up -d --build --force-recreate >/dev/null 2>&1 || true
fi

# Health check
logr "Waiting for backend health/live (up to 5 minutes) ..."
_health_ok=0
for i in $(seq 1 60); do
  if curl --max-time 5 -fsS http://localhost:8021/health/live >/dev/null 2>&1; then
    logr "Backend health/live OK"
    _health_ok=1
    break
  fi
  if [ $((i % 10)) -eq 0 ]; then
    logr "... still waiting for backend (${i}/60)"
  fi
  sleep 5
done

if [ "$_health_ok" -ne 1 ]; then
  logr "Backend did not become healthy in time."
  if [ "$USE_DOCKER" -eq 1 ]; then
    docker compose logs --tail 80 backend >&2 || true
    tail -n 80 logs/docker-build.log >&2 || true
  else
    tail -n 80 logs/deploy.log >&2 || true
  fi
  exit 1
fi

# Wait for frontend health if running in Docker mode
if [ "$USE_DOCKER" -eq 1 ]; then
  _frontend_health_ok=0
  logr "Waiting for frontend health (up to 2 minutes) ..."
  for i in $(seq 1 24); do
    if curl --max-time 5 -fsS http://localhost:8020/ >/dev/null 2>&1; then
      logr "Frontend health OK"
      _frontend_health_ok=1
      break
    fi
    if [ $((i % 6)) -eq 0 ]; then
      logr "... still waiting for frontend (${i}/24)"
    fi
    sleep 5
  done
  if [ "$_frontend_health_ok" -ne 1 ]; then
    logr "Frontend did not become healthy in time."
    docker compose logs --tail 80 frontend >&2 || true
  fi
fi

logr "Public URL: ${PUBLIC_URL}"
logr "Instagram OAuth callback: ${PUBLIC_URL}/api/v1/instagram/oauth/callback"
logr "Instagram Webhooks callback: ${PUBLIC_URL}/api/v1/instagram/webhook"
logr "Deploy finished."
REMOTE
}

server_deploy() {
  log "Deploying to ${DEPLOY_SSH_HOST} ..."
  DEPLOY_OAUTH_SUCCESS_URL="${INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL:-https://frontend-alpha-rust-17.vercel.app/instagram/callback}"

  local server_script_path="/tmp/involo-deploy.sh"
  local server_log="/tmp/involo-deploy.log"

  log "Copying remote deploy script to ${DEPLOY_SSH_HOST} ..."
  server_setup_script | run_ssh "cat > ${server_script_path} && chmod +x ${server_script_path}"

  log "Starting deploy in a background shell (survives broken SSH pipe) ..."
  run_ssh "rm -f /tmp/involo-deploy.done; nohup bash ${server_script_path} $(printf '%q' "$DEPLOY_SERVER_PATH") $(printf '%q' "$DEPLOY_GITHUB_ACCOUNT") $(printf '%q' "$DEPLOY_REPO_NAME") $(printf '%q' "$GITHUB_TOKEN") $(printf '%q' "$INVOLO_NGROK_DOMAIN") $(printf '%q' "$DEPLOY_OAUTH_SUCCESS_URL") > ${server_log} 2>&1 < /dev/null & disown; sleep 1; echo 'deploy started'"

  log "Tailing deploy log on ${DEPLOY_SSH_HOST} (Ctrl-C stops watching, deploy continues on server) ..."
  run_ssh "bash -c 'tail -n +1 -f ${server_log} & TAILPID=\$!; while [ ! -f /tmp/involo-deploy.done ]; do sleep 2; done; kill \$TAILPID 2>/dev/null; wait \$TAILPID 2>/dev/null'" || true

  log "Deploy log finished. Re-attach with: ssh ${DEPLOY_SSH_HOST} -t 'tail -n 100 -f ${server_log}'"
}

main() {
  local_push
  server_deploy
}

main "$@"
