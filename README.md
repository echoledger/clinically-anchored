# Clinically Anchored

Two-way patient messaging for surgical clinics, with an optional AnchorRegistry provenance
receipt layer. Monorepo, deliberately structured so it can be split into separate repos
later without a code refactor if that ever becomes necessary (a hire, a contractor who
should see one part and not the other, or divergent release cadence).

## Structure

```
apps/web       Next.js (TypeScript) -- clinician dashboard, patient check-in flow.
                Deployed to Vercel, root directory apps/web.
apps/api       FastAPI (Python) -- check-in intake, red-flag rule engine, summary
                generation, the hash-chained audit log writer. Deployed to Railway,
                root directory apps/api.
supabase/      Postgres schema and migrations (Supabase, ca-central-1). Row-level
                security enforces clinic-level tenant isolation -- see
                supabase/migrations/00000000000001_init_schema.sql.
packages/      Future shared JS packages (empty for now).
```

The audit-log/hash-chain *logic* itself is meant to be reusable with AnchorRegistry
(a separate, already-shipped product/repo) -- that reuse is real today, not a "maybe
later," so it belongs in its own installable package/repo rather than here. This
monorepo owns the schema and the writer that populates it; a shared package for the
chaining logic itself is a follow-up, not part of this scaffold.

## The one rule that keeps a later split cheap

`apps/web` never imports server code from `apps/api` directly. It only talks to the API
over HTTP, using a client generated from the API's OpenAPI schema (FastAPI serves this
at `/openapi.json` automatically). Hold that line and extracting either app into its own
repo later is a CI/CD re-wiring exercise (`git filter-repo`, re-point Vercel/Railway,
move secrets) -- not a refactor. Break it, and the split gets expensive.

## Local dev

```
pnpm install                      # apps/web
pnpm dev:web                      # runs apps/web on :3000

cd apps/api
uv sync
cp .env.example .env
uv run uvicorn clinically_anchored_api.main:app --reload   # runs on :8000
```

## Deploy

- **Vercel**: project root directory `apps/web`. Independent deploys per push;
  unaffected by changes under `apps/api` or `supabase/`.
- **Railway**: project root directory `apps/api`, start command from `apps/api/Procfile`.
- **Supabase**: `npx supabase link --project-ref <ref>` then `npx supabase db push`
  from `supabase/` (see `supabase/README.md`).

## Status

Early scaffold (2026-09-28). Real infrastructure, synthetic data only -- see the
project's data-catalogue.md for what's allowed once real patients are involved, and
decisions-and-open-questions.md for the standing product decisions this build assumes
(hospital admin out of the loop, structured check-ins, ConnectingOntario excluded).
