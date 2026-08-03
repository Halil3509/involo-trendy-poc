# Vercel Frontend + Local Backend Setup

This guide covers running the FastAPI backend locally and serving the Next.js frontend from Vercel with a shared HTTPS tunnel.

## Overview

- **Frontend**: deployed to Vercel at `https://frontend-alpha-rust-17.vercel.app`.
- **Backend**: runs locally on port `8021`.
- **Public HTTPS tunnel**: configured in `.env` (`INVOLO_NGROK_DOMAIN`) and started by `run-local.sh` / `run.sh`. The default `https://involo-app.loca.lt` is a free `localtunnel` subdomain.
- **Auth flow**: cross-origin cookies use `Secure=true` and `SameSite=None`.

## Fixed public domain

`run-local.sh` and `run.sh` use the variables in `.env`:

```dotenv
INVOLO_NGROK_DOMAIN=involo-app.loca.lt
INVOLO_TUNNEL_STRICT=false
```

- `INVOLO_NGROK_DOMAIN`: fixed `loca.lt` subdomain to request. Only the first label is passed to `localtunnel --subdomain`. Use `auto` for a random URL.
- `INVOLO_TUNNEL_STRICT`: `true` aborts if the fixed subdomain cannot be reserved; `false` falls back to a random URL.

For a production-grade setup, use a paid static domain with Cloudflare Tunnel instead of localtunnel.

## Files changed

- `.env` – backend CORS origins, Instagram OAuth redirect, and public tunnel URL.
- `run-local.sh` / `run.sh` – override `INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL` to `http://localhost:8020/instagram/callback` so the local frontend receives the OAuth callback.
- `deploy.sh` – sets `INVOLO_INSTAGRAM_OAUTH_SUCCESS_URL` to the Vercel frontend for Docker deployments.
- `frontend/src/lib/api.ts` – sends `Bypass-Tunnel-Reminder: true` when the API host is `*.loca.lt`, so localtunnel’s abuse-reminder page does not block API calls.
- `frontend/next.config.ts` – builds with default Vercel output on Vercel and keeps `standalone` locally.
- Vercel project environment variables – `NEXT_PUBLIC_API_URL=https://involo-app.loca.lt` for Preview and Production.

## Start the local backend

```bash
# From the repo root
./run-local.sh up
```

`run-local.sh` reads the tunnel variables from `.env`, starts `localtunnel`, and prints the public URL. For a manual setup, start `localtunnel` on port `8021` and export `INVOLO_NGROK_DOMAIN` / `INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI` with the assigned host.

## Verify

```bash
curl -sS https://involo-app.loca.lt/health/live
```

Expected: `{"status":"ok"}`

## Re-deploy the frontend

```bash
cd frontend
vercel --prod
```

## Notes

- If you restart localtunnel, the Vercel env variable and frontend build must be updated to the new public URL.
- `localtunnel` shows a “Tunnel website ahead!” page to new visitors from the same public IP every 7 days. The frontend sends `Bypass-Tunnel-Reminder: true` to skip that page for API requests.
- For a production-grade setup, use a paid static domain with Cloudflare Tunnel instead of localtunnel.
