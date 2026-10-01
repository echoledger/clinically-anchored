# apps/api

FastAPI backend for Clinically Anchored. Deployed to Railway (root directory: `apps/api`).

Owns: check-in intake, the red-flag rule engine, rolling-summary generation, and the
hash-chained audit log writer. Never imported directly by `apps/web` -- the web app only
talks to this service over HTTP, using the client generated from this service's OpenAPI
schema (`/openapi.json`). That boundary is what keeps a later repo split (if it's ever
needed) a CI/CD re-wiring exercise rather than a refactor.

## Local dev

```
uv sync
cp .env.example .env   # fill in Supabase values
uv run uvicorn clinically_anchored_api.main:app --reload
```

## Tests / lint

```
uv run pytest
uv run ruff check .
```

## Deploying (Railway)

- **Root Directory must be `/apps/api`** (Service -> Settings -> Source). Otherwise Railway
  builds the repo root, detects Node/pnpm from the root `package.json`, and fails with
  "No start command detected".
- Start command comes from `Procfile`; `railway.json` pins the builder, the `/health`
  check and the restart policy.
- With `ENVIRONMENT` set to anything but `development`, the app refuses to boot unless
  `CHECKIN_LINK_SECRET`, `AUDIT_SIGNING_KEY`, `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
  are set. Use fresh secrets, not the dev ones, and keep `AUDIT_SIGNING_KEY` stable (change
  `AUDIT_KEY_ID` if you rotate it).

### If Railway keeps serving old code

Symptom: new routes 404 on the deployed api, variables seem ignored, and "Redeploy" changes
nothing. Check `GET /openapi.json` on the deployed URL against `main` (route count), then:

- Service -> Settings -> Source: if it says "Could not load branches" / "Auto deploy
  unavailable", Railway has lost its GitHub connection (the repo lives in the `echoledger`
  org, so the Railway GitHub app must be installed there with access to this repo).
  Reconnect the repo/branch (and re-check Root Directory `/apps/api`).
- **Redeploy** reruns the old source snapshot. After reconnecting, push to `main` or use
  "Deploy latest commit" to build the newest code.
- Last resort that bypasses GitHub: `railway login`, `railway link`, then `railway up` from
  `apps/api` (deploys your local folder, so keep your checkout on `main`).
