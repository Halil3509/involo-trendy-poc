#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

COMMAND="${1:-up}"

kill_local_services() {
    if [ -s .run.pids ]; then
        while read -r pid; do
            kill -TERM "$pid" 2>/dev/null || true
        done < .run.pids
    fi
    pkill -f 'uvicorn app.main:app' 2>/dev/null || true
    pkill -f 'celery -A app.tasks worker' 2>/dev/null || true
    pkill -f 'celery -A app.tasks beat' 2>/dev/null || true
    pkill -f 'watchfiles.*celery' 2>/dev/null || true
    pkill -f 'next dev --port 8020' 2>/dev/null || true
    pkill -f 'next-server' 2>/dev/null || true
    pkill -f 'localtunnel --port' 2>/dev/null || true
    pkill -f 'lt --port' 2>/dev/null || true
    pkill -f 'npx.*localtunnel' 2>/dev/null || true
    fuser -k 8020/tcp 8021/tcp 2>/dev/null || true
    rm -f .run.pids
}

if [ "$COMMAND" == "down" ] || [ "$COMMAND" == "stop" ]; then
    kill_local_services
    exit 0
fi

if [ "$COMMAND" != "up" ]; then
    echo "Usage: $0 [up|down]"
    exit 1
fi

if [ ! -f .env ]; then
    echo ".env file not found. Copy .env.example and configure it first:"
    echo "  cp .env.example .env"
    exit 1
fi

# Preserve values explicitly passed in the environment before .env is sourced.
_PASSTHROUGH_NGROK_DOMAIN="${INVOLO_NGROK_DOMAIN:-}"
_PASSTHROUGH_OAUTH_SUCCESS_URL="${INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL:-}"

# Export variables from .env for the local backend/frontend processes
set -a
# shellcheck disable=SC1091
source .env
set +a

if [ -n "${_PASSTHROUGH_NGROK_DOMAIN:-}" ]; then
    export INVOLO_NGROK_DOMAIN="$_PASSTHROUGH_NGROK_DOMAIN"
fi

if [ -z "${INVOLO_JWT_SECRET:-}" ]; then
    echo "INVOLO_JWT_SECRET is not set. Add it to .env first."
    exit 1
fi

# Normalize INVOLO_NGROK_DOMAIN if the user pasted a full URL.
if [ -n "${INVOLO_NGROK_DOMAIN:-}" ] && [ "${INVOLO_NGROK_DOMAIN}" != "auto" ]; then
    _RAW_NGROK_DOMAIN="${INVOLO_NGROK_DOMAIN#https://}"
    _RAW_NGROK_DOMAIN="${_RAW_NGROK_DOMAIN#http://}"
    _RAW_NGROK_DOMAIN="${_RAW_NGROK_DOMAIN%%/*}"
    export INVOLO_NGROK_DOMAIN="$_RAW_NGROK_DOMAIN"
fi

mkdir -p logs
rm -f .run.pids
kill_local_services

cleanup() {
    echo
    echo "Shutting down main services..."
    kill_local_services
    exit 0
}
trap cleanup INT TERM EXIT

# Setup backend virtualenv and dependencies with uv
cd backend
uv sync --extra dev --quiet
cd ..

source backend/.venv/bin/activate

# Chromium is only required for the real Instagram Playwright scraper/creator tracking
# and the Playwright brand-analysis PDF provider. Fixture/fake providers need no browser.
# python -m playwright install is idempotent: it skips the download when the browser is
# already cached. Set INVOLO_INSTALL_PLAYWRIGHT=false to opt out.
needs_browser=0
if [ "${INVOLO_SCRAPER_ADAPTER:-fixture}" != "fixture" ] || \
   [ "${INVOLO_CREATOR_TRACKING_PROVIDER:-fixture}" = "playwright" ] || \
   [ "${INVOLO_BRAND_ANALYSIS_PDF_PROVIDER:-playwright}" = "playwright" ]; then
    needs_browser=1
fi
if [ "$needs_browser" -eq 1 ] && [ "${INVOLO_INSTALL_PLAYWRIGHT:-true}" = "true" ]; then
    # System dependencies change rarely and require root, so only install them when missing.
    if ! python -m playwright install-deps chromium --dry-run >/dev/null 2>&1; then
        echo "Installing Playwright system dependencies (may require sudo)..."
        python -m playwright install-deps chromium
    fi
    python -m playwright install chromium chromium-headless-shell
fi

# Optional public HTTPS tunnel for Instagram OAuth (required when Meta app is Live).
# INVOLO_TUNNEL_STRICT=true aborts if a fixed subdomain cannot be reserved.
# Set INVOLO_NGROK_DOMAIN=auto for a random URL, or a fixed loca.lt subdomain.

# Start a public HTTPS tunnel on the given port using localtunnel.
# Sets PUBLIC_URL, INVOLO_NGROK_DOMAIN and INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI.
start_public_tunnel() {
    local port="${1:-8021}"
    local requested_domain="${INVOLO_NGROK_DOMAIN:-}"
    local strict="${INVOLO_TUNNEL_STRICT:-false}"

    if [ -z "$requested_domain" ]; then
        echo "INVOLO_NGROK_DOMAIN is not set; skipping public tunnel."
        return 1
    fi

    local requested_host=""
    local subdomain=""
    if [ "$requested_domain" != "auto" ]; then
        requested_host="$requested_domain"
        subdomain="${requested_domain%%.*}"
    fi

    local public_url=""

    for attempt in $(seq 1 3); do
        rm -f logs/localtunnel.log
        if [ "$requested_domain" = "auto" ]; then
            echo "Starting localtunnel with a random subdomain (attempt $attempt)..."
            npx --yes localtunnel --port "$port" > logs/localtunnel.log 2>&1 &
        else
            echo "Starting localtunnel to https://${requested_host} (attempt $attempt)..."
            npx --yes localtunnel --port "$port" --subdomain "$subdomain" > logs/localtunnel.log 2>&1 &
        fi
        local pid=$!

        local found_url=""
        for i in $(seq 1 60); do
            found_url=$(grep -oE 'https://[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+' logs/localtunnel.log | head -1 || true)
            if [ -n "$found_url" ]; then
                break
            fi
            sleep 0.5
        done

        if [ -z "$found_url" ]; then
            echo "Failed to discover localtunnel URL on attempt $attempt. See logs/localtunnel.log."
            kill -TERM "$pid" 2>/dev/null || true
            sleep 2
            continue
        fi

        local found_host="${found_url#https://}"
        if [ -n "$requested_host" ] && [ "$found_host" != "$requested_host" ]; then
            if [ "$strict" = "true" ]; then
                echo "Requested host ${requested_host} but localtunnel assigned ${found_host} and INVOLO_TUNNEL_STRICT=true. Aborting."
                kill -TERM "$pid" 2>/dev/null || true
                exit 1
            fi
            echo "Requested host ${requested_host} but localtunnel assigned ${found_host}. Retrying..."
            kill -TERM "$pid" 2>/dev/null || true
            sleep 2
            continue
        fi

        public_url="$found_url"
        echo "$pid" >> .run.pids
        break
    done

    if [ -z "$public_url" ] && [ -n "$requested_host" ]; then
        if [ "$strict" = "true" ]; then
            echo "Could not reserve https://${requested_host} after 3 attempts and INVOLO_TUNNEL_STRICT=true. Aborting."
            exit 1
        fi
        echo "Could not reserve https://${requested_host} after 3 attempts. Falling back to a random localtunnel domain..."
        rm -f logs/localtunnel.log
        npx --yes localtunnel --port "$port" > logs/localtunnel.log 2>&1 &
        local pid=$!

        local found_url=""
        for i in $(seq 1 60); do
            found_url=$(grep -oE 'https://[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+' logs/localtunnel.log | head -1 || true)
            if [ -n "$found_url" ]; then
                break
            fi
            sleep 0.5
        done

        if [ -n "$found_url" ]; then
            public_url="$found_url"
            echo "$pid" >> .run.pids
            echo "WARNING: localtunnel assigned ${public_url} instead of the requested https://${requested_host}."
        fi
    fi

    if [ -z "$public_url" ]; then
        echo "WARNING: localtunnel could not start. Continuing with localhost."
        PUBLIC_URL="http://localhost:${port}"
        export INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI="${PUBLIC_URL}/api/v1/instagram/oauth/callback"
        INVOLO_NGROK_DOMAIN=""
        return 0
    fi

    export INVOLO_NGROK_DOMAIN="${public_url#https://}"
    export INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI="${public_url}/api/v1/instagram/oauth/callback"
    PUBLIC_URL="$public_url"
    echo "Public URL: $public_url"
    echo "Instagram OAuth redirect URI (paste this into Meta/Facebook app settings):"
    echo "  ${INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI}"
    echo "Instagram Webhooks callback URL (paste into Meta App Dashboard > Webhooks):"
    echo "  ${public_url}/api/v1/instagram/webhook"
}

# Capture the requested tunnel domain before start_public_tunnel mutates it.
_requested_ngrok_domain="${INVOLO_NGROK_DOMAIN:-}"

if [ -n "${INVOLO_NGROK_DOMAIN:-}" ]; then
    start_public_tunnel 8021
fi

# Point the frontend at the public API only when a fixed tunnel domain was requested,
# otherwise use the local backend. Export it so Next.js does not fall back to a stale
# NEXT_PUBLIC_API_URL inherited from a sourced .env file.
if [ -n "${_requested_ngrok_domain:-}" ] && [ "${_requested_ngrok_domain}" != "auto" ]; then
    NEXT_PUBLIC_API_URL="https://${INVOLO_NGROK_DOMAIN}"
else
    NEXT_PUBLIC_API_URL="http://localhost:8021"
fi
echo "NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}" > frontend/.env.local
export NEXT_PUBLIC_API_URL

# Redirect the Instagram OAuth callback back to the local frontend unless overridden.
export INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL="${_PASSTHROUGH_OAUTH_SUCCESS_URL:-http://localhost:8020/instagram/callback}"

# Start main services in the background and collect their PIDs
cd backend

if [ -n "${INVOLO_NO_RELOAD:-}" ]; then
    echo "Starting FastAPI backend on http://localhost:8021 (no reload)..."
    uvicorn app.main:app --host 0.0.0.0 --port 8021 &
    echo "$!" >> ../.run.pids

    echo "Starting Celery worker..."
    celery -A app.tasks worker --loglevel=INFO --pool=solo &
    echo "$!" >> ../.run.pids

    echo "Starting Celery beat scheduler..."
    celery -A app.tasks beat --loglevel=INFO &
    echo "$!" >> ../.run.pids
else
    echo "Starting FastAPI backend on http://localhost:8021 ..."
    uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0 --port 8021 &
    echo "$!" >> ../.run.pids

    echo "Starting Celery worker (auto-reload on app changes)..."
    watchfiles --filter python --sigint-timeout 15 "celery -A app.tasks worker --loglevel=INFO --pool=solo" app &
    echo "$!" >> ../.run.pids

    echo "Starting Celery beat scheduler (auto-reload on app changes)..."
    watchfiles --filter python --sigint-timeout 15 "celery -A app.tasks beat --loglevel=INFO" app &
    echo "$!" >> ../.run.pids
fi

cd ../frontend

if [ ! -d node_modules ] || [ ! -f node_modules/.install-stamp ] || [ package.json -nt node_modules/.install-stamp ]; then
    echo "Installing frontend dependencies (if needed)..."
    npm install > ../logs/frontend-install.log 2>&1
    touch node_modules/.install-stamp
else
    echo "Frontend dependencies are up to date."
fi

echo "Starting Next.js frontend on http://localhost:8020 ..."
npm run dev &
echo "$!" >> ../.run.pids

cd ..

echo
echo "Involo is running locally:"
echo "  API:       http://localhost:8021"
echo "  Frontend:  http://localhost:8020"
if [ -n "${INVOLO_NGROK_DOMAIN:-}" ]; then
    echo "  Public:    https://${INVOLO_NGROK_DOMAIN}"
    echo "  Instagram OAuth redirect URI: ${INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI}"
fi
echo
echo "Press Ctrl+C to stop everything."

wait
