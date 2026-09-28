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
