# supabase/

Schema and migrations for the Supabase project (Postgres, `ca-central-1`). Managed with the
Supabase CLI; migrations here are the source of truth, not changes made in the dashboard.

## Setup (once you have a Supabase project)

```
npx supabase login
npx supabase link --project-ref <your-project-ref>
npx supabase db push
```

## Adding a migration

```
npx supabase migration new <name>
```

## Design notes

- Tenant isolation is enforced by row-level security (`clinic_id` + `clinic_members`),
  not by application-layer filtering -- see `migrations/00000000000001_init_schema.sql`.
- Patients are not Supabase Auth users. Patient-facing writes (check-ins, replies) go
  through `apps/api` using the service role key, which validates the patient's signed
  link token itself before writing.
- `audit_log` is append-only at the database level (`UPDATE`/`DELETE` revoked for
  `authenticated`/`anon`), matching data-catalogue D16/D17.
- Only synthetic data until the privacy review is done -- see the project's
  data-catalogue.md. Nothing here should be pointed at a real patient yet.
