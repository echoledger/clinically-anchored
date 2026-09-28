# database — service brief

Read this before touching `supabase/`. It's the product context a fresh thread needs;
`supabase/README.md` has the CLI commands (link, push, new migration).

## What's actually provisioned right now

A real Supabase project exists, region `ca-central-1` (Canada Central) — that's a
hard requirement, not a default; data residency matters for PHIPA. It's running on
the free plan for now: 2 active projects per account, shared with AnchorRegistry, so
AnchorRegistry's dev/testnet project is currently paused to make room for this one.
Nothing here should assume Pro-tier features (real backups, longer log retention)
exist yet — see the "Open items" section below.

Migration `00000000000001_init_schema.sql` is applied (or ready to apply via
`npx supabase db push`): `clinics`, `clinic_members`, `patients`, `procedures`,
`check_ins`, `messages`, `audit_log`. All clinic-scoped tables have row-level
security enabled, enforced through `clinic_members` — see below.

## The tenant isolation model

Tenant = clinic. Every clinical table carries `clinic_id`, and RLS policies (via the
`is_clinic_member()` helper function) scope every read to clinics the authenticated
user belongs to. This is enforced by Postgres, not application code — a bug in
`apps/api` can't leak one clinic's patients into another clinic's query results,
because the database refuses the query. When adding a new table that holds
clinic-scoped data, it gets a `clinic_id` column and an RLS policy from the same
migration that creates it — this isn't an optional follow-up step.

**Patients are not Supabase Auth users.** No patient password. Patient-facing writes
(check-ins, patient-side messages) go through `apps/api` using the service role key,
which bypasses RLS by design — `apps/api` is what validates the patient's signed link
token before writing, not the database. RLS in this schema protects the
clinician/delegate-facing paths (the web app talking to Supabase directly for auth,
and any direct client reads that go through Supabase rather than the api).

`audit_log` is append-only at the database level: `UPDATE`/`DELETE` are revoked for
`authenticated`/`anon` entirely, not just gated by a policy. Only the service role
(i.e. only `apps/api`) can write to it, and nothing can edit or delete a row once
written.

## Mapping to the data catalogue

`claude/data-catalogue.md` is the authoritative list of every data class the product
touches, with sensitivity, residency, and retention notes. The current schema covers
a subset of it:

| Catalogue ID | What | Table |
|---|---|---|
| D4 | Patient identity/contact | `patients` |
| D5 | Clinician/delegate accounts | `clinic_members` (roles: owner/clinician/delegate) |
| D6/D7 | Messages (patient/clinician) | `messages` |
| D29 | Structured check-in answers | `check_ins` |
| D16/D17 | Audit log, signed and hash-chained | `audit_log` |

Not yet modeled, and not needed until their stage comes up: D8 (AI drafts), D9
(wound photos — v2, blocked in v1), D10 (confirmed summary facts, sourced from her
own Accuro notes, later stage), D11 (touchpoint events — calls/visits logged
manually), D12 (consent records), D13 (protocol/template library), D14 (derived
scores/triage priority), D22 (model/prompt version metadata, hashed into attestation
events). Add these as their features get built, each with its own migration — don't
pre-build the whole catalogue speculatively.

## Open items (genuinely unresolved, don't guess)

- **Plan tier for compliance features.** Free plan has no backups and 1-day log
  retention. Pro ($25/mo) gets 7-day backups and 7-day logs but still no
  HIPAA-equivalent add-on (that's Team, $599/mo). Fine for synthetic data; revisit
  before any real patient touches this.
- **Inference location** (local vs. Canadian-region zero-retention vs. minimal-fact
  cloud) is unresolved — don't assume where embeddings/LLM calls happen when
  designing tables that might feed them.
- **OHIP number** — data-catalogue.md leans toward not storing it at all. Don't add
  the column without checking whether that's been settled.
- **Retention period and system-of-record question** — is this app the system of
  record, or a channel on top of Accuro (likely)? Affects what "delete" should mean
  here, so don't build hard-delete flows without this being settled.

## Reference

- `../../claude/data-catalogue.md` — full data classification, sensitivity, retention.
- `../../claude/decisions-and-open-questions.md` — what's decided vs. still open.
- `supabase/migrations/00000000000001_init_schema.sql` — read the comments in the
  migration itself; they explain the *why* behind each RLS choice, not just the *what*.
